"""fluentvibe — stateful object-model framework for FluentControl protocols."""

# Build the catalog index on first import (one-time slow startup, ~5–15 s
# on a 629-component install). Quiet no-op if the index is already there.
from .catalog import ensure_index as _ensure_index

_ensure_index()

from .authoring import PromptAuthoringService, author_protocol
from .gripper import Gripper
from .heads import LiHa, MCA96Head, Tip
from .labware import (
    Adapter,
    BeadPhase,
    CatalogIndexMissing,
    EvaAdapter,
    ExternalLabware,
    FCA50Box,
    FCA200Box,
    FCA1000Box,
    FixedDeck,
    Hotel,
    Labware,
    Layer,
    MagnetRack,
    MCA100Box,
    MCA200Box,
    MCA500Box,
    Plate,
    Plate96,
    Plate96Deep,
    Plate384,
    TipBox,
    Trough,
    Trough25mL,
    Trough100mL,
    TubeRack,
    WashStation,
    Waste,
    WasteChute,
    Well,
)
from .reagent import Reagent
from .simulator import (
    CannotAspirateError,
    EffectKind,
    InsufficientVolumeError,
    InvalidSlotError,
    LihaTipMismatchError,
    LiquidClassSectionError,
    MissingAdapterError,
    MissingFCATipBoxError,
    MissingSimValueError,
    MissingTipsError,
    OccupiedSlotError,
    OverdrawError,
    SimulationError,
    SimulationReport,
    Simulator,
    Snapshot,
    StepCoverage,
    TroughPlacementError,
)
from .worklists import Gwl, GwlRecord, WorklistFormatError
from .worktable import Worktable

__all__ = [
    "Reagent",
    "Worktable", "Gripper",
    "Gwl", "GwlRecord", "WorklistFormatError",
    "MCA96Head", "LiHa", "Tip",
    "Labware", "ExternalLabware", "Layer", "Well", "BeadPhase",
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
    "TroughPlacementError", "MissingFCATipBoxError",
    "LihaTipMismatchError", "LiquidClassSectionError",
    "CatalogIndexMissing",
]
