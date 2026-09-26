"""Skeleton drafts built deterministically from a Bench Spec and a deck profile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluentvibe.authoring.bench_spec import spec_context_block, validate_bench_spec
from fluentvibe.authoring.eval_rubric import score_protocol
from fluentvibe.authoring.skeleton import SKELETON_MARKER, build_skeleton, load_deck
from tests.test_blocks import PROFILE, _workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "examples" / "ont_rbk114_spec.json"


def _spec(raw=None):
    spec, problems = validate_bench_spec(raw or json.loads(SPEC.read_text(encoding="utf-8")))
    assert spec is not None
    return spec


@pytest.fixture
def profile(monkeypatch):
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV

    _workspace()
    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE.parent))
    return PROFILE.parent


def test_gold_spec_skeleton_passes_every_check_and_the_authoring_gate(profile, tmp_path):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    source = build_skeleton(_spec(), load_deck(profile))
    assert source.startswith(SKELETON_MARKER)
    path = tmp_path / "skeleton.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec())
    assert result.failed == 0, [i for i in result.invariants if i.status == "fail"]
    assert result.get("spec_conformance").status == "pass"
    assert result.get("reagent_budget").status == "pass"

    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    assert gate["success"] is True, gate.get("failure_message")


def test_skeleton_avoids_positions_the_workspace_occupies(profile):
    deck = load_deck(profile)
    assert ("WS_100ml_1", 1) not in deck.free_trough_sites  # FCA thru-deck waste chute
    source = build_skeleton(_spec(), deck)
    assert '"WS_100ml_1", 1)' not in source


def test_other_step_types_map_to_blocks_and_waits(profile, tmp_path):
    raw = {
        "title": "Reagent add and incubate",
        "sample_count": 96,
        "sample_volume_ul": 20,
        "reagents": [
            {"id": "S", "name": "Sample", "role": "sample"},
            {"id": "MM", "name": "Master mix", "role": "reagent"},
        ],
        "steps": [
            {"id": "s1", "op": "add", "text": "Add 10 ul master mix", "location": "deck",
             "reagent": "MM", "volume_ul": 10},
            {"id": "s2", "op": "incubate", "text": "Incubate 5 min", "location": "deck", "minutes": [5]},
            {"id": "s3", "op": "transfer", "text": "Transfer 25 ul to a new plate", "location": "deck",
             "volume_ul": 25},
            {"id": "s4", "op": "manual", "text": "Seal and store at 4 C", "location": "manual"},
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "distribute_reagent(" in source and "wt.wait(duration_seconds=300)" in source  # master mix: FCA
    assert "stamp(" in source and "offdeck_step(" in source
    path = tmp_path / "s.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec(raw))
    assert result.get("spec_conformance").status == "pass"


def test_reagentless_multi_cleanup_spec_gets_assumed_reagents_and_chains(profile, tmp_path):
    """Corpus drafts name no reagents; three chained clean-ups must still run."""
    raw = {
        "title": "Three clean-ups",
        "sample_count": 24,
        "reagents": [],
        "steps": [
            {"id": f"s{i}", "op": "bead_cleanup", "text": "Clean-up", "location": "deck",
             "volume_ul": 36.0 if i == 1 else None, "elute_ul": 30.0}
            for i in (1, 2, 3)
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert source.count("spri_cleanup(\n") == 3
    assert "ASSUMED (lab stock)" in source and "bead_volume_ul=36" in source
    assert source.count("MCA200Box(") <= 5  # shared reagent box, carried eluate tips
    path = tmp_path / "s.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec(raw))
    assert result.failed == 0, [i for i in result.invariants if i.status == "fail"]


def test_graph_offers_the_skeleton_as_the_current_draft(profile, tmp_path):
    from fluentvibe.authoring.graph import _declare_workflow_from_spec
    from fluentvibe.authoring.lab_scope import LabScope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = LabScope(mode="skills")
    prompt = "Automate it.\n\n" + spec_context_block(_spec())
    update = _declare_workflow_from_spec(registry, prompt)
    message = update["messages"][0].content
    assert "starting draft" in message and "```python" in message
    assert registry.last_draft_source.startswith(SKELETON_MARKER)
    result = registry.edit_draft(SKELETON_MARKER, SKELETON_MARKER)
    assert result["ok"] is True, result


_STREPTAVIDIN = {
    "title": "Streptavidin bead wash (primitives)",
    "sample_count": 96,
    "reagents": [
        {"id": "BEADS", "name": "Streptavidin beads", "role": "bead_carrier"},
        {"id": "BW", "name": "B&W buffer", "role": "wash"},
        {"id": "PROBE", "name": "Biotinylated probe", "role": "reagent"},
    ],
    "steps": [
        {"id": "s1", "op": "add", "text": "Beads", "location": "deck", "reagent": "BEADS", "volume_ul": 20,
         "proposed": ["volume_ul"]},
        {"id": "s2", "op": "separate", "text": "Magnet", "location": "deck", "engage": True, "minutes": [2]},
        {"id": "s3", "op": "remove", "text": "Discard", "location": "deck"},
        {"id": "s4", "op": "separate", "text": "Off", "location": "deck", "engage": False},
        {"id": "s5", "op": "add", "text": "Wash buffer", "location": "deck", "reagent": "BW", "volume_ul": 20},
        {"id": "s6", "op": "mix", "text": "Resuspend", "location": "deck", "cycles": 5},
        {"id": "s7", "op": "add", "text": "Probe", "location": "deck", "reagent": "PROBE", "volume_ul": 40},
        {"id": "s8", "op": "incubate", "text": "15 min rotation", "location": "off_deck", "minutes": [15]},
        {"id": "s9", "op": "separate", "text": "Magnet", "location": "deck", "engage": True},
        {"id": "s10", "op": "remove", "text": "Discard", "location": "deck"},
    ],
}


def test_bead_wash_as_primitives_is_not_forced_into_a_spri_cleanup(profile, tmp_path):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    source = build_skeleton(_spec(_STREPTAVIDIN), load_deck(profile))
    assert "spri_cleanup(" not in source and "Sample matrix" not in source
    assert "starts empty" in source
    for block in ("separate(", "remove_liquid(", "release(", "mix_wells(", "distribute_reagent("):
        assert block in source, block
    path = tmp_path / "s.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec(_STREPTAVIDIN))
    assert result.failed == 0, [i for i in result.invariants if i.status == "fail"]
    assert result.get("spec_conformance").status == "pass"
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    assert gate["success"] is True, gate.get("failure_message")


def test_spec_conformance_fails_when_a_primitive_has_no_effect(profile, tmp_path):
    import re

    source = build_skeleton(_spec(_STREPTAVIDIN), load_deck(profile))
    # Drop every remove and the probe addition (each call ends on its name= line).
    broken = re.sub(r"    remove_liquid\(.*?name=[^\n]*\n", "", source, flags=re.S)
    broken = re.sub(r"    distribute_reagent\(wt, source=probe_trough.*?name=[^\n]*\n", "", broken, flags=re.S)
    assert "remove_liquid(wt" not in broken and "source=probe_trough" not in broken
    result = score_protocol(broken, filename=str(tmp_path / "b.py"), spec=_spec(_STREPTAVIDIN))
    evidence = result.get("spec_conformance").evidence
    assert "nothing ever reaches waste" in evidence and "PROBE never leaves its source" in evidence


def test_open_values_are_questions_not_defaults(profile):
    from fluentvibe.authoring.skeleton import OpenValues

    raw = json.loads(json.dumps(_STREPTAVIDIN))
    raw["steps"][6]["volume_ul"] = None
    spec, problems = validate_bench_spec(raw)
    assert any(p.kind == "open" and "PROBE" in p.message for p in problems)
    with pytest.raises(OpenValues, match="how many µl per well"):
        build_skeleton(spec, load_deck(profile))


def test_custom_bead_step_is_left_for_review_not_mapped_to_spri(profile):
    raw = {
        "title": "Capture", "sample_count": 96, "sample_volume_ul": 20,
        "reagents": [{"id": "S", "name": "Sample", "role": "sample"},
                     {"id": "B", "name": "Beads", "role": "bead_carrier"}],
        "steps": [{"id": "s1", "op": "custom", "text": "Capture on streptavidin beads", "location": "deck",
                   "reagent": "B"}],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "spri_cleanup(" not in source and "TODO Capture on streptavidin beads" in source


def test_proposed_numbers_are_not_traced_to_the_document():
    raw = json.loads(json.dumps(_STREPTAVIDIN))
    spec, problems = validate_bench_spec(raw, source_text="Wash the beads. Add 40 µl probe, 15 minutes, 2 minutes, 5 times.")
    assert spec.steps[0].proposed == ["volume_ul"]
    assert not any(p.where.startswith("steps[0].volume_ul") for p in problems)
    assert any(p.where.startswith("steps[4].volume_ul") for p in problems)  # 20 µl wash: not in the text
