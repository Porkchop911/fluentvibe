"""Multi-family benchmark: corpus Bench Specs -> skeleton drafts -> checks.

For every converted Opentrons draft spec, build the deterministic skeleton on
a deck profile, then run the rubric (with the spec) and the authoring compile
gate. The report shows, per family, how many specs the skeleton builder can
turn into a protocol that compiles and passes every check -- the floor a model
starts from when it authors from that spec.

    python scripts/skeleton_benchmark.py --profile build/workspaces/sat_1080_test \\
        --work <scratch dir> --out build/eval/skeleton-benchmark.md
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LEDGER = REPO / "docs" / "skill-authoring" / "coverage-ledger.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--specs", type=Path, default=REPO / "build" / "eval" / "opentrons-specs")
    ap.add_argument("--profile", type=Path, required=True, help="deck profile directory")
    ap.add_argument("--work", type=Path, required=True, help="existing directory for skeletons and gate output")
    ap.add_argument("--out", type=Path, default=None, help="Markdown report")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from fluentvibe.authoring.profile import PROFILE_DIR_ENV

    os.environ[PROFILE_DIR_ENV] = str(args.profile)
    from fluentvibe.authoring.bench_spec import validate_bench_spec
    from fluentvibe.authoring.eval_rubric import score_protocol
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.skeleton import build_skeleton, load_deck
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    families = {}
    if LEDGER.exists():
        with LEDGER.open(encoding="utf-8") as fh:
            families = {row["folder"]: row["bucket"] for row in csv.DictReader(fh)}
    deck = load_deck(args.profile)
    rows = []
    paths = [p for p in sorted(args.specs.glob("*.json")) if p.name != "summary.json"]
    if args.limit:
        paths = paths[: args.limit]
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_source", None)
        row = {"name": path.stem, "family": families.get(path.stem, "unclassified"),
               "built": False, "gate": False, "fails": [], "error": ""}
        rows.append(row)
        spec, _ = validate_bench_spec(raw)
        if spec is None:
            row["error"] = "invalid spec"
            continue
        try:
            source = build_skeleton(spec, copy.deepcopy(deck))
        except Exception as exc:  # noqa: BLE001 - report every failure kind
            row["error"] = f"build: {type(exc).__name__}: {str(exc)[:120]}"
            continue
        row["built"] = True
        target = args.work / f"{path.stem}.py"
        target.write_text(source, encoding="utf-8")
        try:
            result = score_protocol(source, filename=str(target), spec=spec)
            row["fails"] = [i.key for i in result.invariants if i.status == "fail"]
            registry = AuthoringToolRegistry(output_dir=args.work)
            registry.lab_scope = load_lab_scope("skills")
            gate = registry.compile_and_simulate(source)
            row["gate"] = bool(gate.get("success"))
            if not row["gate"]:
                row["error"] = "gate: " + " ".join(str(gate.get("failure_message", "")).split())[:160]
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"check: {type(exc).__name__}: {str(exc)[:120]}"
            traceback.print_exc(limit=1)
        status = "PASS" if row["gate"] and not row["fails"] else "FAIL"
        print(f"{status} {path.stem} [{row['family']}] fails={row['fails']} {row['error']}", flush=True)

    per = defaultdict(Counter)
    for row in rows:
        c = per[row["family"]]
        c["specs"] += 1
        c["built"] += row["built"]
        c["gate"] += row["gate"]
        c["clean"] += row["gate"] and not row["fails"]
    fail_keys = Counter(k for r in rows for k in r["fails"])
    errors = Counter(r["error"].split(":")[0] + ": " + r["error"].split(":", 2)[-1][:70] for r in rows if r["error"])
    total = Counter()
    for c in per.values():
        total.update(c)
    lines = ["# Skeleton benchmark (corpus specs)", "",
             f"{total['specs']} specs: {total['built']} built, {total['gate']} pass the compile gate, "
             f"{total['clean']} also pass every rubric check.", "",
             "| Family | Specs | Built | Gate | All checks |", "|---|---|---|---|---|"]
    for family in sorted(per, key=lambda f: -per[f]["specs"]):
        c = per[family]
        lines.append(f"| {family} | {c['specs']} | {c['built']} | {c['gate']} | {c['clean']} |")
    lines += ["", "Rubric failures:", ""] + [f"- {k}: {n}" for k, n in fail_keys.most_common()]
    lines += ["", "Errors:", ""] + [f"- {k}: {n}" for k, n in errors.most_common(15)]
    report = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
