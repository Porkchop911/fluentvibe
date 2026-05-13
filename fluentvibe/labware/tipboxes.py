"""Tip-box labware. Tracks whether the box is full of fresh tips."""

from __future__ import annotations

from .base import Labware


class TipBox(Labware):
    """Generic tip box. Subclasses fix the per-tip capacity."""

    category = "tip_box"
    taxonomic_grid = (0, 0)  # tip boxes are not iterated as wells in v1.1
    offline_max_well_volume_ul = 0.0
    capacity_ul: float = 0.0

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        # Tip boxes don't have liquid wells; suppress the parsed pipettable grid
        # if any (some tip-box xcmps populate one).
        self.wells = {}
        self.is_full: bool = True


class MCA100Box(TipBox):
    capacity_ul = 100.0


class MCA200Box(TipBox):
    capacity_ul = 200.0


class MCA500Box(TipBox):
    capacity_ul = 500.0


class FCA50Box(TipBox):
    capacity_ul = 50.0


class FCA200Box(TipBox):
    capacity_ul = 200.0


class FCA1000Box(TipBox):
    capacity_ul = 1000.0
