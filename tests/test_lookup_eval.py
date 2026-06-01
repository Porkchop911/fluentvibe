from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from fluentvibe.authoring.graph import GraphState, build_authoring_graph
from fluentvibe.authoring.lookup_eval import run_lookup_eval, write_lookup_eval_report
from fluentvibe.authoring.tools import AuthoringToolRegistry


def test_lookup_eval_records_model_and_dispatched_calls(tmp_path: Path) -> None:
    report = run_lookup_eval(output_dir=tmp_path)

    assert report["mode"] == "scripted"
    assert report["summary"]["tool_call_count"] >= 7
    scenarios = {scenario["name"]: scenario for scenario in report["scenarios"]}

    catalog = scenarios["catalog_lookup"]
    requested = [call["name"] for call in catalog["model_tool_calls"]]
    dispatched = [call["name"] for call in catalog["dispatched_calls"]]
    assert requested[:5] == [
        "lookup_workspace",
        "search_labware",
        "get_labware",
        "lookup_liquid_class",
        "lookup_compatibility",
    ]
    assert dispatched[:5] == requested[:5]
    assert all(call["elapsed_ms"] >= 0.0 for call in catalog["dispatched_calls"])
    assert {call["dispatch_source"] for call in catalog["dispatched_calls"]} <= {
        "live",
        "cache",
        "parallel",
        "speculative",
    }

    by_name = {call["name"]: call for call in catalog["dispatched_calls"]}
    assert by_name["get_labware"]["result"]["ok"] is True
    assert by_name["get_labware"]["result"]["labware"]["python_class"] == "Plate96"
    assert by_name["lookup_liquid_class"]["result"]["ok"] is True
    assert by_name["lookup_liquid_class"]["result_summary"]["liquid_class_name"] == "Water Free Single"


def test_lookup_eval_recipe_api_and_cache_sources(tmp_path: Path) -> None:
    report = run_lookup_eval(output_dir=tmp_path)
    scenarios = {scenario["name"]: scenario for scenario in report["scenarios"]}

    recipe = scenarios["recipe_api_lookup"]["dispatched_calls"][0]
    assert recipe["name"] == "lookup_api"
    assert recipe["result"]["ok"] is True
    assert recipe["result"]["api"]["object"] == "wt.gripper"
    assert recipe["result"]["api"]["recipes"]
    assert recipe["result_summary"]["api_recipes_count"] >= 1

    cached = scenarios["cached_parallel_lookup"]["dispatched_calls"]
    sources = {call["name"]: call["dispatch_source"] for call in cached}
    assert sources["search_labware"] == "cache"
    assert sources["lookup_rules"] in {"parallel", "live"}
    assert report["summary"]["cache_hits"] >= 1


def test_lookup_eval_report_writer(tmp_path: Path) -> None:
    report = run_lookup_eval(output_dir=tmp_path)
    path = write_lookup_eval_report(report, tmp_path / "lookup_eval.json")
    assert path.exists()
    assert '"schema_version": 1' in path.read_text(encoding="utf-8")


def test_cached_lookup_records_hit_without_rerunning_tool(tmp_path: Path) -> None:
    from fluentvibe.authoring.lookup_eval import ScriptedLookupModel

    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.cache_store(
        "search_labware",
        {"query": "cached"},
        {"ok": True, "matches": [{"name": "cached"}]},
    )
    graph = build_authoring_graph(
        registry=registry,
        client=ScriptedLookupModel([
            AIMessage(
                content="",
                tool_calls=[{"name": "search_labware", "args": {"query": "cached"}, "id": "c"}],
            ),
            AIMessage(content=""),
        ]),
        output_dir=registry.output_dir,
        retry_budget=1,
    )
    with patch.object(registry, "_dispatch_pure", side_effect=AssertionError("cache miss")):
        graph.invoke(GraphState(
            messages=[SystemMessage(content="s"), HumanMessage(content="p")],
            iterations=0,
            tool_call_count=0,
            best_code=None,
            last_validation=None,
            result=None,
            prompt="p",
        ))

    assert registry.calls[0]["name"] == "search_labware"
    assert registry.calls[0]["dispatch_source"] == "cache"
    assert registry.calls[0]["elapsed_ms"] >= 0.0
