"""Run the AMPure prompt through PromptAuthoringSession with canned answers.

This is an opt-in live harness. It expects the configured LM Studio endpoint to
be running and writes a JSONL trace of each chat round.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fluentvibe.authoring import PromptAuthoringSession

DEFAULT_PROMPT = Path(r"C:\Users\Niko\Desktop\ampure protocol.txt")
DEFAULT_OUTPUT_DIR = Path("build") / "ampure_authoring_live"
DEFAULT_ANSWERS = (
    "20 uL per well",
    "Water Free Single",
    "Use all 96 wells.",
    "ABgene plate acceptable; use exact catalog 96_ABgene_SuperPlate_Thermo_AB2800 for source and destination plates.",
    "Use sensible installed reservoirs and waste resources.",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--log-jsonl", type=Path, default=DEFAULT_OUTPUT_DIR / "ampure_authoring_trace.jsonl")
    parser.add_argument("--retry-budget", type=int, default=12)
    parser.add_argument("--workspace", default="SAT_Fluent_780_Rev3")
    parser.add_argument("--workspace-guid", default="291ba293-6361-4f8f-aa8d-7c2643d3f096")
    parser.add_argument(
        "--model", default=None,
        help="LM Studio model name (default: DEFAULT_LM_STUDIO_MODEL from lm_client.py)",
    )
    parser.add_argument("--answer", action="append", default=[], help="Additional canned clarification answer.")
    parser.add_argument(
        "--no-canned-preamble",
        action="store_true",
        help="Do not append the canned answers to the initial prompt.",
    )
    parser.add_argument(
        "--no-auto-approve",
        action="store_true",
        help="Stop on approval_required instead of auto-approving with a canned 'approve' reply.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="Hard cap on session.send rounds (default: 4 * retry-budget).",
    )
    args = parser.parse_args()

    prompt = args.prompt_file.read_text(encoding="utf-8")
    answers = list(args.answer) or list(DEFAULT_ANSWERS)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.log_jsonl.parent.mkdir(parents=True, exist_ok=True)

    session_kwargs: dict[str, Any] = dict(
        output_dir=args.output_dir,
        retry_budget=args.retry_budget,
        workspace_name=args.workspace,
        workspace_guid=args.workspace_guid,
    )
    if args.model:
        session_kwargs["model"] = args.model
    session = PromptAuthoringSession(**session_kwargs)

    printed_calls = 0
    round_index = 0
    result = None
    initial_prompt = prompt
    canned_response = _format_canned_answers(answers)
    if not args.no_canned_preamble:
        initial_prompt = (
            prompt
            + "\n\nCanned authoring answers for this live run:\n"
            + canned_response
        )
    pending_inputs = [initial_prompt, *([canned_response] * len(answers))]
    auto_approve = not args.no_auto_approve
    max_rounds = args.max_rounds or args.retry_budget * 4
    with args.log_jsonl.open("w", encoding="utf-8") as log:
        while round_index < max_rounds:
            if result is None:
                if not pending_inputs:
                    break
                user_text = pending_inputs.pop(0)
            elif result.status.value == "approval_required":
                if not auto_approve:
                    break
                user_text = "approve"
            elif result.status.value == "clarification_required":
                user_text = pending_inputs.pop(0) if pending_inputs else canned_response
            else:
                break
            round_index += 1
            result = session.send(user_text)
            calls = list(result.tool_calls)
            new_calls = calls[printed_calls:]
            printed_calls = len(calls)
            record = {
                "round": round_index,
                "prompt": _one_line(user_text),
                "status": result.status.value,
                "tool_calls": [_tool_call_summary(call) for call in new_calls],
                "failure_category": (
                    result.failure_category.value
                    if result.failure_category is not None
                    else _validation_failure_category(result.validation)
                ),
                "repair_lock_state": _infer_repair_lock_state(calls),
                "generated_draft_path": _validation_path(result.validation, "python_path"),
                "compiled_xscr_path": str(result.compiled_xscr) if result.compiled_xscr else None,
                "validation": result.validation.to_dict() if result.validation else None,
                "clarification_questions": [
                    question.question for question in result.clarification_questions
                ],
            }
            log.write(json.dumps(record, default=str) + "\n")
            log.flush()

        if result is None:
            return 1
        terminal = result.to_dict()
        terminal["trace_path"] = str(args.log_jsonl)
        log.write(json.dumps({"terminal": terminal}, default=str) + "\n")

    print(json.dumps({
        "status": result.status.value,
        "failure_category": result.failure_category.value if result.failure_category else None,
        "compiled_xscr": str(result.compiled_xscr) if result.compiled_xscr else None,
        "trace_path": str(args.log_jsonl),
    }, indent=2))
    return 0 if result.status.value == "success" else 1


def _tool_call_summary(call: dict[str, Any]) -> dict[str, Any]:
    result = call.get("result") if isinstance(call.get("result"), dict) else {}
    return {
        "name": call.get("name"),
        "ok": result.get("ok"),
        "category": result.get("category") or result.get("simulation_failure_category"),
        "stage": result.get("stage"),
        "result_summary": result.get("_summary") or _one_line(result.get("message")),
    }


def _infer_repair_lock_state(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    for call in reversed(calls):
        result = call.get("result") if isinstance(call.get("result"), dict) else {}
        if call.get("name") in {"simulate_python_draft", "compile_and_simulate"} and result.get("ok") is False:
            category = result.get("simulation_failure_category") or result.get("category")
            failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
            return {"category": failure.get("category") or category}
    return None


def _validation_failure_category(validation: Any) -> str | None:
    if validation is None:
        return None
    return validation.simulation_failure_category or (
        validation.failure_category.value if validation.failure_category else None
    )


def _validation_path(validation: Any, attr: str) -> str | None:
    if validation is None:
        return None
    value = getattr(validation, attr, None)
    return str(value) if value else None


def _format_canned_answers(answers: list[str]) -> str:
    return (
        "\n".join(f"- {answer}" for answer in answers)
        + "\n- Yes, use Water Free Single exactly as the liquid class when the model asks to confirm it."
        + "\n- Declare 20 uL per well for intent validation unless the protocol's final eluate transfer is the only destination-volume check."
        + "\n- Use exact installed tip boxes with sufficient capacity for each planned phase."
    )


def _one_line(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] + ("..." if len(text) > limit else "")


if __name__ == "__main__":
    raise SystemExit(main())
