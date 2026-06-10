"""IR walker — reconstructs twin state and snapshots from a Protocol IR list."""

from __future__ import annotations

import ast
import operator
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, Optional

from ..heads.mca96 import Tip
from ..ir.schema import (
    AddLabwareStep, RemoveLabwareStep,
    GetHeadAdapterStep, DropHeadAdapterStep,
    PickUpTipsStep, SetTipsBackStep,
    AspirateStep, DispenseStep,
    RgaTransferLabwareStep, CgaGetFingersStep, CgaDropFingersStep,
    LoopStep, ConditionalStep, Mca384EmptyTipsStep, Mca384MixStep,
    SetLocationStep, SetVariableStep, SubRoutineStep,
    CommentStep, DelayStep, ExecuteApplicationStep, ExportVariableStep,
    GenericStep, ImportVariableStep, LihaAspirateStep, LihaDispenseStep,
    LihaDropTipsStep, LihaEmptyTipsStep, LihaGetTipsStep, LihaMixStep,
    Mca384DropTipsStep, Mca384GetTipsStep, Mca384MoveArmStep,
    QueryVariableStep, ScriptGroupStep, StartTimerStep, UserPromptStep,
    WaitForTimerStep, WaitStep, WorklistImportStep, LoadWorklistStep,
    ExecuteWorklistStep, LegacyDriverMacroStep,
)
from ..labware.base import Labware, Layer
from ..labware.tipboxes import TipBox
from .invariants import (
    CannotAspirateError, InsufficientVolumeError,
    InvalidSlotError, MissingAdapterError, MissingSimValueError,
    MissingTipsError, OccupiedSlotError, OverdrawError,
)
from .snapshots import Snapshot, take_snapshot
from .report import EffectKind, SimulationFailure, SimulationReport, StepCoverage
from .invariants import SimulationError

if TYPE_CHECKING:
    from ..reagent import Reagent
    from ..worktable import Worktable


