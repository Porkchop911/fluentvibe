"""tecanlab — stateful object-model framework for FluentControl protocols."""

# Build the catalog index on first import (one-time slow startup, ~5–15 s
# on a 629-component install). Quiet no-op if the index is already there.
from .catalog import ensure_index as _ensure_index
_ensure_index()

from .reagent import Reagent
from .worktable import Worktable
from .gripper import Gripper
from .heads import LiHa, MCA96Head, Tip
from .labware import (
    Labware, ExternalLabware, Layer, Well,
    Plate, Plate96, Plate96Deep, Plate384,
    Trough, Trough25mL, Trough100mL, Waste,
    TipBox, MCA100Box, MCA200Box, MCA500Box,
    FCA50Box, FCA200Box, FCA1000Box,
    EvaAdapter, MagnetRack,
    TubeRack, WashStation, WasteChute, Hotel, Adapter, FixedDeck,
)
from .authoring import PromptAuthoringService, author_protocol
from .simulator import (
    Simulator, Snapshot, SimulationReport, StepCoverage, EffectKind,
    SimulationError,
    MissingTipsError, InsufficientVolumeError, OverdrawError,
    OccupiedSlotError, CannotAspirateError, MissingAdapterError,
    MissingSimValueError, InvalidSlotError,
)
from .labware import CatalogIndexMissing

__all__ = [
    "Reagent",
    "Worktable", "Gripper",
    "MCA96Head", "LiHa", "Tip",
    "Labware", "ExternalLabware", "Layer", "Well",
    "Plate", "Plate96", "Plate96Deep", "Plate384",
    "Trough", "Trough25mL", "Trough100mL", "Waste",
    "TipBox", "MCA100Box", "MCA200Box", "MCA500Box",
    "FCA50Box", "FCA200Box", "FCA1000Box",
    "EvaAdapter", "MagnetRack",
    "TubeRack", "WashStation", "WasteChute", "Hotel", "Adapter", "FixedDeck",
    "PromptAuthoringService", "author_protocol",
    "Simulator", "Snapshot", "SimulationReport", "StepCoverage", "EffectKind",
    "SimulationError",
    "MissingTipsError", "InsufficientVolumeError", "OverdrawError",
    "OccupiedSlotError", "CannotAspirateError", "MissingAdapterError",
    "MissingSimValueError", "InvalidSlotError",
    "CatalogIndexMissing",
]
