"""Tests for the generic, category-driven repair policy (Chunk 4)."""

from __future__ import annotations

from fluentvibe.authoring.repair_policy import (
    _REPAIR_POLICIES,
    assert_no_domain_vocabulary,
    get_repair_options,
    resolve_repair_policy,
)

# ── Category-driven lookup ────────────────────────────────────────

class TestCategoryDrivenRepairPolicy:
    """Structured categories are consumed first."""

    def test_missing_method_returns_lookup_api_guidance(self):
        policy = resolve_repair_policy(category="missing_method")
        assert policy.category == "missing_method"
        assert "call_lookup_api" in policy.options
        assert "lookup_api" in policy.guidance

    def test_tip_capacity_returns_upgrade_or_split(self):
        policy = resolve_repair_policy(category="tip_capacity")
        assert "use_higher_capacity_tips" in policy.options
        assert "split_operation_volume" in policy.options
        assert "capacity" in policy.guidance.lower()

    def test_tip_box_empty_returns_fresh_box_guidance(self):
        policy = resolve_repair_policy(category="tip_box_empty")
        assert "use_fresh_tip_box" in policy.options
        assert "keep_mounted_tips" in policy.options
        assert "return_tips_before_reuse" in policy.options

    def test_source_volume_short_returns_increase_volume(self):
        policy = resolve_repair_policy(category="source_volume_short")
        assert "increase_source_initial_volume" in policy.options
        assert "reduce_requested_transfer_volume" in policy.options

    def test_well_overflow_returns_aspirate_or_split(self):
        policy = resolve_repair_policy(category="well_overflow")
        assert "aspirate_before_dispensing_more" in policy.options
        assert "split_cycles" in policy.options
        assert "use_higher_capacity_labware" in policy.options

    def test_slot_occupied_returns_choose_another_or_stack(self):
        policy = resolve_repair_policy(category="slot_occupied")
        assert "choose_another_valid_slot" in policy.options
        assert "stack_intentionally_with_gripper_move" in policy.options

    def test_workspace_slot_returns_lookup_workspace(self):
        policy = resolve_repair_policy(category="workspace_slot")
        assert "call_lookup_workspace_and_use_valid_positions" in policy.options

    def test_adapter_state_returns_mount_guidance(self):
        policy = resolve_repair_policy(category="adapter_state")
        assert "mount_adapter_before_pipetting" in policy.options

    def test_tip_state_returns_pick_up_guidance(self):
        policy = resolve_repair_policy(category="tip_state")
        assert "pick_up_tips_before_pipetting" in policy.options

    def test_liquid_state_returns_check_guidance(self):
        policy = resolve_repair_policy(category="liquid_state")
        assert "check_source_volume_and_tip_capacity" in policy.options

    def test_runtime_variable_returns_set_sim_value(self):
        policy = resolve_repair_policy(category="runtime_variable")
        assert "set_sim_value_before_simulation" in policy.options

    def test_intent_not_satisfied_returns_ensure_volume(self):
        policy = resolve_repair_policy(category="intent_not_satisfied")
        assert "ensure_destination_wells_receive_declared_target_volume" in policy.options

    def test_python_build_failure_returns_lookup_or_syntax_guidance(self):
        policy = resolve_repair_policy(category="python_build_failure")
        assert "fix_syntax_or_import" in policy.options
        assert "call_lookup_api_for_unknown_symbol" in policy.options

    def test_workspace_binding_returns_from_workspace_guidance(self):
        policy = resolve_repair_policy(category="workspace_binding")
        assert "call_from_workspace_with_guid" in policy.options

    def test_catalog_returns_search_or_get_labware(self):
        policy = resolve_repair_policy(category="catalog")
        assert "call_search_labware_or_get_labware_for_exact_name" in policy.options

    def test_simulation_state_returns_review_message(self):
        policy = resolve_repair_policy(category="simulation_state")
        assert "review_failure_message_and_rewrite" in policy.options

    def test_opaque_policy_returns_avoid_raw_xml(self):
        policy = resolve_repair_policy(category="opaque_policy")
        assert "avoid_raw_xml_step_and_generic_step" in policy.options

    def test_coverage_policy_returns_use_modeled_steps(self):
        policy = resolve_repair_policy(category="coverage_policy")
        assert "use_modeled_steps" in policy.options

    def test_options_are_real_tuples_not_strings(self):
        """Single-element option tuples must use trailing-comma syntax;
        otherwise list(policy.options) explodes into single characters."""
        for category, policy in _REPAIR_POLICIES.items():
            assert isinstance(policy.options, tuple), (
                f"category={category!r}: options must be a tuple, got {type(policy.options).__name__}"
            )
            for option in policy.options:
                assert isinstance(option, str) and len(option) > 1, (
                    f"category={category!r}: option {option!r} looks like a stray character "
                    f"(missing trailing comma in single-element tuple?)"
                )


# ── Message-text fallback (legacy uncategorised) ───────────────────

