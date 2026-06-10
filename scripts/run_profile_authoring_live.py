"""Run a live skills-mode authoring generation against a workspace_app profile.

Thin live harness over the product path: it constructs a
``PromptAuthoringSession(profile_dir=...)``, which wires the profile's workspace
identity, grounding snapshot (``current_worktable.py``), deck skill, and
labware/liquid whitelist — exactly what ``fluentvibe chat/author --profile``
does. Opt-in: needs the LM Studio endpoint up.
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
DEFAULT_ANSWERS = (
    "20 uL per well",
    "Water Free Single",
    "Use all 96 wells.",
    "ABgene plate acceptable; use exact catalog 96_ABgene_SuperPlate_Thermo_AB2800 for source and destination plates.",
    "Use sensible installed reservoirs and waste resources.",
)


def _format_canned_answers(answers: list[str]) -> str:
    return (
        "\n".join(f"- {a}" for a in answers)
        + "\n- Yes, use Water Free Single exactly as the liquid class when asked to confirm it."
        + "\n- Use exact installed tip boxes with sufficient capacity for each planned phase."
    )


def _one_line(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--retry-budget", type=int, default=12)
    parser.add_argument("--max-rounds", type=int, default=None)
    args = parser.parse_args()

    output_dir = args.output_dir or (Path("build") / f"{args.profile_dir.name}_skills_gen")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_jsonl = output_dir / "trace.jsonl"

    # The product path: profile_dir drives workspace, snapshot, deck skill, and
    # whitelist. No manual env-setting or scope-patching here.
    session = PromptAuthoringSession(
        output_dir=output_dir,
        retry_budget=args.retry_budget,
        lab_scope="skills",
        profile_dir=args.profile_dir,
    )
    deck_names = [s.name for s in session._lab_scope.skill_catalog if s.axis == "deck"]
    print(f"[scope] workspace={session._registry.workspace_name!r} guid={session._registry.workspace_guid}")
    print(f"[scope] active deck skill(s): {deck_names}")
    print(f"[scope] whitelist labware={len(session._lab_scope.labware)} liquid={sorted(session._lab_scope.liquid_classes)}")

    prompt = args.prompt_file.read_text(encoding="utf-8")
    answers = list(DEFAULT_ANSWERS)
    canned = _format_canned_answers(answers)
    initial = prompt + "\n\nCanned authoring answers for this live run:\n" + canned
    pending = [initial, *([canned] * len(answers))]
    max_rounds = args.max_rounds or args.retry_budget * 4

    printed = 0
    round_index = 0
    result = None
    with log_jsonl.open("w", encoding="utf-8") as log:
        while round_index < max_rounds:
            if result is None:
                if not pending:
                    break
                user_text = pending.pop(0)
            elif result.status.value == "approval_required":
                user_text = "approve"
            elif result.status.value == "clarification_required":
                user_text = pending.pop(0) if pending else canned
            else:
                break
            round_index += 1
            result = session.send(user_text)
            calls = list(result.tool_calls)
            new_calls = calls[printed:]
            printed = len(calls)
            record = {
                "round": round_index,
                "status": result.status.value,
                "tool_calls": [
                    {
                        "name": c.get("name"),
                        "ok": (c.get("result") or {}).get("ok") if isinstance(c.get("result"), dict) else None,
                        "summary": _one_line((c.get("result") or {}).get("_summary")
                                             or (c.get("result") or {}).get("message")) if isinstance(c.get("result"), dict) else None,
                    }
                    for c in new_calls
                ],
                "compiled_xscr_path": str(result.compiled_xscr) if result.compiled_xscr else None,
            }
            log.write(json.dumps(record, default=str) + "\n")
            log.flush()
        if result is None:
            return 1
        log.write(json.dumps({"terminal": result.to_dict()}, default=str) + "\n")

    print(json.dumps({
        "status": result.status.value,
        "failure_category": result.failure_category.value if result.failure_category else None,
        "compiled_xscr": str(result.compiled_xscr) if result.compiled_xscr else None,
        "trace_path": str(log_jsonl),
    }, indent=2))
    return 0 if result.status.value == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
