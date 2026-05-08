"""Deck items — wash stations, waste chutes, hotels, fixed-deck infrastructure.

These don't carry pipetted reagents in v1.1 (or, in the case of WashStation,
liquid is flushed away rather than tracked). They populate a worktable slot,
satisfy gripper-move destinations, and surface mm geometry — but no per-well
state tracking.
"""

from __future__ import annotations

from .base import Labware


class WashStation(Labware):
    """Tip-wash station. Aspirate/dispense here is a flush, not a transfer."""
    category = "wash_station"
    taxonomic_grid = (0, 0)


class WasteChute(Labware):
    """Sink for empty-tips / waste-dispense."""
    category = "waste_chute"
    taxonomic_grid = (0, 0)


class Hotel(Labware):
    """Multi-z plate storage. Gripper destination."""
    category = "hotel"
    taxonomic_grid = (0, 0)


class FixedDeck(Labware):
    """Catch-all for unknown / fixed deck items.

    Loads geometry but no behavior — used by `Worktable.from_workspace` for
    components whose category is `fixed_deck`.
    """
    category = "fixed_deck"
    taxonomic_grid = (0, 0)
