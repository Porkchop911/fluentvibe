"""Sample-lineage tracking for cross-contamination findings.

Every well that starts with an ``analyte`` reagent is its own *sample origin*
(``"<labware>:<well>"``). Origins travel with liquid: a tip that touches a
sample well carries that well's origins, and liquid it dispenses carries them
into the destination well. That lets the simulator notice when one tip touches
two different samples, which strict simulation otherwise accepts because the
volumes all add up.

Contact model (deliberately simple, and stated so the findings can be judged):

* **aspirate and mix** put the tip in the liquid, so the tip picks up the
  well's origins;
* **dispense** is treated as a free (non-contact) dispense: it moves the tip's
  origins into the well but does not add the well's origins to the tip. This
  keeps the common "same tips dispense reagent into every sample from above"
  pattern clean;
* **waste** labware is never checked and never contaminates a tip.

Findings are recorded on the report as warnings, never raised, so hand-written
protocols that reuse tips on purpose still simulate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..heads.mca96 import Tip
    from ..labware.base import Labware, Well

CROSS_SAMPLE = "cross_sample_tip_reuse"
CARRYOVER_INTO_SOURCE = "sample_carryover_into_reagent"

_MAX_EVENTS = 50
_MAX_ORIGINS_SHOWN = 4

_DESCRIPTIONS = {
    CROSS_SAMPLE: "a tip that touched one sample touched a different sample",
    CARRYOVER_INTO_SOURCE: "a tip that touched a sample entered a sample-free reagent source",
}


def _is_waste(labware: "Labware") -> bool:
    category = str(getattr(labware, "category", "") or "").lower()
    return "waste" in category or "waste" in str(getattr(labware, "label", "")).lower()


def _shown(origins: frozenset[str]) -> list[str]:
    ordered = sorted(origins)
    if len(ordered) <= _MAX_ORIGINS_SHOWN:
        return ordered
    return ordered[:_MAX_ORIGINS_SHOWN] + [f"+{len(ordered) - _MAX_ORIGINS_SHOWN} more"]


@dataclass
class ContaminationTracker:
    events: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    # MCA tips set back into a box keep their history: box label → tip index → origins.
    _returned: dict[str, dict[int, frozenset[str]]] = field(default_factory=dict)

    # ── seeding ──────────────────────────────────────────────────────

    def seed_labware(self, labware: "Labware") -> None:
        """Give every well that starts with an analyte its own sample origin."""
        for address, well in getattr(labware, "wells", {}).items():
            if any(getattr(layer.reagent, "role", None) == "analyte" for layer in well.layers):
                origin = frozenset({f"{labware.label}:{address}"})
                well.sample_origins = well.sample_origins | origin
                well.liquid_origins = well.liquid_origins | origin

    # ── liquid contact ───────────────────────────────────────────────

    def on_contact(
        self,
        labware: "Labware",
        well: "Well",
        tip: "Tip",
        *,
        operation: str,
        step: Any = None,
        step_index: int | None = None,
    ) -> None:
        """Aspirate or mix: check the tip against the well, then mark the tip."""
        if _is_waste(labware):
            return
        carried = tip.sample_origins
        present = well.sample_origins
        if carried:
            if not present:
                self._record(CARRYOVER_INTO_SOURCE, labware, well, carried, present,
                             operation=operation, step=step, step_index=step_index)
            elif not carried <= present:
                self._record(CROSS_SAMPLE, labware, well, carried, present,
                             operation=operation, step=step, step_index=step_index)
        tip.sample_origins = carried | present
        if operation == "Aspirate":
            tip.load_origins = tip.load_origins | well.liquid_origins

    def on_deliver(self, labware: "Labware", well: "Well", tip: "Tip") -> None:
        """Dispense / empty into a well: the tip's sample origins move with it."""
        if _is_waste(labware):
            return
        if tip.sample_origins:
            well.sample_origins = well.sample_origins | tip.sample_origins
        if tip.load_origins:
            well.liquid_origins = well.liquid_origins | tip.load_origins

    @staticmethod
    def after_release(tip: "Tip") -> None:
        """Once a tip has released all its liquid it carries no sample liquid."""
        if tip.volume_ul <= 1e-9:
            tip.load_origins = frozenset()

    # ── MCA tip set-back / re-pickup ─────────────────────────────────

    def on_return(self, box_label: str, tips: list["Tip"]) -> None:
        memory = self._returned.setdefault(box_label, {})
        for index, tip in enumerate(tips):
            if tip.sample_origins:
                memory[index] = memory.get(index, frozenset()) | tip.sample_origins

    def on_pickup(self, box_label: str, tips: list["Tip"]) -> None:
        memory = self._returned.get(box_label, {})
        for index, tip in enumerate(tips):
            origins = memory.get(index)
            if origins:
                tip.sample_origins = origins

    # ── reporting ────────────────────────────────────────────────────

    def _record(
        self,
        category: str,
        labware: "Labware",
        well: "Well",
        carried: frozenset[str],
        present: frozenset[str],
        *,
        operation: str,
        step: Any,
        step_index: int | None,
    ) -> None:
        self.counts[category] = self.counts.get(category, 0) + 1
        if len(self.events) >= _MAX_EVENTS:
            return
        source_pos = getattr(step, "source_pos", None)
        self.events.append({
            "category": category,
            "operation": operation,
            "step_index": step_index,
            "labware": labware.label,
            "well": well.address,
            "tip_carries": _shown(carried),
            "well_contains": _shown(present),
            "line": getattr(source_pos, "line", None),
        })

    def summary_warnings(self) -> list[str]:
        out: list[str] = []
        for category, count in sorted(self.counts.items()):
            first = next((e for e in self.events if e["category"] == category), None)
            where = ""
            if first is not None:
                line = f" line {first['line']}," if first.get("line") else ""
                where = (
                    f" First at{line} {first['operation']} {first['labware']}:{first['well']}"
                    f" (tip carries {', '.join(first['tip_carries'])})."
                )
            out.append(
                f"{category}: {count} event(s) where {_DESCRIPTIONS[category]}.{where}"
            )
        return out
