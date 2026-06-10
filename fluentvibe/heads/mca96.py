"""MCA-96 pipetting head.

Authoring-side methods on this object emit one IR step per call. The
Simulator consumes the IR list and reconstructs head state (adapter, tip
contents) from scratch — head methods themselves don't mutate twin state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence, Union

from ..ir.schema import (
    GetHeadAdapterStep, DropHeadAdapterStep,
    PickUpTipsStep, SetTipsBackStep,
    AspirateStep, DispenseStep, Mca384EmptyTipsStep, Mca384MixStep,
)
from ..labware.adapters import EvaAdapter
from ..labware.base import Labware

if TYPE_CHECKING:
    from ..reagent import Reagent
    from ..worktable import Worktable


@dataclass
class Tip:
    """One pipetting tip on the head. `layers` is FIFO when dispensing."""

    capacity_ul: float
    layers: list = field(default_factory=list)  # list[Layer]

    @property
    def volume_ul(self) -> float:
        return sum(layer.volume_ul for layer in self.layers)

    @property
    def is_empty(self) -> bool:
        return not self.layers


class MCA96Head:
    """The MCA-96 pipetting head. Bound to a Worktable on construction."""

    def __init__(self, worktable: "Worktable") -> None:
        self._wt = worktable

    # ── Adapter ─────────────────────────────────────────────────────

    def mount_adapter(self, adapter: Optional[EvaAdapter] = None) -> None:
        """Mount a head adapter.

        With no argument, references the default EVA catalog name directly —
        EVA does not need to be placed on the worktable; the FluentControl
        renderer treats it as a head accessory. Pass an explicit `EvaAdapter`
        instance to override the catalog name.
        """
        labware_name = adapter.catalog_name if adapter is not None else "EVA[001]"
        self._wt._emit(GetHeadAdapterStep(labware_name=labware_name))

    def drop_adapter(self, adapter: Optional[EvaAdapter] = None) -> None:
        labware_name = adapter.catalog_name if adapter is not None else "EVA[001]"
        self._wt._emit(DropHeadAdapterStep(labware_name=labware_name))

    # ── Tips ────────────────────────────────────────────────────────

    def _label(self, labware: Union[Labware, str]) -> str:
        return labware.label if isinstance(labware, Labware) else labware

    def pick_up(
        self,
        tip_box: Union[Labware, str],
        *,
        columns: Optional[Sequence[int]] = None,
    ) -> None:
        """Pick up tips, optionally on only a partial block of the head.

        `columns` is the list of 1-based **box columns** to address, e.g. ``[1]``
        for a single column, ``[7,8,9,10,11,12]`` for the right half, or
        ``[1,4,7,10]`` to grab a whole sorted box in one go. Omit `columns` for
        a full pickup.

        The physical offset is **derived** from the columns, never passed in:
        FluentControl stores ``PartialColumnOffset = head_width - max(columns)``
        (box col 1 -> 11, col 12 -> 0). A single column can only be peeled from
        the box's current **left-most or right-most filled** column, so its idle
        channels overhang empty space; the simulator enforces this.
        """
        step = PickUpTipsStep(labware_name=self._label(tip_box))
        if columns is not None:
            step.columns = [int(c) for c in columns]
        self._wt._emit(step)

    def return_tips(
        self,
        tip_box: Optional[Union[Labware, str]] = None,
        *,
        columns: Optional[Sequence[int]] = None,
    ) -> None:
        """Set tips back, optionally as a partial block.

        `columns` are the 1-based **box columns** the tips land in. Setting tips
        back into an empty box has no edge constraint (no neighbouring tips to
        collide with), so any target column is allowed; the
        ``PartialColumnOffset`` is derived from it the same way as `pick_up`.
        """
        labware_name = self._label(tip_box) if tip_box is not None else None
        step = SetTipsBackStep(labware_name=labware_name)
        if columns is not None:
            step.columns = [int(c) for c in columns]
        self._wt._emit(step)

    # ── Pipetting ───────────────────────────────────────────────────

    def aspirate(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str],
        *,
        liquid_class: str,
        columns: Optional[Sequence[int]] = None,
    ) -> None:
        """Aspirate from `target` (auto-parallel over the labware's wells).

        `liquid_class` is required and must be the exact FluentControl
        liquid-class name. No defaults are pulled from elsewhere.

        `columns` selects 1-based plate columns for partial-column pipetting
        (e.g. `[1, 2, 3]` or sparse `[1, 3, 5]`); omit it to address the full
        plate.
        """
        self._wt._emit(AspirateStep(
            labware_name=self._label(target),
            volume=volume_ul,
            liquid_class=liquid_class,
            columns=list(columns) if columns is not None else None,
        ))

    def dispense(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str],
        *,
        liquid_class: str,
        columns: Optional[Sequence[int]] = None,
    ) -> None:
        self._wt._emit(DispenseStep(
            labware_name=self._label(target),
            volume=volume_ul,
            liquid_class=liquid_class,
            columns=list(columns) if columns is not None else None,
        ))

    def mix(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str],
        *,
        cycles: Union[int, str] = 10,
        liquid_class: str,
    ) -> None:
        self._wt._emit(Mca384MixStep(
            labware_name=self._label(target),
            volume=volume_ul,
            cycles=cycles,
            liquid_class=liquid_class,
        ))

    def empty_tips(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str],
        *,
        liquid_class: str = "Empty Tip",
    ) -> None:
        self._wt._emit(Mca384EmptyTipsStep(
            labware_name=self._label(target),
            volume=volume_ul,
            liquid_class=liquid_class,
        ))
