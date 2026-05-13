"""Per-step snapshots of the twin world."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..ir.schema import Step
    from ..labware.base import Labware
    from ..heads.mca96 import Tip


@dataclass
class Snapshot:
    """Frozen view of the simulator's state after one IR step.

    `slot_map`, `mca_tips`, `mca_adapter`, etc. are deep-copied at snapshot
    time so later mutations cannot leak back into history.
    """

    step_index: int
    step: "Step"
    slot_map: dict[tuple[str, int], list["Labware"]]
    mca_adapter_label: Optional[str]
    mca_tips: list["Tip"]
    mca_tip_box_label: Optional[str]
    liha_tips: list["Tip" | None]
    opaque_events: list[dict]
    warnings: list[str]

    def labware(self, label: str) -> "Labware":
        for stack in self.slot_map.values():
            for lw in stack:
                if lw.label == label:
                    return lw
        raise KeyError(f"No labware with label {label!r} at step {self.step_index}")


def take_snapshot(
    step_index: int,
    step: "Step",
    slot_map: dict,
    mca_adapter_label: Optional[str],
    mca_tips: list,
    mca_tip_box_label: Optional[str],
    liha_tips: list,
    opaque_events: list[dict],
    warnings: list[str],
) -> Snapshot:
    return Snapshot(
        step_index=step_index,
        step=step,
        slot_map=copy.deepcopy(slot_map),
        mca_adapter_label=mca_adapter_label,
        mca_tips=copy.deepcopy(mca_tips),
        mca_tip_box_label=mca_tip_box_label,
        liha_tips=copy.deepcopy(liha_tips),
        opaque_events=copy.deepcopy(opaque_events),
        warnings=list(warnings),
    )
