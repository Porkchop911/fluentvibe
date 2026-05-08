"""Tests for Chunk 6 — Rule Retrieval As Domain Context.

Verify that protocol-domain vocabulary (AMPure, SPRIselect, etc.) is absent from
the global SYSTEM_PROMPT and lives only in retrievable rules/fixtures accessed via
lookup_rules().
"""

from __future__ import annotations

import pytest
from pathlib import Path

from tecanlab.authoring.service import (
    SYSTEM_PROMPT,
    _SYSTEM_PROMPT_FORBIDDEN_TOKENS,
    assert_no_domain_vocabulary_in_prompt,
)


# ── Forbidden-token list sanity ────────────────────────────────────────

class TestForbiddenTokenList:
    """The forbidden token set itself must contain the expected terms."""

    def test_contains_ampure(self):
        assert "ampure" in _SYSTEM_PROMPT_FORBIDDEN_TOKENS

    def test_contains_spriselect(self):
        assert "spriselect" in _SYSTEM_PROMPT_FORBIDDEN_TOKENS

    def test_contains_ethanol(self):
        assert "ethanol" in _SYSTEM_PROMPT_FORBIDDEN_TOKENS

    def test_contains_bead_ratio(self):
        assert "bead ratio" in _SYSTEM_PROMPT_FORBIDDEN_TOKENS

    def test_contains_elution(self):
        assert "elution" in _SYSTEM_PROMPT_FORBIDDEN_TOKENS


# ── SYSTEM_PROMPT must be free of domain vocabulary ────────────────────

class TestSystemPromptNoDomainVocabulary:
    """The global system prompt must not contain protocol-domain terms."""

    def test_no_ampure_in_system_prompt(self):
        assert "ampure" not in SYSTEM_PROMPT.lower(), (
            "SYSTEM_PROMPT contains 'AMPure' — move to retrievable rules via lookup_rules()"
        )

    def test_no_spriselect_in_system_prompt(self):
        assert "spriselect" not in SYSTEM_PROMPT.lower(), (
            "SYSTEM_PROMPT contains 'SPRIselect' — move to retrievable rules via lookup_rules()"
        )

    def test_no_ethanol_in_system_prompt(self):
        assert "ethanol" not in SYSTEM_PROMPT.lower(), (
            "SYSTEM_PROMPT contains 'ethanol' — move to retrievable rules via lookup_rules()"
        )

    def test_no_bead_ratio_in_system_prompt(self):
        assert "bead ratio" not in SYSTEM_PROMPT.lower(), (
            "SYSTEM_PROMPT contains 'bead ratio' — move to retrievable rules via lookup_rules()"
        )

    def test_no_elution_in_system_prompt(self):
        assert "elution" not in SYSTEM_PROMPT.lower(), (
            "SYSTEM_PROMPT contains 'elution' — move to retrievable rules via lookup_rules()"
        )

    def test_no_dna_cleanup_in_system_prompt(self):
        assert "dna cleanup" not in SYSTEM_PROMPT.lower()

    def test_no_rna_cleanup_in_system_prompt(self):
        assert "rna cleanup" not in SYSTEM_PROMPT.lower()

    def test_no_wash_step_in_system_prompt(self):
        assert "wash step" not in SYSTEM_PROMPT.lower()

    def test_no_magnetic_bead_in_system_prompt(self):
        assert "magnetic bead" not in SYSTEM_PROMPT.lower()

    def test_assert_guard_passes(self):
        """The guard function itself should pass on the current prompt."""
        assert_no_domain_vocabulary_in_prompt()  # raises AssertionError if violated


# ── SYSTEM_PROMPT still contains required mechanical guidance ───────────

class TestSystemPromptDoesNotHardcodeProtocolPatterns:
    """Pattern-level guidance must live in retrievable rules, not the prompt."""

    def test_no_well_offset_recipe_in_prompt(self):
        # The trough fan-out recipe specifically used `well_offset=column_index*8`.
        # If that string reappears in SYSTEM_PROMPT it means we re-baked a
        # protocol-specific recipe back into the global instructions.
        assert "well_offset=column_index" not in SYSTEM_PROMPT.lower()

    def test_no_full_trough_fan_out_recipe_in_prompt(self):
        # The chunk-6-spirit guard: SYSTEM_PROMPT may *mention* the trough
        # limitation and tell the model to call lookup_rules, but it must not
        # hardcode the full recipe (specific positions, volumes, loop body).
        prompt = SYSTEM_PROMPT.lower()
        # Each token below was part of the prior hardcoded recipe; their
        # co-occurrence is the regression to catch.
        recipe_tokens = ["fca, 1000ul sbs", "5000.0", "ws_100ml_1 position 1"]
        present = [token for token in recipe_tokens if token in prompt]
        assert not present, (
            f"SYSTEM_PROMPT still bakes in protocol-specific recipe tokens: {present}"
        )


