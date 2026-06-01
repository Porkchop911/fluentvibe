"""Evaluation harness for SQL-backed authoring lookups.

The deterministic path uses scripted model responses but still runs the real
LangGraph dispatch loop and registry tools. The optional live path swaps in an
OpenAI-compatible chat client so the report captures actual model tool choice.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from .graph import AuthoringConcurrencyConfig, GraphState, build_authoring_graph
from .models import ValidationReport
from .service import SYSTEM_PROMPT
from .tools import AuthoringToolRegistry


@dataclass(frozen=True)
class LookupEvalScenario:
    name: str
    prompt: str
    responses: tuple[AIMessage, ...] = ()
    expected_tools: tuple[str, ...] = ()
    seed_cache: tuple[tuple[str, dict[str, Any], dict[str, Any]], ...] = ()


class ScriptedLookupModel:
    """Small deterministic chat model for CI evaluation."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)

    def bind_tools(self, tools: Any) -> "ScriptedLookupModel":
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        if self._responses:
            return self._responses.pop(0)
        return AIMessage(content="")


class _PassValidator:
    def validate(self, code, *, output_dir, stem, attempt_index, prompt):
        return ValidationReport(
            success=True,
            python_build_ok=True,
            compile_ok=True,
            strict_simulation_ok=True,
            python_path=None,
            xscr_path=None,
            attempt_index=attempt_index,
        )


def default_lookup_eval_scenarios() -> list[LookupEvalScenario]:
    catalog_calls = (
        {"name": "lookup_workspace", "args": {"name_or_guid": "SAT_Fluent_780_Rev3"}, "id": "cat-ws"},
        {"name": "search_labware", "args": {"query": "96_ABgene", "limit": 5}, "id": "cat-search"},
        {
            "name": "get_labware",
            "args": {"name": "96_ABgene_SuperPlate_Thermo_AB2800"},
            "id": "cat-get",
        },
        {
            "name": "lookup_liquid_class",
            "args": {"name": "Water Free Single", "device_type": "Mca"},
            "id": "cat-lc",
        },
        {
            "name": "lookup_compatibility",
            "args": {"name": "96_ABgene_SuperPlate_Thermo_AB2800", "kind": "all"},
            "id": "cat-compat",
        },
    )
    recipe_calls = (
        {"name": "lookup_api", "args": {"object_or_class": "wt.gripper"}, "id": "api-gripper"},
    )
    cached_calls = (
        {"name": "search_labware", "args": {"query": "cached plate"}, "id": "cache-1"},
        {"name": "lookup_rules", "args": {}, "id": "cache-2"},
    )
    return [
        LookupEvalScenario(
            name="catalog_lookup",
            prompt="Ground a 96 well plate transfer on SAT_Fluent_780_Rev3.",
            responses=(AIMessage(content="", tool_calls=list(catalog_calls)), AIMessage(content="done")),
            expected_tools=tuple(call["name"] for call in catalog_calls),
        ),
        LookupEvalScenario(
            name="recipe_api_lookup",
            prompt="Check the gripper API before moving a plate onto a magnet.",
            responses=(AIMessage(content="", tool_calls=list(recipe_calls)), AIMessage(content="done")),
            expected_tools=("lookup_api",),
        ),
        LookupEvalScenario(
            name="cached_parallel_lookup",
            prompt="Repeat independent lookup calls where one result is already cached.",
            responses=(AIMessage(content="", tool_calls=list(cached_calls)), AIMessage(content="done")),
            expected_tools=tuple(call["name"] for call in cached_calls),
            seed_cache=(
                (
                    "search_labware",
                    {"query": "cached plate"},
                    {"ok": True, "matches": [{"name": "cached plate", "category": "plate"}]},
                ),
            ),
        ),
    ]


