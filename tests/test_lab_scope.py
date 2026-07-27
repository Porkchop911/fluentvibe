"""Narrowed-scope experiment — loader, mode gating, and baseline parity.

``skills`` is the default generation mode (no env/arg). Explicit mode ``off``
MUST be byte-identical to the 7fa6e88 baseline: the loader returns an inert
scope that allows everything and injects nothing. ``cheatsheet`` loads the
markdown; ``enforce`` additionally restricts the curated whitelist. No live
LM here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.authoring.lab_scope import (  # noqa: E402
    LabScope,
    load_lab_scope,
    resolve_lab_scope_mode,
)

_ENV = "FLUENTVIBE_LAB_SCOPE"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)


# ── mode resolution ────────────────────────────────────────────────────

def test_default_mode_is_skills():
    assert resolve_lab_scope_mode() == "skills"
    assert resolve_lab_scope_mode(None) == "skills"


def test_cli_arg_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv(_ENV, "enforce")
    assert resolve_lab_scope_mode("cheatsheet") == "cheatsheet"
    assert resolve_lab_scope_mode() == "enforce"


@pytest.mark.parametrize("bad", ["", "ON", "yes", "1", "garbage"])
def test_unrecognized_value_normalizes_to_default(bad):
    # Unrecognized ⇒ the default mode (skills), never a silent disable.
    assert resolve_lab_scope_mode(bad) == "skills"


@pytest.mark.parametrize("raw", ["off", "Off ", " OFF", "OfF"])
def test_explicit_off_is_honored_case_insensitively(raw):
    assert resolve_lab_scope_mode(raw) == "off"


# ── mode=off parity guard ──────────────────────────────────────────────

def test_off_is_inert_and_allows_everything():
    scope = load_lab_scope("off")
    assert scope is load_lab_scope("off")  # explicit off returns the shared inert singleton
    assert scope.mode == "off"
    assert scope.is_active is False
    assert scope.enforces is False
    assert scope.cheatsheet_text is None
    assert not scope.labware and not scope.liquid_classes
    # Whitelist checks are pure pass-through when not enforcing.
    assert scope.allows_labware("anything at all") is True
    assert scope.allows_labware(None) is True
    assert scope.allows_liquid_class("Some Class") is True


def test_env_unset_yields_skills(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    # Default generation mode is skills (config + skill files ship in the package).
    assert load_lab_scope().mode == "skills"


# ── cheatsheet mode (Lever A) ──────────────────────────────────────────

def test_cheatsheet_mode_loads_markdown_and_whitelist():
    scope = load_lab_scope("cheatsheet")
    assert scope.mode == "cheatsheet"
    assert scope.is_active is True
    assert scope.enforces is False  # cheatsheet does NOT restrict tools
    assert scope.cheatsheet_text and "Lab Scope" in scope.cheatsheet_text
    assert "96_ABgene_SuperPlate_Thermo_AB2800" in scope.labware
    assert "Water Free Single" in scope.liquid_classes
    # Not enforcing ⇒ still allows off-list names.
    assert scope.allows_labware("Some Other Plate") is True


# ── enforce mode (Lever B) ─────────────────────────────────────────────

def test_enforce_mode_restricts_to_whitelist():
    scope = load_lab_scope("enforce")
    assert scope.mode == "enforce"
    assert scope.is_active is True
    assert scope.enforces is True
    # On-list passes, off-list rejected, None rejected.
    assert scope.allows_labware("96_ABgene_SuperPlate_Thermo_AB2800") is True
    assert scope.allows_labware("384 Well LowVol LoBase") is True
    assert scope.allows_labware("Totally Random Plate XYZ") is False
    assert scope.allows_labware(None) is False
    assert scope.allows_liquid_class("Water Free Single") is True
    assert scope.allows_liquid_class("Made Up Class") is False


def test_dataclass_is_frozen():
    scope = LabScope()
    with pytest.raises(Exception):
        scope.mode = "enforce"  # type: ignore[misc]


# ── tools.py wiring: Lever B filter + mode=off parity ──────────────────

from fluentvibe.catalog import index_exists  # noqa: E402


def _registry(tmp_path):
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    return AuthoringToolRegistry(output_dir=tmp_path)


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_search_labware_off_is_byte_identical_to_baseline(tmp_path):
    reg = _registry(tmp_path)
    # Default registry scope is the inert OFF singleton.
    assert reg.lab_scope.mode == "off"
    result = reg.search_labware("Plate", limit=10)
    assert result["ok"] is True
    # Baseline shape: no lab_scope/note keys injected.
    assert "lab_scope" not in result
    assert "note" not in result
    assert set(result.keys()) == {"ok", "matches"}


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_search_labware_enforce_restricts_to_whitelist(tmp_path):
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    result = reg.search_labware("Plate", limit=50)
    assert result["ok"] is True
    assert result["lab_scope"] == "enforce"
    names = {m["name"] for m in result["matches"]}
    # Every returned match must be on the curated whitelist.
    assert names <= reg.lab_scope.labware
    # And a broad query under OFF returns names that are NOT all whitelisted,
    # proving the filter actually removed something.
    off = _registry(tmp_path).search_labware("Plate", limit=50)
    off_names = {m["name"] for m in off["matches"]}
    assert not off_names <= reg.lab_scope.labware


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_lookup_liquid_class_enforce_rejects_off_list(tmp_path):
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    ok = reg.lookup_liquid_class("Water Free Single")
    assert ok["ok"] is True
    bad = reg.lookup_liquid_class("Totally Made Up Class 9000")
    assert bad["ok"] is False
    assert bad["lab_scope"] == "enforce"
    assert bad["default"] == "Water Free Single"


def test_registry_default_scope_is_off(tmp_path):
    reg = _registry(tmp_path)
    assert reg.lab_scope.mode == "off"
    assert reg.lab_scope.enforces is False


# ── enforce: search tools removed + whitelist = grounding ──────────────

def test_denied_tools_only_in_enforce():
    assert load_lab_scope("off").denied_tools() == frozenset()
    assert load_lab_scope("cheatsheet").denied_tools() == frozenset()
    assert load_lab_scope("enforce").denied_tools() == frozenset(
        {"search_labware", "get_labware", "ground_in_parallel"}
    )


def test_make_lc_tools_drops_search_tools_under_enforce(tmp_path):
    from fluentvibe.authoring.lc_tools import make_lc_tools

    reg = _registry(tmp_path)
    base = {t.name for t in make_lc_tools(reg)}
    assert {"search_labware", "get_labware"} <= base  # present at baseline

    scoped = {
        t.name
        for t in make_lc_tools(reg, denied=load_lab_scope("enforce").denied_tools())
    }
    assert "search_labware" not in scoped
    assert "get_labware" not in scoped
    assert "ground_in_parallel" not in scoped
    # Non-search grounding tools survive.
    assert {"lookup_workspace", "lookup_liquid_class", "declare_intent"} <= scoped
    assert scoped == base - {"search_labware", "get_labware", "ground_in_parallel"}


def test_enforce_whitelist_counts_as_confirmed_grounding(tmp_path):
    reg = _registry(tmp_path)
    # OFF: no get_labware calls ⇒ nothing confirmed (baseline behavior).
    assert reg._confirmed_catalog_names() == set()
    assert reg._confirmed_liquid_class_names() == set()

    reg.lab_scope = load_lab_scope("enforce")
    confirmed = reg._confirmed_catalog_names()
    assert "96_ABgene_SuperPlate_Thermo_AB2800" in confirmed
    assert reg.lab_scope.labware <= confirmed
    assert "Water Free Single" in reg._confirmed_liquid_class_names()


def test_missing_grounding_never_requires_search():
    from fluentvibe.authoring.service import missing_authoring_grounding

    # SYSTEM_PROMPT no longer instructs catalog search, so the orchestrator
    # must not re-impose a search/get requirement in ANY mode.
    for kwargs in ({}, {"lab_scope_enforces": True}):
        missing = missing_authoring_grounding((), **kwargs)
        assert "search_labware or get_labware" not in missing
        # Non-search requirements still enforced.
        assert "lookup_workspace or list_valid_positions" in missing
        assert "lookup_rules" in missing


def test_system_prompt_drops_search_and_subagent_mandate():
    from fluentvibe.authoring.service import (
        SYSTEM_PROMPT,
        assert_no_domain_vocabulary_in_prompt,
    )

    lowered = SYSTEM_PROMPT.lower()
    # The forced-tool-call directives are gone.
    assert "search_labware" not in SYSTEM_PROMPT or "do not call" in lowered
    assert "ground facts through tools" not in lowered
    assert "parallel grounding via subagents" not in lowered
    assert "batch independent tool calls" not in lowered
    # The new prohibition is present.
    assert "do not call" in lowered
    assert "ground_in_parallel" in SYSTEM_PROMPT  # named in the prohibition
    # Core authoring instructions survive.
    assert "def build_worktable" in SYSTEM_PROMPT
    assert "Labware Placement" in SYSTEM_PROMPT
    assert "present_object_draft" in SYSTEM_PROMPT
    assert "WORKFLOW MODE HAS PRIORITY" in SYSTEM_PROMPT
    assert "later workflow as authoritative" in SYSTEM_PROMPT
    # Domain-vocab guard still green on the edited constant.
    assert_no_domain_vocabulary_in_prompt()


def test_pipetting_volume_args_empty_for_placement_only_draft():
    """Fix 1 precondition: the first staged draft (Variables + Labware
    Placement, no aspirate/dispense) yields zero pipetting volume args, so
    the 'must be used through variable' half of the contract is deferred."""
    import ast

    from fluentvibe.authoring.tools import _extract_pipetting_volume_arguments

    placement_only = (
        "def build_worktable():\n"
        "    wt = X()\n"
        "    wt.declare_variable('BEAD_VOLUME_UL', 36.0)\n"
        "    wt.set_sim_value('BEAD_VOLUME_UL', 36.0)\n"
        "    wt.group('Labware Placement')\n"
        "    p = wt.place(Plate96('s'), 'Nest61mm_Pos', 1)\n"
        "    p.fill_all(r, 20.0)\n"
    )
    assert _extract_pipetting_volume_arguments(ast.parse(placement_only)) == []

    with_literal = placement_only + "    head.dispense(p, 36.0)\n"
    args_lit = _extract_pipetting_volume_arguments(ast.parse(with_literal))
    assert ("literal", 36.0) in args_lit  # still rejected when present

    with_var = placement_only + "    head.dispense(p, BEAD_VOLUME_UL)\n"
    args_var = _extract_pipetting_volume_arguments(ast.parse(with_var))
    assert ("name", "BEAD_VOLUME_UL") in args_var


def test_liquid_class_contract_accepts_any_declared_variable():
    """The contract must not force the one approved variable name; the
    model legitimately uses semantic names. Reject only hardcoded literals
    and the no-variable case."""
    from fluentvibe.authoring.tools import _check_source_against_approved_objects

    draft = {
        "labware": [],
        "liquid_classes": [
            {"name": "Water Free Single", "variable": "LIQUID_CLASS_TRANSFER"}
        ],
    }
    base = "def build_worktable():\n    wt = X()\n    head = wt.liha\n"

    # A different declared variable name → accepted (was rejected before).
    other_var = base + "    head.dispense(p, 36.0, liquid_class=LIQUID_CLASS_BEADS)\n"
    assert _check_source_against_approved_objects(other_var, draft) is None

    # Hardcoded class string literal → still rejected.
    literal = base + '    head.dispense(p, 36.0, liquid_class="Water Free Single")\n'
    err = _check_source_against_approved_objects(literal, draft)
    assert err is not None and "string literal" in err

    # No liquid_class kwarg anywhere yet → deferred, no error.
    no_lc = base + "    head.dispense(p, 36.0)\n"
    assert _check_source_against_approved_objects(no_lc, draft) is None


def test_enforce_context_message_states_tools_unavailable():
    msg = load_lab_scope("enforce").as_context_message()
    assert msg is not None and "intentionally unavailable" in msg
    cheat = load_lab_scope("cheatsheet").as_context_message()
    assert cheat is not None and "intentionally unavailable" not in cheat


# ── context economization: lean worktable tool results ─────────────────

import json as _json  # noqa: E402


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_lookup_workspace_result_is_lean(tmp_path):
    """The ~112 KB current_worktable snapshot blob must not be echoed to
    the model. lookup_workspace carries only act-on fields and is small."""
    reg = _registry(tmp_path)
    # _bind_current_worktable_snapshot runs in __init__ (no workspace args)
    # and pre-caches the result the model's call hits.
    result = reg.dispatch("lookup_workspace", {})
    assert result.get("ok") is True
    assert set(result) == {"ok", "workspace", "valid_positions", "default_layout"}
    # The dead deck-internals are gone (check JSON-key form so the
    # legitimate "valid_positions" key doesn't false-trip "positions").
    blob = _json.dumps(result, default=str)
    assert "snapshot" not in result
    for dead in (
        "occupants",
        "compatibility_by_occupant",
        "accepts_by_occupied_carrier_site",
        "positions",
    ):
        assert dead not in result
        assert f'"{dead}":' not in blob
    # Was ~112 KB; lean result is tiny.
    assert len(blob) < 4000

    lvp = reg.dispatch("list_valid_positions", {"location": "Nest61mm_Pos"})
    assert set(lvp) <= {"ok", "location", "positions"}
    assert "snapshot" not in lvp


def test_compact_does_not_append_redundant_summary():
    from fluentvibe.authoring.tools import _compact

    big = {"matches": [{"i": i, "pad": "x" * 50} for i in range(100)]}
    out = _compact(big)
    assert out is big  # passthrough, unchanged
    assert "_summary" not in out and "_truncated" not in out


# ── Bet 1: enforce object-draft auto-grounder ──────────────────────────

def _two_labware():
    return [
        {"label": "SamplePlate", "python_class": "Plate96",
         "catalog_name": "96_ABgene_SuperPlate_Thermo_AB2800", "role": "source"},
        {"label": "ElutionPlate", "python_class": "Plate96",
         "catalog_name": "96_ABgene_SuperPlate_Thermo_AB2800", "role": "destination"},
    ]


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_enforce_autogrounds_workspace_and_deck_layout(tmp_path):
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    assert not reg._has_successful_call("lookup_workspace")
    assert not reg._has_successful_call("suggest_deck_layout")

    result = reg.present_object_draft(
        "AMPure", "cleanup", None, _two_labware(),
    )
    # The two deterministic prerequisites were satisfied server-side.
    assert reg._has_successful_call("lookup_workspace")
    assert reg._has_successful_call("suggest_deck_layout")
    # And the gate no longer rejects on those grounds.
    assert result.get("category") != "workspace_grounding_required"
    errs = " ".join(e.get("message", "") for e in result.get("errors", []))
    assert "deck-layout grounding" not in errs
    assert "lookup_workspace or list_valid_positions" not in errs


def test_off_and_cheatsheet_do_not_autoground(tmp_path):
    for mode in ("off", "cheatsheet"):
        reg = _registry(tmp_path)
        reg.lab_scope = load_lab_scope(mode)
        result = reg.present_object_draft("P", "s", None, _two_labware())
        # Unchanged baseline behavior: still demands manual grounding,
        # no synthesized calls.
        assert result.get("category") == "workspace_grounding_required"
        assert not reg._has_successful_call("lookup_workspace")
        assert not reg._has_successful_call("suggest_deck_layout")


# ── Bet 1b: enforce object-draft payload completion ────────────────────

def _bare_labware():
    """Two plates with NO python_class / catalog / location — the exact
    malformed shape that drives the residual enforce thrash."""
    return [
        {"label": "SamplePlate", "role": "source"},
        {"label": "ElutionPlate", "role": "destination"},
    ]


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_enforce_completes_bare_labware_payload(tmp_path):
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    items = _bare_labware()
    result = reg.present_object_draft("Two plate transfer", "move", None, items)

    # The items were completed in place from the curated whitelist.
    for it in items:
        assert it["catalog_name"] == "96_ABgene_SuperPlate_Thermo_AB2800"
        assert it["python_class"] == "Plate96"
        assert it["location"]
        assert it.get("site")
    # And the gate accepts the now-complete draft.
    assert result.get("ok") is True, result
    assert result.get("status") == "needs_approval"


def test_off_and_cheatsheet_leave_bare_labware_untouched(tmp_path):
    for mode in ("off", "cheatsheet"):
        reg = _registry(tmp_path)
        reg.lab_scope = load_lab_scope(mode)
        items = _bare_labware()
        before = [dict(it) for it in items]
        reg.present_object_draft("P", "s", None, items)
        assert items == before  # byte-identical: no completion off-enforce


# ── Bet 2: enforce deterministic group-C volumes ───────────────────────

def _ampure_volume_vars():
    return [
        {"name": "SAMPLE_VOLUME_UL", "default": 30.0},
        {"name": "BEAD_VOLUME_UL", "default": 54.0},
        {"name": "RETAIN_VOLUME_UL", "default": 5.0},
        {"name": "ELUTION_VOLUME_UL", "default": 45.0},
        {"name": "SUPERNATANT_ASPIRATE_UL", "default": 15.0, "sim_value": 15.0},
        {"name": "TRANSFER_VOLUME_UL", "default_value": 20.0},
    ]


def test_enforce_derives_dependent_volumes(tmp_path):
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    v = _ampure_volume_vars()
    reg._enforce_object_draft_volumes(v)
    by = {x["name"]: x for x in v}
    # sample + beads - retain = 30 + 54 - 5
    assert by["SUPERNATANT_ASPIRATE_UL"]["default"] == 79.0
    assert by["SUPERNATANT_ASPIRATE_UL"]["sim_value"] == 79.0
    # elution - retain = 45 - 5  (written to whichever value key exists)
    assert by["TRANSFER_VOLUME_UL"]["default_value"] == 40.0
    # primitives untouched
    assert by["SAMPLE_VOLUME_UL"]["default"] == 30.0


def test_enforce_keeps_volume_when_inputs_incomplete(tmp_path):
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    v = [{"name": "SUPERNATANT_ASPIRATE_UL", "default": 15.0}]  # no primitives
    reg._enforce_object_draft_volumes(v)
    assert v[0]["default"] == 15.0  # left as-is


def test_enforce_accepts_target_volume_as_sample_synonym(tmp_path):
    """The lab cheatsheet's canonical workflow names the input quantity
    TARGET_VOLUME_UL; it must satisfy the SAMPLE_VOLUME_UL primitive."""
    reg = _registry(tmp_path)
    reg.lab_scope = load_lab_scope("enforce")
    v = [
        {"name": "TARGET_VOLUME_UL", "default": 30.0},
        {"name": "BEAD_VOLUME_UL", "default": 54.0},
        {"name": "RETAIN_VOLUME_UL", "default": 5.0},
        {"name": "SUPERNATANT_ASPIRATE_UL", "default": 15.0},
    ]
    reg._enforce_object_draft_volumes(v)
    assert v[-1]["default"] == 79.0  # 30 + 54 - 5, sourced from TARGET_*


def test_off_and_cheatsheet_do_not_derive_volumes(tmp_path):
    for mode in ("off", "cheatsheet"):
        reg = _registry(tmp_path)
        reg.lab_scope = load_lab_scope(mode)
        v = _ampure_volume_vars()
        before = [dict(x) for x in v]
        reg._enforce_object_draft_volumes(v)
        assert v == before  # no-op outside enforce


# ── Bet 3: supersede stale draft echoes ────────────────────────────────

def test_supersede_prior_drafts_stubs_old_keeps_new():
    from langchain_core.messages import ToolMessage

    from fluentvibe.authoring.graph import _SUPERSEDED_STUB, _supersede_prior_drafts

    m1 = ToolMessage(content='{"big":"first attempt"}', tool_call_id="a",
                      name="present_object_draft")
    m2 = ToolMessage(content='{"big":"second attempt"}', tool_call_id="b",
                      name="present_object_draft")
    other = ToolMessage(content='{"keep":"me"}', tool_call_id="c",
                         name="lookup_workspace")

    _supersede_prior_drafts("present_object_draft", [m1, m2, other])

    # All prior present_object_draft echoes collapsed to the stub …
    assert m1.content == _SUPERSEDED_STUB
    assert m2.content == _SUPERSEDED_STUB
    # … structure preserved, unrelated tool message untouched.
    assert m1.tool_call_id == "a" and m1.name == "present_object_draft"
    assert other.content == '{"keep":"me"}'

    # A different kind is not collapsed by an object-draft re-present.
    plan = ToolMessage(content='{"plan":1}', tool_call_id="d",
                        name="present_functional_group_plan")
    _supersede_prior_drafts("present_object_draft", [plan])
    assert plan.content == '{"plan":1}'
