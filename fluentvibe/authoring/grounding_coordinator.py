"""Orchestrator-owned parallel grounding for prompt authoring."""

from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable

from .category_agents import DEFAULT_CATEGORIES, CategoryAgent, run_category_agents


@dataclass(frozen=True)
class AuthoringContext:
    """Stable context passed to automatic grounding workers."""

    original_prompt: str
    latest_user_text: str
    user_history_text: str
    workspace_name: str | None = None
    workspace_guid: str | None = None
    intent: dict[str, Any] | None = None
    pending_approval_kind: str | None = None

    def prompt_for_grounding(self) -> str:
        parts = [
            "Original protocol request:",
            self.original_prompt.strip(),
            "",
            "Full user conversation context:",
            self.user_history_text.strip(),
        ]
        if self.latest_user_text.strip():
            parts.extend(["", "Latest user reply:", self.latest_user_text.strip()])
        if self.intent:
            parts.extend(["", "Declared intent:", json.dumps(self.intent, sort_keys=True)])
        if self.workspace_name or self.workspace_guid:
            parts.extend([
                "",
                "Workspace binding:",
                json.dumps(
                    {"name": self.workspace_name, "guid": self.workspace_guid},
                    sort_keys=True,
                ),
            ])
        return "\n".join(part for part in parts if part is not None).strip()

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "original_prompt": self.original_prompt,
            "latest_user_text": self.latest_user_text,
            "user_history_text": self.user_history_text,
            "workspace_name": self.workspace_name,
            "workspace_guid": self.workspace_guid,
            "intent": self.intent or {},
            "pending_approval_kind": self.pending_approval_kind,
        }


class GroundingCoordinator:
    """Runs selected category agents and stores their lookup results in cache."""

    def __init__(
        self,
        *,
        registry: Any,
        client: Any,
        pool_size: int = 8,
        timeout_s: float = 240.0,
    ) -> None:
        self.registry = registry
        self.client = client
        self.pool_size = max(2, pool_size)
        self.timeout_s = max(15.0, timeout_s)

    def run(
        self,
        context: AuthoringContext,
        *,
        categories: Iterable[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        requested = [str(c).strip() for c in (categories or ()) if str(c).strip()]
        by_name = {cat.name: cat for cat in DEFAULT_CATEGORIES}
        unknown = sorted(set(requested) - by_name.keys())
        prompt = context.prompt_for_grounding()

        if requested:
            selected = [by_name[name] for name in requested if name in by_name]
        else:
            selected = list(_select_categories(prompt, DEFAULT_CATEGORIES))

        if unknown:
            self._log(f"unknown categories ignored: {unknown!r}")
        if not selected:
            return {
                "ok": False,
                "category": "bad_tool_arguments",
                "message": (
                    "No recognised grounding categories were selected."
                    if requested
                    else "Automatic grounding selected no categories."
                ),
                "categories_requested": sorted(requested),
                "categories_unknown": unknown,
            }

        category_names = [cat.name for cat in selected]
        run_key = _run_key(context, category_names)
        completed = getattr(self.registry, "_grounding_runs", None)
        if completed is None:
            completed = set()
            setattr(self.registry, "_grounding_runs", completed)
        if not force and run_key in completed:
            return {
                "ok": True,
                "status": "cached",
                "categories_requested": sorted(requested),
                "categories_unknown": unknown,
                "categories_run": category_names,
                "cache_writes": 0,
                "per_category": [],
            }

        self._log(f"automatic grounding: firing {len(selected)} category agent(s): {category_names!r}")
        with ThreadPoolExecutor(
            max_workers=self.pool_size,
            thread_name_prefix="authoring-grounding",
        ) as pool:
            results = run_category_agents(
                prompt=prompt,
                registry=self.registry,
                client=self.client,
                pool=pool,
                categories=selected,
                timeout_s=self.timeout_s,
                ignore_keyword_filter=True,
            )
        completed.add(run_key)

        ok_count = sum(1 for result in results if result.ok)
        total_writes = sum(result.cache_writes for result in results)
        self._log(
            f"automatic grounding: done {ok_count}/{len(results)} ok, "
            f"{total_writes} cache writes"
        )
        return {
            "ok": ok_count > 0,
            "categories_requested": sorted(requested),
            "categories_unknown": unknown,
            "categories_run": [result.category for result in results],
            "cache_writes": total_writes,
            "per_category": [
                {
                    "category": result.category,
                    "ok": result.ok,
                    "tool_calls": result.tool_calls,
                    "cache_writes": result.cache_writes,
                    "elapsed_s": round(result.elapsed_s, 2),
                    "error": result.error,
                }
                for result in results
            ],
        }

    def _log(self, message: str) -> None:
        print(f"[grounding] {message}", file=sys.stderr, flush=True)


def _select_categories(
    prompt: str,
    categories: Iterable[CategoryAgent],
) -> list[CategoryAgent]:
    lowered = (prompt or "").lower()
    selected: list[CategoryAgent] = []
    no_fca = any(token in lowered for token in ("no fca", "no fac", "no liha"))
    for category in categories:
        if category.name == "fca_tips" and no_fca:
            continue
        if category.always_on or any(trigger in lowered for trigger in category.keyword_triggers):
            selected.append(category)
    return selected


def _run_key(context: AuthoringContext, categories: list[str]) -> str:
    payload = {
        "context": context.fingerprint_payload(),
        "categories": sorted(categories),
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
