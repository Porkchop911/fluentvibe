"""Check converted bead clean-up parameters against the protocols' own source.

For every draft spec in ``build/eval/opentrons-specs`` with a ``bead_cleanup``
step, read the Opentrons protocol source and collect numeric assignments whose
names identify a clean-up quantity (``bead_vol = 60``, ``etoh_vol = 200``,
``elution_vol = 61`` …, including ``# name = value`` commented defaults).
Report, per parameter, how often the converter's value matches one of those
source values — an automatic stand-in for hand-checking.

    python scripts/validate_cleanup_params.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPECS = REPO / "build" / "eval" / "opentrons-specs"

PATTERNS = {
    "bead_ul": re.compile(r"(bead|ampure|axp|spri|mag).*vol|vol.*(bead|ampure|axp|spri)", re.I),
    "wash_ul": re.compile(r"(etoh|ethanol|wash).*vol|vol.*(etoh|ethanol|wash)", re.I),
    "elute_ul": re.compile(r"(elut|eb_|te_|water).*vol|vol.*(elut|eb|te|water)", re.I),
    "washes": re.compile(r"(num|n)_?(of_)?(etoh|ethanol|wash)|wash.*(count|num|times|reps)", re.I),
}
FIELDS = {"bead_ul": "volume_ul", "wash_ul": "wash_ul", "elute_ul": "elute_ul", "washes": "washes"}


def _source_values(source: str) -> dict[str, set[float]]:
    found: dict[str, set[float]] = defaultdict(set)
    assignments: list[tuple[str, object]] = []
    try:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments.append((target.id, node.value.value))
    except SyntaxError:
        pass
    for match in re.finditer(r"^\s*#?\s*([A-Za-z_]\w*)\s*=\s*([0-9.]+)\s*$", source, re.M):
        assignments.append((match.group(1), match.group(2)))
    for name, value in assignments:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        for key, pattern in PATTERNS.items():
            if pattern.search(name):
                found[key].add(number)
    return found


def main() -> int:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # checked, matched, no-source-value
    rows = []
    for path in sorted(SPECS.glob("*.json")):
        if path.name.startswith(("summary", "_")):
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        cleanups = [s for s in spec.get("steps", []) if s.get("op") == "bead_cleanup"]
        if not cleanups:
            continue
        protocol = Path(spec.get("_source", {}).get("protocol", ""))
        if not protocol.exists():
            continue
        values = _source_values(protocol.read_text(encoding="utf-8", errors="replace"))
        for step in cleanups:
            for key, field in FIELDS.items():
                extracted = step.get(field)
                if extracted is None:
                    continue
                candidates = values.get(key, set())
                if not candidates:
                    totals[key][2] += 1
                    continue
                totals[key][0] += 1
                ok = any(abs(float(extracted) - c) <= max(0.5, 0.02 * c) for c in candidates)
                totals[key][1] += ok
                if not ok:
                    rows.append((path.stem, key, extracted, sorted(candidates)[:6]))
    print("| Parameter | Checked against source | Match | No source value |")
    print("|---|---|---|---|")
    for key in FIELDS:
        checked, matched, missing = totals[key]
        rate = f"{matched / checked:.0%}" if checked else "–"
        print(f"| {key} | {checked} | {matched} ({rate}) | {missing} |")
    print()
    print("Mismatches (protocol, parameter, extracted, source values):")
    for row in rows[:40]:
        print("-", *row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
