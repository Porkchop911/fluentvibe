"""Reservoir-shaped labware: troughs and waste pools.

A trough is modelled as a single 'A1' well so the layered/volume bookkeeping
works the same way as for plates and the address matches the rest of the
labware family. The catalog .xcmp may also have a multi-cell arrangement
(e.g. trough + cap segments) — for v1.1 we collapse to one pool; multi-cavity
troughs are a v2 refinement.
"""

from __future__ import annotations

import re

from .base import Labware, Well


def _infer_trough_capacity_ul(catalog_name: str) -> float | None:
    """Infer a single-pool trough capacity from its catalog name.

    The xcmp does not carry per-pool volume, and the same `Trough` Python
    class is used for catalogs that span 25 mL to 300 mL. Read the volume
    out of the catalog name when it advertises one (e.g. `300ml SBS`,
    `100ml`, `25ml_short`). Return None if no marker is present.
    """
    if not catalog_name:
        return None
    lowered = catalog_name.lower()
    m = re.search(r"(\d+)\s*ml\b", lowered)
    if m:
        return float(m.group(1)) * 1_000.0
    if "waste" in lowered:
        return 300_000.0
    return None


class Trough(Labware):
    """Generic single-pool reservoir."""
    category = "trough"
    taxonomic_grid = (0, 0)
    offline_max_well_volume_ul = 25_000.0
    pool_address = "A1"

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        # If the catalog/.xcmp populated multi-well pipettable wells, replace
        # them with a single pool whose max_volume is the sum.
        total_max = sum(w.max_volume_ul for w in self.wells.values()) if self.wells else 0.0
        catalog_name = getattr(catalog_entry, "name", "") or ""
        inferred = _infer_trough_capacity_ul(catalog_name)
        if max_well_volume_ul is not None:
            max_vol = max_well_volume_ul
        elif inferred is not None:
            # Catalog name advertises a volume (`300ml SBS`, `100ml`, …) —
            # use it. The xcmp has no per-pool volume, so the fallback that
            # `_populate_from_catalog` stored is the class default, not
            # truth from the catalog.
            max_vol = inferred
        elif total_max > 0:
            max_vol = total_max
        else:
            max_vol = self.offline_max_well_volume_ul
        self.wells = {self.pool_address: Well(address=self.pool_address, max_volume_ul=max_vol)}

    @property
    def pool(self) -> Well:
        return self.wells[self.pool_address]


class Trough25mL(Trough):
    offline_max_well_volume_ul = 25_000.0


class Trough100mL(Trough):
    offline_max_well_volume_ul = 100_000.0


class Waste(Trough):
    """A waste reservoir behaves like a trough for state-tracking purposes."""
    offline_max_well_volume_ul = 300_000.0
