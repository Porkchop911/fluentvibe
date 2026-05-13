"""Tube racks — discrete tube positions (Eppendorf, Falcon, Cryo, …)."""

from __future__ import annotations

from .base import Labware, Well, well_grid_addresses


class TubeRack(Labware):
    """Tube rack with discrete tube positions.

    For v1.1 the rack's positions are exposed as `wells` so existing
    aspirate/dispense semantics work uniformly. Per-position max volume comes
    from the catalog (.xcmp pipettable cavity); offline defaults to 1.5mL.

    Future v2 work: per-tube reagent identity, partial fills, tube-cap state.
    """

    category = "tube_rack"
    taxonomic_grid = (0, 0)            # discovered from catalog
    offline_max_well_volume_ul = 1500.0
