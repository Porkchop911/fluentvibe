"""Tip-box labware. Tracks whether the box is full of fresh tips."""

from __future__ import annotations

from .base import Labware


class TipBox(Labware):
    """Generic tip box. Subclasses fix the per-tip capacity.

    Tip occupancy is tracked per *column* so the simulator can model partial
    pickups and tip sorting (a partial pickup removes only the addressed
    columns; a partial set-back fills only the target columns). ``is_full`` is
    kept as a derived property so all the legacy full-box paths
    (``box.is_full = True/False``) keep working unchanged — a full pickup
    clears every column, a full return refills them.
    """

    category = "tip_box"
    taxonomic_grid = (0, 0)  # tip boxes are not iterated as wells in v1.1
    offline_max_well_volume_ul = 0.0
    capacity_ul: float = 0.0
    total_columns: int = 12  # 96-format boxes are 12 columns wide

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        # Tip boxes don't have liquid wells; suppress the parsed pipettable grid
        # if any (some tip-box xcmps populate one).
        self.wells = {}
        self._tips_columns: set[int] = set(range(1, self.total_columns + 1))

    # ── Per-column tip occupancy ────────────────────────────────────
    def _cols(self) -> set[int]:
        cols = getattr(self, "_tips_columns", None)
        if cols is None:  # defensive: constructed without _post_populate
            cols = set(range(1, self.total_columns + 1))
            self._tips_columns = cols
        return cols

    @property
    def is_full(self) -> bool:
        return len(self._cols()) == self.total_columns

    @is_full.setter
    def is_full(self, value: bool) -> None:
        self._tips_columns = set(range(1, self.total_columns + 1)) if value else set()

    @property
    def is_empty(self) -> bool:
        return not self._cols()

    @property
    def columns_present(self) -> set[int]:
        """1-based columns that currently hold tips."""
        return set(self._cols())

    def remove_columns(self, columns) -> None:
        self._cols().difference_update(int(c) for c in columns)

    def add_columns(self, columns) -> None:
        self._cols().update(int(c) for c in columns if 1 <= int(c) <= self.total_columns)


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