class TestTroughPatternRetrievableViaLookupRules:
    """The recipe removed from SYSTEM_PROMPT must be discoverable via lookup_rules."""

    def test_lookup_rules_transfer_returns_trough_to_plate_pattern(self):
        from pathlib import Path
        from tecanlab.authoring.tools import AuthoringToolRegistry

        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_domain_context" / "trough_pattern",
        )
        result = tools.lookup_rules(protocol_type="transfer")
        assert result["ok"] is True
        names = {entry["name"] for entry in result["patterns"]}
        assert "trough_to_96_well_liha_column_fan_out" in names

    def test_trough_pattern_includes_anti_patterns(self):
        from pathlib import Path
        from tecanlab.authoring.tools import AuthoringToolRegistry

        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_domain_context" / "trough_anti",
        )
        result = tools.lookup_rules(protocol_type="transfer")
        pattern = next(
            entry for entry in result["patterns"]
            if entry["name"] == "trough_to_96_well_liha_column_fan_out"
        )
        # The anti-patterns block warns against the hallucinated APIs the
        # model used to invent (subscripting labware, MCA column kwarg, etc.).
        anti = " ".join(pattern["anti_patterns"]).lower()
        assert "fill_all" in anti
        assert "subscripting" in anti or "['a1']" in anti


class TestSystemPromptRetainsMechanicalGuidance:
    """Removing domain terms must not accidentally strip API mechanics."""

    def test_mentions_lookup_api(self):
        assert "lookup_api" in SYSTEM_PROMPT

    def test_mentions_simulate_python_draft(self):
        assert "simulate_python_draft" in SYSTEM_PROMPT

    def test_mentions_compile_and_simulate(self):
        assert "compile_and_simulate" in SYSTEM_PROMPT

    def test_mentions_lookup_rules(self):
        """lookup_rules must be advertised so the model knows to call it."""
        assert "lookup_rules" in SYSTEM_PROMPT

    def test_mentions_suggest_deck_layout(self):
        assert "suggest_deck_layout" in SYSTEM_PROMPT

    def test_mentions_build_worktable(self):
        assert "build_worktable" in SYSTEM_PROMPT


# ── lookup_rules tool is properly defined ──────────────────────────────

class TestLookupRulesToolDefinition:
    """The lookup_rules tool must be available with correct parameters."""

    def test_lookup_rules_in_tool_definitions(self):
        from tecanlab.authoring.tools import tool_definitions

        tools = {t["function"]["name"]: t for t in tool_definitions()}
        assert "lookup_rules" in tools, (
            "lookup_rules is not registered in tool definitions"
        )

    def test_lookup_rules_has_protocol_type_param(self):
        from tecanlab.authoring.tools import tool_definitions

        tools = {t["function"]["name"]: t for t in tool_definitions()}
        props = tools["lookup_rules"]["function"]["parameters"]["properties"]
        assert "protocol_type" in props, (
            "lookup_rules must accept protocol_type parameter"
        )

    def test_lookup_rules_has_category_param(self):
        from tecanlab.authoring.tools import tool_definitions

        tools = {t["function"]["name"]: t for t in tool_definitions()}
        props = tools["lookup_rules"]["function"]["parameters"]["properties"]
        assert "category" in props, (
            "lookup_rules must accept category parameter"
        )


# ── lookup_rules returns structured data from catalog DB ───────────────

class TestLookupRulesReturnsStructuredData:
    """The tool dispatches to the catalog database and returns rules/modules/patterns."""

    def test_lookup_rules_returns_ok_with_keys(self):
        from pathlib import Path
        from tecanlab.authoring.tools import AuthoringToolRegistry

        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_domain_context" / "rules",
        )
        result = tools.lookup_rules(protocol_type="transfer")
        assert result["ok"] is True
        assert set(result).issuperset({"rules", "modules", "patterns"})

    def test_lookup_rules_with_category(self):
        from pathlib import Path
        from tecanlab.authoring.tools import AuthoringToolRegistry

        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_domain_context" / "rules_cat",
        )
        result = tools.lookup_rules(category="liquid_handling")
        assert result["ok"] is True
        assert isinstance(result["rules"], list)

    def test_lookup_rules_with_both_params(self):
        from pathlib import Path
        from tecanlab.authoring.tools import AuthoringToolRegistry

        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_domain_context" / "rules_both",
        )
        result = tools.lookup_rules(protocol_type="ampure", category="magnet")
        assert result["ok"] is True
        # Even if no rules match, the structure should be valid.
        assert isinstance(result["rules"], list)


# ── Domain terms ARE allowed in example fixtures (not the prompt) ───────

class TestDomainTermsAllowedInFixtures:
    """Protocol-domain vocabulary is fine in examples/ and tests/ — just not SYSTEM_PROMPT."""

    def test_example_ampure_cleanup_exists(self):
        from pathlib import Path
        ampure = Path("examples") / "ampure_cleanup.py"
        assert ampure.exists(), "AMPure example fixture should exist for round-trip testing"
        content = ampure.read_text(encoding="utf-8")
        # The example is allowed to contain domain terms — it's a fixture, not the prompt.
        assert "AMPure" in content or "ampure" in content.lower()

    def test_example_ampure_not_in_system_prompt(self):
        """Domain terms from examples must NOT leak into SYSTEM_PROMPT."""
        ampure = Path("examples") / "ampure_cleanup.py"
        if not ampure.exists():
            pytest.skip("AMPure example fixture not available")
        content = ampure.read_text(encoding="utf-8")
        # Verify the example contains domain terms (sanity check)
        assert "AMPure" in content or "ampure" in content.lower()
        # But SYSTEM_PROMPT must NOT
        for token in _SYSTEM_PROMPT_FORBIDDEN_TOKENS:
            assert token not in SYSTEM_PROMPT.lower(), (
                f"Domain term {token!r} from examples leaked into SYSTEM_PROMPT"
            )
