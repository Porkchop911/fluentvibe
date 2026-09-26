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


def test_offdeck_separation_runs_on_the_deck_magnet(profile):
    raw = json.loads(json.dumps(_STREPTAVIDIN))
    for step in raw["steps"]:
        if step["op"] == "separate":
            step["location"] = "off_deck"  # written for a hand-held magnet
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "separate(wt, plate=work, magnet=magnet" in source and "runs on the deck magnet" in source


def test_kit_reagent_beyond_its_supply_is_a_question(profile):
    from fluentvibe.authoring.skeleton import OpenValues

    raw = json.loads(json.dumps(_STREPTAVIDIN))
    raw["reagents"][0].update(supply_ul=2000, supply_count=1)
    raw["steps"][0]["volume_ul"] = 50
    with pytest.raises(OpenValues, match="BEADS is added at 4800 µl"):
        build_skeleton(_spec(raw), load_deck(profile))


class _ScriptedClient:
    def __init__(self, *arguments):
        self.replies = list(arguments)
        self.calls = []

    def complete(self, *, messages, tools):
        self.calls.append(messages)
        args = self.replies.pop(0)
        return {"content": "", "tool_calls": [{"function": {"name": "submit_bench_spec", "arguments": json.dumps(args)}}]}


def test_extraction_retries_an_empty_spec_and_drops_invented_supplies():
    from fluentvibe.authoring.bench_spec import extract_bench_spec

    good = json.loads(json.dumps(_STREPTAVIDIN))
    good["reagents"][1].update(supply_ul=4800, supply_count=1)  # not in the document: invented
    empty = {**good, "steps": []}
    client = _ScriptedClient(empty, good)
    spec, problems, raw = extract_bench_spec(client, "Streptavidin beads. B&W buffer. 2 minutes, 15 minutes.")
    assert spec is not None and len(client.calls) == 2
    assert "at least one step" in client.calls[1][-1]["content"]
    bw = next(r for r in spec.reagents if r.id == "BW")
    assert bw.supply_ul is None and any("treated as lab stock" in n for n in spec.notes)


def test_spec_path_asks_revises_and_builds(profile, tmp_path):
    from fluentvibe.authoring.spec_path import author_from_document

    first = json.loads(json.dumps(_STREPTAVIDIN))
    first["reagents"][0].update(supply_ul=2000, supply_count=1)
    first["steps"][0]["volume_ul"] = 50  # 50 ul x 96 > 2000 ul
    revised = json.loads(json.dumps(first))
    revised["steps"][0]["volume_ul"] = 20
    client = _ScriptedClient(first, revised)
    asked = []

    def ask(questions):
        asked.append(questions)
        return "Use 20 ul beads per well."

    result = author_from_document(client, "Streptavidin beads, 2000 µl. 2 minutes. 15 minutes.", profile,
                                  tmp_path / "out", ask=ask, examples=False)
    assert result.stage == "done", (result.stage, result.error)
    assert "BEADS is added at 4800" in asked[0][0]
    assert result.rounds[0]["answer"] == "Use 20 ul beads per well."
    assert "Use 20 ul beads per well." in client.calls[1][-1]["content"]
    assert result.gate["success"] is True


def test_spec_path_hands_questions_back_without_an_answer(profile, tmp_path):
    from fluentvibe.authoring.spec_path import author_from_document

    first = json.loads(json.dumps(_STREPTAVIDIN))
    first["steps"][6]["volume_ul"] = None  # probe volume open
    result = author_from_document(_ScriptedClient(first), "Streptavidin beads.", profile, tmp_path / "out",
                                  ask=lambda questions: None, examples=False)
    assert result.stage == "questions" and "PROBE" in result.open_questions[0]


