"""Background grounding prefetch for the LM authoring loop.

Starts at the top of `PromptAuthoringService.author` /
`PromptAuthoringSession.send` and runs concurrently with the first LM
turn. Populates `registry._grounding_cache` so that when the model later
calls a tool whose result is already cached, dispatch returns it
without hitting the catalog or making another LM call.

Two stages:

- **C1 (deterministic, no LM)** — keyword heuristics on the user prompt
  fan out into the parallel-safe tool surface
  (`lookup_workspace`, `list_valid_positions`, `search_labware`,
  `lookup_rules`). Cheap, always on by default.
- **C2 (subagent, optional)** — one independent LM completion that
  emits a JSON list of catalog lookups. Time-budgeted; merged into the
  cache. Failures are logged and swallowed; the main loop is unaffected.

The prefetcher never blocks the main authoring graph. If a cache miss
happens because the prefetch hasn't completed yet, dispatch falls through
to a live call exactly as before.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .tools import PARALLEL_SAFE_TOOLS, AuthoringToolRegistry

_log = logging.getLogger(__name__)


# Keyword heuristics — terms in user prompts that map to likely catalog
# searches. Intentionally narrow; false positives just waste a DB query.
_KEYWORD_TO_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "trough": ("100ml Trough", "300ml SBS"),
    "reservoir": ("100ml Trough", "300ml SBS"),
    "plate": ("96_ABgene", "Plate96"),
    "96-well": ("96_ABgene", "Plate96"),
    "96 well": ("96_ABgene", "Plate96"),
    "tip": ("MCA96", "FCA, 1000ul"),
    "magnet": ("24 Magnet", "SPRIPlate"),
    "ampure": ("100ml Trough", "24 Magnet"),
    "spri": ("SPRIPlate", "24 Magnet"),
    "wash": ("100ml Trough",),
    "elution": ("96_ABgene",),
}


@dataclass
class PrefetchConfig:
    deterministic: bool = True
    subagent: bool = False
    subagent_timeout_s: float = 30.0
    pool_size: int = 8
    # When True (and `subagent=True`), use the multi-category subagent
    # fan-out (`category_agents.run_category_agents`) instead of the
    # legacy single-subagent JSON-emitter path. Defaults to True because
    # the multi-agent path is the only one that actually fires concurrent
    # LM requests — the single-agent path was retained only for tests.
    multi_agent: bool = True


class GroundingPrefetcher:
    """Schedules deterministic + (optional) subagent prefetch.

    Owns a private ThreadPoolExecutor that lives until `shutdown()` is
    called. Safe to start once per session — `start(prompt)` may be
    invoked again with a new prompt to refresh deterministic guesses
    (cache entries are write-once so refreshes are no-ops on hits).
    """

    def __init__(
        self,
        *,
        registry: AuthoringToolRegistry,
        config: PrefetchConfig | None = None,
        subagent_invoke: "callable | None" = None,
        lc_client: Any | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or PrefetchConfig()
        self._subagent_invoke = subagent_invoke
        # LangChain-shaped client (post-`adapt_client`). Used by the
        # multi-agent C2 path — each category agent calls
        # `lc_client.bind_tools(...).invoke(...)` on this instance.
        self._lc_client = lc_client
        self._pool: ThreadPoolExecutor | None = None
        self._futures: list[Future[Any]] = []
        self._lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────

    def _ensure_pool(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=max(2, self.config.pool_size),
                    thread_name_prefix="authoring-prefetch",
                )
            return self._pool

    def shutdown(self) -> None:
        with self._lock:
            for fut in self._futures:
                fut.cancel()
            self._futures.clear()
            if self._pool is not None:
                self._pool.shutdown(wait=False)
                self._pool = None

    # ── stage entry points ───────────────────────────────────────

    def start(self, prompt: str) -> None:
        """Kick off prefetch stages. Returns immediately."""
        if self.config.deterministic:
            self._start_deterministic(prompt)
        if self.config.subagent:
            if self.config.multi_agent and self._lc_client is not None:
                self._start_category_agents(prompt)
            elif self._subagent_invoke is not None:
                self._start_subagent(prompt)

    def _start_deterministic(self, prompt: str) -> None:
        pool = self._ensure_pool()
        ws = self.registry.workspace_name
        tasks: list[tuple[str, dict[str, Any]]] = []
        if ws:
            tasks.append(("lookup_workspace", {"name_or_guid": ws}))
        tasks.append(("lookup_rules", {}))
        for term in _extract_search_terms(prompt):
            tasks.append(("search_labware", {"query": term}))
        for name, payload in tasks:
            if name not in PARALLEL_SAFE_TOOLS:
                continue
            if self.registry.cache_lookup(name, payload) is not None:
                continue
            self._futures.append(pool.submit(self._run_and_cache, name, payload))

    def _run_and_cache(self, name: str, payload: dict[str, Any]) -> None:
        try:
            result = self.registry._dispatch_pure(name, payload)
        except Exception as exc:
            _log.debug("prefetch %s failed: %s", name, exc)
            return
        self.registry.cache_store(name, payload, result)

    def _start_subagent(self, prompt: str) -> None:
        pool = self._ensure_pool()
        self._futures.append(pool.submit(self._run_subagent, prompt))

    def _start_category_agents(self, prompt: str) -> None:
        """Spawn the multi-category subagent fan-out on a dedicated thread.

        We submit a single supervisor future to our own pool; the
        supervisor builds its own pool inside `run_category_agents` so
        the N agents run concurrently against LM Studio.
        """
        pool = self._ensure_pool()
        self._futures.append(pool.submit(self._run_category_agents, prompt))

    def _run_category_agents(self, prompt: str) -> None:
        from .category_agents import run_category_agents

        # Each category agent does one LM round-trip; we want them all
        # in flight at once, so the inner pool sizes itself to the
        # number of selected categories (capped).
        with ThreadPoolExecutor(
            max_workers=max(4, self.config.pool_size),
            thread_name_prefix="authoring-category",
        ) as inner:
            try:
                run_category_agents(
                    prompt=prompt,
                    registry=self.registry,
                    client=self._lc_client,
                    pool=inner,
                    timeout_s=self.config.subagent_timeout_s,
                )
            except Exception as exc:
                _log.debug("category-agent prefetch failed: %s", exc)

    def _run_subagent(self, prompt: str) -> None:
        try:
            plan = self._subagent_invoke(prompt, self.config.subagent_timeout_s)  # type: ignore[misc]
        except Exception as exc:
            _log.debug("subagent prefetch failed: %s", exc)
            return
        if not isinstance(plan, list):
            return
        # Each plan entry: {"tool": "search_labware", "arguments": {...}}
        for entry in plan:
            if not isinstance(entry, dict):
                continue
            tool = entry.get("tool")
            args = entry.get("arguments") or {}
            if not isinstance(tool, str) or tool not in PARALLEL_SAFE_TOOLS:
                continue
            if not isinstance(args, dict):
                continue
            if self.registry.cache_lookup(tool, args) is not None:
                continue
            try:
                result = self.registry._dispatch_pure(tool, args)
            except Exception as exc:
                _log.debug("subagent-suggested %s failed: %s", tool, exc)
                continue
            self.registry.cache_store(tool, args, result)


# ── helpers ──────────────────────────────────────────────────────────


_WORD_RE = re.compile(r"[A-Za-z0-9_-]+")


def _extract_search_terms(prompt: str) -> list[str]:
    """Map prompt keywords to a deduped list of catalog search seeds.

    Crude on purpose — false positives just spend a DB query. The point
    is to populate the cache for the lookups the model is *most likely*
    to issue in the first few turns.
    """
    if not prompt:
        return []
    lowered = prompt.lower()
    seeds: list[str] = []
    seen: set[str] = set()
    for keyword, terms in _KEYWORD_TO_SEARCH_TERMS.items():
        if keyword in lowered:
            for term in terms:
                if term not in seen:
                    seen.add(term)
                    seeds.append(term)
    return seeds


def make_subagent_invoke(client: Any, model: str | None = None) -> "callable":
    """Build the subagent-invoke callable that issues one independent
    LM completion and parses its JSON output.

    Returns a function `(prompt, timeout_s) -> list[dict]`. The function
    is sync (concurrent execution is provided by the prefetcher's thread
    pool); `timeout_s` is honored only if the underlying client respects
    it — LMStudioChatClient currently does not, so the timeout is best-
    effort.
    """
    SUBAGENT_SYSTEM = (
        "You are a grounding-planner. Given a protocol authoring request, "
        "emit ONLY a compact JSON list of catalog lookups you would run to "
        "ground the request. Each item: {\"tool\": <name>, \"arguments\": {...}}. "
        "Allowed tool names: lookup_workspace, list_valid_positions, "
        "search_labware, get_labware, lookup_liquid_class, lookup_rules. "
        "Output strictly the JSON list — no prose, no code fences."
    )

    def invoke(prompt: str, timeout_s: float) -> list[dict[str, Any]]:
        messages = [
            {"role": "system", "content": SUBAGENT_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        # Talk to the same backend over its OpenAI-compatible surface.
        # We accept either a raw LMStudioChatClient (sync `complete`) or
        # a LangChain ChatModel (async `invoke`). LangChain models don't
        # respect the timeout — that's acceptable; the outer cap is the
        # main loop's max_iterations.
        if hasattr(client, "complete"):
            response = client.complete(messages=messages, tools=[])
            content = response.get("content") or ""
        elif hasattr(client, "invoke"):
            response = client.invoke(messages)
            content = getattr(response, "content", "") or ""
        else:
            return []
        return _parse_subagent_json(content)

    return invoke


def _parse_subagent_json(content: str) -> list[dict[str, Any]]:
    if not content:
        return []
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to recover a JSON list embedded in prose.
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    return parsed if isinstance(parsed, list) else []
