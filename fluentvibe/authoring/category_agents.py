"""Parallel category-agent prefetch.

Each `CategoryAgent` is a small dedicated LM round-trip with a focused
system prompt and a restricted toolset (e.g. the *tips* agent only sees
`search_labware` and `get_labware`). Selected agents fire concurrently
on a thread pool; their tool_calls are executed against the registry
and the results land in `registry._grounding_cache`. When the main
authoring loop later asks for any of those grounding facts, dispatch
returns the cached answer without re-querying the catalog or the LM.

Why this beats a single subagent:

* **Real parallel LM throughput.** N independent agents fire N
  concurrent requests against LM Studio. With `parallel_tool_calls`
  inside one agent we'd still see one stream; with N agents we see N.
* **Higher-quality grounding per category.** A focused mini-prompt
  + restricted toolset coaches the model to do thorough work in one
  domain instead of spreading across all of them in one breath.
* **Extensible.** Adding a new domain (e.g. heating blocks, gripper
  paths) is one entry in `DEFAULT_CATEGORIES` — no other code paths
  to touch.

Selection: `always_on=True` agents fire every session. The rest are
gated by lowercase substring matches on the user's prompt against
`keyword_triggers`. False positives waste tokens but don't hurt
correctness; false negatives just leave the cache cold for that
domain (main loop falls through to live lookups, same as today).
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Iterable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .lc_tools import make_lc_tools
from .tools import PARALLEL_SAFE_TOOLS, AuthoringToolRegistry

_log = logging.getLogger(__name__)


_BASE_SYSTEM_PROMPT = (
    "You are a focused grounding subagent for a Tecan FluentControl protocol "
    "authoring system. Your job is to ground ONE category of facts that the "
    "main authoring agent will need. You have a restricted toolset and a "
    "narrow scope.\n\n"
    "Output ONLY tool_calls in a single response. Do NOT write prose or "
    "Python. The orchestrator will execute your tool_calls and cache the "
    "results for the main agent. After your tool_calls fire, your job is "
    "done — do not return additional turns.\n\n"
    "Be thorough but not wasteful: emit every tool_call that is clearly "
    "useful for your category, but avoid duplicates and stay within your "
    "category's scope."
)


@dataclass(frozen=True)
class CategoryAgent:
    """One category subagent — name, scope, tools, triggers.

    Add new categories by appending to `DEFAULT_CATEGORIES`. The
    `allowed_tools` MUST be a subset of `PARALLEL_SAFE_TOOLS` so the
    cache layer accepts the results.
    """
    name: str
    description: str
    keyword_triggers: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    system_prompt_extra: str
    always_on: bool = False
    max_tool_calls: int = 6


@dataclass
class CategoryAgentResult:
    category: str
    ok: bool
    tool_calls: int = 0
    cache_writes: int = 0
    elapsed_s: float = 0.0
    error: str | None = None


# Default seed list. Extend this — don't replace it — when adding
# new domains. Each entry is independent of the others.
DEFAULT_CATEGORIES: tuple[CategoryAgent, ...] = (
    CategoryAgent(
        name="workspace",
        description="canonical workspace and valid deck slot positions",
        keyword_triggers=(),
        allowed_tools=("lookup_workspace", "list_valid_positions"),
        system_prompt_extra=(
            "Category: WORKSPACE. Resolve the canonical or named workspace "
            "with `lookup_workspace`. Then call `list_valid_positions` for "
            "every site family the workspace exposes (e.g. Nest61mm_Pos, "
            "WS_100ml_1)."
        ),
        always_on=True,
        max_tool_calls=6,
    ),
    CategoryAgent(
        name="rules",
        description="protocol-family rules and learned recipes",
        keyword_triggers=(),
        allowed_tools=("lookup_rules",),
        system_prompt_extra=(
            "Category: RULES. Call `lookup_rules` for any protocol_type or "
            "category that fits the user's request (e.g. transfer, "
            "trough_to_plate, cleanup). Issue at most three calls."
        ),
        always_on=True,
        max_tool_calls=3,
    ),
    CategoryAgent(
        name="plates",
        description="standard plates (96-well, 384-well)",
        keyword_triggers=("plate", "well", "96-well", "96 well", "384"),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: PLATES. Search for standard 96-well and 384-well "
            "plates installed in the catalog. Pass category=\"plate\" to "
            "search_labware. Then call get_labware on every promising hit "
            "so the main agent has full metadata."
        ),
        max_tool_calls=8,
    ),
    CategoryAgent(
        name="pcr_plates",
        description="PCR plates and strip tubes",
        keyword_triggers=("pcr", "thermocycle", "thermal cycle", "strip tube", "twin tec"),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: PCR_PLATES. Search for PCR plates and strip tubes "
            "(e.g. 'PCR', 'TwinTec', 'AB-2700'). Use category=\"plate\". "
            "get_labware on top hits."
        ),
        max_tool_calls=6,
    ),
    CategoryAgent(
        name="deep_well_plates",
        description="deep-well plates",
        keyword_triggers=("deep-well", "deep well", "dwp", "2ml plate", "2 ml plate"),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: DEEP_WELL_PLATES. Search for deep-well plates "
            "(square or round, large per-well volume)."
        ),
        max_tool_calls=4,
    ),
    CategoryAgent(
        name="troughs",
        description="troughs and reservoirs",
        keyword_triggers=(
            "trough", "reservoir", "100ml", "300ml", "100 ml", "300 ml",
            "fill all", "fan out", "fan-out", "shared source",
        ),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: TROUGHS. Search for trough and reservoir labware "
            "(100ml, 300ml, SBS troughs). Use category=\"trough\". "
            "get_labware on hits."
        ),
        max_tool_calls=6,
    ),
    CategoryAgent(
        name="mca_tips",
        description="MCA-96 / MCA-384 tip boxes",
        keyword_triggers=(
            "mca", "tip", "96-channel", "96 channel", "multi-channel",
            "multichannel", "fan out", "fan-out",
        ),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: MCA_TIPS. Search for MCA tip boxes (e.g. "
            "'MCA96, 100ul, Box', 'MCA96, 200ul, Box', 'MCA384'). Use "
            "category=\"tip_box\"."
        ),
        max_tool_calls=6,
    ),
    CategoryAgent(
        name="fca_tips",
        description="FCA / LiHa disposable tip boxes",
        keyword_triggers=(
            "fca", "liha", "tip", "single-channel", "single channel",
            "diti", "column-wise", "column wise",
        ),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: FCA_TIPS. Search for FCA / LiHa disposable tip "
            "boxes (e.g. 'FCA, 1000ul SBS', 'FCA, 200ul', 'DiTi'). Use "
            "category=\"tip_box\"."
        ),
        max_tool_calls=6,
    ),
    CategoryAgent(
        name="magnets",
        description="magnet racks and SPRI plates",
        keyword_triggers=(
            "magnet", "spri", "ampure", "bead", "cleanup", "binding",
            "wash step", "elution",
        ),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: MAGNETS. Search for magnet racks and SPRI plates "
            "(e.g. '24 Magnet Plate', 'SPRIPlate'). Use "
            "category=\"magnet_rack\"."
        ),
        max_tool_calls=6,
    ),
    CategoryAgent(
        name="tube_racks",
        description="tube racks and eppendorf carriers",
        keyword_triggers=(
            "tube", "eppendorf", "rack", "low-volume", "low volume",
            "alpaqua", "lv_alpaqua",
        ),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: TUBE_RACKS. Search for tube racks, eppendorf "
            "carriers, and low-volume holders (e.g. 'LV_Alpaqua_A000350')."
        ),
        max_tool_calls=4,
    ),
    CategoryAgent(
        name="waste",
        description="waste chutes and waste containers",
        keyword_triggers=(
            "waste", "chute", "discard", "trash", "tip waste",
            "liquid waste",
        ),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: WASTE. Search for waste chutes, liquid waste "
            "containers, and tip waste sinks."
        ),
        max_tool_calls=4,
    ),
    CategoryAgent(
        name="adapters",
        description="MCA / gripper adapters",
        keyword_triggers=("adapter", "gripper", "mca adapter"),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: ADAPTERS. Search for MCA adapters and gripper "
            "adapters."
        ),
        max_tool_calls=4,
    ),
    CategoryAgent(
        name="filter_plates",
        description="filter plates",
        keyword_triggers=("filter", "filtration", "purification"),
        allowed_tools=("search_labware", "get_labware"),
        system_prompt_extra=(
            "Category: FILTER_PLATES. Search for filter plates."
        ),
        max_tool_calls=4,
    ),
    CategoryAgent(
        name="liquid_classes",
        description="installed liquid classes",
        keyword_triggers=(
            "liquid class", "viscous", "ethanol", "water", "buffer",
            "transfer", "aspirate", "dispense", "pipette", "transferring",
            "uL", " ul ", " ml ",
        ),
        allowed_tools=("lookup_liquid_class",),
        system_prompt_extra=(
            "Category: LIQUID_CLASSES. Resolve installed liquid classes "
            "matching the user's intent (e.g. 'Water Free Single', "
            "'Ethanol Free Single', or any name implied by the prompt). "
            "Issue lookup_liquid_class for each candidate."
        ),
        max_tool_calls=4,
    ),
)


# ── orchestration ────────────────────────────────────────────────────


def _select(prompt: str, categories: Iterable[CategoryAgent]) -> list[CategoryAgent]:
    """Filter categories by always_on + lowercase keyword match."""
    selected: list[CategoryAgent] = []
    lowered = (prompt or "").lower()
    for cat in categories:
        if cat.always_on:
            selected.append(cat)
            continue
        if any(trigger in lowered for trigger in cat.keyword_triggers):
            selected.append(cat)
    return selected


def run_category_agents(
    *,
    prompt: str,
    registry: AuthoringToolRegistry,
    client: Any,
    pool: ThreadPoolExecutor,
    categories: Iterable[CategoryAgent] | None = None,
    timeout_s: float = 240.0,
    ignore_keyword_filter: bool = False,
) -> list[CategoryAgentResult]:
    """Spawn selected category agents in parallel; populate registry cache.

    Returns one `CategoryAgentResult` per agent, in completion order.
    Logs each agent's elapsed time and tool-call count to stderr so the
    user can see the parallelism happening at a glance.

    Args:
        ignore_keyword_filter: when True, every passed category is run
            regardless of keyword match. Used by the `ground_in_parallel`
            tool, where the main LM has already chosen which categories
            apply.
    """
    cats = list(categories or DEFAULT_CATEGORIES)
    if ignore_keyword_filter:
        selected = list(cats)
    else:
        selected = _select(prompt, cats)
    if not selected:
        return []

    started_at = time.monotonic()
    futures: dict[Future[CategoryAgentResult], CategoryAgent] = {}
    for cat in selected:
        futures[pool.submit(_run_one, cat, prompt, registry, client)] = cat

    completed: dict[str, CategoryAgentResult] = {}
    try:
        for fut in as_completed(futures, timeout=timeout_s):
            cat = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = CategoryAgentResult(
                    category=cat.name,
                    ok=False,
                    elapsed_s=time.monotonic() - started_at,
                    error=str(exc),
                )
            completed[cat.name] = res
            _log_result(res)
    except Exception as exc:
        # `as_completed` raises TimeoutError when the deadline is hit;
        # report the real elapsed time on each abandoned agent so the log
        # actually reflects how long they ran. Cancel the queued ones;
        # in-flight ones will finish and have their results discarded.
        elapsed = time.monotonic() - started_at
        for fut, cat in futures.items():
            if cat.name in completed:
                continue
            fut.cancel()
            res = CategoryAgentResult(
                category=cat.name,
                ok=False,
                elapsed_s=elapsed,
                error=f"timeout after {elapsed:.1f}s: {exc}",
            )
            completed[cat.name] = res
            _log_result(res)

    final = [completed[c.name] for c in selected if c.name in completed]

    # Concurrency summary: wall-clock vs sum of per-agent times.
    # If wall-clock ≈ sum-of-times, agents ran serially (LM Studio
    # serializing). If wall-clock ≈ max-of-times, they ran concurrently.
    import sys as _sys
    wall = time.monotonic() - started_at
    total_agent_time = sum(r.elapsed_s for r in final if r.elapsed_s)
    n = len(final)
    if n > 0:
        concurrency = total_agent_time / wall if wall > 0 else 0.0
        print(
            f"[prefetch] summary: {n} agents, wall {wall:.1f}s, "
            f"sum {total_agent_time:.1f}s, effective concurrency "
            f"{concurrency:.1f}x (1.0=serial, {n}.0=fully parallel)",
            file=_sys.stderr, flush=True,
        )
    return final


def _run_one(
    cat: CategoryAgent,
    prompt: str,
    registry: AuthoringToolRegistry,
    client: Any,
) -> CategoryAgentResult:
    """Drive one category agent: build a restricted toolset, fire one LM
    call, execute the returned tool_calls, store results in the cache.
    """
    # Sanity: every allowed tool must be parallel-safe (so the cache
    # accepts it). Skip the agent rather than throwing — wrong wiring
    # shouldn't crash the main loop.
    bad = [t for t in cat.allowed_tools if t not in PARALLEL_SAFE_TOOLS]
    if bad:
        return CategoryAgentResult(
            category=cat.name, ok=False,
            error=f"non-parallel-safe tools in agent: {bad}",
        )

    t0 = time.monotonic()
    # Log the moment this agent actually begins running on its worker
    # thread. If LM Studio is serving requests in parallel we'll see N
    # of these "started" lines bunched together; if it's serializing,
    # each "started" will appear right after the previous "finished".
    import sys as _sys
    print(
        f"[prefetch] category {cat.name!r}: started at t+{t0 % 100:.2f}s",
        file=_sys.stderr, flush=True,
    )
    lc_tools = make_lc_tools(registry, allowed=set(cat.allowed_tools))
    if not lc_tools:
        return CategoryAgentResult(
            category=cat.name, ok=False,
            elapsed_s=time.monotonic() - t0,
            error="no matching tools resolved",
        )

    try:
        bound = client.bind_tools(lc_tools)
    except Exception as exc:
        return CategoryAgentResult(
            category=cat.name, ok=False,
            elapsed_s=time.monotonic() - t0,
            error=f"bind_tools failed: {exc}",
        )

    sys_prompt = _BASE_SYSTEM_PROMPT + "\n\n" + cat.system_prompt_extra
    messages = [SystemMessage(content=sys_prompt), HumanMessage(content=prompt)]

    try:
        response = bound.invoke(messages)
    except Exception as exc:
        return CategoryAgentResult(
            category=cat.name, ok=False,
            elapsed_s=time.monotonic() - t0,
            error=f"invoke failed: {exc}",
        )

    tool_calls = list(getattr(response, "tool_calls", None) or [])
    if cat.max_tool_calls and len(tool_calls) > cat.max_tool_calls:
        tool_calls = tool_calls[: cat.max_tool_calls]

    cache_writes = 0
    for tc in tool_calls:
        name = tc.get("name") or ""
        if name not in cat.allowed_tools:
            continue  # model went off-script; ignore
        args = tc.get("args") or {}
        if not isinstance(args, dict):
            continue
        if registry.cache_lookup(name, args) is not None:
            continue
        try:
            result = registry._dispatch_pure(name, args)
        except Exception as exc:
            _log.debug("category %s: %s failed: %s", cat.name, name, exc)
            continue
        registry.cache_store(name, args, result)
        cache_writes += 1

    return CategoryAgentResult(
        category=cat.name,
        ok=True,
        tool_calls=len(tool_calls),
        cache_writes=cache_writes,
        elapsed_s=time.monotonic() - t0,
    )


def _log_result(res: CategoryAgentResult) -> None:
    import sys

    if res.ok:
        msg = (
            f"[prefetch] category {res.category!r}: "
            f"{res.tool_calls} tool_call(s), {res.cache_writes} cached, "
            f"{res.elapsed_s:.1f}s"
        )
    else:
        msg = (
            f"[prefetch] category {res.category!r}: FAILED in "
            f"{res.elapsed_s:.1f}s — {res.error}"
        )
    print(msg, file=sys.stderr, flush=True)
