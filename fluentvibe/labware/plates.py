"""Plate-shaped labware: 96-well, 96-deep, 384-well.

Behavior is shared (`Plate`); subclasses fix the *taxonomic* grid for
authoring ergonomics and offline-mode synthesis. Per-catalog facts
(exact dimensions, well max-volume from cavity geometry) come from the
SQL index + .xcmp parse on construction.
"""

from __future__ import annotations

from .base import Labware


class Plate(Labware):
    """Generic plate. Subclass for fixed-grid families."""

    category = "plate"
    taxonomic_grid = (0, 0)
    offline_max_well_volume_ul = 350.0


class Plate96(Plate):
    """Standard 96-well plate (8 rows × 12 cols)."""
    taxonomic_grid = (8, 12)
    offline_max_well_volume_ul = 350.0


class Plate96Deep(Plate):
    """96-deep-well plate."""
    taxonomic_grid = (8, 12)
    offline_max_well_volume_ul = 1000.0


class Plate384(Plate):
    """Standard 384-well plate (16 rows × 24 cols)."""
    taxonomic_grid = (16, 24)
    offline_max_well_volume_ul = 90.0
