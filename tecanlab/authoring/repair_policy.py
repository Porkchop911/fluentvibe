"""Generic, category-driven repair policy for authoring failures.

Replaces string-specific _repair_hint behaviour with a structured lookup that
consumes simulation failure categories first and falls back to message-text
matching only for uncategorised legacy failures.

Design invariant: **no protocol-domain vocabulary** (AMPure, SPRIselect, ethanol,
bead ratio, elution, etc.) appears in any repair text.  The policy is purely about
the *mechanics* of the tecanlab API and simulator constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Category → (repair options, human-readable guidance) ────────────

@dataclass(frozen=True)
class RepairPolicy:
    """Structured repair advice for a single failure category."""
    category: str
    options: tuple[str, ...] = ()
    guidance: str = ""


# Canonical mapping — the source of truth for all authoring repair paths.
_REPAIR_POLICIES: dict[str, RepairPolicy] = {
    "missing_method": RepairPolicy(
        category="missing_method",
        options=("call_lookup_api", "rewrite_using_supported_method"),
        guidance=(
            "Call lookup_api with the object name to see supported methods and examples. "
            "Rewrite using a method that exists on the tecanlab API."
        ),
    ),
    "tip_capacity": RepairPolicy(
        category="tip_capacity",
        options=("use_higher_capacity_tips", "split_operation_volume"),
        guidance=(
            "The mounted tips cannot hold the requested volume. "
            "Use a tip box with higher capacity (e.g. MCA200Box or MCA500Box instead of MCA100Box), "
            "or split the operation into smaller volumes."
        ),
    ),
    "tip_box_empty": RepairPolicy(
        category="tip_box_empty",
        options=("use_fresh_tip_box", "keep_mounted_tips", "return_tips_before_reuse"),
        guidance=(
            "The tip box has already been consumed (tips picked up once). "
            "Place a second fresh tip box at another valid slot, or return tips before re-picking them."
        ),
    ),
    "source_volume_short": RepairPolicy(
        category="source_volume_short",
        options=("increase_source_initial_volume", "reduce_requested_transfer_volume"),
        guidance=(
            "The source well does not contain enough liquid for the requested aspirate. "
            "Increase the initial fill volume on the source labware (account for dead volume), "
            "or reduce the per-well transfer volume."
        ),
    ),
    "well_overflow": RepairPolicy(
        category="well_overflow",
        options=("aspirate_before_dispensing_more", "split_cycles", "use_higher_capacity_labware"),
        guidance=(
            "Dispensing more liquid would exceed the well's maximum volume. "
            "Aspirate some liquid before dispensing, split into smaller cycles, "
            "or use labware with higher per-well capacity."
        ),
    ),
    "slot_occupied": RepairPolicy(
        category="slot_occupied",
        options=("choose_another_valid_slot", "stack_intentionally_with_gripper_move"),
        guidance=(
            "The target slot is already occupied by another labware. "
            "Choose a different valid position, or use gripper.move(..., onto=...) to stack intentionally."
        ),
    ),
    "workspace_slot": RepairPolicy(
        category="workspace_slot",
        options=("call_lookup_workspace_and_use_valid_positions",),
        guidance=(
            "The referenced slot is not on the configured workspace. "
            "Call lookup_workspace and list_valid_positions to find valid locations."
        ),
    ),
    "adapter_state": RepairPolicy(
        category="adapter_state",
        options=("mount_adapter_before_pipetting",),
        guidance=(
            "An MCA-96 pipetting step requires a mounted adapter. "
            "Call head.mount_adapter() before pick_up / aspirate."
        ),
    ),
    "tip_state": RepairPolicy(
        category="tip_state",
        options=("pick_up_tips_before_pipetting",),
        guidance=(
            "A pipetting step requires tips on the head. "
            "Call head.pick_up(tip_box) (MCA-96) or head.get_tips(tip_box) (LiHa) before aspirate/dispense."
        ),
    ),
    "liquid_state": RepairPolicy(
        category="liquid_state",
        options=("check_source_volume_and_tip_capacity",),
        guidance=(
            "A liquid-handling invariant was violated. Check that source wells have enough volume, "
            "tips can hold the requested amount, and destination wells will not overflow."
        ),
    ),
    "runtime_variable": RepairPolicy(
        category="runtime_variable",
        options=("set_sim_value_before_simulation",),
        guidance=(
            "A runtime variable used in a step has no sim-time value. "
            "Call wt.set_sim_value(name, value) before simulation."
        ),
    ),
    "intent_not_satisfied": RepairPolicy(
        category="intent_not_satisfied",
        options=("ensure_destination_wells_receive_declared_target_volume",),
        guidance=(
            "The declared intent (target volume per well) was not met by the simulated result. "
            "Check that destination wells actually receive the declared volume."
        ),
    ),
    "python_build_failure": RepairPolicy(
        category="python_build_failure",
        options=("fix_syntax_or_import", "call_lookup_api_for_unknown_symbol"),
        guidance=(
            "build_worktable() raised before simulation could run. "
            "Read the exception type and message: ImportError or NameError "
            "means a symbol is wrong (call lookup_api), SyntaxError means the "
            "draft is malformed."
        ),
    ),
    "workspace_binding": RepairPolicy(
        category="workspace_binding",
        options=("call_from_workspace_with_guid",),
        guidance=(
            "The Worktable is not bound to a FluentControl workspace. "
            "Use Worktable.from_workspace(name, workspace_guid=...) — never "
            "the bare Worktable() constructor for protocols intended to compile."
        ),
    ),
    "catalog": RepairPolicy(
        category="catalog",
        options=("call_search_labware_or_get_labware_for_exact_name",),
        guidance=(
            "A labware references a catalog entry that is not installed or not "
            "indexed. Use search_labware then get_labware to obtain the exact "
            "catalog name and pass it via catalog=..."
        ),
    ),
    "simulation_state": RepairPolicy(
        category="simulation_state",
        options=("review_failure_message_and_rewrite",),
        guidance=(
            "The simulator hit an internal invariant that does not map to a "
            "specific category. Read the failure message for the operation and "
            "labware involved, then rewrite that step."
        ),
    ),
    "opaque_policy": RepairPolicy(
        category="opaque_policy",
        options=("avoid_raw_xml_step_and_generic_step", "use_supported_public_api"),
        guidance=(
            "The simulator encountered raw XML or a GenericStep that has no "
            "modeled effect. Use the public tecanlab API (call lookup_api) "
            "instead of raw_xml_step or generic_step."
        ),
    ),
    "coverage_policy": RepairPolicy(
        category="coverage_policy",
        options=("use_modeled_steps", "avoid_unsupported_commands"),
        guidance=(
            "Simulation modeled-coverage fell below the required threshold "
            "because too many steps were opaque. Replace opaque/raw steps "
            "with public API calls so the simulator can model their effect."
        ),
    ),
}


# ── Public API ─────────────────────────────────────────────────────

def resolve_repair_policy(
    *,
    category: str | None = None,
    message: str | None = None,
) -> RepairPolicy:
    """Return a RepairPolicy for the given failure.

    Priority order:
    1. Exact match on *category* (structured simulation failure).
    2. Message-text fallback for uncategorised legacy failures.
    3. Generic catch-all.
    """
    if category and category in _REPAIR_POLICIES:
        return _REPAIR_POLICIES[category]

    # Fallback: try to infer from message text (legacy path).
    inferred = _infer_category_from_message(message or "")
    if inferred is not None:
        return _REPAIR_POLICIES[inferred]

    return RepairPolicy(
        category=category or "unknown",
        options=("review_failure_message_and_rewrite",),
        guidance=message or "Review the failure message and adjust the protocol.",
    )


def get_repair_options(category: str) -> list[str]:
    """Return repair option identifiers for a known category."""
    policy = _REPAIR_POLICIES.get(category)
    return list(policy.options) if policy else []


# ── Message-text fallback (legacy uncategorised failures only) ─────

def _infer_category_from_message(message: str) -> str | None:
    """Best-effort category inference from raw error text.

    Used *only* when no structured category is available (e.g. Python build
    errors that are not AttributeError, or legacy exception strings).
    """
    lowered = message.lower()
    if "object has no attribute" in lowered:
        return "missing_method"
    if "capacity" in lowered and ("tip would hold" in lowered or "overdraw" in lowered):
        return "tip_capacity"
    if "short by" in lowered or "insufficient volume" in lowered:
        return "source_volume_short"
    if "overflow" in lowered:
        return "well_overflow"
    if "occupied" in lowered and ("slot" in lowered or "position" in lowered):
        return "slot_occupied"
    if "valid slot" in lowered or "workspace" in lowered:
        return "workspace_slot"
    if "adapter" in lowered and ("mount" in lowered or "no adapter" in lowered):
        return "adapter_state"
    if "tip" in lowered and ("empty" in lowered or "consumed" in lowered or "already picked up" in lowered):
        return "tip_box_empty"
    return None


# ── Domain-vocabulary guard (tested) ───────────────────────────────

_DOMAIN_FORBIDDEN_TOKENS = frozenset(
    token.lower()
    for token in [
        "ampure", "spriselect", "ethanol", "bead ratio", "elution",
        "dna cleanup", "rna cleanup", "wash step", "resuspension",
        "magnetic bead", "binding buffer", "peb", "wbs", "wes",
    ]
)


def assert_no_domain_vocabulary() -> None:
    """Raise AssertionError if any repair policy text contains protocol-domain terms.

    This is the enforcement mechanism for the Chunk 4 invariant that repair
    guidance stays generic and mechanical, never SOP-specific.
    """
    violations: list[str] = []
    for category, policy in _REPAIR_POLICIES.items():
        combined_text = f"{policy.options} {policy.guidance}".lower()
        for token in _DOMAIN_FORBIDDEN_TOKENS:
            if token in combined_text:
                violations.append(f"category={category!r}: found domain term {token!r}")
    if violations:
        raise AssertionError(
            "Repair policy contains forbidden domain vocabulary:\n  " + "\n  ".join(violations)
        )
