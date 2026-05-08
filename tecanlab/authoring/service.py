"""End-to-end LM Studio prompt authoring orchestration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .lm_client import (
    DEFAULT_LM_STUDIO_ENDPOINT,
    DEFAULT_LM_STUDIO_MODEL,
    LMStudioChatClient,
    LMStudioError,
)
from .models import AuthoringResult, AuthoringStatus, ClarificationQuestion, FailureCategory
from .repair_lock import RepairLockState
from .tools import AuthoringToolRegistry, tool_definitions
from .validator import AuthoringValidator


SYSTEM_PROMPT = """You are authoring executable Python protocols for tecanlab.

Work cooperatively before drafting executable Python. First resolve
clarifications. Then ground the workspace, labware, liquid classes, API shapes,
and applicable rules. Next call present_object_draft to show the user the
planned worktable objects and the exact worktable/workspace you are using. Wait
for approval or changes. After the object draft is approved, call
present_functional_group_plan to show the ordered implementation groups. Wait
for approval or changes again. Only after both approvals should you draft
Python.

After approvals, work through protocol authoring in staged functional groups.
The first two workflow groups are mandatory and fixed: Variables, then Labware
Placement. Variables are top-level wt.declare_variable(...) and
wt.set_sim_value(...) calls before any wt.group(...). Labware placement starts
with wt.group("Labware Placement"). After that, extend the same draft one
functional group at a time, call simulate_python_draft after each group, repair
the current group if needed, and only call compile_and_simulate after all groups
have passed draft simulation.

INTENT FIRST. Before grounding or drafting code, confirm you know the values for
every required axis: target per-well volume, source labware, destination labware,
well coverage (which wells receive volume), and liquid class. If any are
unspecified or ambiguous in the user's prompt, call ask_user with one focused
question per missing axis BEFORE generating Python. Do not guess. After the user
responds, call declare_intent with the resolved values; the post-simulation
check verifies that destination wells actually received the declared volume.

You must ground facts through tools before writing final code:
- resolve the workspace and valid positions
- call lookup_api when using an unfamiliar tecanlab object or after any
  missing-attribute Python build failure
- search/get exact installed labware names
- resolve exact liquid-class names
- call lookup_rules for protocol-family patterns and learned best practices
- call plan_protocol_resources before drafting multi-step protocols or after
  source_volume_short, well_overflow, or tip_capacity simulator failures
- call suggest_deck_layout when placing multiple items on the deck, or after
  a slot_occupied failure to find distinct valid slots for each resource
- call simulate_python_draft during generation
- call compile_and_simulate before final answer
- call validate_fluentcontrol_shell when the caller asks for FluentControl/vendor validation
- call present_object_draft and wait for approval before functional-group planning
- call present_functional_group_plan and wait for approval before drafting Python;
  its first groups must be Variables and Labware Placement
Native tool use is mandatory. If you answer with Python before grounding,
the orchestrator will reject that draft and ask you to call the missing tools.

The final protocol must be Python source only. It must define:
    def build_worktable() -> Worktable:
and must use Worktable.from_workspace(..., workspace_guid=...). Use exact catalog=
names returned by tools. Do not use raw_xml_step or generic_step.

Use the tecanlab public API. Typical imports are:
    from tecanlab import Worktable, Reagent, Plate96, MCA100Box, MCA200Box, MCA500Box

For a simple 96-well MCA transfer, seed the source plate with enough reagent,
mount the adapter, pick up MCA96 tips, aspirate from source, dispense to
destination, return tips, and drop the adapter.

Use this API shape, replacing exact names only with tool-grounded values:

