"""Benchmark the fast path (document -> spec -> protocol) over documents, runs and reasoning efforts.

Each run calls ``author_from_document`` (spec + instruction checklist in
parallel, skeleton, compile + simulate, optional FluentControl check) and
writes one JSON line: time, stage, FluentControl verdict, TODO steps and the
instruction verdicts. Questions are answered by the model ("choose values").

    python scripts/bench_author_spec.py --profile build/workspaces/sat_1080_test \\
        --doc "ampure=C:/path/ampure.txt::use 20 ul samples, whole plate, ..." \\
        --doc "dyna=C:/path/dynabeads.pdf::bead wash for 96 wells" \\
        --runs 3 --effort medium --effort xhigh --out build/eval/bench-author-spec.jsonl --fc-check

Model endpoint, name and key come from FLUENTVIBE_LM_* as usual.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--doc", action="append", required=True,
                    help="name=path::request (the request is optional)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--effort", action="append", default=None, help="reasoning effort(s); default: env")
    ap.add_argument("--out", type=Path, required=True, help="JSONL results (appended)")
    ap.add_argument("--work", type=Path, default=None, help="per-run output folders (default next to --out)")
    ap.add_argument("--fc-check", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    from fluentvibe.authoring.attachments import extract_file_text
    from fluentvibe.authoring.lm_client import LMStudioChatClient
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.authoring.spec_path import author_from_document, choose_yourself

    os.environ[PROFILE_DIR_ENV] = str(args.profile)
    docs = []
    for item in args.doc:
        name, rest = item.split("=", 1)
        path, _, request = rest.partition("::")
        docs.append((name, Path(path), request.strip() or None))
    efforts = args.effort or [os.environ.get("FLUENTVIBE_LM_REASONING_EFFORT") or "xhigh"]
    work = args.work or args.out.with_suffix("")
    jobs = [(name, path, request, effort, run) for run in range(1, args.runs + 1)
            for name, path, request in docs for effort in efforts]
    random.Random(args.seed).shuffle(jobs)
    for name, path, request, effort, run in jobs:
        text = extract_file_text(path)[0]
        out_dir = work / f"{name}-{effort}-r{run}"
        started = time.monotonic()
        row = {"doc": name, "effort": effort, "run": run}
        try:
            result = author_from_document(
                LMStudioChatClient(reasoning_effort=effort, request_timeout_s=1800), text, args.profile, out_dir,
                request=request, ask=choose_yourself, fluentcontrol=args.fc_check,
                check_requirements=bool(request),
            )
            summary = result.summary()
            if result.spec_raw is not None:   # to replay a failing build without the model
                (Path(out_dir) / "spec.json").write_text(json.dumps(result.spec_raw, indent=1, ensure_ascii=False),
                                                         encoding="utf-8")
            row.update(stage=summary["stage"], error=summary["error"], fc_ok=summary["fc_ok"],
                       todo_steps=summary["todo_steps"], instructions=summary["instructions"],
                       custom_steps=summary.get("custom_steps"), timings=summary["timings"],
                       failed=[r["text"][:80] for r in result.requirements if r["status"] != "pass"])
            if result.source:
                (out_dir / "draft.py").write_text(result.source, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - a crash is a result
            row.update(stage="crash", error=f"{type(exc).__name__}: {exc}"[:300])
        row["seconds"] = round(time.monotonic() - started, 1)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