_SPRI_PRIMITIVES = {
    "title": "PCR clean-up as primitives",
    "sample_count": 96,
    "sample_volume_ul": 20,
    "reagents": [
        {"id": "PCR", "name": "PCR product", "role": "sample"},
        {"id": "AXP", "name": "AMPure XP beads", "role": "bead_carrier"},
        {"id": "ETOH", "name": "70% ethanol", "role": "wash", "liquid_type": "ethanol"},
        {"id": "EB", "name": "Elution buffer", "role": "eluent"},
    ],
    "steps": [
        {"id": "s1", "op": "add", "text": "Beads 1.8x", "location": "deck", "reagent": "AXP", "volume_ul": 36},
        {"id": "s2", "op": "mix", "text": "Mix", "location": "deck", "cycles": 10},
        {"id": "s3", "op": "incubate", "text": "Bind 5 min", "location": "deck", "minutes": [5]},
        {"id": "s4", "op": "separate", "text": "Magnet", "location": "deck", "engage": True, "minutes": [2]},
        {"id": "s5", "op": "remove", "text": "Discard supernatant", "location": "deck"},
        {"id": "s6", "op": "add", "text": "Ethanol", "location": "deck", "reagent": "ETOH", "volume_ul": 200},
        {"id": "s7", "op": "remove", "text": "Discard ethanol", "location": "deck"},
        {"id": "s8", "op": "separate", "text": "Off", "location": "deck", "engage": False},
        {"id": "s9", "op": "add", "text": "Elution buffer", "location": "deck", "reagent": "EB", "volume_ul": 40},
        {"id": "s10", "op": "mix", "text": "Resuspend", "location": "deck", "cycles": 10},
        {"id": "s11", "op": "separate", "text": "Magnet", "location": "deck", "engage": True},
        {"id": "s12", "op": "transfer", "text": "Eluate to a new plate", "location": "deck", "volume_ul": 38},
    ],
}


def test_spri_written_as_primitives_is_checked_in_simulation(profile, tmp_path):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    source = build_skeleton(_spec(_SPRI_PRIMITIVES), load_deck(profile))
    assert "spri_cleanup(" not in source
    path = tmp_path / "s.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec(_SPRI_PRIMITIVES))
    assert result.failed == 0, [i for i in result.invariants if i.status == "fail"]
    for key in ("magnet_roundtrip", "eluate_recovered", "analyte_not_in_waste", "spec_conformance"):
        assert result.get(key).status == "pass", key
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    assert gate["success"] is True, gate.get("failure_message")  # bound marker is not over-drawn


def test_missing_sample_volume_is_a_question(profile):
    from fluentvibe.authoring.skeleton import OpenValues

    raw = json.loads(json.dumps(_SPRI_PRIMITIVES))
    raw["sample_volume_ul"] = None
    with pytest.raises(OpenValues, match="volume per well is not given"):
        build_skeleton(_spec(raw), load_deck(profile))


def test_room_temperature_incubation_is_a_wait_and_a_warm_one_goes_to_the_operator(profile):
    raw = json.loads(json.dumps(_SPRI_PRIMITIVES))
    raw["steps"][2]["temp_c"] = [25]  # "room temperature (25 C)"
    raw["steps"].insert(3, {"id": "s3b", "op": "incubate", "text": "37 C 30 min", "location": "deck",
                            "temp_c": [37], "minutes": [30]})
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "wt.wait(duration_seconds=300)  # room temperature" in source
    assert "TODO" not in source
    assert "37 C incubation handed to the operator" in source and "offdeck_step(wt, \"37 C 30 min\"" in source