class TestMessageTextFallback:
    """When no structured category is available, message text is used."""

    def test_attribute_error_infers_missing_method(self):
        policy = resolve_repair_policy(
            message="'MCA96Head' object has no attribute 'pick_up_tips'"
        )
        assert policy.category == "missing_method"
        assert "call_lookup_api" in policy.options

    def test_capacity_message_infers_tip_capacity(self):
        policy = resolve_repair_policy(
            message="tip would hold 200.0 uL but capacity is 100.0 uL"
        )
        assert policy.category == "tip_capacity"

    def test_short_by_message_infers_source_volume_short(self):
        policy = resolve_repair_policy(
            message="well 'A1' on SourcePlate short by 5.00 uL"
        )
        assert policy.category == "source_volume_short"

    def test_overflow_message_infers_well_overflow(self):
        policy = resolve_repair_policy(
            message="well A1 would overflow (400 > 350)"
        )
        assert policy.category == "well_overflow"

    def test_occupied_slot_message_infers_slot_occupied(self):
        policy = resolve_repair_policy(
            message="Slot ('Nest61mm_Pos', 2) is already occupied by 'Plate'"
        )
        assert policy.category == "slot_occupied"

    def test_unknown_message_returns_generic_fallback(self):
        policy = resolve_repair_policy(message="something weird happened")
        assert policy.category == "unknown"
        assert policy.options == ("review_failure_message_and_rewrite",)

    def test_none_category_none_message_returns_empty_fallback(self):
        policy = resolve_repair_policy()
        assert policy.category == "unknown"


# ── Category takes priority over message text ──────────────────────

class TestCategoryPriority:
    """Structured category always wins over message-text inference."""

    def test_category_wins_over_conflicting_message(self):
        # Even if the message looks like tip_capacity, the explicit category wins.
        policy = resolve_repair_policy(
            category="slot_occupied",
            message="tip would hold 200 uL but capacity is 100 uL",
        )
        assert policy.category == "slot_occupied"


# ── Domain-vocabulary guard ───────────────────────────────────────

class TestNoDomainVocabulary:
    """Repair policies must never mention protocol-domain terms."""

    def test_no_ampure_in_any_policy(self):
        for cat, pol in _REPAIR_POLICIES.items():
            combined = f"{pol.options} {pol.guidance}".lower()
            assert "ampure" not in combined, (
                f"category={cat!r}: repair policy mentions 'ampure'"
            )

    def test_no_spriselect_in_any_policy(self):
        for cat, pol in _REPAIR_POLICIES.items():
            combined = f"{pol.options} {pol.guidance}".lower()
            assert "spriselect" not in combined

    def test_no_ethanol_in_any_policy(self):
        for cat, pol in _REPAIR_POLICIES.items():
            combined = f"{pol.options} {pol.guidance}".lower()
            assert "ethanol" not in combined

    def test_no_bead_ratio_in_any_policy(self):
        for cat, pol in _REPAIR_POLICIES.items():
            combined = f"{pol.options} {pol.guidance}".lower()
            assert "bead ratio" not in combined

    def test_no_elution_in_any_policy(self):
        for cat, pol in _REPAIR_POLICIES.items():
            combined = f"{pol.options} {pol.guidance}".lower()
            assert "elution" not in combined

    def test_assert_no_domain_vocabulary_passes(self):
        """The guard function itself should pass on the current policies."""
        assert_no_domain_vocabulary()  # raises AssertionError if violated


# ── get_repair_options helper ──────────────────────────────────────

class TestGetRepairOptions:
    def test_known_category_returns_list(self):
        options = get_repair_options("tip_capacity")
        assert isinstance(options, list)
        assert "use_higher_capacity_tips" in options

    def test_unknown_category_returns_empty(self):
        options = get_repair_options("nonexistent_category_xyz")
        assert options == []


# ── Integration: tools.py uses category-driven policy ──────────────

class TestToolsIntegration:
    """Verify that simulate_python_draft outputs use the new repair policy."""

    def test_simulate_draft_uses_policy_guidance(self):
        from pathlib import Path

        from fluentvibe.authoring.tools import AuthoringToolRegistry

        draft = '''"""Tip capacity failure fixture."""
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="TipCap Test",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    water = Reagent("Water")
    wt.group("Labware Placement")
    source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    source.fill_all(water, 300.0)
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(source, 200.0, liquid_class="Water Free Single")
    head.dispense(dest, 200.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    return wt
'''
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_repair_policy" / "integration",
        )
        result = tools.simulate_python_draft(draft, strict=True)
        assert result["ok"] is False
        assert result["category"] == "tip_capacity"
        # repair_hint should come from the policy, not string matching.
        assert result.get("repair_hint") is not None
        assert "higher capacity" in result["repair_hint"].lower() or "split" in result["repair_hint"].lower()

    def test_simulate_draft_source_short_uses_policy(self):
        from pathlib import Path

        from fluentvibe.authoring.tools import AuthoringToolRegistry

        draft = '''"""Source volume short fixture."""
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="ShortVol Test",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    water = Reagent("Water")
    wt.group("Labware Placement")
    source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    source.fill_all(water, 5.0)
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(source, 20.0, liquid_class="Water Free Single")
    head.dispense(dest, 20.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    return wt
'''
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_repair_policy" / "integration_short",
        )
        result = tools.simulate_python_draft(draft, strict=True)
        assert result["ok"] is False
        assert result["category"] == "source_volume_short"
        assert result.get("repair_hint") is not None
        hint_lower = result["repair_hint"].lower()
        assert any(token in hint_lower for token in ("increase", "initial fill", "reduce"))
