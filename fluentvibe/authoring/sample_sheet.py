"""Sample sheets: each sample's concentration, for per-sample normalisation.

A sheet comes as a CSV attachment (a well or sample-number column and a
concentration column) or as text in the chat ("A1 45, A2 30.5, ..."). The
concentrations never go through the model; the per-well volumes are computed
here: sample = target_ng / concentration, diluent = final volume - sample.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

_ROWS = "ABCDEFGH"
_WELL = re.compile(r"^\s*([A-Ha-h])\s*0*(1[0-2]|[1-9])\s*$")
_PAIR = re.compile(r"\b([A-Ha-h])0*(1[0-2]|[1-9])\b\s*[:=,;\t ]\s*(\d+(?:[.,]\d+)?)")


def _well(text: str) -> str | None:
    """"A1", "a01", or a 1-based sample number (column-major: 9 -> A2)."""
    text = str(text).strip()
    m = _WELL.match(text)
    if m:
        return f"{m.group(1).upper()}{int(m.group(2))}"
    if text.isdigit() and 1 <= int(text) <= 96:
        n = int(text) - 1
        return f"{_ROWS[n % 8]}{n // 8 + 1}"
    return None


def _number(text: str) -> float | None:
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        return None


def parse_sample_sheet(text: str) -> dict[str, float]:
    """Well -> concentration (ng/µl) from a CSV/TSV sample sheet; {} if none found."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return {}
    header = [c.strip().lower() for c in rows[0]]
    well_col = next((i for i, h in enumerate(header) if re.search(r"well|position|pos\b|sample ?(no|#|number)", h)), None)
    conc_col = next((i for i, h in enumerate(header) if re.search(r"conc|ng/|ng per|qubit|nanodrop|dna", h)), None)
    body = rows[1:] if (well_col is not None or conc_col is not None) else rows
    if well_col is None or conc_col is None:
        # No telling header: the first column that holds wells, the last numeric one.
        for row in body[:3]:
            if well_col is None:
                well_col = next((i for i, c in enumerate(row) if _well(c)), None)
            numeric = [i for i, c in enumerate(row) if _number(c) is not None and i != well_col]
            if conc_col is None and numeric:
                conc_col = numeric[-1]
    if well_col is None or conc_col is None:
        return {}
    out: dict[str, float] = {}
    for row in body:
        if max(well_col, conc_col) >= len(row):
            continue
        well, conc = _well(row[well_col]), _number(row[conc_col])
        if well and conc is not None:
            out[well] = conc
    return out


def parse_concentrations(text: str) -> dict[str, float]:
    """Well -> concentration from free text ("A1 45, A2: 30.5, B1=12")."""
    return {f"{m.group(1).upper()}{int(m.group(2))}": float(m.group(3).replace(",", "."))
            for m in _PAIR.finditer(text or "")}


@dataclass
class Normalisation:
    sample_ul: dict[str, float] = field(default_factory=dict)     # from the sample plate
    diluent_ul: dict[str, float] = field(default_factory=dict)    # into the destination
    # Too concentrated for a direct draw: pre-diluted to target_ng / final_ul
    # (sample, diluent into an intermediate plate), then final_ul of that.
    predilute: dict[str, tuple[float, float]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # questions: the plan cannot be built as is


def normalisation(sheet: dict[str, float], target_ng: float, final_ul: float,
                  *, min_ul: float = 1.0, predilute_ul: float = 2.0, max_well_ul: float = 200.0) -> Normalisation:
    """Per-well volumes to put ``target_ng`` in ``final_ul``.

    As the ONT table does: a sample that would need less than ``min_ul`` is
    first diluted (``predilute_ul`` of it plus diluent) to target/final ng/µl,
    and ``final_ul`` of that dilution is used.
    """
    plan = Normalisation()
    working = target_ng / final_ul          # ng/µl of a normalised well
    too_dilute, prediluted = [], []
    for well in sorted(sheet, key=lambda w: (int(w[1:]), w[0])):
        conc = sheet[well]
        if conc <= 0:
            plan.problems.append(f"{well}: concentration {conc:g} ng/µl is not usable")
            continue
        sample = target_ng / conc
        if sample > final_ul:
            too_dilute.append(well)
            sample = final_ul                  # all of it, as the document does below the threshold
        if sample < min_ul:
            total = predilute_ul * conc / working
            if total > max_well_ul:
                plan.problems.append(f"{well}: {conc:g} ng/µl needs more than {max_well_ul:g} µl to dilute "
                                     f"{predilute_ul:g} µl; pre-dilute it by hand and give the new concentration")
                continue
            plan.predilute[well] = (predilute_ul, round(total - predilute_ul, 2))
            prediluted.append(well)
            continue
        diluent = final_ul - sample
        if 0 < diluent < min_ul:
            sample, diluent = final_ul, 0.0    # a sub-µl top-up is not pipettable: take the sample
        plan.sample_ul[well] = round(sample, 2)
        if diluent > 0:
            plan.diluent_ul[well] = round(diluent, 2)
    if too_dilute:
        plan.notes.append(f"{len(too_dilute)} sample(s) below {working:.2f} ng/µl use the full "
                          f"{final_ul:g} µl ({', '.join(too_dilute[:8])}{' ...' if len(too_dilute) > 8 else ''})")
    if prediluted:
        plan.notes.append(f"{len(prediluted)} concentrated sample(s) are pre-diluted to {working:.2f} ng/µl "
                          f"({predilute_ul:g} µl + diluent), then {final_ul:g} µl is used "
                          f"({', '.join(prediluted[:8])}{' ...' if len(prediluted) > 8 else ''})")
    return plan