def run_lookup_eval(
    *,
    output_dir: Path = Path("build"),
    live_client: Any | None = None,
    scenarios: list[LookupEvalScenario] | None = None,
    concurrency: AuthoringConcurrencyConfig | None = None,
) -> dict[str, Any]:
    """Run lookup scenarios and return a compact JSON-serializable report."""
    scenarios = scenarios or default_lookup_eval_scenarios()
    scenario_reports: list[dict[str, Any]] = []
    started = time.monotonic()

    for scenario in scenarios:
        registry = AuthoringToolRegistry(output_dir=output_dir / "lookup_eval" / scenario.name)
        registry.set_authoring_context(
            original_prompt=scenario.prompt,
            latest_user_text=scenario.prompt,
            user_history_text=scenario.prompt,
        )
        for name, args, result in scenario.seed_cache:
            registry.cache_store(name, args, result)

        client = live_client if live_client is not None else ScriptedLookupModel(list(scenario.responses))
        graph = build_authoring_graph(
            registry=registry,
            client=client,
            output_dir=registry.output_dir,
            retry_budget=1,
            validator=_PassValidator(),
            system_prompt=SYSTEM_PROMPT,
            concurrency=concurrency or AuthoringConcurrencyConfig(
                parallel_tool_dispatch=True,
                speculative_compile=False,
                prefetch_deterministic=False,
                prefetch_subagent=False,
                orchestrator_grounding=False,
            ),
        )
        state = GraphState(
            messages=[SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=scenario.prompt)],
            iterations=0,
            tool_call_count=0,
            best_code=None,
            last_validation=None,
            result=None,
            prompt=scenario.prompt,
        )
        graph_started = time.monotonic()
        final = graph.invoke(state)
        elapsed_ms = (time.monotonic() - graph_started) * 1000.0
        calls = [dict(call) for call in registry.calls]
        model_tool_calls = [
            call
            for turn in registry.model_turns
            for call in turn.get("tool_calls", [])
        ]
        scenario_reports.append({
            "name": scenario.name,
            "prompt": scenario.prompt,
            "status": getattr(final.get("result"), "status", None).value if final.get("result") else None,
            "wall_ms": elapsed_ms,
            "expected_tools": list(scenario.expected_tools),
            "selected_required_tools": _selected_required_tools(scenario.expected_tools, model_tool_calls),
            "model_turns": list(registry.model_turns),
            "model_tool_calls": model_tool_calls,
            "dispatched_calls": calls,
            "metrics": _metrics(registry.model_turns, calls),
        })

    all_calls = [
        call
        for scenario_report in scenario_reports
        for call in scenario_report["dispatched_calls"]
    ]
    all_turns = [
        turn
        for scenario_report in scenario_reports
        for turn in scenario_report["model_turns"]
    ]
    report = {
        "schema_version": 1,
        "mode": "live" if live_client is not None else "scripted",
        "wall_ms": (time.monotonic() - started) * 1000.0,
        "scenarios": scenario_reports,
        "summary": _metrics(all_turns, all_calls),
    }
    return report


def write_lookup_eval_report(report: dict[str, Any], path: Path = Path("build") / "lookup_eval.json") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def _selected_required_tools(expected: tuple[str, ...], model_calls: list[dict[str, Any]]) -> dict[str, bool]:
    selected = [call.get("name") for call in model_calls]
    return {name: name in selected for name in expected}


def _metrics(model_turns: list[dict[str, Any]], calls: list[dict[str, Any]]) -> dict[str, Any]:
    tool_latencies = [float(call.get("elapsed_ms") or 0.0) for call in calls]
    model_latencies = [float(turn.get("elapsed_ms") or 0.0) for turn in model_turns]
    cache_hits = sum(1 for call in calls if call.get("dispatch_source") == "cache")
    return {
        "model_turn_count": len(model_turns),
        "model_turn_ms_total": sum(model_latencies),
        "model_turn_ms_p50": _percentile(model_latencies, 50),
        "model_turn_ms_p95": _percentile(model_latencies, 95),
        "tool_call_count": len(calls),
        "tool_ms_total": sum(tool_latencies),
        "tool_ms_p50": _percentile(tool_latencies, 50),
        "tool_ms_p95": _percentile(tool_latencies, 95),
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / len(calls)) if calls else 0.0,
        "dispatch_sources": {
            source: sum(1 for call in calls if call.get("dispatch_source") == source)
            for source in ("live", "cache", "parallel", "speculative")
        },
    }


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return max(0.0, values[0])
    ordered = sorted(max(0.0, value) for value in values)
    if pct == 50:
        return float(statistics.median(ordered))
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    weight = rank - lo
    return float(ordered[lo] * (1.0 - weight) + ordered[hi] * weight)
