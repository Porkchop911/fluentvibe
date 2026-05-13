"""LiHa authoring helpers."""

from __future__ import annotations

from typing import Optional, Union

from ..ir.schema import (
    LihaAspirateStep,
    LihaDispenseStep,
    LihaDropTipsStep,
    LihaEmptyTipsStep,
    LihaGetTipsStep,
    LihaMixStep,
)
from ..labware.base import Labware


class LiHa:
    """Liquid Handling Arm authoring facade."""

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
    ) -> None:
        self.worktable._emit(
            LihaAspirateStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
            )
        )

    def dispense(
        self,
        labware: Union[Labware, str],
        volume: Union[float, str],
        *,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str]] = None,
    ) -> None:
        self.worktable._emit(
            LihaDispenseStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
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
    ) -> None:
        self.worktable._emit(
            LihaMixStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                cycles=cycles,
                liquid_class=liquid_class,
                well_offset=well_offset,
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
