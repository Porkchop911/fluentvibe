"""FluentControl worklist file helpers.

This module models the GWL text file format. Worktable methods model the
FluentControl commands that load/execute these files.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

PathLike = Union[str, Path]

STANDARD_COLUMNS = {
    "A": "SourceLabel",
    "B": "SourcePosition",
    "C": "DestLabel",
    "D": "DestPosition",
    "E": "Volume",
}

POSITION_GWL_FIELDS = {"SourcePosition", "DestPosition"}
_ALPHA_POS_RE = re.compile(r"^[A-Za-z]+[0-9]+$")
_NUMERIC_POS_RE = re.compile(r"^[0-9]+$")


class WorklistFormatError(ValueError):
    """Raised when a worklist file has ambiguous or unsupported structure."""


@dataclass
class GwlRecord:
    code: str
    fields: list[str] = field(default_factory=list)

    def to_line(self) -> str:
        if self.fields:
            return ";".join([self.code, *[str(v) for v in self.fields]])
        return f"{self.code};"


@dataclass
class GwlPipetteRecord(GwlRecord):
    rack_label: str = ""
    rack_id: str = ""
    rack_type: str = ""
    position: str = ""
    tube_id: str = ""
    volume: Union[float, int, str] = ""
    liquid_class: str = ""
    tip_type: str = ""
    tip_mask: Optional[int] = None
    forced_rack_type: str = ""

    def __post_init__(self) -> None:
        self.fields = [
            self.rack_label,
            self.rack_id,
            self.rack_type,
            str(self.position),
            self.tube_id,
            _format_volume(self.volume),
            self.liquid_class,
            self.tip_type,
            "" if self.tip_mask is None else str(self.tip_mask),
            self.forced_rack_type,
        ]


@dataclass
class GwlDistributionRecord(GwlRecord):
    source: Sequence[str] = field(default_factory=list)
    destination: Sequence[str] = field(default_factory=list)
    volume: Union[float, int, str] = ""
    liquid_class: str = ""
    diti_reuses: Union[int, str] = ""
    multidispense: Union[int, str] = ""
    direction: Union[int, str] = ""
    excluded_dest_wells: Sequence[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fields = [
            *[str(v) for v in self.source],
            *[str(v) for v in self.destination],
            _format_volume(self.volume),
            str(self.liquid_class),
            str(self.diti_reuses),
            str(self.multidispense),
            str(self.direction),
            *[str(v) for v in self.excluded_dest_wells],
        ]


@dataclass
class GwlSampleTransferRecord(GwlRecord):
    source: Sequence[str] = field(default_factory=list)
    destination: Sequence[str] = field(default_factory=list)
    volume: Union[float, int, str] = ""
    liquid_class: str = ""
    diti_reuses: Union[int, str] = ""
    multidispense: Union[int, str] = ""
    sample_count: Union[int, str] = ""
    replication_count: Union[int, str] = ""
    sample_direction: Union[int, str] = ""
    replicate_direction: Union[int, str] = ""
    excluded_dest_wells: Sequence[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fields = [
            *[str(v) for v in self.source],
            *[str(v) for v in self.destination],
            _format_volume(self.volume),
            str(self.liquid_class),
            str(self.diti_reuses),
            str(self.multidispense),
            str(self.sample_count),
            str(self.replication_count),
            str(self.sample_direction),
            str(self.replicate_direction),
            *[str(v) for v in self.excluded_dest_wells],
        ]


class Gwl:
    """Builder/parser for FluentControl GWL worklist text files."""

    def __init__(self, records: Optional[Iterable[GwlRecord]] = None) -> None:
        self.records: list[GwlRecord] = list(records or [])

    @classmethod
    def read(cls, path: PathLike) -> "Gwl":
        records: list[GwlRecord] = []
        for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = line.split(";")
            code = parts[0]
            fields = parts[1:]
            if fields and fields[-1] == "":
                fields = fields[:-1]
            records.append(GwlRecord(code=code, fields=fields))
        return cls(records)

    def write(self, path: PathLike) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(r.to_line() for r in self.records) + "\n", encoding="utf-8")
        return out

    def aspirate(
        self,
        rack_label: str,
        *,
        position: Union[str, int],
        volume: Union[float, int, str],
        rack_id: str = "",
        rack_type: str = "",
        tube_id: str = "",
        liquid_class: str = "",
        tip_mask: Optional[int] = None,
        forced_rack_type: str = "",
    ) -> "Gwl":
        self.records.append(GwlPipetteRecord(
            code="A",
            rack_label=rack_label,
            rack_id=rack_id,
            rack_type=rack_type,
            position=str(position),
            tube_id=tube_id,
            volume=volume,
            liquid_class=liquid_class,
            tip_mask=tip_mask,
            forced_rack_type=forced_rack_type,
        ))
        return self

    def dispense(self, rack_label: str, **kwargs) -> "Gwl":
        rec = GwlPipetteRecord(code="D", rack_label=rack_label, **kwargs)
        self.records.append(rec)
        return self

    def wash(self) -> "Gwl":
        self.records.append(GwlRecord("W"))
        return self

    def decontamination_wash(self) -> "Gwl":
        self.records.append(GwlRecord("WD"))
        return self

    def flush(self) -> "Gwl":
        self.records.append(GwlRecord("F"))
        return self

    def break_records(self) -> "Gwl":
        self.records.append(GwlRecord("B"))
        return self

    def set_diti_type(self, diti_index: Union[int, str]) -> "Gwl":
        self.records.append(GwlRecord("S", [str(diti_index)]))
        return self

    def comment(self, text: str) -> "Gwl":
        self.records.append(GwlRecord("C", [text]))
        return self

    def start_timer(self, timer: Union[int, str]) -> "Gwl":
        self.records.append(GwlRecord("TS", [str(timer)]))
        return self

    def wait_for_timer(self, timer: Union[int, str], seconds: Union[int, float, str]) -> "Gwl":
        self.records.append(GwlRecord("TW", [str(timer), str(seconds)]))
        return self

    def reagent_distribution(
        self,
        *,
        source: Sequence[str],
        destination: Sequence[str],
        volume: Union[float, int, str],
        liquid_class: str = "",
        diti_reuses: Union[int, str] = "",
        multidispense: Union[int, str] = "",
        direction: Union[int, str] = "",
        excluded_dest_wells: Sequence[str] = (),
    ) -> "Gwl":
        self.records.append(GwlDistributionRecord(
            code="R",
            source=source,
            destination=destination,
            volume=volume,
            liquid_class=liquid_class,
            diti_reuses=diti_reuses,
            multidispense=multidispense,
            direction=direction,
            excluded_dest_wells=excluded_dest_wells,
        ))
        return self

    def sample_transfer(
        self,
        *,
        source: Sequence[str],
        destination: Sequence[str],
        volume: Union[float, int, str],
        liquid_class: str = "",
        diti_reuses: Union[int, str] = "",
        multidispense: Union[int, str] = "",
        sample_count: Union[int, str] = "",
        replication_count: Union[int, str] = "",
        sample_direction: Union[int, str] = "",
        replicate_direction: Union[int, str] = "",
        excluded_dest_wells: Sequence[str] = (),
    ) -> "Gwl":
        self.records.append(GwlSampleTransferRecord(
            code="T",
            source=source,
            destination=destination,
            volume=volume,
            liquid_class=liquid_class,
            diti_reuses=diti_reuses,
            multidispense=multidispense,
            sample_count=sample_count,
            replication_count=replication_count,
            sample_direction=sample_direction,
            replicate_direction=replicate_direction,
            excluded_dest_wells=excluded_dest_wells,
        ))
        return self


def infer_gwl_well_positions(path: PathLike) -> str:
    return _classify_positions(_gwl_positions(Path(path)))


def infer_csv_well_positions(
    path: PathLike,
    *,
    columns: dict[str, str] | str = "standard",
    start_line: int = 2,
    separator: str = ",",
) -> str:
    mapping = standard_columns() if columns == "standard" else dict(columns)
    position_indexes = [
        _column_letter_to_index(letter)
        for letter, name in mapping.items()
        if name in POSITION_GWL_FIELDS
    ]
    positions: list[str] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh, delimiter=separator)
        for line_no, row in enumerate(reader, start=1):
            if line_no < start_line:
                continue
            for idx in position_indexes:
                if idx < len(row) and row[idx].strip():
                    positions.append(row[idx].strip())
    return _classify_positions(positions)


def standard_columns() -> dict[str, str]:
    return dict(STANDARD_COLUMNS)


def normalize_columns(columns: dict[str, str] | str) -> list[tuple[str, int, str]]:
    mapping = standard_columns() if columns == "standard" else dict(columns)
    out: list[tuple[str, int, str]] = []
    for letter, gwl_index in mapping.items():
        name = str(letter).strip().upper()
        out.append((name, _column_letter_to_index(name), str(gwl_index)))
    out.sort(key=lambda item: item[1])
    return out


def _gwl_positions(path: Path) -> list[str]:
    positions: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(";")
        code = parts[0]
        if code in {"A", "D"} and len(parts) > 4 and parts[4].strip():
            positions.append(parts[4].strip())
        elif code in {"R", "T"}:
            for idx in (4, 5, 9, 10):
                if len(parts) > idx and parts[idx].strip():
                    positions.append(parts[idx].strip())
    return positions


def _classify_positions(values: Iterable[str]) -> str:
    saw_alpha = False
    saw_numeric = False
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        if _ALPHA_POS_RE.fullmatch(value):
            saw_alpha = True
        elif _NUMERIC_POS_RE.fullmatch(value):
            saw_numeric = True
        else:
            raise WorklistFormatError(f"Cannot infer worklist well position format from {value!r}")
    if saw_alpha and saw_numeric:
        raise WorklistFormatError("Mixed numeric and alphanumeric well positions in worklist source")
    if saw_alpha:
        return "alphanumeric"
    return "numeric"


def _column_letter_to_index(letter: str) -> int:
    total = 0
    for ch in letter.upper():
        if not ("A" <= ch <= "Z"):
            raise WorklistFormatError(f"Invalid CSV column letter {letter!r}")
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total - 1


def _format_volume(value: Union[float, int, str]) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
