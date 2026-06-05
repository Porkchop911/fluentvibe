"""Labware base — Layer, Well, Labware.

`Labware` is the root for everything that occupies a worktable slot. Per-well
content is modelled as a list of `Layer` objects, bottom→top, so the
simulator can honestly track stratified contents.

In v1.1, per-catalog facts (well count, max volume, mm dimensions) are
loaded from the FluentControl install via the SQL catalog index. The class
itself only declares the *taxonomic* shape (`Plate96` → 8×12 wells); the
catalog tells you which catalog entry produces which facts.

When the catalog index is empty (CI / dev box without FC), classes
synthesize a generic default from their taxonomic shape so tests can still
run; mm geometry returns `None` and a one-shot warning is emitted.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from ..reagent import Reagent
    from ..catalog.xcmp import XcmpComponent


_ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ── Errors / warnings ──────────────────────────────────────────────


class CatalogIndexMissing(UserWarning):
    """Raised as a UserWarning when the catalog index is empty and a labware
    class falls back to synthesised defaults."""


_warned_offline_once = False


def _warn_offline_once() -> None:
    global _warned_offline_once
    if not _warned_offline_once:
        warnings.warn(
            "fluentvibe catalog index is empty; using synthesised offline defaults. "
            "Run `fluentvibe catalog refresh` for accurate per-catalog geometry.",
            CatalogIndexMissing,
            stacklevel=3,
        )
        _warned_offline_once = True


# ── Data types ─────────────────────────────────────────────────────


@dataclass
class Layer:
    reagent: "Reagent"
    volume_ul: float


@dataclass
class BeadPhase:
    """Magnetic-bead solid phase of a well.

    Beads are an *attribute*, not a liquid layer — they are non-volumetric
    (never counted in `Well.volume_ul`) and a magnet never withholds liquid
    from a tip. The magnet only immobilises the beads (and any analyte bound
    to them). `bound` holds analyte that has associated with the beads;
    binding/release is driven by mix steps (see the simulator).
    """

    present: bool = True
    suspended: bool = True
    """True when the beads are dispersed in the liquid (labware not
    magnetized); False when pelleted against the magnet wall."""
    bound: list[Layer] = field(default_factory=list)
    """Analyte associated with the beads (travels with the beads, not the
    free liquid)."""

    @property
    def bound_volume_ul(self) -> float:
        return sum(layer.volume_ul for layer in self.bound)


@dataclass
class Well:
    address: str
    max_volume_ul: float
    layers: list[Layer] = field(default_factory=list)
    position_mm: Optional[tuple[float, float, float]] = None
    bead_phase: Optional[BeadPhase] = None
    """Solid-phase bead attribute, or None when the well has never received a
    bead-carrier reagent. Non-volumetric — excluded from `volume_ul`."""

    @property
    def volume_ul(self) -> float:
        """Free (aspirable) liquid only. Beads and bead-bound analyte are a
        separate solid-phase attribute and never count here."""
        return sum(layer.volume_ul for layer in self.layers)

    @property
    def is_empty(self) -> bool:
        return not self.layers

    def add_layer(self, reagent: "Reagent", volume_ul: float) -> None:
        if volume_ul <= 0:
            return
        if self.layers and self.layers[-1].reagent is reagent:
            self.layers[-1].volume_ul += volume_ul
        else:
            self.layers.append(Layer(reagent=reagent, volume_ul=volume_ul))


def well_grid_addresses(rows: int, cols: int) -> list[str]:
    """Column-major: A1..H1, A2..H2, …"""
    out: list[str] = []
    for c in range(1, cols + 1):
        for r in range(rows):
            out.append(f"{_ROW_LETTERS[r]}{c}")
    return out


# ── Labware base ───────────────────────────────────────────────────


class Labware:
    """Base for any worktable-residing item.

    Subclasses declare the *taxonomic* shape via class attributes:

    - `category`: matches one of `fluentvibe.catalog.inference.CATEGORIES`.
    - `taxonomic_grid`: (rows, cols) for plate-shaped families. (0, 0)
      means "no fixed grid" (catalog determines).
    - `offline_max_well_volume_ul`: used only when the catalog index is
      empty.

    Per-catalog facts (well count if not fixed, max volume, mm dims, well
    positions) are loaded from the catalog row + .xcmp parse on construction.
    """

    category: str = "fixed_deck"
    taxonomic_grid: tuple[int, int] = (0, 0)
    offline_max_well_volume_ul: float = 0.0

    def __init__(
        self,
        label: str,
        *,
        catalog: Optional[str] = None,
        max_well_volume_ul: Optional[float] = None,
    ) -> None:
        self.label: str = label
        self.slot: Optional[tuple[str, int]] = None
        self.stack_below: list[Labware] = []

        # Geometry / wells get filled in by one of two paths.
        self.catalog_name: str = ""
        self.dim_mm: Optional[tuple[float, float, float]] = None
        self.site_offsets_mm: tuple[tuple[float, float, float], ...] = ()
        self.wells: dict[str, Well] = {}

        # Resolution rules:
        #   - index present + catalog given        → look up + parse xcmp.
        #   - index present + no catalog           → raise (refuse to guess).
        #   - index missing + catalog given        → warn, offline synthesis.
        #   - index missing + no catalog           → offline synthesis silently
        #     (offline-fallback path; catalog name becomes `<offline:ClassName>`).
        from ..catalog.catalog import index_exists, resolve_by_name, suggest_names

        if index_exists():
            if not catalog:
                raise ValueError(
                    f"{type(self).__name__}({label!r}): the catalog index is built; "
                    f"you must pass `catalog=<exact FluentControl name>`. "
                    f"Run `fluentvibe catalog find <pattern>` to search."
                )
            entry = resolve_by_name(catalog)
            if entry is None:
                hint = ""
                suggestions = suggest_names(catalog)
                if suggestions:
                    hint = " Did you mean: " + ", ".join(repr(s) for s in suggestions) + "?"
                raise ValueError(
                    f"Catalog name {catalog!r} not found in fluentvibe catalog index.{hint} "
                    f"Run `fluentvibe catalog find {catalog!r}` to search."
                )
            self._populate_from_catalog(entry, max_well_volume_ul=max_well_volume_ul)
        else:
            if catalog:
                _warn_offline_once()
            self.catalog_name = catalog or self._offline_synthetic_catalog_name()
            self._populate_offline(max_well_volume_ul=max_well_volume_ul)

    # ── Catalog-driven population ─────────────────────────────────

    def _populate_from_catalog(self, entry, *, max_well_volume_ul: Optional[float]) -> None:
        from ..catalog.xcmp import load_xcmp

        self.catalog_name = entry.name
        if entry.dim_x_mm is not None:
            self.dim_mm = (entry.dim_x_mm, entry.dim_y_mm or 0.0, entry.dim_z_mm or 0.0)

        # Lazily parse the .xcmp for full geometry. Most callers won't need
        # site_offsets_mm or per-well positions, but they're cheap.
        try:
            comp = load_xcmp(entry.file_path)
        except Exception:
            comp = None

        # Pipettable wells override taxonomic_grid for any catalog that has them.
        if comp is not None and comp.pipettable is not None:
            pip = comp.pipettable
            cavity_vol = pip.cavity.volume_ul if pip.cavity else None
            max_vol = (
                max_well_volume_ul
                if max_well_volume_ul is not None
                else (cavity_vol if cavity_vol is not None else self.offline_max_well_volume_ul)
            )
            for c in range(1, pip.x_wells + 1):
                for r in range(pip.y_wells):
                    addr = f"{_ROW_LETTERS[r]}{c}"
                    pos_mm = (
                        pip.first_well_mm[0] + (c - 1) * pip.x_spacing_mm,
                        pip.first_well_mm[1] + r * pip.y_spacing_mm,
                        pip.first_well_mm[2],
                    )
                    self.wells[addr] = Well(
                        address=addr,
                        max_volume_ul=max_vol,
                        position_mm=pos_mm,
                    )

        if comp is not None and comp.arrangement is not None:
            self.site_offsets_mm = tuple(
                comp.arrangement.site_offsets_mm.get(i, (0.0, 0.0, 0.0))
                for i in range(comp.arrangement.site_count)
            )

        # Subclass hook for non-plate populations (Trough single-pool, TipBox
        # is_full, etc.) — invoked AFTER pipettable wells are populated so the
        # subclass can override or supplement.
        self._post_populate(catalog_entry=entry, comp=comp,
                            max_well_volume_ul=max_well_volume_ul)

    # ── Offline synthesis ─────────────────────────────────────────

    def _populate_offline(self, *, max_well_volume_ul: Optional[float]) -> None:
        rows, cols = self.taxonomic_grid
        max_vol = (
            max_well_volume_ul
            if max_well_volume_ul is not None
            else self.offline_max_well_volume_ul
        )
        if rows and cols:
            for addr in well_grid_addresses(rows, cols):
                self.wells[addr] = Well(address=addr, max_volume_ul=max_vol)
        # Subclasses can override / supplement.
        self._post_populate(catalog_entry=None, comp=None,
                            max_well_volume_ul=max_well_volume_ul)

    def _offline_synthetic_catalog_name(self) -> str:
        """Catalog name used in IR steps when no real catalog row was resolved."""
        return f"<offline:{type(self).__name__}>"

    # ── Subclass hook ─────────────────────────────────────────────

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        """Subclasses extend this to set family-specific state.

        Default: no-op. `Trough` builds a single-pool well; `TipBox` sets
        `is_full = True` and `capacity_ul`; etc.
        """
        return

    # ── Well selection ────────────────────────────────────────────

    def well(self, address: str) -> Well:
        if address not in self.wells:
            raise KeyError(f"{self.label}: no well at address {address!r}")
        return self.wells[address]

    def all_wells(self) -> list[Well]:
        return list(self.wells.values())

    def column(self, idx: int) -> list[Well]:
        rows, cols = self._effective_grid()
        if not rows or idx < 1 or idx > cols:
            raise ValueError(f"{self.label}: column {idx} out of range (1..{cols})")
        return [self.wells[f"{_ROW_LETTERS[r]}{idx}"] for r in range(rows)]

    def row(self, letter: str) -> list[Well]:
        rows, cols = self._effective_grid()
        letter = letter.upper()
        if letter not in _ROW_LETTERS[:rows]:
            raise ValueError(f"{self.label}: row {letter!r} out of range")
        return [self.wells[f"{letter}{c}"] for c in range(1, cols + 1)]

    def _effective_grid(self) -> tuple[int, int]:
        if self.taxonomic_grid != (0, 0):
            return self.taxonomic_grid
        # Derive from wells if no taxonomic grid set.
        if not self.wells:
            return (0, 0)
        # Try to back-derive (rows, cols) from addresses like 'A1', 'H12'.
        rows = max(_ROW_LETTERS.index(a[0]) for a in self.wells if a[0] in _ROW_LETTERS) + 1
        cols = max(int(a[1:]) for a in self.wells if a[1:].isdigit())
        return (rows, cols)

    def fill_all(self, reagent: "Reagent", volume_ul: float) -> None:
        for w in self.wells.values():
            w.layers = [Layer(reagent=reagent, volume_ul=volume_ul)]

    # ── Stacking / state ──────────────────────────────────────────

    @property
    def is_magnetized(self) -> bool:
        from .magnet import MagnetRack
        return any(isinstance(x, MagnetRack) for x in self.stack_below)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.label!r}, catalog={self.catalog_name!r})"


class ExternalLabware(Labware):
    """Placeholder for exact FluentControl catalog names outside the local index."""

    def __init__(self, label: str, *, catalog: str, max_well_volume_ul: Optional[float] = None) -> None:
        self.label: str = label
        self.slot: Optional[tuple[str, int]] = None
        self.stack_below: list[Labware] = []
        self.catalog_name: str = catalog
        self.dim_mm: Optional[tuple[float, float, float]] = None
        self.site_offsets_mm: tuple[tuple[float, float, float], ...] = ()
        self.wells: dict[str, Well] = {}
