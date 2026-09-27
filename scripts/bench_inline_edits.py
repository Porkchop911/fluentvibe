"""Benchmark Ctrl+I inline edits: latency and correctness per reasoning effort.

Runs the editor's ``edit_region`` on a protocol with a set of edit tasks and
records, per task and effort: seconds, whether the edit did what was asked
(a regex on the replacement), syntax errors, and new analyzer errors.

    python scripts/bench_inline_edits.py --protocol examples/ampure_resolver.py \\
        --effort low --effort medium --effort xhigh --runs 3 --out build/eval/bench-inline.jsonl

Tasks are given as JSON (``--tasks``): a list of
{"name", "anchor": "<text on the first selected line>", "lines": N, "instruction", "expect": "<regex>"}.
Without --tasks the five AMPure tasks used for the effort study are run.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DEFAULT_TASKS = [
    {"name": "mca_ethanol", "anchor": 'head="fca", liquid_class_var="LC_ETHANOL"', "lines": 2,
     "instruction": "dispense the ethanol with the MCA instead of the FCA", "expect": r'head="mca"'},
    {"name": "bead_volume", "anchor": "wt.add(beads, to=samples", "lines": 1,
     "instruction": "use 30 ul of beads instead", "expect": r"volume_ul\s*=\s*30\b"},
    {"name": "mix_cycles", "anchor": 'name="Mix beads"', "lines": 1,
     "instruction": "mix only 5 times", "expect": r"cycles\s*=\s*5\b"},
    {"name": "wait_after_mix", "anchor": 'name="Mix beads"', "lines": 1,
     "instruction": "after mixing, wait 2 minutes before continuing",
     "expect": r"wt\.wait\(\s*(duration_seconds\s*=\s*)?120"},
    {"name": "partial_beads", "anchor": "wt.add(beads, to=samples", "lines": 1,
     "instruction": "only add the beads to plate columns 1 to 3",
     "expect": r"columns\s*=\s*(\[1,\s*2,\s*3\]|range\(1,\s*4\))"},
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, default=None)
    ap.add_argument("--effort", action="append", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", type=Path, default=REPO / "build" / "workspaces" / "sat_1080_test")
    args = ap.parse_args()

    import os

    os.environ.setdefault("FLUENTVIBE_PROFILE_DIR", str(args.profile))
    from fluentvibe.authoring.lm_client import LMStudioChatClient
    from fluentvibe.copilot.analyzer import analyze_source
    from fluentvibe.copilot.edit import _replace_lines, edit_region

    tasks = json.loads(args.tasks.read_text(encoding="utf-8")) if args.tasks else DEFAULT_TASKS
    # A copy without a requirements sidecar: edits are judged on correctness and errors only.
    work = args.out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    proto = work / "protocol.py"
    shutil.copy(args.protocol, proto)
    source = proto.read_text(encoding="utf-8")
    lines = source.splitlines()
    baseline = {(d.line, d.code) for d in analyze_source(source, proto)}
    for run in range(1, args.runs + 1):
        efforts = list(args.effort)
        random.Random(run).shuffle(efforts)
        for effort in efforts:
            for task in tasks:
                start = next(i for i, text in enumerate(lines, 1) if task["anchor"] in text)
                end = start + int(task.get("lines", 1)) - 1
                client = LMStudioChatClient(reasoning_effort=effort, request_timeout_s=600)
                t = time.monotonic()
                row = {"task": task["name"], "effort": effort, "run": run}
                try:
                    result = edit_region(source, start, end, task["instruction"], client=client, path=str(proto),
                                         revalidate=False)
                    row["seconds"] = round(time.monotonic() - t, 1)
                    edited = _replace_lines(lines, start, end, result.new_text)
                    try:
                        ast.parse(edited)
                        row["syntax_error"] = False
                    except SyntaxError:
                        row["syntax_error"] = True
                    row["correct"] = bool(re.search(task["expect"], result.new_text or ""))
                    row["new_errors"] = [f"{d.line}:{d.code}" for d in analyze_source(edited, proto)
                                         if d.severity == "error" and (d.line, d.code) not in baseline][:3]
                except Exception as exc:  # noqa: BLE001
                    row.update(seconds=round(time.monotonic() - t, 1), error=f"{type(exc).__name__}: {exc}"[:200])
                with args.out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
