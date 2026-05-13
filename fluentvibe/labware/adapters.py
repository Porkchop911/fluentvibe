"""MCA head adapters (e.g. EVA — Extended Volume Adapter).

Adapters live on the head, not on a worktable slot. They're modelled as
Labware so they can be referenced by catalog name; `place()`-ing one is
unusual (the renderer treats EVA as implicit).
"""

from __future__ import annotations

from .base import Labware


class Adapter(Labware):
    """Generic head adapter."""
    category = "adapter"
    taxonomic_grid = (0, 0)


class EvaAdapter(Adapter):
    """Extended-volume adapter for the MCA-96 head."""
    pass
