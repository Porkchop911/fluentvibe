"""Replay the AMPure XP prompt through the authoring pipeline.

Auto-approves the staged checkpoints so we can observe the full
behavior (object draft -> functional groups -> Python -> simulate).

Usage:
    PYTHONPATH=. python scripts/ampure_replay.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fluentvibe.authoring.models import AuthoringStatus
from fluentvibe.authoring.session import PromptAuthoringSession

AMPURE_PROMPT = """AMPure XP Beads Protocol for PCR Cleanup. Process a full 96-well plate.
Use 20 uL PCR samples; bind with 1.8x AMPure XP beads (36 uL per well), incubate
5 min, capture on a magnet rack 2 min, remove supernatant leaving 5 uL behind,
wash twice with 200 uL of 70% ethanol, elute in 40 uL of elution buffer, capture
on magnet 1 min, transfer eluate to a clean plate.

Use ABgene SuperPlate AB2800 for both source and elution plate. Use FCA (i.e.
wt.liha) to dispense beads and ethanol from troughs; otherwise use the MCA96.
Take 200 uL MCA tips. Use string variables for the different liquid classes
(default value "Water Free Single").
"""

CANNED = [
    "Use the same Water Free Single liquid class for all transfers. Bead source: a separate 100 mL trough. Ethanol source: a separate 100 mL trough. Elution buffer source: a separate 100 mL trough.",
    "Discard supernatant and ethanol washes to a thru-deck waste chute.",
    "Use sensible defaults for incubation timing and magnet timing.",
    "Default any other unspecified detail.",
]


def main() -> int:
    out = Path("build") / "ampure_replay"
    out.mkdir(parents=True, exist_ok=True)
    session = PromptAuthoringSession(output_dir=out, retry_budget=10)

    t0 = time.time()
    log_lines: list[str] = []

    def log(line: str) -> None:
        stamp = f"{time.time() - t0:6.1f}s"
        prefix = f"[{stamp}] "
        print(prefix + line, flush=True)
        log_lines.append(prefix + line)

    log("SEND: initial prompt")
    result = session.send(AMPURE_PROMPT)
    clarif_idx = 0
    approval_count = 0
    max_steps = 25
    step = 0
    while step < max_steps:
        step += 1
        names = [c.get("name") for c in result.tool_calls]
        log(f"status={result.status.value} tools_this_turn={names[-15:]}")
        if result.status is AuthoringStatus.CLARIFICATION_REQUIRED:
            if clarif_idx < len(CANNED):
                msg = CANNED[clarif_idx]
            else:
                msg = "Use sensible defaults for anything else."
            clarif_idx += 1
            log(f"REPLY (clarification {clarif_idx}): {msg[:80]}")
            result = session.send(msg)
            continue
        if result.status is AuthoringStatus.APPROVAL_REQUIRED:
            approval_count += 1
            kind = "?"
            if result.approval_request is not None:
                kind = result.approval_request.kind
                title = result.approval_request.title
                log(f"APPROVAL #{approval_count} kind={kind} title={title!r}")
            log("REPLY: approve")
            result = session.send("approve")
            continue
        break

    elapsed = time.time() - t0
    log(f"FINAL status={result.status.value} after {step} send/recv steps, "
        f"elapsed={elapsed:.1f}s, clarifications={clarif_idx}, approvals={approval_count}")

    if result.failure_category:
        log(f"failure_category={result.failure_category}")
    if result.failure_message:
        log(f"failure_message={result.failure_message[:300]}")
    if result.validation is not None:
        v = result.validation
        log(
            "validation: compile_ok=%s strict_sim_ok=%s sim_failure=%s repair=%s python_path=%s"
            % (
                v.compile_ok,
                v.strict_simulation_ok,
                v.simulation_failure_category,
                bool(v.repair_options or v.repair_hint),
                v.python_path,
            )
        )
    if result.best_draft_code:
        log(f"best_draft_code chars={len(result.best_draft_code)}")
    if result.compiled_xscr is not None:
        log(f"compiled_xscr={result.compiled_xscr}")

    # Surface a count of every tool call invoked across the whole session.
    all_names = [c.get("name") for c in result.tool_calls]
    histogram: dict[str, int] = {}
    for n in all_names:
        histogram[n] = histogram.get(n, 0) + 1
    log("tool histogram: " + json.dumps(dict(sorted(histogram.items(), key=lambda kv: -kv[1]))))

    (out / "replay.log").write_text("\n".join(log_lines))
    return 0 if result.status is AuthoringStatus.SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main())
