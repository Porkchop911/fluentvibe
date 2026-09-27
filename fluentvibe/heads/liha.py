"""LiHa authoring helpers."""

from __future__ import annotations

from typing import Optional, Sequence, Union

from ..ir.schema import (
    LihaAspirateStep,
    LihaDispenseStep,
    LihaDropTipsStep,
    LihaEmptyTipsStep,
    LihaGetTipsStep,
    LihaMixStep,
)
from ..labware.base import Labware


def _selection(wells: Optional[Sequence[str]], well_offset) -> Optional[str]:
    """Per-channel wells -> the step's selection (channel i uses ``wells[i]``)."""
    if wells is None:
        return None
    if well_offset is not None:
        raise ValueError("give either wells= (one well per channel) or well_offset=, not both")
    addresses = [str(w).strip().upper() for w in wells]
    if not 1 <= len(addresses) <= 8:
        raise ValueError(f"wells= needs 1-8 wells (one per LiHa channel), got {len(addresses)}")
    return ";".join(addresses)


class LiHa:
    """Liquid Handling Arm authoring facade.

    Pipetting addresses 8 consecutive wells from ``well_offset`` by default.
    ``wells=["A1", "B1", "C1"]`` instead gives each channel its own well (channel
    *i* uses ``wells[i]``; fewer wells use fewer channels): a partial column, a
    cherry-pick, or ``["A1"] * 3`` to dispense three channels into one well (pooling).
    """

    def __init__(self, worktable) -> None:
        self.worktable = worktable

    @staticmethod
    def _label(labware: Optional[Union[Labware, str]]) -> Optional[str]:
        if labware is None:
            return None
        if isinstance(labware, str):
            return labware
        return labware.label

    def get_tips(self, labware: Optional[Union[Labware, str]] = None) -> None:
        self.worktable._emit(LihaGetTipsStep(labware_name=self._label(labware)))

    def drop_tips(self, labware: Optional[Union[Labware, str]] = None) -> None:
        self.worktable._emit(LihaDropTipsStep(labware_name=self._label(labware)))

    def aspirate(
        self,
        labware: Union[Labware, str],
        volume: Union[float, str],
        *,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str]] = None,
        wells: Optional[Sequence[str]] = None,
        volumes: Optional[Sequence[Union[float, str]]] = None,
        channels: Optional[Sequence[int]] = None,
    ) -> None:
        """``volumes`` (with ``wells``): one volume per well/channel (normalisation, dilutions).
        ``channels``: which LiHa channels (0-7) serve the wells, in order (default:
        the row of each well within one column, else 0..n-1)."""
        if volumes is not None and (wells is None or len(volumes) != len(wells)):
            raise ValueError("volumes= needs wells= of the same length (one volume per well)")
        if channels is not None and (wells is None or len(channels) != len(wells)
                                     or any(not 0 <= int(c) <= 7 for c in channels)):
            raise ValueError("channels= needs wells= of the same length and channels 0-7")
        self.worktable._emit(
            LihaAspirateStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=_selection(wells, well_offset),
                volumes=list(volumes) if volumes is not None else None,
                channels=[int(c) for c in channels] if channels is not None else None,
            )
        )

    def dispense(
        self,
        labware: Union[Labware, str],
        volume: Union[float, str],
        *,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str]] = None,
        wells: Optional[Sequence[str]] = None,
        volumes: Optional[Sequence[Union[float, str]]] = None,
        channels: Optional[Sequence[int]] = None,
    ) -> None:
        """``volumes`` (with ``wells``): one volume per well/channel (normalisation, dilutions).
        ``channels``: which LiHa channels (0-7) serve the wells, in order (default:
        the row of each well within one column, else 0..n-1)."""
        if volumes is not None and (wells is None or len(volumes) != len(wells)):
            raise ValueError("volumes= needs wells= of the same length (one volume per well)")
        if channels is not None and (wells is None or len(channels) != len(wells)
                                     or any(not 0 <= int(c) <= 7 for c in channels)):
            raise ValueError("channels= needs wells= of the same length and channels 0-7")
        self.worktable._emit(
            LihaDispenseStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=_selection(wells, well_offset),
                volumes=list(volumes) if volumes is not None else None,
                channels=[int(c) for c in channels] if channels is not None else None,
            )
        )

    def mix(
        self,
        labware: Union[Labware, str],
        volume: Union[float, str],
        *,
        cycles: Union[int, str] = 10,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str]] = None,
        wells: Optional[Sequence[str]] = None,
    ) -> None:
        self.worktable._emit(
            LihaMixStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                cycles=cycles,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=_selection(wells, well_offset),
            )
        )

    def empty_tips(
        self,
        labware: Union[Labware, str],
        volume: Union[float, str] = 0,
        *,
        liquid_class: Optional[str] = None,
    ) -> None:
        self.worktable._emit(
            LihaEmptyTipsStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                liquid_class=liquid_class,
            )
        )
