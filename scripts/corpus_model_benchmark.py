"""Model benchmark across protocol families: corpus Bench Specs -> authored protocol.

Picks one converted Opentrons spec per family (among those whose skeleton
passes every check, so a spec that fits the deck), gives the model the spec
the way an approved spec reaches authoring, and scores what it produced with
the rubric against that spec. Each spec runs in two modes:

* ``skeleton`` -- the graph offers the deterministic skeleton as the draft;
* ``model``    -- ``FLUENTVIBE_SKELETON=0``: the model authors from the spec alone.

    python scripts/corpus_model_benchmark.py --profile build/workspaces/sat_1080_test \\
        --work <scratch dir> --out build/eval/corpus-model-benchmark.md

Uses the endpoint/model from the usual ``FLUENTVIBE_LM_*`` environment.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

LEDGER = REPO / "docs" / "skill-authoring" / "coverage-ledger.csv"
PROMPT = "Automate this protocol on the deck, following the approved Bench Spec."


def _pick(specs_dir: Path, profile: Path, per_family: int, names: list[str]) -> list[tuple[str, str, Path]]:
    from fluentvibe.authoring.bench_spec import validate_bench_spec
    from fluentvibe.authoring.eval_rubric import score_protocol
    from fluentvibe.authoring.skeleton import build_skeleton, load_deck

    families = {}
    with LEDGER.open(encoding="utf-8") as fh:
        families = {row["folder"]: row["bucket"] for row in csv.DictReader(fh)}
    deck = load_deck(profile)
    chosen: dict[str, list[tuple[str, str, Path]]] = {}
    for path in sorted(specs_dir.glob("*.json")):
        if path.name == "summary.json" or (names and path.stem not in names):
            continue
        family = families.get(path.stem, "unclassified")
        if not names and len(chosen.get(family, [])) >= per_family:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_source", None)
        deck_steps = [s for s in raw.get("steps", []) if s.get("location") == "deck"]
        if not names and not 2 <= len(deck_steps) <= 8:
            continue
        spec, _ = validate_bench_spec(raw)
        if spec is None:
            continue
        try:
            source = build_skeleton(spec, copy.deepcopy(deck))
            if score_protocol(source, spec=spec).failed:
                continue
        except Exception:  # noqa: BLE001 - only specs that fit the deck
            continue
        chosen.setdefault(family, []).append((path.stem, family, path))
    return [item for items in chosen.values() for item in items]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--specs", type=Path, default=REPO / "build" / "eval" / "opentrons-specs")
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True, help="existing directory for run outputs")
    ap.add_argument("--out", type=Path, default=None, help="Markdown report")
    ap.add_argument("--per-family", type=int, default=1)
    ap.add_argument("--only", default="", help="comma-separated spec names instead of per-family picks")
    ap.add_argument("--modes", default="model,skeleton")
    ap.add_argument("--run-timeout", type=float, default=1800.0)
    ap.add_argument("--retry-budget", type=int, default=3)
    args = ap.parse_args()

    from eval_authoring import _activate_profile, _fc_verdict, _run_once

    from fluentvibe.authoring import PromptAuthoringService
    from fluentvibe.authoring.bench_spec import spec_context_block, validate_bench_spec
    from fluentvibe.authoring.eval_rubric import score_protocol
    from fluentvibe.authoring.trace import ModelTraceConfig

    ws_name, ws_guid = _activate_profile(args.profile)
    picks = _pick(args.specs, args.profile, args.per_family, [n for n in args.only.split(",") if n])
    print(f"[bench] {len(picks)} specs: {', '.join(f'{n} [{f}]' for n, f, _ in picks)}", flush=True)
    service = PromptAuthoringService(request_timeout_s=2400.0)
    rows = []
    for name, family, path in picks:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_source", None)
        spec, _ = validate_bench_spec(raw)
        prompt = f"{PROMPT}\n\n{spec_context_block(spec)}"
        for mode in args.modes.split(","):
            os.environ["FLUENTVIBE_SKELETON"] = "1" if mode == "skeleton" else "0"
            run_dir = args.work / f"{name}-{mode}"
            run_dir.mkdir(parents=True, exist_ok=True)
            row = {"spec": name, "family": family, "mode": mode, "status": "", "score": "", "failed": "", "minutes": ""}
            started = time.monotonic()
            try:
                trace = ModelTraceConfig(enabled=True, live=False, output_dir=run_dir, session_id=f"{name}-{mode}")
                result, _rubric, py_path = _run_once(
                    service, prompt, None, run_dir, ws_name, ws_guid, "skills", True,
                    args.retry_budget, trace, args.run_timeout,
                )
                row["status"] = result.status.value
                if py_path is not None and py_path.exists():
                    rubric = score_protocol(py_path.read_text(encoding="utf-8"), filename=str(py_path), spec=spec)
                    row["score"] = f"{rubric.score:.2f}"
                    row["failed"] = " ".join(i.key for i in rubric.invariants if i.status == "fail")
            except Exception as exc:  # noqa: BLE001 - record, never abort the batch
                row["status"] = f"error: {type(exc).__name__}"
                (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            row["minutes"] = f"{(time.monotonic() - started) / 60:.1f}"
            row["fluentcontrol"] = _fc_verdict(run_dir)
            rows.append(row)
            print(f"[bench] {name} [{family}] {mode}: {row['status']} score={row['score']} "
                  f"fails={row['failed']} {row['minutes']} min", flush=True)

    lines = ["# Corpus model benchmark", "",
             f"Model: {os.environ.get('FLUENTVIBE_LM_MODEL', 'default')} at "
             f"{os.environ.get('FLUENTVIBE_LM_ENDPOINT', 'default endpoint')}; deck {ws_name}.", "",
             "| Spec | Family | Mode | Status | Score | Failed checks | FluentControl | Minutes |",
             "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['spec']} | {r['family']} | {r['mode']} | {r['status']} | {r['score']} | {r['failed']} | "
              f"{r.get('fluentcontrol', '')} | {r['minutes']} |" for r in rows]
    for mode in args.modes.split(","):
        scored = [float(r["score"]) for r in rows if r["mode"] == mode and r["score"]]
        clean = sum(1 for r in rows if r["mode"] == mode and r["score"] and not r["failed"])
        total = sum(1 for r in rows if r["mode"] == mode)
        mean = sum(scored) / len(scored) if scored else 0.0
        lines.append("")
        lines.append(f"- **{mode}**: {clean}/{total} pass every check, mean score {mean:.2f}")
    report = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
