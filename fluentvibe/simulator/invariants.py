"""Physical invariants — raised by the simulator on impossible operations.

These are *physics*, not domain rules. No keyword lists, no scenario logic.
"""


class SimulationError(Exception):
    """Base class for all simulator-raised invariant violations."""


class MissingTipsError(SimulationError):
    """Aspirate / dispense / mix attempted with no tips on the head."""


class MissingAdapterError(SimulationError):
    """MCA pipetting attempted with no adapter mounted."""


class InsufficientVolumeError(SimulationError):
    """A well does not have enough volume to satisfy an aspirate request."""


class OverdrawError(SimulationError):
    """A tip cannot dispense more than it currently holds."""


class OccupiedSlotError(SimulationError):
    """A slot is already occupied and the operation does not stack."""


class CannotAspirateError(SimulationError):
    """Aspirate target violates physical rules. Reserved; the bead model
    handles magnet retention via the well bead phase rather than refusing
    aspiration (a magnet never withholds liquid)."""


class MissingSimValueError(SimulationError):
    """A runtime variable's sim-time value was required but not provided."""


class InvalidSlotError(SimulationError):
    """`place()` targets a (location, position) not on the workspace."""


class TroughPlacementError(InvalidSlotError):
    """Trough placed on a non-`WS_100ml_*` slot, or wrong catalog for the
    arm's reach. The arm on SAT_Fluent_780 can only reach trough sites named
    `WS_100ml_*`; the `100ml` catalog is too tall for standard tips and
    should only be used for high-volume reagents (ethanol washes)."""


class MissingFCATipBoxError(SimulationError):
    """A LiHa/FCA pipetting step or worklist load exists but no FCA tip
    box is placed on the worktable. FluentControl raises
    `No DiTi-Labware … found` / `Tip(s) are not mounted` at runtime; the
    DSL refuses the compile early."""


class LihaTipMismatchError(MissingFCATipBoxError):
    """The LiHa head picks up a non-FCA tip box (e.g. an `MCA96 …` box).
    The LiHa/FCA arm can only mount FCA DiTis; FluentControl raises
    `No DiTi-Labware … found` + `Tip(s) are not mounted`. Subclasses
    `MissingFCATipBoxError` so existing handlers still catch it."""


class LiquidClassSectionError(SimulationError):
    """A Mix step references a liquid class with no `Mix` micro-script
    section (e.g. `Water Free Single`). FluentControl raises
    `Liquid subclass section "Mix" is missing`; the DSL refuses the compile
    early and points at a Mix-capable class (`Water Mix`)."""
