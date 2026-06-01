"""Reagent identity model.

Reagents are first-class objects whose identity is the Python object reference.
The simulator tracks reagent identity through aspirate/dispense, layered well
contents, and tip carry-over.

A reagent also carries a `role` that the simulator uses to model magnetic-bead
chemistry deterministically (no fake chemistry — every behaviour is keyed off
the author-declared role):

- ``plain``         — ordinary liquid (default).
- ``bead_carrier``  — magnetic-bead suspension. Its µL is normal aspirable
                       liquid; dispensing it marks the destination well's
                       bead phase present. The magnet retains the *beads*,
                       never the liquid.
- ``analyte``       — the species captured by beads (e.g. DNA). Moves to the
                       bead-bound phase on a mix with suspended beads, returns
                       to free liquid on a mix with an ``eluent`` present.
- ``eluent``        — release/elution buffer (EB/water) that frees bound
                       analyte on an off-magnet mix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROLES = ("plain", "bead_carrier", "analyte", "eluent")


@dataclass(frozen=True, eq=False)
class Reagent:
    """A single reagent. Identity is `is`-based, not name-based.

    Two `Reagent("Ethanol 70%")` calls produce two distinct reagents — keep one
    canonical instance per reagent and reference it everywhere.
    """

    name: str
    role: str = "plain"
    """One of :data:`ROLES`. Drives the simulator's bead/analyte model.
    Author-side only; never serialised into IR/.xscr."""

    metadata: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    """Free-form metadata for downstream tooling. Not interpreted by the
    simulator; not rendered into IR."""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(
                f"Reagent role {self.role!r} is not one of {ROLES}."
            )

    @property
    def pinned_when_magnetized(self) -> bool:
        """Back-compat read-only alias. The old binary "pinned liquid layer"
        model is gone; bead retention is now the ``bead_carrier`` role plus a
        well bead phase. ``True`` iff this reagent carries beads."""
        return self.role == "bead_carrier"

    @property
    def carries_beads(self) -> bool:
        return self.role == "bead_carrier"

    @property
    def is_analyte(self) -> bool:
        return self.role == "analyte"

    @property
    def is_eluent(self) -> bool:
        return self.role == "eluent"

    def __repr__(self) -> str:
        flags = "" if self.role == "plain" else f" {self.role}"
        return f"Reagent({self.name!r}{flags})"
