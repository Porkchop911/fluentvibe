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

from fluentvibe.authoring.lab_scope import load_lab_scope, resolve_lab_scope_mode  # noqa: E402
from fluentvibe.authoring.lab_skills import (  # noqa: E402
    Skill,
    apply_profile_deck,
    assemble_context,
    build_initial_scope_message,
    discover_skills,
    select_skills,
)

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
    assert {"core-worktable-api", "deck-sat-780", "family-bead-cleanup-ampure"} <= names
    assert all(s.axis in {"api", "deck", "family"} for s in catalog)
    assert all(s.description for s in catalog)
    # at least the core api + the deck are always_on
    always = {s.name for s in catalog if s.always_on}
    assert {"core-worktable-api", "deck-sat-780"} <= always


def test_shipped_catalog_is_well_formed():
    """Integrity guard for the whole shipped skill library as it grows.

    Every file parses, names are unique, axes valid, descriptions non-empty,
    and the always_on set stays the intended minimal core (core api + labware
    + the single deck). New skills must be always_on=false so the LM pre-pass
    stays purely additive.
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
        "deck-sat-780",
    }


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
    # identical tool surface to enforce
    enforce = load_lab_scope("enforce")
    assert scope.allowed_tools() == enforce.allowed_tools()
    assert scope.denied_tools() == enforce.denied_tools()


# ── selection ──────────────────────────────────────────────────────────

def _catalog():
    return load_lab_scope("skills").skill_catalog


def test_select_includes_always_on_and_picked():
    cat = _catalog()
    names = select_skills(
        "AMPure bead cleanup",
        cat,
        _FakeClient('["family-bead-cleanup-ampure", "head-mca96"]'),
    )
    assert "core-worktable-api" in names  # always_on
    assert "deck-sat-780" in names  # always_on
    assert "family-bead-cleanup-ampure" in names
    assert "head-mca96" in names
    assert "family-simple-transfer" not in names  # not picked


def test_select_drops_unknown_names_then_falls_back():
    cat = _catalog()
    # only an unknown name → nothing valid picked → fallback to all optional
    names = select_skills("x", cat, _FakeClient('["does-not-exist"]'))
    assert "family-simple-transfer" in names
    assert "family-bead-cleanup-ampure" in names


def test_select_falls_back_on_transport_error():
    cat = _catalog()
    names = select_skills("x", cat, _BoomClient())
    assert set(names) == {s.name for s in cat}  # everything loaded


def test_select_falls_back_on_garbage_reply():
    cat = _catalog()
    names = select_skills("x", cat, _FakeClient("sorry, I cannot help"))
    assert set(names) == {s.name for s in cat}


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


# ── assembly ───────────────────────────────────────────────────────────

def test_assemble_orders_api_deck_family_with_header():
    scope = load_lab_scope("skills")
    names = select_skills(
        "bead cleanup",
        scope.skill_catalog,
        _FakeClient('["family-bead-cleanup-ampure", "head-liha", "head-gripper"]'),
    )
    ctx = assemble_context(scope, names)
    assert ctx is not None
    assert ctx.startswith("LAB SCOPE (authoritative — this IS the catalog")
    # axis ordering: every api skill appears before the family skill
    fam_pos = ctx.index("AMPure XP PCR cleanup")
    api_pos = ctx.index("## `Worktable`")
    assert api_pos < fam_pos


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
