"""Structured models for prompt-to-protocol authoring."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class AuthoringStatus(str, Enum):
    SUCCESS = "success"
    CLARIFICATION_REQUIRED = "clarification_required"
    APPROVAL_REQUIRED = "approval_required"
    FAILURE = "failure"


class FailureCategory(str, Enum):
    MISSING_PROMPT_INFORMATION = "missing_prompt_information"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    WORKSPACE_SLOT_INVALIDITY = "workspace_slot_invalidity"
    MISSING_CATALOG_ITEM = "missing_catalog_item"
    LIQUID_CLASS_RESOLUTION_FAILURE = "liquid_class_resolution_failure"
    PYTHON_BUILD_FAILURE = "python_build_failure"
    COMPILE_FAILURE = "compile_failure"
    STRICT_SIMULATION_FAILURE = "strict_simulation_failure"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"
    MODEL_AUTHORING_FAILURE = "model_authoring_failure"
    FLUENTCONTROL_SHELL_FAILURE = "fluentcontrol_shell_failure"


@dataclass(frozen=True)
class ClarificationQuestion:
    key: str
    question: str
    why_it_matters: str


@dataclass(frozen=True)
class ApprovalRequest:
    kind: str
    title: str
    summary: str
    payload: dict[str, Any]
    question: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "payload": self.payload,
            "question": self.question,
        }


@dataclass(frozen=True)
class IntentSpec:
    """Resolved authoring intent. Optional fields stay None until the LM
    declares them via the `declare_intent` tool. When set, downstream
    validation cross-checks them against the simulator's `final_labware`.
    """

    target_volume_ul: float | None = None
    source_label: str | None = None
    destination_label: str | None = None
    destination_wells: tuple[str, ...] | None = None
    liquid_class: str | None = None

    def is_specified(self) -> bool:
        return any(
            value is not None
            for value in (
                self.target_volume_ul,
                self.source_label,
                self.destination_label,
                self.destination_wells,
                self.liquid_class,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_volume_ul": self.target_volume_ul,
            "source_label": self.source_label,
            "destination_label": self.destination_label,
            "destination_wells": list(self.destination_wells) if self.destination_wells else None,
            "liquid_class": self.liquid_class,
        }


@dataclass(frozen=True)
class WorkspaceBinding:
    name: str
    guid: str


@dataclass(frozen=True)
class SampleBinding:
    role: str
    labware_label: str
    reagent_name: str
    initial_volume_ul: float


@dataclass(frozen=True)
class LabwareBinding:
    label: str
    role: str
    family: str
    python_class: str
    catalog_name: str
    location: str
    position: int


@dataclass(frozen=True)
class LayerSeed:
    reagent_name: str
    volume_ul: float
    pinned_when_magnetized: bool = False


@dataclass(frozen=True)
class SeededLabware:
    labware_label: str
    mode: str
    layers: tuple[LayerSeed, ...]


@dataclass(frozen=True)
class VariableBinding:
    name: str
    default: int | float | str | bool
    sim_value: int | float | str | bool


@dataclass(frozen=True)
class FunctionalGroupPlan:
    name: str
    objective: str = ""
    expected_steps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "objective": self.objective,
            "expected_steps": list(self.expected_steps),
        }


@dataclass(frozen=True)
class ProtocolWorkflowPlan:
    protocol_name: str
    summary: str
    labware: tuple[dict[str, Any], ...]
    variables: tuple[VariableBinding, ...]
    groups: tuple[FunctionalGroupPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_name": self.protocol_name,
            "summary": self.summary,
            "labware": [dict(item) for item in self.labware],
            "variables": [
                {
                    "name": variable.name,
                    "default": variable.default,
                    "sim_value": variable.sim_value,
                }
                for variable in self.variables
            ],
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass(frozen=True)
class HeadAction:
    action: str
    head: str
    labware_label: str | None = None


@dataclass(frozen=True)
class LiquidAction:
    action: str
    head: str
    labware_label: str
    volume_ul: float
    liquid_class: str
    cycles: int | None = None


@dataclass(frozen=True)
class TransferAction:
    head: str
    source_label: str
    destination_label: str
    volume_ul: float
    liquid_class: str


@dataclass(frozen=True)
class MoveLabwareAction:
    labware_label: str
    location: str | None = None
    position: int | None = None
    onto_label: str | None = None


@dataclass(frozen=True)
class ConditionalStep:
    name: str
    left_variable: str
    operator: str
    right_value: int | float | str | bool
    steps: tuple["ProtocolStep", ...]


@dataclass(frozen=True)
class LoopStep:
    name: str
    iterations: int | str
    steps: tuple["ProtocolStep", ...]


ProtocolStep = HeadAction | LiquidAction | TransferAction | MoveLabwareAction | ConditionalStep | LoopStep


@dataclass(frozen=True)
class ProtocolGroup:
    name: str
    steps: tuple[ProtocolStep, ...]


@dataclass(frozen=True)
class ProtocolSpec:
    prompt: str
    protocol_name: str
    intent: str
    workflow_kind: str
    workspace: WorkspaceBinding
    samples: tuple[SampleBinding, ...]
    labware: tuple[LabwareBinding, ...]
    seeded_labware: tuple[SeededLabware, ...]
    variables: tuple[VariableBinding, ...]
    groups: tuple[ProtocolGroup, ...]
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]
    open_questions: tuple[ClarificationQuestion, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    success: bool
    python_build_ok: bool
    compile_ok: bool
    strict_simulation_ok: bool
    failure_category: FailureCategory | None = None
    failure_message: str | None = None
    python_path: Path | None = None
    xscr_path: Path | None = None
    simulation_failure_category: str | None = None
    simulation_failure_details: dict[str, Any] | None = None
    repair_options: tuple[str, ...] = ()
    repair_hint: str | None = None
    attempt_index: int = 1
    state_summary: dict[str, Any] | None = None
    final_labware: dict[str, Any] | None = None
    intent_check_ok: bool | None = None
    document_adherence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "python_build_ok": self.python_build_ok,
            "compile_ok": self.compile_ok,
            "strict_simulation_ok": self.strict_simulation_ok,
            "failure_category": self.failure_category.value if self.failure_category else None,
            "failure_message": self.failure_message,
            "python_path": str(self.python_path) if self.python_path else None,
            "xscr_path": str(self.xscr_path) if self.xscr_path else None,
            "simulation_failure_category": self.simulation_failure_category,
            "simulation_failure_details": self.simulation_failure_details,
            "repair_options": list(self.repair_options),
            "repair_hint": self.repair_hint,
            "attempt_index": self.attempt_index,
            "state_summary": self.state_summary,
            "final_labware": self.final_labware,
            "intent_check_ok": self.intent_check_ok,
            "document_adherence": self.document_adherence,
        }


@dataclass(frozen=True)
class AuthoringResult:
    status: AuthoringStatus
    prompt: str
    spec: ProtocolSpec | None
    generated_code: str | None
    validation: ValidationReport | None
    compiled_xscr: Path | None
    failure_category: FailureCategory | None = None
    failure_message: str | None = None
    clarification_questions: tuple[ClarificationQuestion, ...] = ()
    approval_request: ApprovalRequest | None = None
    best_draft_code: str | None = None
    attempts: int = 0
    tool_calls: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "prompt": self.prompt,
            "spec": None if self.spec is None else {
                "prompt": self.spec.prompt,
                "protocol_name": self.spec.protocol_name,
                "intent": self.spec.intent,
                "workflow_kind": self.spec.workflow_kind,
                "workspace": {
                    "name": self.spec.workspace.name,
                    "guid": self.spec.workspace.guid,
                },
                "samples": [sample.__dict__ for sample in self.spec.samples],
                "labware": [labware.__dict__ for labware in self.spec.labware],
                "constraints": list(self.spec.constraints),
                "assumptions": list(self.spec.assumptions),
                "open_questions": [question.__dict__ for question in self.spec.open_questions],
            },
            "generated_code": self.generated_code,
            "validation": None if self.validation is None else self.validation.to_dict(),
            "compiled_xscr": str(self.compiled_xscr) if self.compiled_xscr else None,
            "failure_category": self.failure_category.value if self.failure_category else None,
            "failure_message": self.failure_message,
            "clarification_questions": [question.__dict__ for question in self.clarification_questions],
            "approval_request": None if self.approval_request is None else self.approval_request.to_dict(),
            "best_draft_code": self.best_draft_code,
            "attempts": self.attempts,
            "tool_calls": list(self.tool_calls),
        }
