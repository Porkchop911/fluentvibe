"""Simulator — walks the protocol IR and reconstructs a snapshotted twin."""

from .invariants import (
    CannotAspirateError,
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
    TroughPlacementError,
)
from .report import EffectKind, SimulationFailure, SimulationReport, StepCoverage
from .snapshots import Snapshot
from .walk import Simulator

__all__ = [
    "Simulator", "Snapshot", "SimulationReport", "SimulationFailure", "StepCoverage", "EffectKind",
    "SimulationError",
    "MissingTipsError", "InsufficientVolumeError", "OverdrawError",
    "OccupiedSlotError", "CannotAspirateError", "MissingAdapterError",
    "MissingSimValueError", "InvalidSlotError",
    "TroughPlacementError", "MissingFCATipBoxError",
    "LihaTipMismatchError", "LiquidClassSectionError",
]
