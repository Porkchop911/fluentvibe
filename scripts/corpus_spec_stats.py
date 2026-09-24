"""Validate converted Opentrons draft specs and build per-family parameter tables.

Reads the JSON drafts written by ``scripts/opentrons_to_spec.py`` (default
``build/eval/opentrons-specs``), validates each with the Bench Spec schema
checks, groups them by the triage ledger's family bucket
(``docs/skill-authoring/coverage-ledger.csv``) and writes a Markdown report:

* conversion and validation counts per family;
* step-operation mix per family;
* the bead clean-up parameter table (bead volume, washes, wash volume,
  elution volume) across every converted protocol that has one.

    python scripts/corpus_spec_stats.py --out build/eval/opentrons-specs/report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluentvibe.authoring.bench_spec import validate_bench_spec  # noqa: E402

LEDGER = REPO / "docs" / "skill-authoring" / "coverage-ledger.csv"


def _buckets() -> dict[str, str]:
    if not LEDGER.exists():
        return {}
    with LEDGER.open(encoding="utf-8") as fh:
        return {row["folder"]: row["bucket"] for row in csv.DictReader(fh)}


def _describe(values: list[float]) -> str:
    if not values:
        return "–"
    values = sorted(values)
    median = statistics.median(values)
    return f"{median:g} (n={len(values)}, {values[0]:g}–{values[-1]:g})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--specs", type=Path, default=REPO / "build" / "eval" / "opentrons-specs")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    buckets = _buckets()
    summary_path = args.specs / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else []
    errors = Counter()
    for row in summary:
        if row.get("status") == "error":
            errors[str(row.get("error", "")).split(":")[0]] += 1

    per_family: dict[str, dict] = defaultdict(lambda: {"converted": 0, "valid": 0, "ops": Counter(), "steps": []})
    cleanup = defaultdict(list)
    cleanup_by_family = defaultdict(lambda: defaultdict(list))
    for path in sorted(args.specs.glob("*.json")):
        if path.name == "summary.json":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_source", None)
        family = buckets.get(path.stem, "unclassified")
        entry = per_family[family]
        entry["converted"] += 1
        spec, problems = validate_bench_spec(raw)
        if spec is None:
            continue
        if not [p for p in problems if p.kind == "schema"]:
            entry["valid"] += 1
        entry["ops"].update(step.op for step in spec.steps)
        entry["steps"].append(len(spec.steps))
        for step in spec.steps:
            if step.op != "bead_cleanup":
                continue
            for key, value in (("bead_ul", step.volume_ul), ("washes", step.washes),
                               ("wash_ul", step.wash_ul), ("elute_ul", step.elute_ul)):
                if value is not None:
                    cleanup[key].append(float(value))
                    cleanup_by_family[family][key].append(float(value))

    lines = ["# Opentrons corpus → Bench Spec drafts", ""]
    ok = sum(1 for r in summary if r.get("status") == "ok")
    lines += [f"Converted {ok} of {len(summary)} protocols via the Opentrons simulator.", ""]
    if errors:
        lines += ["Conversion failures by error type:", ""]
        lines += [f"- {name}: {count}" for name, count in errors.most_common()]
        lines += [""]
    lines += ["## Per family", "", "| Family | Converted | Schema-valid | Median steps | Step ops |",
              "|---|---|---|---|---|"]
    for family in sorted(per_family, key=lambda f: -per_family[f]["converted"]):
        e = per_family[family]
        ops = ", ".join(f"{op} {n}" for op, n in e["ops"].most_common())
        med = statistics.median(e["steps"]) if e["steps"] else 0
        lines.append(f"| {family} | {e['converted']} | {e['valid']} | {med:g} | {ops} |")
    lines += ["", "## Bead clean-up parameters (all converted protocols)", "",
              "| Parameter | Median (n, range) |", "|---|---|"]
    for key, label in (("bead_ul", "Bead volume (µl)"), ("washes", "Washes"),
                       ("wash_ul", "Wash volume (µl)"), ("elute_ul", "Elution volume (µl)")):
        lines.append(f"| {label} | {_describe(cleanup[key])} |")
    lines += ["", "### By family", "", "| Family | Beads µl | Washes | Wash µl | Elution µl |", "|---|---|---|---|---|"]
    for family, values in sorted(cleanup_by_family.items()):
        lines.append(
            f"| {family} | {_describe(values['bead_ul'])} | {_describe(values['washes'])} | "
            f"{_describe(values['wash_ul'])} | {_describe(values['elute_ul'])} |"
        )
    report = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