class Simulator:
    """Walks a Worktable's protocol IR and reconstructs the twin.

    Author method calls update the worktable's slot_map at authoring time
    (so subsequent author calls see consistent state); the simulator
    rebuilds an independent twin from the IR alone, decoupled from
    authoring-time mutations.
    """

    def __init__(self, worktable: "Worktable") -> None:
        self._wt = worktable
        # Twin state — fresh slot map keyed on (loc, pos), bottom→top.
        self._slot_map: dict[tuple[str, int], list[Labware]] = {}
        # Maps catalog label → twin Labware copy.
        self._twin: dict[str, Labware] = {}
        # MCA-96 head state.
        self._mca_adapter_label: Optional[str] = None
        self._mca_tips: list[Tip] = []
        self._mca_tip_box_label: Optional[str] = None
        # LiHa/FCA state: eight independent channels.
        self._liha_tips: list[Tip | None] = [None] * 8
        self._liha_tip_box_label: Optional[str] = None
        # Running tally of total volume requested per source labware /
        # per-well across the whole sim. Always populated, regardless of
        # whether a source_volume_short failure occurs.
        self._source_requested_ul: dict[str, float] = {}
        self._source_requested_by_well_ul: dict[str, dict[str, float]] = {}
        self._report = SimulationReport()
        # Initial-state reagents declared via fill_all() etc. live on the
        # author-side Labware. We deep-copy them into the twin at place time.
        self._step_index = 0
        self._current_step = None
        self._strict = False

    def run(
        self,
        *,
        fail_on_opaque: bool = False,
        min_coverage: float | None = None,
        strict: bool = False,
    ) -> None:
        self._strict = strict
        self._wt.snapshots.clear()
        self._wt.simulation_report = self._report
        protocol = self._wt.to_protocol()
        try:
            if strict:
                self._preflight_strict()
            for group in protocol.groups:
                for step in group.steps:
                    self._dispatch(step)
            if self._report.opaque_events:
                self._warn(
                    f"{len(self._report.opaque_events)} step(s) were opaque to the simulator"
                )
            if min_coverage is not None and self._report.modeled_coverage < min_coverage:
                self._warn(
                    f"simulation coverage {self._report.modeled_coverage:.3f} is below "
                    f"minimum {min_coverage:.3f}"
                )
            if fail_on_opaque and self._report.opaque_events:
                raise SimulationError("simulation encountered opaque GenericStep/raw XML commands")
            if min_coverage is not None and self._report.modeled_coverage < min_coverage:
                raise SimulationError(
                    f"simulation coverage {self._report.modeled_coverage:.3f} is below "
                    f"minimum {min_coverage:.3f}"
                )
        except Exception as exc:
            self._record_failure(exc)
            self._finalize_report()
            self._wt.simulation_report = self._report
            raise
        self._finalize_report()
        self._wt.simulation_report = self._report

    # ── Step dispatch ───────────────────────────────────────────────

    def _dispatch(self, step) -> None:
        self._current_step = step
        effect = EffectKind.OPAQUE
        message = ""
        if isinstance(step, AddLabwareStep):
            self._on_add_labware(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, RemoveLabwareStep):
            self._on_remove_labware(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, GetHeadAdapterStep):
            self._on_get_adapter(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, DropHeadAdapterStep):
            self._on_drop_adapter(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, PickUpTipsStep):
            self._on_pickup_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, SetTipsBackStep):
            self._on_return_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, AspirateStep):
            self._on_aspirate(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, DispenseStep):
            self._on_dispense(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, Mca384MixStep):
            self._on_mix(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, Mca384EmptyTipsStep):
            self._on_empty_tips(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, Mca384GetTipsStep):
            self._on_mca384_get_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, Mca384DropTipsStep):
            self._on_mca384_drop_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, Mca384MoveArmStep):
            self._on_mca384_move_arm(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, LihaGetTipsStep):
            self._on_liha_get_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, LihaDropTipsStep):
            self._on_liha_drop_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, LihaAspirateStep):
            self._on_liha_aspirate(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, LihaDispenseStep):
            self._on_liha_dispense(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, LihaMixStep):
            self._on_liha_mix(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, LihaEmptyTipsStep):
            self._on_liha_empty_tips(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, SetVariableStep):
            self._on_set_variable(step)
            effect = EffectKind.VARIABLE_CHANGE
        elif isinstance(step, SetLocationStep):
            self._on_set_location(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, CgaGetFingersStep):
            effect = EffectKind.VALIDATION_ONLY
            pass  # gripper-finger-pickup is a no-op in the twin
        elif isinstance(step, CgaDropFingersStep):
            effect = EffectKind.VALIDATION_ONLY
            pass  # gripper-finger-drop is a no-op in the twin
        elif isinstance(step, RgaTransferLabwareStep):
            self._on_gripper_move(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, LoopStep):
            self._on_loop(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, ConditionalStep):
            self._on_conditional(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, ScriptGroupStep):
            for child in step.steps:
                self._dispatch(child)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, SubRoutineStep):
            # v1: subroutines are referenced by path; the twin steps in only
            # if the body is inlined as IR. With external .smt references,
            # we treat as opaque (no twin change beyond the snapshot).
            message = "external subroutine body is not available to the simulator"
        elif isinstance(step, (WaitStep, DelayStep, CommentStep, UserPromptStep,
                              StartTimerStep, WaitForTimerStep, ExportVariableStep)):
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, (ImportVariableStep, QueryVariableStep, ExecuteApplicationStep)):
            message = "runtime/user/external side effect is not modeled"
        elif isinstance(step, (WorklistImportStep, LoadWorklistStep, ExecuteWorklistStep)):
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, LegacyDriverMacroStep):
            # External device driver macro (e.g. ODTC SiLA-ODTC); no twin effect.
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, GenericStep):
            effect, message = self._on_generic_step(step)
        # All other step types (waits, comments, timers, variables, externals)
        # are opaque to the twin. They still get a snapshot below.

        self._snapshot(step, effect, message)
        self._current_step = None

    def _snapshot(self, step, effect: EffectKind, message: str = "") -> None:
        command_id = getattr(step, "step_type", type(step).__name__)
        command_id = getattr(command_id, "value", command_id)
        raw_xml = isinstance(step, GenericStep) and bool(step.parameters.get("raw_xml"))
        coverage = StepCoverage(
            step_index=self._step_index,
            step_type=type(step).__name__,
            command_id=str(command_id),
            effect=effect,
            raw_xml=raw_xml,
            message=message,
        )
        self._report.add_step(coverage)
        self._wt.snapshots.append(take_snapshot(
            step_index=self._step_index,
            step=step,
            slot_map=self._slot_map,
            mca_adapter_label=self._mca_adapter_label,
            mca_tips=self._mca_tips,
            mca_tip_box_label=self._mca_tip_box_label,
            liha_tips=self._liha_tips,
            opaque_events=self._report.opaque_events,
            warnings=self._report.warnings,
        ))
        self._step_index += 1

    def _finalize_report(self) -> None:
        self._report.final_labware = self._labware_summary()
        self._report.final_mca_tips = self._tip_summary(self._mca_tips)
        self._report.final_liha_tips = [
            None if tip is None else self._tip_summary([tip], start_index=i)[0]
            for i, tip in enumerate(self._liha_tips)
        ]
        self._report.state_summary = self._state_summary()

    def _record_failure(self, exc: Exception) -> None:
        if self._report.failure is not None:
            return
        step = self._current_step
        step_type = type(step).__name__ if step is not None else None
        command_id = None
        if step is not None:
            command_id = getattr(step, "step_type", type(step).__name__)
            command_id = getattr(command_id, "value", command_id)
            command_id = str(command_id)
        self._report.failure = SimulationFailure(
            category=self._classify_failure(exc),
            exception_type=type(exc).__name__,
            message=str(exc),
            step_index=self._step_index if step is not None else None,
            step_type=step_type,
            command_id=command_id,
            operation=_failure_operation(command_id),
            details=dict(getattr(exc, "sim_details", {}) or {}),
            repair_options=_repair_options(getattr(exc, "sim_category", None) or self._classify_failure(exc)),
        )

    def _classify_failure(self, exc: Exception) -> str:
        structured_category = getattr(exc, "sim_category", None)
        if structured_category:
            return str(structured_category)
        if isinstance(exc, InvalidSlotError):
            return "workspace_slot"
        if isinstance(exc, OccupiedSlotError):
            return "workspace_slot"
        if isinstance(exc, MissingSimValueError):
            return "runtime_variable"
        if isinstance(exc, MissingAdapterError):
            return "adapter_state"
        if isinstance(exc, MissingTipsError):
            return "tip_state"
        if isinstance(exc, (InsufficientVolumeError, OverdrawError, CannotAspirateError)):
            return "liquid_state"
        message = str(exc).lower()
        if "not bound to a specific fluentcontrol workspace" in message:
            return "workspace_binding"
        if "catalog-backed" in message or "catalog index" in message:
            return "catalog"
        if "opaque" in message:
            return "opaque_policy"
        if "coverage" in message:
            return "coverage_policy"
        return "simulation_state"

    def _preflight_strict(self) -> None:
        try:
            self._wt._require_bound_workspace()
        except ValueError as exc:
            raise SimulationError(str(exc)) from exc
        if self._wt.valid_slots is None:
            raise SimulationError(
                "Strict simulation requires workspace slot metadata to be loaded."
            )
        for stack in self._wt.slot_map.values():
            for labware in stack:
                if labware.slot is not None:
                    self._validate_workspace_slot(
                        labware.slot,
                        action=f"Labware {labware.label!r}",
                    )
                self._require_catalog_backed_labware(labware)

    def _validate_workspace_slot(self, slot: tuple[str, int], *, action: str) -> None:
        valid_slots = self._wt.valid_slots
        if valid_slots is None:
            return
        if slot in valid_slots:
            return
        raise InvalidSlotError(
            f"{action} references slot {slot!r}, which is not on workspace "
            f"{self._wt.workspace_name!r}."
        )

    def _require_catalog_backed_labware(self, labware: Labware) -> None:
        catalog_name = (labware.catalog_name or "").strip()
        if not catalog_name or catalog_name.startswith("<offline:"):
            raise SimulationError(
                f"Strict simulation requires installed catalog-backed labware semantics, "
                f"but {labware.label!r} is using unresolved catalog {catalog_name!r}."
            )
        if labware.category in {"plate", "trough", "tube_rack", "wash_station"} and not labware.wells:
            raise SimulationError(
                f"Strict simulation requires pipettable well geometry for {labware.label!r} "
                f"({catalog_name!r}), but no wells were resolved."
            )

    # ── Worktable / labware ─────────────────────────────────────────

    def _on_add_labware(self, step: AddLabwareStep) -> None:
        slot = (step.location, step.position)
        if self._strict:
            self._validate_workspace_slot(slot, action=f"AddLabware({step.label!r})")
        if slot in self._slot_map and self._slot_map[slot]:
            raise _with_sim_details(
                OccupiedSlotError(
                    f"Slot {slot} is already occupied by "
                    f"{self._slot_map[slot][-1].label!r}"
                ),
                category="slot_occupied",
                slot={"location": slot[0], "position": slot[1]},
                labware=step.label,
                occupied_by=self._slot_map[slot][-1].label,
            )
        # Find the original author-side labware (by label) and use it as the twin.
        # The author's `place()` already set its slot/stack_below; copies
        # propagate via deepcopy when snapshotted.
        try:
            original = self._wt.labware_by_label(step.label)
        except KeyError as exc:
            raise SimulationError(
                f"AddLabware({step.label!r}) has no resolved author-side labware instance."
            ) from exc
        # Reset twin-only state — re-apply position/stack from this step.
        original.slot = slot
        original.stack_below = []
        self._slot_map[slot] = [original]
        self._twin[step.label] = original

    def _on_remove_labware(self, step: RemoveLabwareStep) -> None:
        labware = self._twin.pop(step.labware_name, None)
        if self._strict and labware is None:
            raise SimulationError(
                f"RemoveLabware({step.labware_name!r}) references unknown labware."
            )
        if labware and labware.slot:
            stack = self._slot_map.get(labware.slot, [])
            if labware in stack:
                stack.remove(labware)
                if not stack:
                    del self._slot_map[labware.slot]
            labware.slot = None
            labware.stack_below = []

    def _on_gripper_move(self, step: RgaTransferLabwareStep) -> None:
        labware = self._twin.get(step.labware_name)
        if self._strict and (labware is None or labware.slot is None):
            raise SimulationError(
                f"RgaTransferLabware({step.labware_name!r}) references labware that is not present."
            )
        if labware is None or labware.slot is None:
            return
        dest = (step.destination_location, step.destination_site)
        if self._strict:
            self._validate_workspace_slot(dest, action=f"RgaTransferLabware({step.labware_name!r})")
        # Pop from current stack.
        stack = self._slot_map.get(labware.slot, [])
        if labware in stack:
            stack.remove(labware)
            if not stack:
                del self._slot_map[labware.slot]
        # Push onto destination stack.
        dest_stack = self._slot_map.setdefault(dest, [])
        labware.stack_below = list(dest_stack)
        dest_stack.append(labware)
        labware.slot = dest

    # ── Head state ──────────────────────────────────────────────────

    def _on_get_adapter(self, step: GetHeadAdapterStep) -> None:
        self._mca_adapter_label = step.labware_name

    def _on_drop_adapter(self, step: DropHeadAdapterStep) -> None:
        self._mca_adapter_label = None

    def _on_pickup_tips(self, step: PickUpTipsStep) -> None:
        if self._mca_adapter_label is None:
            raise MissingAdapterError(
                f"PickUpTips({step.labware_name!r}) but no adapter is mounted on the MCA-96 head"
            )
        tip_box = self._twin.get(step.labware_name)
        if not isinstance(tip_box, TipBox):
            raise MissingTipsError(
                f"PickUpTips: {step.labware_name!r} is not a tip box"
            )
        picked = self._partial_box_columns(step, tip_box)
        present = tip_box.columns_present
        missing = picked - present
        if missing:
            raise _with_sim_details(
                MissingTipsError(
                    f"PickUpTips: {step.labware_name!r} has no tips in "
                    f"column(s) {sorted(missing)} (already picked up)"
                ),
                category="tip_box_empty",
                tip_box=step.labware_name,
            )
        self._check_peel_edge(step, picked, present)
        capacity = tip_box.capacity_ul
        tip_box.remove_columns(picked)
        self._mca_tips = [Tip(capacity_ul=capacity) for _ in range(96)]
        self._mca_tip_box_label = step.labware_name

    def _on_return_tips(self, step: SetTipsBackStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError("ReturnTips called but no tips on the head")
        target = step.labware_name or self._mca_tip_box_label
        if target is not None:
            tip_box = self._twin.get(target)
            if isinstance(tip_box, TipBox):
                tip_box.add_columns(self._partial_box_columns(step, tip_box))
        self._mca_tips = []
        self._mca_tip_box_label = None

    @staticmethod
    def _check_peel_edge(step, picked: set[int], present: set[int]) -> None:
        """Enforce the physical peel rule for a partial pickup.

        A partial selection can lift either the **whole** filled box at once, or
        a **contiguous block flush to the current left-most or right-most filled
        column** — so the head's idle channels overhang empty space and don't
        knock neighbouring tips. Anything else (an interior or sparse subset that
        leaves tips on both sides) is physically impossible: the idle channels
        would land on tips you don't mean to pick.
        """
        if not picked or picked == present:
            return
        lo, hi = min(picked), max(picked)
        contiguous = picked == set(range(lo, hi + 1))
        flush = lo == min(present) or hi == max(present)
        if not (contiguous and flush):
            raise _with_sim_details(
                MissingTipsError(
                    f"PickUpTips: {step.labware_name!r} column(s) {sorted(picked)} "
                    f"are not a left/right edge of the filled columns "
                    f"{sorted(present)} — a single column can only be peeled from "
                    f"the current outermost filled column."
                ),
                category="partial_pickup_not_edge",
                tip_box=step.labware_name,
            )

    @staticmethod
    def _partial_box_columns(step, tip_box: "TipBox") -> set[int]:
        """1-based box columns a pickup/set-back addresses.

        ``step.columns`` *is* the addressed box columns (the well-selection); the
        ``PartialColumnOffset`` is derived from them at render time and does not
        independently change which columns are touched. ``columns=None`` is a
        full pickup.
        """
        total = getattr(tip_box, "total_columns", 12)
        cols = getattr(step, "columns", None)
        if cols:
            return {int(c) for c in cols if 1 <= int(c) <= total}
        return set(range(1, total + 1))

    def _on_mca384_get_tips(self, step: Mca384GetTipsStep) -> None:
        label = step.labware_name
        capacity = 50.0
        if label:
            tip_box = self._twin.get(label)
            if isinstance(tip_box, TipBox):
                if not tip_box.is_full:
                    raise _with_sim_details(
                        MissingTipsError(f"Mca384GetTips: {label!r} is empty"),
                        category="tip_box_empty",
                        tip_box=label,
                    )
                capacity = tip_box.capacity_ul
                tip_box.is_full = False
            elif tip_box is not None:
                self._warn(
                    f"Mca384GetTips({label!r}) is not a known TipBox; using {capacity:.0f} uL tips"
                )
        self._mca_tips = [Tip(capacity_ul=capacity) for _ in range(384)]
        self._mca_tip_box_label = label

    def _on_mca384_drop_tips(self, step: Mca384DropTipsStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError("Mca384DropTips called but no tips on the head")
        target = step.labware_name or self._mca_tip_box_label
        if target is not None:
            tip_box = self._twin.get(target)
            if isinstance(tip_box, TipBox):
                tip_box.is_full = True
        self._mca_tips = []
        self._mca_tip_box_label = None

    def _on_mca384_move_arm(self, step: Mca384MoveArmStep) -> None:
        if step.labware_name and step.labware_name not in self._twin:
            if self._strict:
                raise SimulationError(
                    f"Mca384MoveArm references unknown labware {step.labware_name!r}."
                )
            self._warn(f"Mca384MoveArm references unknown labware {step.labware_name!r}")

    # ── Pipetting ───────────────────────────────────────────────────

    def _on_aspirate(self, step: AspirateStep) -> None:
        if self._mca_adapter_label is None:
            raise MissingAdapterError(
                f"Aspirate({step.labware_name!r}) without an adapter mounted"
            )
        if not self._mca_tips:
            raise MissingTipsError(
                f"Aspirate({step.labware_name!r}) without tips picked up"
            )
        target = self._twin.get(step.labware_name)
        if target is None:
            raise InsufficientVolumeError(
                f"Aspirate target {step.labware_name!r} is not on the worktable"
            )
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        # Auto-parallel over wells: aspirate `volume` from each addressed well.
        wells = self._iter_aspirate_wells(target)
        if len(wells) == 1 and target.category == "trough":
            for tip in self._mca_tips:
                self._aspirate_one(target, wells[0], volume, tip)
            return
        wells = self._select_mca_columns(wells, getattr(step, "columns", None))
        for tip, well in zip(self._mca_tips, wells):
            self._aspirate_one(target, well, volume, tip)

    def _on_dispense(self, step: DispenseStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError(
                f"Dispense({step.labware_name!r}) without tips picked up"
            )
        target = self._twin.get(step.labware_name)
        if target is None:
            raise InsufficientVolumeError(
                f"Dispense target {step.labware_name!r} is not on the worktable"
            )
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        wells = self._iter_aspirate_wells(target)
        wells = self._select_mca_columns(wells, getattr(step, "columns", None))
        for tip, well in zip(self._mca_tips, wells):
            self._dispense_one(target, well, volume, tip)

    def _on_mix(self, step: Mca384MixStep) -> None:
        if self._mca_adapter_label is None:
            raise MissingAdapterError(
                f"Mix({step.labware_name!r}) without an adapter mounted"
            )
        if not self._mca_tips:
            raise MissingTipsError(
                f"Mix({step.labware_name!r}) without tips picked up"
            )
        if step.labware_name not in self._twin:
            raise InsufficientVolumeError(
                f"Mix target {step.labware_name!r} is not on the worktable"
            )
        target = self._twin[step.labware_name]
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        cycles = int(step.cycles) if not isinstance(step.cycles, str) else int(self._resolve_sim_number(step.cycles))
        if cycles <= 0:
            return
        for tip, well in zip(self._mca_tips, self._iter_aspirate_wells(target)):
            self._validate_mix_one(target, well, volume, tip)
            self._mix_equilibrate(target, well)

    def _on_empty_tips(self, step: Mca384EmptyTipsStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError(
                f"EmptyTips({step.labware_name!r}) without tips picked up"
            )
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        target = self._twin.get(step.labware_name)
        wells = self._iter_aspirate_wells(target) if target is not None else []
        for i, tip in enumerate(self._mca_tips):
            well = wells[i] if i < len(wells) else None
            self._empty_tip_one(tip, volume, well)

    def _on_liha_get_tips(self, step: LihaGetTipsStep) -> None:
        channels = self._liha_channels(step.tip_index)
        capacity = 1000.0
        if step.labware_name:
            tip_box = self._twin.get(step.labware_name)
            if isinstance(tip_box, TipBox):
                if not tip_box.is_full:
                    raise _with_sim_details(
                        MissingTipsError(f"LihaGetTips: {step.labware_name!r} is empty"),
                        category="tip_box_empty",
                        tip_box=step.labware_name,
                    )
                capacity = tip_box.capacity_ul
                tip_box.is_full = False
            elif tip_box is not None:
                capacity = self._infer_liha_tip_capacity(tip_box.catalog_name)
                self._warn(
                    f"LihaGetTips({step.labware_name!r}) is not a known TipBox; "
                    f"using {capacity:.0f} uL tips"
                )
        for ch in channels:
            self._liha_tips[ch] = Tip(capacity_ul=capacity)
        self._liha_tip_box_label = step.labware_name

    def _on_liha_drop_tips(self, step: LihaDropTipsStep) -> None:
        channels = [i for i, tip in enumerate(self._liha_tips) if tip is not None]
        if not channels:
            raise MissingTipsError("LihaDropTips called but no LiHa tips are mounted")
        target = step.labware_name or self._liha_tip_box_label
        if target is not None:
            tip_box = self._twin.get(target)
            if isinstance(tip_box, TipBox):
                tip_box.is_full = True
        for ch in channels:
            self._liha_tips[ch] = None
        self._liha_tip_box_label = None

    def _on_liha_aspirate(self, step: LihaAspirateStep) -> None:
        target = self._require_labware(step.labware_name, "LiHa aspirate")
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        wells = self._liha_wells(target, step.well_offset, step.selection)
        for ch, well in wells:
            tip = self._require_liha_tip(ch, "Aspirate")
            self._aspirate_one(target, well, volume, tip)

    def _on_liha_dispense(self, step: LihaDispenseStep) -> None:
        target = self._require_labware(step.labware_name, "LiHa dispense")
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        wells = self._liha_wells(target, step.well_offset, step.selection)
        for ch, well in wells:
            tip = self._require_liha_tip(ch, "Dispense")
            self._dispense_one(target, well, volume, tip)

    def _on_liha_mix(self, step: LihaMixStep) -> None:
        target = self._require_labware(step.labware_name, "LiHa mix")
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        cycles = int(step.cycles) if not isinstance(step.cycles, str) else int(self._resolve_sim_number(step.cycles))
        if cycles <= 0:
            return
        for ch, well in self._liha_wells(target, step.well_offset, step.selection):
            tip = self._require_liha_tip(ch, "Mix")
            self._validate_mix_one(target, well, volume, tip)
            self._mix_equilibrate(target, well)

    def _on_liha_empty_tips(self, step: LihaEmptyTipsStep) -> None:
        target = self._twin.get(step.labware_name)
        wells = self._liha_wells(target, None, None) if target is not None else []
        volume = float(step.volume) if not isinstance(step.volume, str) else self._resolve_sim_number(step.volume)
        channels = [i for i, tip in enumerate(self._liha_tips) if tip is not None]
        if not channels:
            raise MissingTipsError("LihaEmptyTips called but no LiHa tips are mounted")
        for ch in channels:
            well = next((w for c, w in wells if c == ch), None)
            self._empty_tip_one(self._liha_tips[ch], volume, well)

    def _on_set_variable(self, step: SetVariableStep) -> None:
        self._wt.protocol_variables[step.variable_name] = step.value
        self._wt.sim_values[step.variable_name] = step.value

    def _on_set_location(self, step: SetLocationStep) -> None:
        labware = self._twin.get(step.labware)
        if self._strict and labware is None:
            raise SimulationError(
                f"SetLocation({step.labware!r}) references unknown labware."
            )
        if labware is None:
            return
        dest = (step.location, step.site)
        if self._strict:
            self._validate_workspace_slot(dest, action=f"SetLocation({step.labware!r})")
            if dest in self._slot_map and self._slot_map[dest]:
                raise _with_sim_details(
                    OccupiedSlotError(
                        f"SetLocation({step.labware!r}) targets occupied slot {dest!r}."
                    ),
                    category="slot_occupied",
                    slot={"location": dest[0], "position": dest[1]},
                    labware=step.labware,
                    occupied_by=self._slot_map[dest][-1].label,
                )
        if labware.slot:
            stack = self._slot_map.get(labware.slot, [])
            if labware in stack:
                stack.remove(labware)
                if not stack:
                    del self._slot_map[labware.slot]
        dest_stack = self._slot_map.setdefault(dest, [])
        labware.stack_below = list(dest_stack)
        dest_stack.append(labware)
        labware.slot = dest

    def _on_generic_step(self, step: GenericStep) -> tuple[EffectKind, str]:
        raw_xml = step.parameters.get("raw_xml")
        command_id = step.step_type
        if raw_xml:
            adapted = self._adapt_raw_generic(step, raw_xml)
            if adapted is not None:
                return self._apply_adapted_generic(adapted)
        if command_id in {"Wait", "Delay", "StartTimer", "WaitForTimer", "Comment"}:
            return EffectKind.VALIDATION_ONLY, "known no-liquid-effect command"
        if command_id in {"SetVariable"}:
            name = step.parameters.get("variable_name") or step.parameters.get("VariableName")
            value = step.parameters.get("value") or step.parameters.get("Value")
            if name:
                self._wt.protocol_variables[str(name)] = value
                self._wt.sim_values[str(name)] = value
                return EffectKind.VARIABLE_CHANGE, "generic variable assignment modeled"
        if command_id in {"SetLocation"}:
            labware = step.parameters.get("labware") or step.parameters.get("LabwareName")
            location = step.parameters.get("location") or step.parameters.get("Location")
            site = step.parameters.get("site") or step.parameters.get("Site")
            if labware and location and site:
                self._on_set_location(SetLocationStep(labware=str(labware), location=str(location), site=int(site)))
                return EffectKind.LABWARE_MOVEMENT, "generic labware location modeled"
        return EffectKind.OPAQUE, "GenericStep/raw XML command is not modeled"

    def _apply_adapted_generic(self, adapted) -> tuple[EffectKind, str]:
        if isinstance(adapted, LihaGetTipsStep):
            self._on_liha_get_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML LiHa get tips modeled"
        if isinstance(adapted, LihaDropTipsStep):
            self._on_liha_drop_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML LiHa drop tips modeled"
        if isinstance(adapted, LihaAspirateStep):
            self._on_liha_aspirate(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML LiHa aspirate modeled"
        if isinstance(adapted, LihaDispenseStep):
            self._on_liha_dispense(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML LiHa dispense modeled"
        if isinstance(adapted, LihaMixStep):
            self._on_liha_mix(adapted)
            return EffectKind.VALIDATION_ONLY, "raw XML LiHa mix modeled"
        if isinstance(adapted, LihaEmptyTipsStep):
            self._on_liha_empty_tips(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML LiHa empty tips modeled"
        if isinstance(adapted, Mca384GetTipsStep):
            self._on_mca384_get_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML MCA384 get tips modeled"
        if isinstance(adapted, Mca384DropTipsStep):
            self._on_mca384_drop_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML MCA384 drop tips modeled"
        if isinstance(adapted, Mca384MoveArmStep):
            self._on_mca384_move_arm(adapted)
            return EffectKind.VALIDATION_ONLY, "raw XML MCA384 move modeled"
        if isinstance(adapted, Mca384MixStep):
            self._on_mix(adapted)
            return EffectKind.VALIDATION_ONLY, "raw XML MCA384 mix modeled"
        if isinstance(adapted, Mca384EmptyTipsStep):
            self._on_empty_tips(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML MCA384 empty tips modeled"
        return EffectKind.OPAQUE, "adapted raw XML command is not modeled"

    def _adapt_raw_generic(self, step: GenericStep, raw_xml: str):
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return None
        command_id = step.step_type
        text = self._xml_text
        labware = text(root, "LabwareName")
        volume = self._xml_volume(root)
        liquid_class = text(root, "LiquidClassName")
        well_offset_text = text(root, "WellOffset")
        well_offset = int(well_offset_text) if well_offset_text and well_offset_text.lstrip("-").isdigit() else None
        selection = text(root, "SerializedWellIndexes") or text(root, "SelectedWellsString")
        if command_id in {"LihaGetTips", "LihaPickUp"}:
            return LihaGetTipsStep(labware_name=labware)
        if command_id == "LihaDropTips":
            return LihaDropTipsStep(labware_name=labware)
        if command_id == "LihaAspirate" and labware and volume is not None:
            return LihaAspirateStep(
                labware_name=labware,
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=selection,
            )
        if command_id == "LihaDispense" and labware and volume is not None:
            return LihaDispenseStep(
                labware_name=labware,
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=selection,
            )
        if command_id == "LihaMix" and labware and volume is not None:
            cycles_text = text(root, "Cycles")
            cycles = int(cycles_text) if cycles_text and cycles_text.isdigit() else 10
            return LihaMixStep(
                labware_name=labware,
                volume=volume,
                cycles=cycles,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=selection,
            )
        if command_id == "LihaEmptyTips" and labware:
            return LihaEmptyTipsStep(
                labware_name=labware,
                volume=volume or 0,
                liquid_class=liquid_class,
            )
        if command_id == "Mca384GetTips":
            return Mca384GetTipsStep(labware_name=labware)
        if command_id == "Mca384DropTips":
            return Mca384DropTipsStep(labware_name=labware)
        if command_id == "Mca384MoveArm":
            movement_type = text(root, "MovementType") or "GlobalZTravel"
            return Mca384MoveArmStep(movement_type=movement_type, labware_name=labware)
        if command_id == "Mca384Mix" and labware and volume is not None:
            cycles_text = text(root, "Cycles")
            cycles = int(cycles_text) if cycles_text and cycles_text.isdigit() else 10
            return Mca384MixStep(
                labware_name=labware,
                volume=volume,
                cycles=cycles,
                liquid_class=liquid_class,
            )
        if command_id == "Mca384EmptyTips" and labware:
            return Mca384EmptyTipsStep(
                labware_name=labware,
                volume=volume or 0,
                liquid_class=liquid_class,
            )
        return None

    def _xml_text(self, root: ET.Element, tag: str) -> str | None:
        for elem in root.iter():
            if elem.tag.split("}")[-1] == tag and elem.text is not None:
                text = elem.text.strip()
                if text:
                    return text
        return None

    def _xml_volume(self, root: ET.Element) -> float | str | None:
        for tag in ("Volume", "Volumes"):
            holder = next((elem for elem in root.iter() if elem.tag.split("}")[-1] == tag), None)
            if holder is None:
                continue
            direct = (holder.text or "").strip()
            if direct:
                return self._parse_number_or_name(direct)
            for child in holder.iter():
                if child is holder:
                    continue
                child_text = (child.text or "").strip()
                if child_text:
                    return self._parse_number_or_name(child_text)
        return None

    def _parse_number_or_name(self, value: str) -> float | str:
        try:
            return float(value)
        except ValueError:
            return value

    def _aspirate_one(self, labware: Labware, well, volume_ul: float, tip: Tip) -> None:
        # A magnet never withholds liquid from a tip — it only immobilises the
        # magnetic beads (and analyte bound to them). So liquid always draws
        # top-down across ALL layers, magnetized or not. Bead retention vs.
        # entrainment is handled separately via the well's bead phase.
        is_mag = labware.is_magnetized
        remaining = volume_ul
        if remaining <= 0:
            return
        # Tally the request before drawing — counts intent, not delivery,
        # so the running total is meaningful even on failed aspirates.
        self._source_requested_ul[labware.label] = (
            self._source_requested_ul.get(labware.label, 0.0) + volume_ul
        )
        well_map = self._source_requested_by_well_ul.setdefault(labware.label, {})
        well_map[well.address] = well_map.get(well.address, 0.0) + volume_ul
        free_before = well.volume_ul
        # Walk liquid layers top-down — nothing is skipped for magnetization.
        i = len(well.layers) - 1
        while remaining > 0 and i >= 0:
            layer = well.layers[i]
            take = min(layer.volume_ul, remaining)
            layer.volume_ul -= take
            remaining -= take
            tip.layers.append(Layer(reagent=layer.reagent, volume_ul=take))
            if layer.volume_ul <= 1e-9:
                # Remove the now-empty layer; index unchanged for next iter.
                del well.layers[i]
            i -= 1
        # Bead phase: the magnet holds beads + bound analyte in the well; off
        # the magnet (beads suspended) a draw entrains them into the tip in
        # proportion to the free liquid removed. Modelled silently in state.
        bp = getattr(well, "bead_phase", None)
        if bp is not None and bp.present and not is_mag and free_before > 1e-9:
            drawn = free_before - well.volume_ul
            frac = max(0.0, min(1.0, drawn / free_before))
            if frac > 1e-9 and bp.bound:
                for bl in list(bp.bound):
                    moved = bl.volume_ul * frac
                    if moved <= 1e-9:
                        continue
                    tip.layers.append(Layer(reagent=bl.reagent, volume_ul=moved))
                    bl.volume_ul -= moved
                bp.bound = [b for b in bp.bound if b.volume_ul > 1e-9]
            if well.volume_ul <= 1e-9:
                # The last of the liquid left, carrying the beads with it.
                bp.present = False
                bp.bound = []
        if remaining > 1e-6:
            raise _with_sim_details(
                InsufficientVolumeError(
                    f"Aspirate: well {well.address!r} on {labware.label!r} short by "
                    f"{remaining:.2f} uL"
                ),
                category="source_volume_short",
                operation="Aspirate",
                labware=labware.label,
                well=well.address,
                requested_volume_ul=volume_ul,
                short_by_ul=remaining,
                current_volume_ul=well.volume_ul,
            )
        if tip.volume_ul > tip.capacity_ul + 1e-6:
            raise _with_sim_details(
                OverdrawError(
                    f"Aspirate: tip would hold {tip.volume_ul:.2f} uL but capacity is "
                    f"{tip.capacity_ul:.2f} uL"
                ),
                category="tip_capacity",
                operation="Aspirate",
                requested_volume_ul=volume_ul,
                current_volume_ul=tip.volume_ul,
                capacity_ul=tip.capacity_ul,
            )

    def _dispense_one(self, labware: Labware, well, volume_ul: float, tip: Tip) -> None:
        if volume_ul <= 0:
            return
        if tip.volume_ul + 1e-9 < volume_ul:
            raise OverdrawError(
                f"Dispense: tip holds {tip.volume_ul:.2f} µL but {volume_ul:.2f} µL requested"
            )
        if well.volume_ul + volume_ul > well.max_volume_ul + 1e-6:
            raise _with_sim_details(
                OverdrawError(
                    f"Dispense: well {well.address!r} would overflow "
                    f"({well.volume_ul + volume_ul:.2f} > {well.max_volume_ul:.2f} uL; "
                    f"current {well.volume_ul:.2f} uL, dispense {volume_ul:.2f} uL)"
                ),
                category="well_overflow",
                operation="Dispense",
                labware=labware.label,
                well=well.address,
                current_volume_ul=well.volume_ul,
                attempted_delta_ul=volume_ul,
                capacity_ul=well.max_volume_ul,
            )
        # Tip dispenses FIFO (bottom of tip's layer stack first).
        remaining = volume_ul
        deposited_bead_carrier = False
        while remaining > 0 and tip.layers:
            layer = tip.layers[0]
            take = min(layer.volume_ul, remaining)
            layer.volume_ul -= take
            remaining -= take
            well.add_layer(layer.reagent, take)
            if layer.reagent.carries_beads:
                deposited_bead_carrier = True
            if layer.volume_ul <= 1e-9:
                del tip.layers[0]
        # A bead-carrier reagent landing in a well establishes (or refreshes)
        # the well's bead phase. Suspended unless the plate is magnetized.
        if deposited_bead_carrier:
            from ..labware.base import BeadPhase

            if getattr(well, "bead_phase", None) is None:
                well.bead_phase = BeadPhase(
                    present=True, suspended=not labware.is_magnetized
                )
            else:
                well.bead_phase.present = True
                well.bead_phase.suspended = not labware.is_magnetized

    def _empty_tip_one(self, tip: Tip | None, volume_ul: float, well=None) -> None:
        if tip is None:
            return
        remaining = tip.volume_ul if volume_ul <= 0 else min(volume_ul, tip.volume_ul)
        while remaining > 0 and tip.layers:
            layer = tip.layers[0]
            take = min(layer.volume_ul, remaining)
            layer.volume_ul -= take
            remaining -= take
            if well is not None:
                well.add_layer(layer.reagent, take)
            if layer.volume_ul <= 1e-9:
                del tip.layers[0]

    def _validate_mix_one(self, labware: Labware, well, volume_ul: float, tip: Tip) -> None:
        if volume_ul <= 0:
            return
        # A magnet does not withhold liquid — all free liquid is mixable.
        available = sum(layer.volume_ul for layer in well.layers)
        if available + 1e-9 < volume_ul:
            raise InsufficientVolumeError(
                f"Mix: well {well.address!r} on {labware.label!r} holds "
                f"{available:.2f} uL available but {volume_ul:.2f} uL requested"
            )
        if tip.volume_ul + volume_ul > tip.capacity_ul + 1e-6:
            raise OverdrawError(
                f"Mix: tip would hold {tip.volume_ul + volume_ul:.2f} uL but "
                f"capacity is {tip.capacity_ul:.2f} uL"
            )

    def _mix_equilibrate(self, labware: Labware, well) -> None:
        """A mix is the only equilibrating event for the bead phase.

        Off the magnet (beads suspended): an ``analyte`` in the free liquid
        binds to the beads; conversely, if an ``eluent`` is present and the
        beads carry bound analyte, the analyte is released back into the free
        liquid. On the magnet the beads are pelleted — a mix cannot
        re-suspend them, so it only records that state. Bind/release tracks
        analyte *association* with the beads; the bulk liquid (sample buffer,
        bead suspension, eluent) stays liquid, so supernatant/eluate volume
        accounting is unaffected. Authors model the captured species as a
        small ``analyte`` reagent distinct from the bulk ``plain`` buffer.
        """
        bp = getattr(well, "bead_phase", None)
        if bp is None or not bp.present:
            return
        if labware.is_magnetized:
            bp.suspended = False
            return
        bp.suspended = True
        free_analyte = [layer for layer in well.layers if layer.reagent.is_analyte]
        if free_analyte:
            for layer in free_analyte:
                bp.bound.append(Layer(reagent=layer.reagent, volume_ul=layer.volume_ul))
                well.layers.remove(layer)
        elif bp.bound and any(layer.reagent.is_eluent for layer in well.layers):
            for b in list(bp.bound):
                well.add_layer(b.reagent, b.volume_ul)
            bp.bound = []

    def _iter_aspirate_wells(self, labware: Labware):
        if labware.wells:
            return list(labware.wells.values())
        return []

    @staticmethod
    def _select_mca_columns(wells: list, columns: Optional[list[int]]) -> list:
        """Restrict addressed wells to the requested 1-based plate columns.

        Wells are column-major (A1..H1, A2..H2, …); a column is the trailing
        number of the well address. ``columns`` of ``None`` addresses every
        well (full-plate, current behavior)."""
        if not columns:
            return wells
        wanted = {int(c) for c in columns}
        selected = []
        for well in wells:
            addr = getattr(well, "address", "") or ""
            digits = "".join(ch for ch in addr if ch.isdigit())
            if digits and int(digits) in wanted:
                selected.append(well)
        return selected

    def _liha_channels(self, tip_index: int | None = None) -> list[int]:
        if tip_index is None:
            return list(range(8))
        if tip_index < 0 or tip_index > 7:
            raise MissingTipsError(f"LiHa channel index {tip_index} is outside 0..7")
        return [tip_index]

    def _liha_wells(self, labware: Labware, well_offset, selection: str | None) -> list[tuple[int, object]]:
        wells = self._iter_aspirate_wells(labware)
        if not wells:
            raise InsufficientVolumeError(f"{labware.label!r} has no pipettable wells")
        selected_channels = [i for i, tip in enumerate(self._liha_tips) if tip is not None]
        if not selected_channels:
            raise MissingTipsError("LiHa pipetting requested without mounted tips")
        if len(wells) == 1 and labware.category == "trough" and selection is None and well_offset is None:
            return [(channel, wells[0]) for channel in selected_channels]
        indexes: list[int]
        if selection:
            indexes = self._parse_selection(selection, [well.address for well in wells])
        else:
            offset = 0
            if well_offset is not None:
                offset = int(well_offset) if not isinstance(well_offset, str) else int(self._resolve_sim_number(well_offset))
            indexes = [offset + i for i in range(len(selected_channels))]
        out: list[tuple[int, object]] = []
        for channel, index in zip(selected_channels, indexes):
            if index < 0 or index >= len(wells):
                raise InsufficientVolumeError(
                    f"LiHa well index {index} is outside {labware.label!r} well range"
                )
            out.append((channel, wells[index]))
        return out

    def _parse_selection(self, selection: str, well_addresses: list[str]) -> list[int]:
        address_to_index = {
            address.upper(): index for index, address in enumerate(well_addresses)
        }
        values: list[int] = []
        for token in selection.replace(",", ";").split(";"):
            token = token.strip()
            if not token:
                continue
            upper = token.upper()
            if upper in address_to_index:
                values.append(address_to_index[upper])
                continue
            if ">" in token:
                parts = [part.strip() for part in token.split(">") if part.strip()]
                if len(parts) == 3:
                    try:
                        start, stride, end = (int(part) for part in parts)
                    except ValueError:
                        pass
                    else:
                        if stride == 0:
                            raise MissingSimValueError(
                                f"LiHa serialized selection {selection!r} has stride 0"
                            )
                        stop = end + (1 if stride > 0 else -1)
                        values.extend(range(start, stop, stride))
                        continue
            try:
                values.append(int(token))
            except ValueError:
                if "-" in token:
                    left, right = (part.strip() for part in token.split("-", 1))
                    left_upper = left.upper()
                    right_upper = right.upper()
                    if left_upper in address_to_index and right_upper in address_to_index:
                        start = address_to_index[left_upper]
                        end = address_to_index[right_upper]
                        step = 1 if end >= start else -1
                        values.extend(range(start, end + step, step))
                        continue
                    values.extend(range(int(left), int(right) + 1))
        return values

    def _require_liha_tip(self, channel: int, action: str) -> Tip:
        tip = self._liha_tips[channel]
        if tip is None:
            raise MissingTipsError(f"{action}: LiHa channel {channel} has no tip")
        return tip

    def _require_labware(self, label: str, action: str) -> Labware:
        target = self._twin.get(label)
        if target is None:
            raise InsufficientVolumeError(f"{action} target {label!r} is not on the worktable")
        return target

    def _infer_liha_tip_capacity(self, catalog_name: str) -> float:
        lowered = (catalog_name or "").lower()
        if "50ul" in lowered or "50 ul" in lowered:
            return 50.0
        if "200ul" in lowered or "200 ul" in lowered:
            return 200.0
        return 1000.0

    # ── Loops / conditionals ────────────────────────────────────────

    def _on_loop(self, step: LoopStep) -> None:
        count = step.number_of_loops if step.number_of_loops is not None else step.iterations
        if isinstance(count, str):
            count = self._resolve_sim_number(count)
        previous = self._wt.sim_values.get(step.loop_variable) if step.loop_variable else None
        had_previous = bool(step.loop_variable and step.loop_variable in self._wt.sim_values)
        try:
            for index in range(1, int(count) + 1):
                if step.loop_variable:
                    self._wt.sim_values[step.loop_variable] = index
                for child in step.steps:
                    self._dispatch(child)
        finally:
            if step.loop_variable:
                if had_previous:
                    self._wt.sim_values[step.loop_variable] = previous
                else:
                    self._wt.sim_values.pop(step.loop_variable, None)

    def _on_conditional(self, step: ConditionalStep) -> None:
        left = self._resolve_sim_value(step.left_variable)
        right = (
            self._resolve_sim_value(step.right_value)
            if step.right_is_variable and isinstance(step.right_value, str)
            else step.right_value
        )
        op = step.operator
        # Coerce to common type for comparison.
        try:
            l, r = float(left), float(right)
        except (TypeError, ValueError):
            l, r = left, right
        truth_table = {
            "==": l == r, "!=": l != r,
            ">": l > r, "<": l < r, ">=": l >= r, "<=": l <= r,
        }
        truthy = truth_table.get(op, False)
        branch = step.then_steps if truthy else step.else_steps
        for child in branch:
            self._dispatch(child)

    # ── Reporting helpers ───────────────────────────────────────────

    def _warn(self, message: str) -> None:
        if message not in self._report.warnings:
            self._report.warnings.append(message)

    def _labware_summary(self) -> dict:
        out = {}
        for stack in self._slot_map.values():
            for labware in stack:
                wells = {}
                for address, well in labware.wells.items():
                    if well.volume_ul <= 1e-9 and not well.layers:
                        continue
                    wells[address] = {
                        "volume_ul": well.volume_ul,
                        "layers": [
                            {
                                "reagent": layer.reagent.name,
                                "volume_ul": layer.volume_ul,
                            }
                            for layer in well.layers
                        ],
                    }
                out[labware.label] = {
                    "catalog_name": labware.catalog_name,
                    "slot": list(labware.slot) if labware.slot else None,
                    "wells": wells,
                    "total_volume_ul": sum(
                        well.volume_ul for well in labware.wells.values()
                    ),
                }
        return out

    def _tip_summary(self, tips: list[Tip], *, start_index: int = 0) -> list[dict]:
        return [
            {
                "index": start_index + i,
                "capacity_ul": tip.capacity_ul,
                "volume_ul": tip.volume_ul,
                "layers": [
                    {
                        "reagent": layer.reagent.name,
                        "volume_ul": layer.volume_ul,
                    }
                    for layer in tip.layers
                ],
            }
            for i, tip in enumerate(tips)
        ]

    def _state_summary(self) -> dict:
        failure = self._report.failure.to_dict() if self._report.failure else None
        failure_details = (failure or {}).get("details") or {}
        failing_labware = failure_details.get("labware")
        failing_well = failure_details.get("well")
        return {
            "deck_occupancy": self._deck_occupancy_summary(),
            "tip_state": self._tip_state_summary(),
            "labware_volumes": self._labware_volume_summary(failing_labware, failing_well),
            "reagent_source_sufficiency": self._source_sufficiency_summary(failure),
        }

    def _deck_occupancy_summary(self) -> dict:
        occupied_slots = []
        for slot, stack in sorted(self._slot_map.items()):
            if not stack:
                continue
            occupied_slots.append(
                {
                    "location": slot[0],
                    "position": slot[1],
                    "stack": [labware.label for labware in stack],
                }
            )
        return {
            "occupied_slots": occupied_slots,
            "occupied_count": len(occupied_slots),
        }

    def _tip_state_summary(self) -> dict:
        tip_boxes = []
        for stack in self._slot_map.values():
            for labware in stack:
                if isinstance(labware, TipBox):
                    tip_boxes.append(
                        {
                            "label": labware.label,
                            "slot": list(labware.slot) if labware.slot else None,
                            "capacity_ul": labware.capacity_ul,
                            "is_full": bool(labware.is_full),
                            "consumed": not bool(labware.is_full),
                        }
                    )
        mounted_mca = self._tip_summary(self._mca_tips)
        mounted_liha = [
            None if tip is None else self._tip_summary([tip], start_index=i)[0]
            for i, tip in enumerate(self._liha_tips)
        ]
        return {
            "mca96": {
                "mounted_count": len(mounted_mca),
                "capacity_ul": _unique_values(tip["capacity_ul"] for tip in mounted_mca),
                "total_volume_ul": sum(float(tip["volume_ul"]) for tip in mounted_mca),
                "tip_box_label": self._mca_tip_box_label,
            },
            "liha": {
                "mounted_count": sum(1 for tip in mounted_liha if tip is not None),
                "capacity_ul": _unique_values(tip["capacity_ul"] for tip in mounted_liha if tip is not None),
                "total_volume_ul": sum(float(tip["volume_ul"]) for tip in mounted_liha if tip is not None),
                "tip_box_label": self._liha_tip_box_label,
            },
            "tip_boxes": sorted(tip_boxes, key=lambda item: item["label"]),
        }

    def _labware_volume_summary(self, failing_labware=None, failing_well=None) -> dict:
        labware_summaries = {}
        for stack in self._slot_map.values():
            for labware in stack:
                if isinstance(labware, TipBox):
                    continue
                volumes = [float(well.volume_ul) for well in labware.wells.values()]
                nonempty = [volume for volume in volumes if volume > 1e-9]
                failing_wells = {}
                if labware.label == failing_labware and failing_well in labware.wells:
                    well = labware.wells[failing_well]
                    failing_wells[failing_well] = {
                        "volume_ul": well.volume_ul,
                        "max_volume_ul": well.max_volume_ul,
                    }
                labware_summaries[labware.label] = {
                    "catalog_name": labware.catalog_name,
                    "slot": list(labware.slot) if labware.slot else None,
                    "well_count": len(volumes),
                    "nonempty_well_count": len(nonempty),
                    "total_volume_ul": sum(volumes),
                    "min_volume_ul": min(volumes) if volumes else 0.0,
                    "max_volume_ul": max(volumes) if volumes else 0.0,
                    "failing_wells": failing_wells,
                }
        return labware_summaries

    def _source_sufficiency_summary(self, failure: dict | None) -> dict:
        # Running tally of total requested volume across the sim, keyed by
        # source labware. Populated on every run regardless of failure state.
        sources: dict[str, dict] = {}
        for label, total in sorted(self._source_requested_ul.items()):
            twin = self._twin.get(label)
            remaining = (
                sum(float(well.volume_ul) for well in twin.wells.values())
                if twin is not None and twin.wells
                else None
            )
            wells_drawn_full = dict(self._source_requested_by_well_ul.get(label, {}))
            sample_wells_drawn = dict(list(sorted(wells_drawn_full.items()))[:12])
            max_per_well = max(wells_drawn_full.values()) if wells_drawn_full else 0.0
            sources[label] = {
                "total_requested_ul": total,
                "remaining_volume_ul": remaining,
                "well_count": len(wells_drawn_full),
                "max_per_well_requested_ul": max_per_well,
                "sample_wells_drawn": sample_wells_drawn,
                "wells_drawn": sample_wells_drawn,
                "wells_drawn_truncated": len(wells_drawn_full) > len(sample_wells_drawn),
            }
        summary: dict[str, Any] = {"sources": sources}
        # Backwards-compatible top-level fields populate only on the
        # specific source_volume_short failure case (existing tests rely
        # on this shape).
        if failure and failure.get("category") == "source_volume_short":
            details = failure.get("details") or {}
            label = details.get("labware")
            if label:
                summary.update(
                    {
                        "source_label": label,
                        "well": details.get("well"),
                        "requested_volume_ul": details.get("requested_volume_ul"),
                        "remaining_volume_ul": details.get("current_volume_ul"),
                        "short_by_ul": details.get("short_by_ul"),
                    }
                )
        return summary

    # ── Sim-time variable helpers ───────────────────────────────────

    def _resolve_sim_number(self, name: str) -> float:
        v = self._resolve_sim_value(name)
        try:
            return float(v)
        except (TypeError, ValueError) as exc:
            raise MissingSimValueError(
                f"Sim-time value for {name!r} is not numeric: {v!r}"
            ) from exc

    def _resolve_sim_value(self, name: str):
        if name in self._wt.sim_values:
            return self._wt.sim_values[name]
        if name in self._wt.protocol_variables:
            return self._wt.protocol_variables[name]
        if _looks_like_numeric_expr(name):
            return self._eval_numeric_expr(name)
        raise MissingSimValueError(
            f"No sim-time value for runtime variable {name!r}. "
            f"Call `wt.set_sim_value({name!r}, <value>)` before simulating."
        )

    def _eval_numeric_expr(self, expr: str) -> float:
        allowed_binary = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
        }
        allowed_unary = {
            ast.UAdd: operator.pos,
            ast.USub: operator.neg,
        }

        def visit(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.Name):
                return self._resolve_sim_number(node.id)
            if isinstance(node, ast.BinOp) and type(node.op) in allowed_binary:
                return allowed_binary[type(node.op)](visit(node.left), visit(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary:
                return allowed_unary[type(node.op)](visit(node.operand))
            raise MissingSimValueError(f"Unsupported sim-time numeric expression {expr!r}")

        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise MissingSimValueError(f"Invalid sim-time numeric expression {expr!r}") from exc
        return visit(tree)


def _with_sim_details(exc: Exception, *, category: str, **details):
    setattr(exc, "sim_category", category)
    setattr(exc, "sim_details", {key: value for key, value in details.items() if value is not None})
    return exc


def _looks_like_numeric_expr(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and any(op in value for op in "+-*/()%")
        and any(ch.isalpha() or ch.isdigit() for ch in value)
    )


def _failure_operation(command_id: str | None) -> str | None:
    if not command_id:
        return None
    lowered = command_id.lower()
    if "aspirate" in lowered:
        return "Aspirate"
    if "dispense" in lowered:
        return "Dispense"
    if "gettips" in lowered or "pickup" in lowered:
        return "GetTips"
    if "addlabware" in lowered:
        return "AddLabware"
    if "setlocation" in lowered:
        return "SetLocation"
    return command_id


def _repair_options(category: str) -> list[str]:
    options = {
        "tip_capacity": ["use_higher_capacity_tips", "split_operation_volume"],
        "tip_box_empty": ["use_fresh_tip_box", "keep_mounted_tips", "return_tips_before_reuse"],
        "source_volume_short": ["increase_source_initial_volume", "reduce_requested_transfer_volume"],
        "well_overflow": ["aspirate_before_dispensing_more", "split_cycles", "use_higher_capacity_labware"],
        "slot_occupied": ["choose_another_valid_slot", "stack_intentionally_with_gripper_move"],
    }
    return options.get(category, [])


def _unique_values(values) -> list:
    return sorted({value for value in values})
