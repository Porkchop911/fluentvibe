"""Tests for the authoring quality rubric (``fluentvibe.authoring.eval_rubric``).

Source tier runs on embedded snippets (pure AST/regex, no catalog needed).
Semantic tier reuses the golden bead-cleanup shape from ``test_ampure_sat_1080``
(skips cleanly when the 1080 workspace is not installed) plus a deliberately
broken variant that elutes but never recovers the eluate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TESTS_DIR))

from fluentvibe.authoring import eval_rubric  # noqa: E402
from fluentvibe.authoring.eval_rubric import (  # noqa: E402
    RubricResult,
    score_protocol,
    score_semantic,
    score_source,
)
from fluentvibe.authoring.lab_skills import discover_skills  # noqa: E402

# Source snippets are only parsed/regex-scanned, never executed — they need to be
# syntactically valid Python but not runnable.

# Mirrors the f16 attempt2 defects: no analyte role, a single MCA box, no eluate
# transfer (and thus no recovery), supernatant correctly derived.
FAIL_SOURCE = '''
from fluentvibe import Worktable, Reagent, Plate96, MCA200Box, MagnetRack

def build_worktable():
    wt = Worktable.from_workspace("ws")
    wt.declare_variable("SAMPLE_VOLUME_UL", 9.0)
    wt.declare_variable("BEAD_VOLUME_UL", 9.0)
    wt.declare_variable("RETAIN_VOLUME_UL", 2.0)
    wt.declare_variable("SUPERNATANT_ASPIRATE_UL", 16.0)
    beads = Reagent("AMPure XP Beads", role="bead_carrier")
    amplicon = Reagent("Amplicon DNA")
    mca = wt.place(MCA200Box("MCATips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 3)
    magnet = wt.place(MagnetRack("Magnet", catalog="m"), "Nest61mm_Pos", 13)
    wt.gripper.move(plate, onto=magnet)
    wt.gripper.move(plate, to=("Nest61mm_Pos", 1))
    return wt
'''

# All source-tier invariants satisfied: analyte tagged, two MCA boxes, eluate
# transfer derived, both supernatant + eluate derived, onto→off→on moves.
PASS_SOURCE = '''
from fluentvibe import Worktable, Reagent, Plate96, MCA200Box, MagnetRack

def build_worktable():
    wt = Worktable.from_workspace("ws")
    wt.declare_variable("SAMPLE_VOLUME_UL", 9.0)
    wt.declare_variable("BEAD_VOLUME_UL", 9.0)
    wt.declare_variable("RETAIN_VOLUME_UL", 2.0)
    wt.declare_variable("SUPERNATANT_ASPIRATE_UL", 16.0)
    wt.declare_variable("ELUTION_VOLUME_UL", 15.0)
    wt.declare_variable("TRANSFER_VOLUME_UL", 13.0)
    beads = Reagent("AMPure XP Beads", role="bead_carrier")
    amplicon = Reagent("Amplicon DNA", role="analyte")
    eb = Reagent("Elution Buffer", role="eluent")
    mca = wt.place(MCA200Box("MCATips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 3)
    mca2 = wt.place(MCA200Box("MCATipsEluate", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4)
    magnet = wt.place(MagnetRack("Magnet", catalog="m"), "Nest61mm_Pos", 13)
    wt.gripper.move(plate, onto=magnet)
    wt.gripper.move(plate, to=("Nest61mm_Pos", 1))
    wt.gripper.move(plate, onto=magnet)
    return wt
'''


def _status(invs, key):
    return next(i.status for i in invs if i.key == key)


# ── Source tier ──────────────────────────────────────────────────────────


def test_fail_source_flags_the_catastrophic_defects():
    invs = score_source(FAIL_SOURCE)
    assert _status(invs, "analyte_role_tagged") == "fail"
    assert _status(invs, "separate_eluate_destination") == "fail"  # only 1 MCA box
    assert _status(invs, "derived_eluate") == "na"                 # no eluate transfer
    # The parts attempt2 got right are still credited:
    assert _status(invs, "derived_supernatant") == "pass"
    assert _status(invs, "off_magnet_elution") == "pass"


def test_pass_source_satisfies_every_source_invariant():
    invs = score_source(PASS_SOURCE)
    for key in ("analyte_role_tagged", "derived_supernatant", "derived_eluate",
                "off_magnet_elution", "separate_eluate_destination"):
        assert _status(invs, key) == "pass", key


def test_derived_supernatant_detects_wrong_math():
    bad = FAIL_SOURCE.replace("SUPERNATANT_ASPIRATE_UL\", 16.0", "SUPERNATANT_ASPIRATE_UL\", 9.0")
    invs = score_source(bad)
    assert _status(invs, "derived_supernatant") == "fail"


def test_coverage_is_na_without_source_document():
    invs = score_source(FAIL_SOURCE)
    assert _status(invs, "coverage_complete") == "na"


def test_coverage_complete_uses_document_adherence():
    src = 'wt.add_comment("AMPure bead magnet ethanol elution buffer barcode")'
    doc = "Use AMPure beads on the magnet, ethanol wash, elution buffer, barcode."
    invs = score_source(src, source_text=doc)
    assert _status(invs, "coverage_complete") == "pass"


def test_score_protocol_semantic_degrades_to_na_when_unrunnable():
    # FAIL_SOURCE references an undefined `plate` → build_worktable raises.
    result = score_protocol(FAIL_SOURCE, simulate=True)
    assert isinstance(result, RubricResult)
    for key in ("magnet_roundtrip", "eluate_recovered", "analyte_not_in_waste"):
        assert result.get(key).status == "na"
    # Source tier still scored.
    assert result.get("analyte_role_tagged").status == "fail"


def test_rubric_result_score_excludes_na():
    result = RubricResult(tuple(score_source(PASS_SOURCE)))
    # coverage is NA (no source doc); the other five source checks pass.
    assert result.na == 1
    assert result.passed == 5
    assert result.score == 1.0


# ── Semantic tier (needs the 1080 workspace; skips otherwise) ─────────────

from fluentvibe import (  # noqa: E402
    Layer,
    MagnetRack,
    MCA100Box,
    Plate96,
    Reagent,
    Worktable,
)
from fluentvibe.catalog.catalog import (  # noqa: E402
    index_exists,
    resolve_workspace_by_guid,
    resolve_workspace_by_name,
)

PROFILE_DIR = REPO_ROOT / "build" / "workspaces" / "sat_1080_test"
NEST = "Nest61mm_Pos"
LIQUID_CLASS = "Water Free Single"
PLACEMENT = {"magnet": 7, "sample": 1, "waste": 2, "beads": 3, "eb": 4, "tips": 5, "eluate": 6}


def _profile() -> dict:
    import json
    path = PROFILE_DIR / "workspace_profile.json"
    if not path.exists():
        pytest.skip(f"sat_1080_test profile not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_workspace(profile: dict) -> None:
    if not index_exists():
        pytest.skip("catalog index empty")
    name = profile["workspace"]["name"]
    guid = profile["workspace"]["guid"]
    if resolve_workspace_by_guid(guid) is None and resolve_workspace_by_name(name) is None:
        pytest.skip(f"workspace {name!r} not installed in this catalog")


def _build_cleanup(profile: dict, *, recover: bool) -> Worktable:
    """Golden bead cleanup; when ``recover`` is False the eluate is never moved
    back onto the magnet and transferred — the catastrophic f16 defect."""
    name, guid = profile["workspace"]["name"], profile["workspace"]["guid"]
    wt = Worktable.from_workspace(name, workspace_guid=guid, auto_place=False,
                                  protocol_name="cleanup", comment="rubric fixture")
    sample_buffer = Reagent("Sample buffer")
    dna = Reagent("Sample DNA", role="analyte")
    ampure = Reagent("AMPure beads", role="bead_carrier")
    elution = Reagent("Elution buffer", role="eluent")

    wt.group("Setup")
    magnet = wt.place(MagnetRack("Magnet", catalog="24 Magnet Plate"), NEST, PLACEMENT["magnet"])
    plate = wt.place(Plate96("Sample", catalog="96 Well Flat"), NEST, PLACEMENT["sample"])
    waste = wt.place(Plate96("Waste", catalog="96 Well Flat"), NEST, PLACEMENT["waste"])
    bead_src = wt.place(Plate96("Beads", catalog="96 Well Flat"), NEST, PLACEMENT["beads"])
    eb_src = wt.place(Plate96("EB", catalog="96 Well Flat"), NEST, PLACEMENT["eb"])
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), NEST, PLACEMENT["tips"])
    final = wt.place(Plate96("Eluate", catalog="96 Well Flat"), NEST, PLACEMENT["eluate"])

    plate.fill_all(sample_buffer, 30.0)
    for w in plate.wells.values():
        w.layers.append(Layer(reagent=dna, volume_ul=2.0))
    bead_src.fill_all(ampure, 60.0)
    eb_src.fill_all(elution, 40.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)

    wt.group("Add beads and bind")
    head.aspirate(bead_src, 60.0, liquid_class=LIQUID_CLASS)
    head.dispense(plate, 60.0, liquid_class=LIQUID_CLASS)
    head.mix(plate, 40.0, liquid_class=LIQUID_CLASS)

    wt.group("Magnetise, remove supernatant")
    wt.gripper.move(plate, onto=magnet)
    head.aspirate(plate, 90.0, liquid_class=LIQUID_CLASS)
    head.dispense(waste, 90.0, liquid_class=LIQUID_CLASS)
    wt.gripper.move(plate, to=(NEST, PLACEMENT["sample"]))

    wt.group("Elute")
    head.aspirate(eb_src, 40.0, liquid_class=LIQUID_CLASS)
    head.dispense(plate, 40.0, liquid_class=LIQUID_CLASS)
    head.mix(plate, 30.0, liquid_class=LIQUID_CLASS)

    if recover:
        wt.group("Magnetise, recover eluate")
        wt.gripper.move(plate, onto=magnet)
        head.aspirate(plate, 42.0, liquid_class=LIQUID_CLASS)
        head.dispense(final, 42.0, liquid_class=LIQUID_CLASS)
        wt.gripper.move(plate, to=(NEST, PLACEMENT["sample"]))

    head.return_tips(tips)
    head.drop_adapter()
    return wt


def test_semantic_passes_on_correct_recovery():
    profile = _profile()
    _require_workspace(profile)
    wt = _build_cleanup(profile, recover=True)
    wt.simulate()
    invs = {i.key: i.status for i in score_semantic(wt)}
    assert invs["magnet_roundtrip"] == "pass"
    assert invs["eluate_recovered"] == "pass"
    assert invs["analyte_not_in_waste"] == "pass"


def test_semantic_fails_when_eluate_never_recovered():
    profile = _profile()
    _require_workspace(profile)
    wt = _build_cleanup(profile, recover=False)
    wt.simulate()
    invs = {i.key: i.status for i in score_semantic(wt)}
    # No second magnetise → no round-trip, and the analyte stays stranded.
    assert invs["magnet_roundtrip"] == "fail"
    assert invs["eluate_recovered"] == "fail"


# ── Skill still well-formed after the tightening ──────────────────────────


def test_spri_skill_keeps_heading_and_triggers():
    skills_dir = REPO_ROOT / "fluentvibe" / "_assets" / "config" / "skills"
    catalog = discover_skills(skills_dir)
    spri = next((s for s in catalog if s.name == "family-bead-cleanup-spri"), None)
    assert spri is not None
    assert "AMPure XP" in spri.body
    triggers = set(spri.select_when)
    assert {"bead", "magnetic"} <= triggers
    assert "ampure" not in triggers  # brand-neutral
