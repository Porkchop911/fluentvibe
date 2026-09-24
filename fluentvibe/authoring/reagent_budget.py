"""Reagent budget: does the protocol need more of a reagent than the kit has?

The Bench Spec can record what the kit supplies for each reagent
(``supply_ul`` per vial or well × ``supply_count`` vials or wells). The
simulator knows what the protocol makes the operator load: every reagent's
volume in the authored initial state (``fill_all`` and friends). When a
protocol loads more of a kit reagent than the kit provides, the run is not
possible as written — for example adding a full adapter dilution to every well
when the kit ships 15 µl of adapter.

Reagents are matched to spec reagents by the spec ``id`` appearing as a whole
word in the reagent name (``"Rapid Adapter (RA)"`` ↔ ``RA``), or by an exact,
case-insensitive name match. Reagents without a known supply are not checked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .bench_spec import SPEC_MARKER, BenchSpec, SpecReagent, parse_bench_spec


@dataclass(frozen=True)
class BudgetFinding:
    reagent_id: str
    reagent_name: str
    loaded_ul: float
    consumed_ul: float
    available_ul: float
    labware: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reagent": self.reagent_id,
            "name": self.reagent_name,
            "loaded_ul": round(self.loaded_ul, 2),
            "used_ul": round(self.consumed_ul, 2),
            "kit_provides_ul": round(self.available_ul, 2),
            "labware": list(self.labware),
        }

    @property
    def message(self) -> str:
        where = ", ".join(self.labware)
        return (
            f"{self.reagent_id} ({self.reagent_name}): the protocol loads "
            f"{self.loaded_ul:g} µl ({where}) and uses {self.consumed_ul:g} µl, but "
            f"the kit provides {self.available_ul:g} µl"
        )


def available_ul(reagent: SpecReagent) -> float | None:
    if reagent.supply_ul is None:
        return None
    return float(reagent.supply_ul) * float(reagent.supply_count or 1)


def _matches(spec_reagent: SpecReagent, name: str) -> bool:
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(spec_reagent.id)}(?![A-Za-z0-9])", name):
        return True
    return name.strip().lower() == spec_reagent.name.strip().lower()


def _volumes_by_reagent(labware_iter) -> dict[tuple[str, str], float]:
    """``{(labware_label, reagent_name): µl}`` over free liquid and bead phase."""
    out: dict[tuple[str, str], float] = {}
    for lw in labware_iter:
        for well in getattr(lw, "wells", {}).values():
            layers = list(well.layers)
            bead_phase = getattr(well, "bead_phase", None)
            if bead_phase is not None:
                layers += list(getattr(bead_phase, "bound", []) or [])
            for layer in layers:
                key = (lw.label, layer.reagent.name)
                out[key] = out.get(key, 0.0) + float(layer.volume_ul)
    return out


def check_reagent_budget(wt: Any, spec: BenchSpec) -> list[BudgetFinding]:
    """Kit reagents the simulated protocol loads more of than the kit provides.

    ``wt`` must have been simulated (its final snapshot is used for the amount
    actually consumed). The loaded amount comes from the authored initial state.
    """
    budgeted = [(r, available_ul(r)) for r in spec.reagents]
    budgeted = [(r, a) for r, a in budgeted if a is not None]
    if not budgeted:
        return []
    initial = _volumes_by_reagent(lw for stack in wt.slot_map.values() for lw in stack)
    final: dict[tuple[str, str], float] = {}
    if getattr(wt, "snapshots", None):
        final = _volumes_by_reagent(
            lw for stack in wt.snapshots[-1].slot_map.values() for lw in stack
        )
    findings: list[BudgetFinding] = []
    for spec_reagent, available in budgeted:
        loaded = 0.0
        consumed = 0.0
        labware: list[str] = []
        for (label, name), volume in initial.items():
            if volume <= 0 or not _matches(spec_reagent, name):
                continue
            loaded += volume
            consumed += max(0.0, volume - final.get((label, name), volume))
            if label not in labware:
                labware.append(label)
        if loaded > available + 1e-6:
            findings.append(BudgetFinding(
                reagent_id=spec_reagent.id,
                reagent_name=spec_reagent.name,
                loaded_ul=loaded,
                consumed_ul=consumed,
                available_ul=available,
                labware=tuple(labware),
            ))
    return findings


def spec_from_prompt(text: str | None) -> BenchSpec | None:
    """The approved Bench Spec embedded in an authoring prompt, if any."""
    if not text or SPEC_MARKER not in text:
        return None
    start = text.find("{", text.index(SPEC_MARKER))
    if start < 0:
        return None
    try:
        raw, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    spec, _problems = parse_bench_spec(raw)
    return spec