from tecanlab import Worktable, Reagent, Plate96, MCA100Box

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "WORKSPACE_NAME_FROM_TOOLS",
        workspace_guid="WORKSPACE_GUID_FROM_TOOLS",
        auto_place=False,
        protocol_name="Authored Transfer",
        comment="20 uL 96-well transfer",
    )
    wt.declare_variable("RunId", "authored_run")
    wt.set_sim_value("RunId", "authored_run")
    wt.declare_variable("LIQUID_CLASS_TRANSFER", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_TRANSFER", "Water Free Single")
    LIQUID_CLASS_TRANSFER = "Water Free Single"
    sample = Reagent("Sample")
    wt.group("Labware Placement")
    source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    source.fill_all(sample, 80.0)
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(source, 20.0, liquid_class=LIQUID_CLASS_TRANSFER)
    head.dispense(dest, 20.0, liquid_class=LIQUID_CLASS_TRANSFER)
    head.return_tips(tips)
    head.drop_adapter()
    return wt

If the request cannot be safely authored, return a concise refusal or
clarification request instead of code.

When FluentControl shell validation is requested or available as the final
vendor gate, use validate_fluentcontrol_shell only after compile_and_simulate
has passed. Treat zero load failures and zero InfoPad error lines as vendor
acceptance.

For site-specific decks, use only workspace names, GUIDs, labware catalogs,
liquid classes, and positions returned by grounding tools. Do not assume a
canonical local deck unless the tools confirm it in the current environment.

When a user says liquid classes should be variables, declare one variable per
liquid-class role with default and simulator value "Water Free Single", include
those variable names in present_object_draft liquid_classes, and pass the
variables to aspirate/dispense instead of hardcoded liquid-class string
literals.

For trough/reservoir-to-plate fill requests, MCA-96 cannot fan one trough
well across 96 destination wells in a single aspirate. Call lookup_rules
with protocol_type="transfer" or category="trough_to_plate" before drafting
to retrieve the supported pattern.

For large waste-producing workflows, plan resources generically before drafting.
Do not use a shallow 96-well plate as waste if predicted waste exceeds capacity;
prefer a waste chute, Waste resource, or high-capacity trough-like waste sink.

"""


# ── Domain-vocabulary guard for SYSTEM_PROMPT (Chunk 6) ────────────────
# Protocol-domain terms must NOT appear in the global system prompt.
# They belong only in retrievable rules/fixtures accessed via lookup_rules().

_SYSTEM_PROMPT_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    token.lower()
    for token in [
        "ampure", "spriselect", "ethanol", "bead ratio", "elution",
        "dna cleanup", "rna cleanup", "wash step", "resuspension",
        "magnetic bead", "binding buffer", "peb", "wbs", "wes",
    ]
)


def assert_no_domain_vocabulary_in_prompt() -> None:
    """Raise AssertionError if SYSTEM_PROMPT contains protocol-domain terms.

    This enforces the Chunk 6 invariant: SOP-specific sequence guidance must
    live in retrievable rules (via lookup_rules), not in the global system prompt.
    The prompt may contain generic API mechanics and workspace defaults but never
    mentions of specific reagent brands or protocol families.
    """
    violations: list[str] = []
    for token in _SYSTEM_PROMPT_FORBIDDEN_TOKENS:
        if token in SYSTEM_PROMPT.lower():
            violations.append(f"found domain term {token!r} in SYSTEM_PROMPT")
    if violations:
        raise AssertionError(
            "SYSTEM_PROMPT contains forbidden domain vocabulary:\n  " + "\n  ".join(violations)
        )


# Self-check at import time — catches regressions early.
assert_no_domain_vocabulary_in_prompt()


class PromptAuthoringService:
    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT,
        model: str = DEFAULT_LM_STUDIO_MODEL,
        client: LMStudioChatClient | None = None,
    ) -> None:
        self._client = client or LMStudioChatClient(endpoint=endpoint, model=model)
        self._validator = AuthoringValidator()

    def author(
        self,
        prompt: str,
        *,
        output_dir: Path,
        retry_budget: int = 2,
        workspace_name: str | None = None,
        workspace_guid: str | None = None,
    ) -> AuthoringResult:
        # Delegated to the LangGraph state machine. The graph encodes the same
        # control flow that previously lived inline here: model_call →
        # dispatch_tools (with repair_lock + grounding gates) → extract_python
        # → validate_locally → terminal {success, clarify, fail}.
        from .graph import run_graph

        registry = AuthoringToolRegistry(
            output_dir=output_dir,
            workspace_name=workspace_name,
            workspace_guid=workspace_guid,
            current_prompt=prompt,
        )
        try:
            return run_graph(
                prompt=prompt,
                output_dir=output_dir,
                retry_budget=retry_budget,
                registry=registry,
                client=self._client,
                system_prompt=SYSTEM_PROMPT,
            )
        except LMStudioError as exc:
            return self._failure(
                prompt,
                FailureCategory.MODEL_AUTHORING_FAILURE,
                str(exc),
                tools=registry,
                best_code=None,
                validation=None,
                attempts=0,
            )

    def _assistant_message(self, message: dict[str, Any]) -> dict[str, Any]:
        out = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        return out

    def _extract_python(self, content: str) -> str | None:
        fenced = re.search(r"```(?:python)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else content.strip()
        if "def build_worktable()" in candidate or "def build_worktable() -> Worktable:" in candidate:
            return candidate
        return None

    def _report_from_tool_result(self, result: dict[str, Any]):
        # The tool result already mirrors ValidationReport.to_dict(); callers only
        # need the concrete report object for existing API compatibility.
        from .models import ValidationReport

        category = result.get("failure_category")
        return ValidationReport(
            success=bool(result.get("success")),
            python_build_ok=bool(result.get("python_build_ok")),
            compile_ok=bool(result.get("compile_ok")),
            strict_simulation_ok=bool(result.get("strict_simulation_ok")),
            failure_category=FailureCategory(category) if category else None,
            failure_message=result.get("failure_message"),
            python_path=Path(result["python_path"]) if result.get("python_path") else None,
            xscr_path=Path(result["xscr_path"]) if result.get("xscr_path") else None,
            simulation_failure_category=result.get("simulation_failure_category"),
            simulation_failure_details=result.get("simulation_failure_details"),
            repair_options=tuple(result.get("repair_options") or ()),
            repair_hint=result.get("repair_hint"),
            attempt_index=int(result.get("attempt_index") or 1),
            state_summary=result.get("state_summary"),
        )

    def _failure(
        self,
        prompt: str,
        category: FailureCategory,
        message: str,
        *,
        tools: AuthoringToolRegistry,
        best_code: str | None,
        validation,
        attempts: int,
    ) -> AuthoringResult:
        return AuthoringResult(
            status=AuthoringStatus.FAILURE,
            prompt=prompt,
            spec=None,
            generated_code=None,
            validation=validation,
            compiled_xscr=validation.xscr_path if validation is not None else None,
            failure_category=category,
            failure_message=message,
            best_draft_code=best_code,
            attempts=attempts,
            tool_calls=tuple(tools.calls),
        )


def _clarification_question(question: str) -> ClarificationQuestion:
    return ClarificationQuestion(
        key="model_clarification_request",
        question=question or "The model needs more information.",
        why_it_matters="The model paused authoring to resolve an unspecified intent axis.",
        )


def missing_authoring_grounding(calls: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[str]:
    names = {str(call.get("name") or "") for call in calls}
    missing: list[str] = []
    if not names.intersection({"lookup_workspace", "list_valid_positions"}):
        missing.append("lookup_workspace or list_valid_positions")
    if not names.intersection({"search_labware", "get_labware"}):
        missing.append("search_labware or get_labware")
    if "lookup_liquid_class" not in names:
        missing.append("lookup_liquid_class")
    if "lookup_rules" not in names:
        missing.append("lookup_rules")
    if "simulate_python_draft" not in names:
        missing.append("simulate_python_draft")
    return missing


def _missing_grounding_message(missing: list[str]) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "You returned Python before completing mandatory tool grounding. "
            "Do not provide final Python yet. Call these missing tools first: "
            + ", ".join(missing)
            + ". After simulator feedback, call compile_and_simulate."
        ),
    }


def _draft_pressure_message(calls: tuple[dict[str, Any], ...] | list[dict[str, Any]], *, iteration: int) -> dict[str, str] | None:
    names = [str(call.get("name") or "") for call in calls]
    if "present_object_draft" not in names:
        if len(names) >= 4:
            return {
                "role": "user",
                "content": (
                    "Before functional-group planning or Python drafting, stop and call "
                    "present_object_draft. Include the worktable/workspace, planned "
                    "variables, reagents, liquid classes, labware labels/classes/catalogs, "
                    "roles, and deck locations for user approval."
                ),
            }
        return None
    if "present_functional_group_plan" not in names:
        return {
            "role": "user",
            "content": (
                "After the object draft is approved, stop and call "
                "present_functional_group_plan. The first two groups must be exactly "
                "Variables and Labware Placement."
            ),
        }
    if "simulate_python_draft" in names or "compile_and_simulate" in names:
        return None
    if len(names) < 6:
        return None
    has_workspace = any(name in {"lookup_workspace", "list_valid_positions"} for name in names)
    has_labware = any(name in {"search_labware", "get_labware"} for name in names)
    has_liquid = "lookup_liquid_class" in names
    has_rules = "lookup_rules" in names
    if not (has_workspace and has_labware and has_liquid and has_rules):
        return None
    return {
        "role": "user",
        "content": (
            "You have enough grounding to make a concrete checkpoint. Stop calling "
            "additional search/lookup tools unless a simulator result identifies a "
            "specific missing exact name. Draft the current functional-group checkpoint "
            "now and call simulate_python_draft. Use structured simulator feedback for "
            "any next repair."
        ),
    }


def _missing_intent_axes(text: str) -> list[str]:
    lowered = text.lower()
    missing: list[str] = []
    if not re.search(r"\b\d+(?:\.\d+)?\s*(?:u|µ|μ|milli)?l\b", lowered):
        missing.append("target per-well volume")
    if not any(token in lowered for token in ("liquid class", "water free", "water wet", "water mix")):
        missing.append("liquid class")
    return missing


def _intent_axis_message(missing_axes: list[str]) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "Before grounding or drafting, these intent axes are still missing or ambiguous: "
            + ", ".join(missing_axes)
            + ". Call ask_user with one focused question that resolves them."
        ),
    }


def author_protocol(
    prompt: str,
    *,
    output_dir: Path,
    retry_budget: int = 2,
    workspace_name: str | None = None,
    workspace_guid: str | None = None,
) -> AuthoringResult:
    return PromptAuthoringService().author(
        prompt,
        output_dir=output_dir,
        retry_budget=retry_budget,
        workspace_name=workspace_name,
        workspace_guid=workspace_guid,
    )
