"""Repeatable Strata tool-selection benchmark using the real authoring loop."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lm_client import make_chat_client
from .lookup_eval import LookupEvalScenario, run_lookup_eval, write_lookup_eval_report

STRATA_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
STRATA_MODEL = "qwen3.8-flash-next-iq3_xxs"


def strata_scenarios() -> list[LookupEvalScenario]:
    return [
        LookupEvalScenario(
            name="catalog_grounding",
            prompt="Use the lookup tools to check workspace SAT_Fluent_780_Rev3, search for 96_ABgene plates, fetch details for 96_ABgene_SuperPlate_Thermo_AB2800, check its compatibility, and look up Water Free Single for Mca. Summarize the findings; do not write a protocol.",
            expected_tools=("lookup_workspace", "search_labware", "get_labware", "lookup_compatibility", "lookup_liquid_class"),
        ),
        LookupEvalScenario(
            name="gripper_api",
            prompt="Look up the wt.gripper API and summarize how to move a plate onto a magnet. Do not write or compile a protocol.",
            expected_tools=("lookup_api",),
        ),
        LookupEvalScenario(
            name="rules_and_labware",
            prompt="Use tools to retrieve the authoring rules and search for 96_ABgene labware. Summarize both results; do not write a protocol.",
            expected_tools=("lookup_rules", "search_labware"),
        ),
    ]


def run_strata_eval(*, models: list[str], output: Path, endpoint: str = STRATA_ENDPOINT,
                    repeats: int = 1, client_factory: Any = make_chat_client) -> dict[str, Any]:
    if not models or repeats < 1:
        raise ValueError("Provide at least one model and a positive repeat count")
    report: dict[str, Any] = {
        "schema_version": 1, "benchmark": "strata_tool_selection",
        "created_at": datetime.now(timezone.utc).isoformat(), "endpoint": endpoint,
        "repeats": repeats, "runs": [],
        "scope": "Required tool selection and successful dispatch; not protocol correctness.",
    }
    for model_index, model in enumerate(models):
        for repeat in range(repeats):
            for scenario in strata_scenarios():
                run: dict[str, Any] = {"model": model, "repeat": repeat + 1, "scenario": scenario.name}
                try:
                    client = client_factory(model=model, endpoint=endpoint, temperature=0, streaming=False)
                    # Bound stalled requests and avoid implicit retries affecting latency.
                    client = client.model_copy(update={"request_timeout": 120.0, "max_retries": 0})
                    result = run_lookup_eval(
                        output_dir=output.parent / "strata_runs" / str(model_index) / str(repeat + 1) / scenario.name,
                        live_client=client, scenarios=[scenario],
                    )
                    detail = result["scenarios"][0]
                    selected = detail["selected_required_tools"]
                    successful = {call["name"] for call in detail["dispatched_calls"]
                                  if call.get("result", {}).get("ok") is True}
                    run.update(detail=detail, required_tool_recall=sum(selected.values()) / len(selected),
                               passed=all(selected.values()) and set(scenario.expected_tools) <= successful)
                except Exception as exc:
                    run.update(passed=False, required_tool_recall=0.0, error=f"{type(exc).__name__}: {exc}")
                report["runs"].append(run)
                write_lookup_eval_report(report, output)
    report["summary"] = [
        {"model": model, "runs": len(rows), "passed": sum(row["passed"] for row in rows),
         "errors": sum("error" in row for row in rows),
         "mean_required_tool_recall": sum(row["required_tool_recall"] for row in rows) / len(rows)}
        for model in dict.fromkeys(models)
        if (rows := [row for row in report["runs"] if row["model"] == model])
    ]
    write_lookup_eval_report(report, output)
    return report
