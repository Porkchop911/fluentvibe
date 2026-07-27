"""``--lab-scope skills`` — catalog discovery, LM pre-pass selection, assembly.

skills mode decomposes the enforce monolith into granular frontmatter-tagged
skill files and injects only the relevant subset (plus the always_on core)
chosen by an LM pre-pass. It shares enforce's runtime posture (tool
restriction + whitelist). No live LM here — selection uses a fake client.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.authoring import lab_scope as lab_scope_mod  # noqa: E402
from fluentvibe.authoring.lab_scope import (  # noqa: E402
    LabScope,
    LabScopeSetupError,
    load_lab_scope,
    resolve_lab_scope_mode,
)
from fluentvibe.authoring.lab_skills import (  # noqa: E402
    apply_profile_deck,
    assemble_context,
    build_initial_scope_message,
    discover_skills,
    select_deck_for_workspace,
    select_skills,
)
from fluentvibe.authoring.workspace_modules import WorkspaceModule  # noqa: E402

_ENV = "FLUENTVIBE_LAB_SCOPE"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)


# ── fakes ──────────────────────────────────────────────────────────────

class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    """Returns a canned ``.content`` from ``.invoke`` like an adapted client."""

    def __init__(self, content):
        self._content = content

    def invoke(self, messages):
        return _FakeMsg(self._content)


class _BoomClient:
    def invoke(self, messages):
        raise RuntimeError("LM unreachable")


# ── mode resolution ────────────────────────────────────────────────────

def test_skills_is_a_valid_mode():
    assert resolve_lab_scope_mode("skills") == "skills"
    # unknown normalizes to the default mode (skills)
    assert resolve_lab_scope_mode("skill") == "skills"


# ── discovery ──────────────────────────────────────────────────────────

def test_discover_real_catalog():
    config_dir = REPO_ROOT / "fluentvibe" / "_assets" / "config" / "skills"
    catalog = discover_skills(config_dir)
    assert catalog, "no skill files discovered"
    names = {s.name for s in catalog}
    assert {"core-worktable-api", "deck-sat-780", "family-bead-cleanup-spri"} <= names
    assert all(s.axis in {"api", "deck", "family"} for s in catalog)
    assert all(s.description for s in catalog)
    # the core api skills are always_on; decks are NOT (they are selected by
    # workspace match, never hardwired) and carry workspace frontmatter.
    always = {s.name for s in catalog if s.always_on}
    assert "core-worktable-api" in always
    assert "deck-sat-780" not in always
    deck = next(s for s in catalog if s.name == "deck-sat-780")
    assert deck.workspace_name == "SAT_Fluent_780_Rev3"
    assert deck.workspace_guid == "291ba293-6361-4f8f-aa8d-7c2643d3f096"


def test_shipped_catalog_is_well_formed():
    """Integrity guard for the whole shipped skill library as it grows.

    Every file parses, names are unique, axes valid, descriptions non-empty,
    and the always_on set stays the intended minimal core (core api + labware).
    Decks are never always_on — they are selected by workspace match — and all
    other skills must be always_on=false so the LM pre-pass stays additive.
    """
    config_dir = REPO_ROOT / "fluentvibe" / "_assets" / "config" / "skills"
    md_files = sorted(config_dir.rglob("*.md"))  # recurse category subfolders
    catalog = discover_skills(config_dir)
    # no file silently dropped for malformed frontmatter
    assert len(catalog) == len(md_files), "a shipped skill failed to parse"
    names = [s.name for s in catalog]
    assert len(names) == len(set(names)), "duplicate skill names"
    assert all(s.axis in {"api", "deck", "family"} for s in catalog)
    assert all(s.description.strip() for s in catalog)
    assert all(s.body.strip() for s in catalog)
    # each skill lives in a folder named for its axis
    assert all(s.path.parent.name == s.axis for s in catalog), (
        "a skill file is not under its axis folder"
    )
    always = {s.name for s in catalog if s.always_on}
    assert always == {
        "core-worktable-api",
        "labware-and-liquid-classes",
    }
    # every deck skill declares the workspace it targets (for deck selection)
    decks = [s for s in catalog if s.axis == "deck"]
    assert decks, "no deck skills shipped"
    assert all(s.workspace_name for s in decks), "a deck skill lacks workspace frontmatter"
    assert not any(s.always_on for s in decks), "a deck skill is hardwired always_on"


def test_discover_skips_malformed_and_missing(tmp_path):
    # no frontmatter
    (tmp_path / "bad1.md").write_text("# just a heading\n", encoding="utf-8")
    # invalid axis
    (tmp_path / "bad2.md").write_text(
        "---\nname: x\naxis: nonsense\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    # missing name
    (tmp_path / "bad3.md").write_text(
        "---\naxis: api\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    # one good file
    (tmp_path / "good.md").write_text(
        "---\nname: ok\naxis: api\ndescription: a good skill\n---\nthe body\n",
        encoding="utf-8",
    )
    catalog = discover_skills(tmp_path)
    assert [s.name for s in catalog] == ["ok"]
    assert catalog[0].body == "the body"


def test_discover_missing_dir_is_empty(tmp_path):
    assert discover_skills(tmp_path / "nope") == ()


# ── load_lab_scope("skills") ───────────────────────────────────────────

def test_skills_load_shares_enforce_posture():
    scope = load_lab_scope("skills")
    assert scope.mode == "skills"
    assert scope.is_active is True
    assert scope.enforces is True  # inherits enforce restriction
    assert scope.skill_catalog, "catalog not populated"
    assert scope.cheatsheet_text is None  # monolith not loaded
    # whitelist still populated (shared with enforce)
    assert "96_ABgene_SuperPlate_Thermo_AB2800" in scope.labware
    assert "Water Free Single" in scope.liquid_classes
    # tool surface mirrors enforce's two judging tools, plus the one extra tool
    # skills needs to stage a multi-stage protocol group-by-group.
    enforce = load_lab_scope("enforce")
    assert scope.allowed_tools() == enforce.allowed_tools() | {"declare_protocol_workflow"}
    assert scope.denied_tools() == enforce.denied_tools()


# ── selection ──────────────────────────────────────────────────────────

def _catalog():
    return load_lab_scope("skills").skill_catalog


def test_select_includes_always_on_and_picked():
    cat = _catalog()
    names = select_skills(
        "AMPure bead cleanup",
        cat,
        _FakeClient('["family-bead-cleanup-spri", "head-mca96"]'),
    )
    assert "core-worktable-api" in names  # always_on
    assert "deck-sat-780" in names  # always_on
    assert "family-bead-cleanup-spri" in names
    assert "head-mca96" in names
    assert "family-simple-transfer" not in names  # not picked


def test_select_drops_unknown_names_then_falls_back():
    cat = _catalog()
    # only an unknown name → nothing valid picked → fallback to all optional
    names = select_skills("x", cat, _FakeClient('["does-not-exist"]'))
    assert "family-simple-transfer" in names
    assert "family-bead-cleanup-spri" in names


def test_select_falls_back_on_transport_error():
    cat = _catalog()
    names = select_skills("x", cat, _BoomClient())
    assert set(names) == {s.name for s in cat}  # everything loaded


def test_select_falls_back_on_garbage_reply():
    cat = _catalog()
    names = select_skills("x", cat, _FakeClient("sorry, I cannot help"))
    assert set(names) == {s.name for s in cat}


# ── deterministic augmentation: cross-refs + select_when triggers ───────

def test_select_pulls_cross_referenced_skill():
    # family-ngs-library-prep's body defers the cleanup to
    # family-bead-cleanup-spri ("reuses ..."). Selecting the parent must
    # actually deliver the referenced skill, even if the LM omitted it.
    cat = _catalog()
    names = select_skills(
        "NGS library prep with size selection",
        cat,
        _FakeClient('["family-ngs-library-prep"]'),
    )
    assert "family-ngs-library-prep" in names
    assert "family-bead-cleanup-spri" in names  # pulled via cross-reference
    # it also references family-pcr-setup
    assert "family-pcr-setup" in names


def test_select_when_force_includes_bead_cleanup_despite_lm_omission():
    # The LM (stubbed) picks only the library-prep family and the head, NOT the
    # bead-cleanup family — but the prompt mentions AMPure, so the deterministic
    # trigger force-includes it regardless.
    cat = _catalog()
    names = select_skills(
        "Resuspend the AMPure XP beads and clean up the library",
        cat,
        _FakeClient('["head-liha"]'),
    )
    assert "family-bead-cleanup-spri" in names


def test_select_when_pooling_trigger():
    cat = _catalog()
    names = select_skills(
        "Pool all barcoded samples into one tube",
        cat,
        _FakeClient('["head-liha"]'),
    )
    assert "family-pooling" in names


def test_select_when_does_not_overfire_on_unrelated_prompt():
    # A plain transfer must not drag in the bead-cleanup or pooling families.
    cat = _catalog()
    names = select_skills(
        "transfer 20 uL from a source plate to a destination plate",
        cat,
        _FakeClient('["family-simple-transfer", "head-liha"]'),
    )
    assert "family-bead-cleanup-spri" not in names
    assert "family-pooling" not in names


def test_select_accepts_multiple_family_skills():
    cat = _catalog()
    names = select_skills(
        "library prep then pool",
        cat,
        _FakeClient('["family-ngs-library-prep", "family-pooling", "head-liha"]'),
    )
    fam = {n for n in names if n.startswith("family-")}
    assert {"family-ngs-library-prep", "family-pooling", "family-bead-cleanup-spri"} <= fam


def test_select_when_frontmatter_parses_on_shipped_skills():
    cat = _catalog()
    by_name = {s.name: s for s in cat}
    # Triggers are brand-neutral generic terms (not "ampure"): any SPRI / magnetic
    # bead cleanup selects the skill, regardless of bead brand.
    bead_triggers = set(by_name["family-bead-cleanup-spri"].select_when)
    assert {"bead", "magnetic"} <= bead_triggers
    assert "ampure" not in bead_triggers
    assert "pool" in by_name["family-pooling"].select_when
    # skills without the field default to an empty tuple (degrade cleanly)
    assert by_name["family-simple-transfer"].select_when == ()


# ── profile deck swap ──────────────────────────────────────────────────

def test_apply_profile_deck_swaps_the_deck(tmp_path):
    cat = _catalog()
    assert "deck-sat-780" in {s.name for s in cat}
    deck = tmp_path / "deck-myprofile.md"
    deck.write_text(
        "---\nname: deck-myprofile\naxis: deck\ndescription: a profile deck\n"
        "always_on: true\n---\nbind here\n",
        encoding="utf-8",
    )
    swapped = apply_profile_deck(cat, deck)
    deck_names = {s.name for s in swapped if s.axis == "deck"}
    assert deck_names == {"deck-myprofile"}  # shipped 780 dropped, profile spliced
    # non-deck skills are untouched
    assert {s.name for s in cat if s.axis != "deck"} == {s.name for s in swapped if s.axis != "deck"}


def test_apply_profile_deck_degrades_on_bad_file(tmp_path):
    cat = _catalog()
    missing = tmp_path / "deck-nope.md"
    assert apply_profile_deck(cat, missing) == cat  # unchanged, not dropped


def test_apply_profile_deck_forces_the_deck_always_on(tmp_path):
    cat = _catalog()
    deck = tmp_path / "deck-myprofile.md"
    # note: always_on omitted in the file — selection must force it on anyway
    deck.write_text(
        "---\nname: deck-myprofile\naxis: deck\ndescription: a profile deck\n---\nbind\n",
        encoding="utf-8",
    )
    swapped = apply_profile_deck(cat, deck)
    spliced = next(s for s in swapped if s.name == "deck-myprofile")
    assert spliced.always_on is True


# ── workspace-matched deck selection ───────────────────────────────────

def test_select_deck_for_workspace_matches_by_guid_then_name():
    cat = discover_skills(
        REPO_ROOT / "fluentvibe" / "_assets" / "config" / "skills"
    )
    # by guid
    by_guid = select_deck_for_workspace(
        cat, None, "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    )
    assert by_guid is not None
    deck = next(s for s in by_guid if s.axis == "deck")
    assert deck.name == "deck-sat-780" and deck.always_on is True
    # by name
    by_name = select_deck_for_workspace(cat, "SAT_Fluent_780_Rev3", None)
    assert by_name is not None
    assert {s.name for s in by_name if s.axis == "deck"} == {"deck-sat-780"}


def test_select_deck_for_workspace_returns_none_when_unmatched():
    cat = discover_skills(
        REPO_ROOT / "fluentvibe" / "_assets" / "config" / "skills"
    )
    assert select_deck_for_workspace(cat, "No_Such_Workspace", None) is None


def test_skills_fails_loud_when_no_deck_matches(monkeypatch):
    # no profile + a configured workspace with no shipped deck => fail loud,
    # never a silent default deck.
    monkeypatch.setattr(
        lab_scope_mod, "_configured_worktable", lambda: ("No_Such_Workspace", None)
    )
    with pytest.raises(LabScopeSetupError):
        load_lab_scope("skills", profile=None)


def test_skills_default_deck_follows_configured_worktable():
    # the shipped generation.yaml worktable is SAT_Fluent_780_Rev3, so the bare
    # (profile-less) skills scope resolves to that deck — driven by config, not
    # a hardwired always_on flag.
    scope = load_lab_scope("skills", profile=None)
    deck_names = {s.name for s in scope.skill_catalog if s.axis == "deck"}
    assert deck_names == {"deck-sat-780"}


# ── assembly ───────────────────────────────────────────────────────────

def test_assemble_orders_api_deck_family_with_header():
    scope = load_lab_scope("skills")
    names = select_skills(
        "bead cleanup",
        scope.skill_catalog,
        _FakeClient('["family-bead-cleanup-spri", "head-liha", "head-gripper"]'),
    )
    ctx = assemble_context(scope, names)
    assert ctx is not None
    assert ctx.startswith("LAB SCOPE (authoritative — this IS the catalog")
    # axis ordering: every api skill appears before the family skill
    fam_pos = ctx.index("AMPure XP PCR cleanup")
    api_pos = ctx.index("## `Worktable`")
    assert api_pos < fam_pos


def test_assemble_context_includes_workspace_modules(tmp_path):
    module_path = tmp_path / "workspace_modules.py"
    module_path.write_text("def spri_cleanup():\n    pass\n", encoding="utf-8")
    scope = load_lab_scope("skills")
    scope = LabScope(
        mode=scope.mode,
        cheatsheet_text=scope.cheatsheet_text,
        labware=scope.labware,
        labware_classes=scope.labware_classes,
        liquid_classes=scope.liquid_classes,
        skill_catalog=scope.skill_catalog,
        workspace_modules=(
            WorkspaceModule(
                name="spri_cleanup",
                description="validated cleanup",
                source_path=module_path,
                import_name="workspace_modules",
                function="spri_cleanup",
                triggers=("spri", "bead"),
                approved=True,
                validation_status="passed",
                parameters=("sample_plate", "magnet"),
                required_roles=("analyte", "separate tips"),
            ),
        ),
    )

    ctx = assemble_context(scope, ["core-worktable-api"])

    assert ctx is not None
    assert "## Available workspace modules" in ctx
    assert "from workspace_modules import spri_cleanup" in ctx
    assert "Prefer these approved profile-local Python helpers" in ctx


def test_build_initial_scope_message_skills_path():
    scope = load_lab_scope("skills")
    msg = build_initial_scope_message(
        scope, "simple transfer", _FakeClient('["family-simple-transfer", "head-liha"]')
    )
    assert msg and "simple transfer" in msg.lower()
    assert "AMPure XP PCR cleanup" not in msg  # bead family not selected


def test_build_initial_scope_message_non_skills_is_static():
    # enforce path must not touch the client (returns the monolith)
    scope = load_lab_scope("enforce")
    msg = build_initial_scope_message(scope, "anything", _BoomClient())
    assert msg and scope.cheatsheet_text in msg