@pytest.mark.parametrize("samples,columns", [(24, [1, 2, 3]), (20, [1, 2, 3])])
def test_partial_plate_pipettes_only_the_used_columns(profile, tmp_path, samples, columns):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    raw = json.loads(json.dumps(_SPRI_PRIMITIVES))
    raw["sample_count"] = samples
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert f"columns={columns}" in source and "fill_all(Reagent(\"PCR product matrix\")" not in source
    assert (f"first_wells({samples})" in source)
    assert ("Blank (unused well" in source) == (samples % 8 != 0)
    path = tmp_path / "s.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec(raw))
    assert result.failed == 0, [i for i in result.invariants if i.status == "fail"]
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    assert gate["success"] is True, gate.get("failure_message")
    # The eluate lands in columns 1-3 only; columns 4-12 stay empty.
    from fluentvibe.authoring.eval_rubric import _iter_labware, build_worktable_from_source

    wt = build_worktable_from_source(source, str(path))
    wt.simulate()
    eluate = next(lw for lw in _iter_labware(wt.snapshots[-1]) if lw.label.startswith("s12_"))
    assert all(eluate.well(f"{r}{c}").layers for r in "ABCDEFGH" for c in columns)
    assert not any(eluate.well(f"{r}{c}").layers for r in "ABCDEFGH" for c in range(4, 13))


