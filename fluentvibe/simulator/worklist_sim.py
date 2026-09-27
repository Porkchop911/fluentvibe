"""Worklist records for the simulator.

FluentControl runs a worklist in three commands: *Worklist Import* converts a
CSV into a GWL file (one row: aspirate, dispense, new tips), *Load Worklist*
queues a GWL for the FCA (LiHa) with a liquid class and tip type, and
*Execute Worklist* runs everything loaded since the last execute. The
simulator reads the same files when they exist at simulation time and applies
the aspirate/dispense records to the wells.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..worklists import normalize_columns


@dataclass
class WorklistRecord:
    code: str                  # "A" aspirate, "D" dispense, "W" new tips / wash
    label: str = ""
    position: str = ""
    volume: float = 0.0
    line: int = 0              # line in the source file (1-based)


class WorklistFileError(ValueError):
    pass


def resolve(path: str, base: Optional[Path] = None) -> Optional[Path]:
    """The worklist file on disk, or None when it does not exist yet."""
    candidate = Path(path)
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    return candidate if candidate.is_file() else None


def records_from_csv(path: Path, *, columns, start_line: int, separator: str,
                     stop_with_last_line: bool = True, stop_with_line: int = 1) -> list[WorklistRecord]:
    """What *Worklist Import* makes of a CSV: per row aspirate, dispense, new tips."""
    by_name = {name: index for _letter, index, name in normalize_columns(
        {m.column_name: m.gwl_index for m in columns} if columns else "standard")}
    missing = [n for n in ("SourceLabel", "SourcePosition", "DestLabel", "DestPosition", "Volume") if n not in by_name]
    if missing:
        raise WorklistFileError(f"{path.name}: the column mapping has no {', '.join(missing)}")
    out: list[WorklistRecord] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for line_no, row in enumerate(csv.reader(fh, delimiter=separator or ","), start=1):
            if line_no < start_line:
                continue
            if not stop_with_last_line and line_no > stop_with_line:
                break
            if not any(cell.strip() for cell in row):
                continue

            def cell(name: str) -> str:
                index = by_name[name]
                return row[index].strip() if index < len(row) else ""

            volume = _volume(cell("Volume"), path, line_no)
            out.append(WorklistRecord("A", cell("SourceLabel"), cell("SourcePosition"), volume, line_no))
            out.append(WorklistRecord("D", cell("DestLabel"), cell("DestPosition"), volume, line_no))
            out.append(WorklistRecord("W", line=line_no))
    return out


def records_from_gwl(path: Path) -> tuple[list[WorklistRecord], list[str]]:
    """Aspirate / dispense / wash records of a GWL, and the codes not simulated."""
    out: list[WorklistRecord] = []
    skipped: list[str] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        parts = raw.strip().split(";")
        code = parts[0].strip().upper()
        if not code:
            continue
        if code in {"A", "D"}:
            field = lambda i: parts[i].strip() if len(parts) > i else ""  # noqa: E731
            out.append(WorklistRecord(code, field(1), field(4), _volume(field(6), path, line_no), line_no))
        elif code in {"W", "WD"} or code.startswith("W"):
            out.append(WorklistRecord("W", line=line_no))
        elif code in {"B", "C", "F"}:
            continue  # break, comment, flush: no liquid moves
        else:
            skipped.append(f"{code} (line {line_no})")
    return out, skipped


def _volume(text: str, path: Path, line_no: int) -> float:
    try:
        return float(text)
    except ValueError:
        raise WorklistFileError(f"{path.name} line {line_no}: volume {text!r} is not a number") from None


def well_address(labware, position: str) -> Optional[str]:
    """``position`` ("A1" or FluentControl's 1-based column-major number) as a
    well address of ``labware``; None when the labware has no such well."""
    wells = list(getattr(labware, "wells", {}) or {})
    if not wells:
        return None
    text = str(position).strip().upper()
    if text in labware.wells:
        return text
    if not text.isdigit():
        return None
    if len(wells) == 1:           # a trough: every position is the one well
        return wells[0]
    rows = sorted({w[0] for w in wells if w[:1].isalpha()})
    n = int(text)
    if n < 1 or not rows:
        return None
    column, row = divmod(n - 1, len(rows))
    address = f"{rows[row]}{column + 1}"
    return address if address in labware.wells else None