def test_partial_plate_spri_macro(profile, tmp_path):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    raw = {
        "title": "24-sample clean-up", "sample_count": 24, "sample_volume_ul": 20,
        "reagents": [{"id": "S", "name": "Sample", "role": "sample"}],
        "steps": [{"id": "s1", "op": "bead_cleanup", "text": "Clean-up", "location": "deck",
                   "ratio": 1.8, "elute_ul": 30}],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "spri_cleanup(" in source and "columns=[1, 2, 3]" in source
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    assert gate["success"] is True, gate.get("failure_message")


def test_liha_per_channel_wells_render_like_fluentcontrol(profile, tmp_path):
    """Encoding copied from FluentControl reference scripts: one column-major
    well index per channel; the display string is a range, a repeat or a list."""
    import html
    import re

    from fluentvibe import FCA200Box, Plate96, Reagent, Worktable

    deck = load_deck(profile)
    wt = Worktable.from_workspace(deck.workspace_name, workspace_guid=deck.workspace_guid, auto_place=False)
    wt.group("Setup")
    src = wt.place(Plate96("Src", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dst = wt.place(Plate96("Dst", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(FCA200Box("Tips", catalog="FCA, 200ul SBS"), "Nest61mm_Pos", 3)
    src.fill_wells(src.first_wells(8), Reagent("S"), 20)
    wt.group("Pool")
    wt.liha.get_tips(tips)
    wt.liha.aspirate(src, 5, liquid_class="Water Free Single", wells=["A1", "B1", "C1"])
    wt.liha.dispense(dst, 5, liquid_class="Water Free Single", wells=["A1"] * 3)
    wt.liha.drop_tips()
    wt.simulate(strict=True)
    final = wt.snapshots[-1]
    assert final.labware("Dst").well("A1").volume_ul == pytest.approx(15.0)
    assert final.labware("Src").well("D1").volume_ul == pytest.approx(20.0)
    xml = html.unescape(html.unescape(wt.compile(tmp_path / "p.xscr").read_text(encoding="utf-8")))
    aspirate = xml[xml.index("LihaAspirateScriptCommandData"):]
    assert "<SerializedWellIndexes>0;1;2;</SerializedWellIndexes>" in aspirate[:12000]
    assert "<SelectedWellsString>A1 - C1</SelectedWellsString>" in aspirate[:12000]
    dispense = xml[xml.index("LihaDispenseScriptCommandData"):]
    assert "<SerializedWellIndexes>0;0;0;</SerializedWellIndexes>" in dispense[:12000]
    assert "<SelectedWellsString>3 * A1</SelectedWellsString>" in dispense[:12000]
    tips_block = re.search(r"<SelectedTipsIndexes>(.*?)</SelectedTipsIndexes>", dispense, re.S).group(1)
    assert re.findall(r"<int>(\d+)</int>", tips_block) == ["0", "1", "2"]


def test_few_samples_pool_into_one_well(profile, tmp_path):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    raw = {
        "title": "Pool 3 libraries", "sample_count": 3, "sample_volume_ul": 20,
        "reagents": [{"id": "L", "name": "Library", "role": "sample"}],
        "steps": [{"id": "s1", "op": "pool", "text": "Pool 10 ul of each", "location": "deck", "volume_ul": 10}],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "pool_wells(" in source and "first_wells(3)" in source
    path = tmp_path / "p.py"
    path.write_text(source, encoding="utf-8")
    result = score_protocol(source, filename=str(path), spec=_spec(raw))
    assert result.get("spec_conformance").status == "pass", result.get("spec_conformance").evidence
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    assert registry.compile_and_simulate(source)["success"] is True


def test_request_head_and_liquid_class_variables_survive_the_spec_path(profile, tmp_path):
    """The two instructions the spec path used to drop: ethanol via the FCA and
    liquid classes as string variables (checked with the requirements ledger)."""
    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.requirements import Requirement, verify

    raw = json.loads(json.dumps(_SPRI_PRIMITIVES))
    raw["liquid_class_variables"] = True
    assert raw["steps"][5]["reagent"] == "ETOH" and raw["steps"][6]["op"] == "remove"
    raw["steps"][5]["head"] = "fca"                       # s6 ethanol wash
    raw["steps"][5]["volume_ul"] = 100                    # fits a 25 ml trough; the second wash does not
    raw["steps"].insert(7, {"id": "s7b", "op": "add", "text": "Ethanol again", "location": "deck",
                            "reagent": "ETOH", "volume_ul": 150, "head": "fca"})
    raw["steps"].insert(8, {"id": "s7c", "op": "remove", "text": "Discard", "location": "deck"})
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert source.count("_trough") >= 3 and "ETOH_trough_2" in source   # 25 ml overflowed -> second trough
    path = tmp_path / "s.py"
    path.write_text(source, encoding="utf-8")
    wt = build_worktable_from_source(source, str(path))
    wt.simulate(strict=True)
    verdicts = {v.id: v for v in verify(wt, [
        Requirement("R1", "ethanol via the FCA", "head_for_reagent", {"reagent": "ethanol", "head": "fca"}),
        Requirement("R2", "liquid classes as string variables", "liquid_class_variables",
                    {"default": "Water Free Single", "mix_default": "Water Mix"}),
    ])}
    assert all(v.status == "pass" for v in verdicts.values()), {k: v.evidence for k, v in verdicts.items()}


def test_spec_parses_head_and_liquid_class_variables():
    raw = json.loads(json.dumps(_SPRI_PRIMITIVES))
    raw["liquid_class_variables"] = True
    raw["steps"][0]["head"] = "FCA"
    raw["steps"][1]["head"] = "robot arm"                 # not a head: ignored
    spec = _spec(raw)
    assert spec.liquid_class_variables and spec.steps[0].head == "fca" and spec.steps[1].head is None


def test_repeat_unrolls_a_wash_block(profile, tmp_path):
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    raw = json.loads(json.dumps(_STREPTAVIDIN))
    # s2..s6 is one wash (magnet, discard, off, buffer, resuspend): run it twice more
    raw["steps"].insert(6, {"id": "r1", "op": "repeat", "text": "Two more washes", "location": "deck",
                            "first_step": "s2", "last_step": "s6", "times": 2})
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "TODO" not in source
    # magnet on: the wash (s2) three times plus the final one (s9); magnet off: three times
    assert source.count("    separate(wt") == 4 and source.count("    release(wt") == 3 and '"s4-3: Off"' in source
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.lab_scope = load_lab_scope("skills")
    assert registry.compile_and_simulate(source)["success"] is True


def test_repeat_of_unknown_steps_is_a_question(profile):
    from fluentvibe.authoring.skeleton import OpenValues

    raw = json.loads(json.dumps(_STREPTAVIDIN))
    raw["steps"].append({"id": "r1", "op": "repeat", "text": "again", "location": "deck",
                         "first_step": "nope", "last_step": "s3", "times": 1})
    with pytest.raises(OpenValues, match="repeat 'r1' needs"):
        build_skeleton(_spec(raw), load_deck(profile))
