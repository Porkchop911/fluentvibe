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
    with pytest.raises(OpenValues, match="How many µl of"):
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


def test_a_broken_first_spec_is_not_sent_back_as_a_tool_call():
    """Dynabeads: the first call's arguments were invalid JSON; sending them
    back in the retry's history made vLLM reject it (HTTP 400 'Expecting
    value: line 1 column 866'). The retry says what failed, in plain text."""
    from fluentvibe.authoring.bench_spec import extract_bench_spec

    good = json.loads(json.dumps(_STREPTAVIDIN))

    class Broken:
        calls = []

        def complete(self, *, messages, tools):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return {"content": "", "tool_calls": [{"function": {"name": "submit_bench_spec",
                                                                    "arguments": '{"title": "x", "steps": [,'}}]}
            return {"content": "", "tool_calls": [{"function": {"name": "submit_bench_spec",
                                                                "arguments": json.dumps(good)}}]}

    client = Broken()
    spec, problems, raw = extract_bench_spec(client, "Streptavidin beads. B&W buffer. 2 minutes, 15 minutes.")
    assert spec is not None and len(client.calls) == 2
    assert not any(m.get("tool_calls") for m in client.calls[1])
    json.dumps(client.calls[1])   # the retry request is plain, valid JSON


def test_spec_path_asks_revises_and_builds(profile, tmp_path):
    from fluentvibe.authoring.spec_path import ACCEPT, author_from_document

    first = json.loads(json.dumps(_STREPTAVIDIN))
    first["reagents"][0].update(supply_ul=2000, supply_count=1)
    first["steps"][0]["volume_ul"] = 50  # 50 ul x 96 > 2000 ul
    revised = json.loads(json.dumps(first))
    revised["steps"][0]["volume_ul"] = 20
    client = _ScriptedClient(first, revised)
    asked = []

    def ask(questions):
        if "(assumed)" in questions[0]:
            return ACCEPT                     # the confirm round: keep the assumptions
        asked.append(questions)
        return "Use 20 ul beads per well."

    result = author_from_document(client, "Streptavidin beads, 2000 µl. 2 minutes. 15 minutes.", profile,
                                  tmp_path / "out", ask=ask, examples=False)
    assert result.stage == "done", (result.stage, result.error)
    assert "BEADS is added at 4800" in asked[0][0]
    assert result.rounds[-1]["answer"].startswith("Use 20 ul beads per well.")
    assert "Use 20 ul beads per well." in client.calls[1][-1]["content"]
    assert result.gate["success"] is True


def test_spec_path_hands_questions_back_without_an_answer(profile, tmp_path):
    from fluentvibe.authoring.spec_path import author_from_document

    first = json.loads(json.dumps(_STREPTAVIDIN))
    first["steps"][6]["volume_ul"] = None  # probe volume open
    from fluentvibe.authoring.spec_path import ACCEPT

    result = author_from_document(_ScriptedClient(first), "Streptavidin beads.", profile, tmp_path / "out",
                                  ask=lambda questions: ACCEPT if "(assumed)" in questions[0] else None,
                                  examples=False)
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


def test_add_without_a_reagent_is_a_question(profile):
    from fluentvibe.authoring.skeleton import OpenValues

    raw = json.loads(json.dumps(_STREPTAVIDIN))
    raw["steps"].append({"id": "s11", "op": "add", "text": "Resuspend in buffer", "location": "deck", "volume_ul": 20})
    with pytest.raises(OpenValues, match="which reagent or buffer"):
        build_skeleton(_spec(raw), load_deck(profile))


def test_document_sequence_catches_a_missing_wait_and_a_skipped_step(profile, tmp_path):
    import re

    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.requirements import requirements_from_spec, verify_all

    spec = _spec(_SPRI_PRIMITIVES)
    (req,) = requirements_from_spec(spec)
    source = build_skeleton(spec, load_deck(profile))

    def verdict(src):
        wt = build_worktable_from_source(src, str(tmp_path / "d.py"))
        wt.simulate()
        return verify_all(wt, [req])[0]

    assert verdict(source).status == "pass"
    no_wait = re.sub(r"    wt\.wait\(duration_seconds=300\)[^\n]*\n", "    wt.add_comment('bind 5 min')\n", source, count=1)
    assert "duration_seconds=300" not in no_wait
    missing = verdict(no_wait)
    assert missing.status == "fail" and "300 s" in missing.evidence
    no_transfer = re.sub(r"    stamp\(.*?name=[^\n]*\n", "", source, count=1, flags=re.S)
    assert verdict(no_transfer).status == "fail"


def test_do_this_protocol_from_the_request_is_checked_on_the_document_steps(profile, tmp_path):
    """'for DNA immobilisation' in the request names the document's protocol:
    it is checked on the document's steps, not left as 'not checkable'."""
    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.requirements import Requirement, requirements_from_spec, verify_all

    spec = _spec(_SPRI_PRIMITIVES)
    asked = Requirement("req_1", "for SPRI clean-up", "document_sequence", {"protocol": "SPRI"})
    wt = build_worktable_from_source(build_skeleton(spec, load_deck(profile)), str(tmp_path / "d.py"))
    wt.simulate()
    verdicts = verify_all(wt, [asked, *requirements_from_spec(spec)])
    assert [v.status for v in verdicts] == ["pass", "pass"]


def test_named_volumes_carry_a_change_through(profile, tmp_path):
    """Editing one per-well volume in the Python (e.g. with Ctrl+I) must not
    break the removals and mixes that depend on it."""
    import re

    from fluentvibe.copilot.analyzer import analyze_source

    source = build_skeleton(_spec(_SPRI_PRIMITIVES), load_deck(profile))
    assert 'SAMPLE_UL = wt.volume("SAMPLE_UL", 20)' in source and "SAMPLE_UL + S1_AXP_UL" in source
    path = tmp_path / "d.py"
    for old, new in (('"S1_AXP_UL", 36)', '"S1_AXP_UL", 30)'), ('"SAMPLE_UL", 20)', '"SAMPLE_UL", 40)')):
        edited = source.replace(old, new)
        assert edited != source
        assert not [d for d in analyze_source(edited, path) if d.severity == "error"], old


class _TextClient:
    def __init__(self, text):
        self.text = text

    def complete(self, *, messages, tools):
        return {"content": self.text}


def test_custom_steps_get_model_code_only_when_it_is_clean(profile, tmp_path):
    from fluentvibe.authoring.spec_path import fill_custom_steps

    raw = json.loads(json.dumps(_STREPTAVIDIN))
    raw["steps"].insert(7, {"id": "c1", "op": "custom", "text": "Let the probe bind for one minute on the deck",
                            "location": "deck"})
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert 'wt.add_comment("TODO' in source
    path = str(tmp_path / "d.py")
    filled, n_filled, n_left = fill_custom_steps(_TextClient("    wt.wait(duration_seconds=60)"), source, path)
    assert (n_filled, n_left) == (1, 0) and "TODO" not in filled and "wt.wait(duration_seconds=60)" in filled
    broken, n_filled, n_left = fill_custom_steps(_TextClient("    wt.wait(duration_seconds=60"), source, path)
    assert (n_filled, n_left) == (0, 1) and broken == source


def test_per_well_volumes_normalise_a_plate(profile, tmp_path):
    """Normalisation: a different buffer volume per well, then a different sample
    volume per well, each well on the channel of its row."""
    import html
    import re

    from fluentvibe import FCA200Box, Plate96, Reagent, Trough25mL, Worktable
    from fluentvibe.blocks import distribute_volumes, transfer_volumes

    deck = load_deck(profile)
    wt = Worktable.from_workspace(deck.workspace_name, workspace_guid=deck.workspace_guid, auto_place=False)
    wt.group("Setup")
    stock = wt.place(Plate96("Stock", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    norm = wt.place(Plate96("Norm", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    buffer = wt.place(Trough25mL("Buffer", catalog="25ml_short"), "WS_100ml_1", 2)
    tips = wt.place(FCA200Box("Tips", catalog="FCA, 200ul SBS"), "Nest61mm_Pos", 3)
    sample_tips = wt.place(FCA200Box("SampleTips", catalog="FCA, 200ul SBS"), "Nest61mm_Pos", 4)
    stock.fill_all(Reagent("DNA"), 50)
    buffer.fill_all(Reagent("TE"), 10000)
    buffer_ul = {"C1": 10.0, "E1": 25.0, "A2": 40.0, "B2": 5.0}
    sample_ul = {"C1": 20.0, "E1": 5.0, "A2": 250.0 / 2, "B2": 30.0}
    distribute_volumes(wt, source=buffer, plate=norm, volumes=buffer_ul, tips=tips,
                       liquid_class="Water Free Single", name="Buffer")
    transfer_volumes(wt, source=stock, dest=norm, volumes={w: min(v, 45.0) for w, v in sample_ul.items()},
                     tips=sample_tips, liquid_class="Water Free Single", name="Samples")
    wt.simulate(strict=True)
    final = wt.snapshots[-1].labware("Norm")
    for well in buffer_ul:
        assert final.well(well).volume_ul == pytest.approx(buffer_ul[well] + min(sample_ul[well], 45.0))
    assert final.well("D1").volume_ul == 0
    xml = html.unescape(html.unescape(wt.compile(tmp_path / "n.xscr").read_text(encoding="utf-8")))
    dispense = xml[xml.index("LihaDispenseScriptCommandData"):][:12000]
    assert "<SelectedWellsString>C1, E1</SelectedWellsString>" in dispense
    tips_block = re.search(r"<SelectedTipsIndexes>(.*?)</SelectedTipsIndexes>", dispense, re.S).group(1)
    assert re.findall(r"<int>(\d+)</int>", tips_block) == ["2", "4"]          # rows C and E
    volumes = re.search(r"<Volumes>(.*?)</Volumes>", dispense, re.S).group(1)
    assert re.findall(r"<string>([^<]*)</string>", volumes) == ["0", "0", "10", "0", "25", "0", "0", "0"]


def test_volumes_are_a_fluentcontrol_variables_block(profile, tmp_path):
    """Volumes are FluentControl variables in a group at the top; dependent ones
    are Set Variable expressions, and every step references a variable."""
    import re

    source = build_skeleton(_spec(_SPRI_PRIMITIVES), load_deck(profile))
    namespace: dict = {}
    exec(compile(source, str(tmp_path / "d.py"), "exec"), namespace)
    wt = namespace["build_worktable"]()
    variables = wt.to_protocol().groups[0]
    assert variables.name == "Variables"
    derived = {s.variable_name: s.value for s in variables.steps if getattr(s, "expression", False)}
    assert derived and all("SAMPLE_UL" in v or "_UL" in v for v in derived.values())
    out = tmp_path / "d.xscr"
    wt.compile(str(out))
    text = out.read_text(encoding="utf-8")
    name, expr = next(iter(derived.items()))
    assert f"<Name>{name}</Name><Value>{expr}</Value>" in text      # unquoted expression
    volumes = set(re.findall(r"<Volume>([^<]*)</Volume>", text))
    assert volumes and not [v for v in volumes if re.fullmatch(r"[0-9.]+", v)]


def test_simulation_marker_is_zero_on_the_instrument(profile, tmp_path):
    """The analyte marker is a simulator device: FluentControl volumes must be
    the bench volumes (marker 0), the simulator's must include it."""
    source = build_skeleton(_spec(_SPRI_PRIMITIVES), load_deck(profile))
    namespace: dict = {}
    exec(compile(source, str(tmp_path / "d.py"), "exec"), namespace)
    wt = namespace["build_worktable"]()
    assert wt.protocol_variables["SIM_ANALYTE_UL"] == 0 and wt.sim_values["SIM_ANALYTE_UL"] > 0
    assert "SIM_ANALYTE_UL" in source and " - 2 " not in source.split('wt.group("Labware Placement")')[0]
    wt.simulate()


def test_spec_path_confirms_assumptions_first(profile, tmp_path):
    """The values the model assumed are shown for confirmation before building;
    keeping them costs no model call, a change revises the spec once."""
    from fluentvibe.authoring.spec_path import ACCEPT, author_from_document

    spec = json.loads(json.dumps(_STREPTAVIDIN))
    assert any(step.get("proposed") for step in spec["steps"])
    kept_client = _ScriptedClient(spec)
    asked = []
    kept = author_from_document(kept_client, "Streptavidin beads.", profile, tmp_path / "a",
                                ask=lambda q: asked.append(q) or ACCEPT, examples=False)
    assert kept.stage == "done" and "(assumed)" in asked[0][0] and len(kept_client.calls) == 1

    changed = json.loads(json.dumps(spec))
    changed["steps"][0]["volume_ul"] = 30
    client = _ScriptedClient(spec, changed)
    result = author_from_document(client, "Streptavidin beads.", profile, tmp_path / "b",
                                  ask=lambda q: f"- {q[0]}\n  answer: 30", examples=False)
    assert result.stage == "done" and len(client.calls) == 2
    assert "answer: 30" in client.calls[1][-1]["content"] and result.spec.steps[0].volume_ul == 30


class _UnderstandingThenSpec(_ScriptedClient):
    """First call: the model states its understanding; then scripted specs."""

    def __init__(self, understood, questions, *specs):
        super().__init__(*specs)
        self.understanding = {"understood": understood, "questions": questions}

    def complete(self, *, messages, tools):
        if tools and tools[0]["function"]["name"] == "state_understanding":
            self.calls.append(messages)
            return {"content": "", "tool_calls": [{"function": {
                "name": "state_understanding", "arguments": json.dumps(self.understanding)}}]}
        return super().complete(messages=messages, tools=tools)


def test_spec_path_states_its_understanding_before_the_long_read(profile, tmp_path):
    """Generation takes minutes, so the request is confirmed first; the user's
    answer goes into the spec prompt and the instruction checklist."""
    from fluentvibe.authoring.spec_path import ACCEPT, author_from_document

    spec = json.loads(json.dumps(_STREPTAVIDIN))
    asked = []

    def ask(questions):
        asked.append(questions)
        if questions[0].startswith("I understood:"):
            return "2: 48 samples"
        return ACCEPT

    client = _UnderstandingThenSpec("Streptavidin capture of 96 samples on the magnet.",
                                    ["Which procedure?", "How many samples?"], spec)
    result = author_from_document(client, "Streptavidin beads.", profile, tmp_path / "a", request="dna",
                                  ask=ask, examples=False, understand=True)
    assert asked[0][0] == "I understood: Streptavidin capture of 96 samples on the magnet."
    assert asked[0][1:] == ["Which procedure?", "How many samples?"]
    spec_prompt = client.calls[1][-1]["content"]
    assert "The user answered: 2: 48 samples" in spec_prompt
    assert result.rounds[0]["kind"] == "understand"
    # Asked once, up front: the assumptions are listed with the result, not asked again.
    assert len(asked) == 1 and result.stage == "done"
    assert result.summary()["assumed"], "the model's own choices are shown with the result"

    stopped = author_from_document(_UnderstandingThenSpec("x", [], spec), "Streptavidin beads.", profile,
                                   tmp_path / "b", ask=lambda q: None, examples=False, understand=True)
    assert stopped.stage == "questions" and stopped.spec is None


def test_a_second_read_of_the_same_document_reuses_the_spec(profile, tmp_path):
    """Same document, request and answers: the spec is reused, no model call."""
    from fluentvibe.authoring.spec_path import author_from_document

    spec = json.loads(json.dumps(_STREPTAVIDIN))
    cache = tmp_path / "spec_cache.json"
    first = _ScriptedClient(spec)
    author_from_document(first, "Streptavidin beads.", profile, tmp_path / "a", examples=False, spec_cache=cache)
    notes = []
    again = _ScriptedClient()                       # no replies: any model call would fail
    result = author_from_document(again, "Streptavidin beads.", profile, tmp_path / "b", examples=False,
                                  spec_cache=cache, progress=notes.append)
    assert result.stage == "done" and not again.calls
    assert any("reused the spec" in n for n in notes)
    changed = _ScriptedClient(spec)
    author_from_document(changed, "Streptavidin beads, 2 minutes.", profile, tmp_path / "c", examples=False,
                         spec_cache=cache)
    assert len(changed.calls) == 1                  # another document: read again


def test_a_transfer_into_a_still_empty_plate_adds_the_liquid(profile, tmp_path):
    """Seen on Dynabeads: "Transfer 25 µl bead suspension to each well" written
    as a transfer while the plate is still empty built a -1 µl transfer."""
    raw = {
        "title": "Bead wash", "starts_empty": True, "sample_count": 96,
        "reagents": [{"id": "beads", "name": "Dynabeads M-280", "role": "bead_carrier"},
                     {"id": "bw1x", "name": "1X B&W buffer", "role": "wash"}],
        "steps": [
            {"id": "t1", "op": "transfer", "text": "Transfer 25 µl bead suspension to each well",
             "location": "deck", "volume_ul": 25},
            {"id": "a1", "op": "add", "text": "Add 100 µl 1X B&W buffer", "location": "deck",
             "reagent": "bw1x", "volume_ul": 100},
            {"id": "m1", "op": "separate", "text": "Place on the magnet for 2 min", "location": "deck",
             "minutes": [2]},
            {"id": "r1", "op": "remove", "text": "Discard the supernatant", "location": "deck"},
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "0 - 1" not in source and "(-1" not in source
    namespace: dict = {}
    exec(compile(source, str(tmp_path / "d.py"), "exec"), namespace)
    wt = namespace["build_worktable"]()
    wt.simulate()

    from fluentvibe.authoring.skeleton import OpenValues

    raw["reagents"] = raw["reagents"][1:]
    raw["steps"][0]["text"] = "Transfer 25 µl to each well"
    with pytest.raises(OpenValues, match="still empty"):
        build_skeleton(_spec(raw), load_deck(profile))


def test_repeats_become_native_loops_when_every_pass_is_the_same(profile, tmp_path):
    """A wash repeated 3 times is one FluentControl loop with its count as a
    variable, not 3 copies; it simulates like the copies."""
    raw = {
        "title": "Bead wash", "sample_count": 96, "sample_volume_ul": 20,
        "reagents": [{"id": "dna", "name": "DNA", "role": "sample"},
                     {"id": "bw", "name": "1X B&W buffer", "role": "wash"}],
        "steps": [
            {"id": "w_add", "op": "add", "text": "Add 100 µl buffer", "location": "deck", "reagent": "bw",
             "volume_ul": 100, "head": "mca"},
            {"id": "w_mag", "op": "separate", "text": "Magnet 2 min", "location": "deck", "minutes": [2]},
            {"id": "w_rem", "op": "remove", "text": "Discard the buffer", "location": "deck", "residual_ul": 5},
            {"id": "w_off", "op": "separate", "text": "Off the magnet", "location": "deck", "engage": False},
            {"id": "rep", "op": "repeat", "text": "Repeat the wash 2 more times", "location": "deck",
             "first_step": "w_add", "last_step": "w_off", "times": 2},
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    # The first wash also removes the sample: it differs, so it stays in front
    # of the loop, which runs the two identical washes after it.
    assert source.count("wt.loop(") == 1 and 'wt.declare_variable("REP_TIMES", 2)' in source
    assert "w_add-3" not in source and "W_ADD_3" not in source
    namespace: dict = {}
    exec(compile(source, str(tmp_path / "d.py"), "exec"), namespace)
    wt = namespace["build_worktable"]()
    wt.simulate()
    # The same wells after three passes as the unrolled copies would leave.
    well = wt.snapshots[-1].labware("Samples").well("A1")
    assert abs(sum(layer.volume_ul for layer in well.layers) - 5) < 1.0

    # When every pass is the same (the first starts from the residual too), all of them loop.
    raw["steps"].insert(0, {"id": "pre", "op": "remove", "text": "Remove the sample", "location": "deck",
                            "residual_ul": 5})
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert 'wt.declare_variable("REP_TIMES", 3)' in source and "W_ADD_2" not in source
    namespace = {}
    exec(compile(source, str(tmp_path / "e.py"), "exec"), namespace)
    namespace["build_worktable"]().simulate()


def test_steps_after_pooling_are_one_operator_hand_off(profile, tmp_path):
    """ONT rapid barcoding: after pooling 48 barcoded samples the clean-up and
    adapter steps are tube work. They go to the operator in one hand-off that
    says where the pool is, not to TODOs for the model (which took 48 min)."""
    raw = {
        "title": "Rapid barcoding", "sample_count": 48, "sample_volume_ul": 9,
        "reagents": [{"id": "dna", "name": "Amplicon DNA", "role": "sample"},
                     {"id": "rb", "name": "Rapid Barcodes", "role": "per_sample"},
                     {"id": "axp", "name": "AMPure XP", "role": "bead_carrier", "supply_ul": 2400},
                     {"id": "ra", "name": "Rapid Adapter", "role": "reagent", "supply_ul": 15}],
        "steps": [
            {"id": "bc", "op": "add", "text": "Add 1 µl Rapid Barcode", "location": "deck", "reagent": "rb",
             "volume_ul": 1},
            {"id": "pool", "op": "pool", "text": "Pool all barcoded samples", "location": "deck", "volume_ul": 10},
            {"id": "axp", "op": "add", "text": "Add an equal volume of AXP to the pool", "location": "deck",
             "reagent": "axp", "volume_ul": 480},
            {"id": "mag", "op": "separate", "text": "Pellet on a magnet", "location": "deck", "minutes": [2]},
            {"id": "ra", "op": "add", "text": "Add 1 µl diluted RA", "location": "deck", "reagent": "ra",
             "volume_ul": 1.5},
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))   # RA 1.5 µl once, not 48 x
    assert "TODO" not in source
    hand_offs = [line for line in source.splitlines() if "offdeck_step(" in line]
    assert len(hand_offs) == 1 and "column 1 of" in hand_offs[0] and "equal volume of AXP" in hand_offs[0]
    namespace: dict = {}
    exec(compile(source, str(tmp_path / "d.py"), "exec"), namespace)
    wt = namespace["build_worktable"]()
    wt.simulate()
    # The checklist does not expect the operator's steps on the deck.
    from fluentvibe.authoring.requirements import requirements_from_spec, verify_all

    assert verify_all(wt, requirements_from_spec(_spec(raw)))[0].status == "pass"


def test_samples_added_later_come_from_a_sample_plate(profile, tmp_path):
    """Dynabeads: beads first, then each sample's DNA. 96 samples are 96
    liquids: a sample plate stamped 1:1, not one trough into every well --
    also when the spec forgot to mark the DNA as the samples."""
    from fluentvibe.authoring.requirements import Requirement, verify_all

    raw = {
        "title": "Immobilize DNA", "sample_count": 96, "starts_empty": True,
        "reagents": [{"id": "beads", "name": "Dynabeads M-280", "role": "bead_carrier"},
                     {"id": "bw", "name": "2X B&W buffer", "role": "wash"},
                     {"id": "dna", "name": "Biotinylated DNA in water", "role": "reagent"}],
        "steps": [
            {"id": "b", "op": "add", "text": "Add 10 µl beads", "location": "deck", "reagent": "beads",
             "volume_ul": 10},
            {"id": "w", "op": "add", "text": "Add 10 µl 2X B&W", "location": "deck", "reagent": "bw",
             "volume_ul": 10},
            {"id": "d", "op": "add", "text": "Add 20 µl DNA", "location": "deck", "reagent": "dna",
             "volume_ul": 20},
            {"id": "m", "op": "separate", "text": "Magnet 2 min", "location": "deck", "minutes": [2]},
            {"id": "r", "op": "remove", "text": "Discard the supernatant", "location": "deck", "residual_ul": 5},
        ],
    }
    for role in ("reagent", "sample"):
        raw["reagents"][2]["role"] = role
        source = build_skeleton(_spec(raw), load_deck(profile))
        assert "dna_Plate" in source and "dna_trough" not in source, role
        namespace: dict = {}
        exec(compile(source, str(tmp_path / f"{role}.py"), "exec"), namespace)
        wt = namespace["build_worktable"]()
        wt.simulate()
        [verdict] = verify_all(wt, [Requirement(id="n", text="96 samples", kind="sample_count",
                                                params={"count": 96})])
        assert verdict.status == "pass", verdict.evidence


def test_an_operator_incubation_counts_as_waiting():
    from fluentvibe.authoring.requirements import _stated_seconds

    assert _stated_seconds("Incubate 15 min at room temperature using gentle rotation.") == 900
    assert _stated_seconds("30 °C for 2 minutes, then 80 °C for 2 minutes") == 240
    assert _stated_seconds("Spin down briefly") == 0


def test_normalisation_asks_for_the_sheet_and_takes_it_from_the_chat(profile, tmp_path):
    """The concentrations are the user's data: asked for, parsed, never sent
    to the model; every normalised well ends at the target volume."""
    from fluentvibe.authoring.spec_path import author_from_document

    raw = {
        "title": "Rapid barcoding", "sample_count": 8,
        "reagents": [{"id": "dna", "name": "Amplicon DNA", "role": "sample"},
                     {"id": "water", "name": "Nuclease-free water", "role": "reagent"}],
        "steps": [{"id": "norm", "op": "normalize", "text": "50 ng in 9 ul per sample", "location": "deck",
                   "reagent": "water", "target_ng": 50, "volume_ul": 9}],
    }
    client = _ScriptedClient(raw)
    asked = []

    def ask(questions):
        asked.append(questions)
        return "A1 45, B1 10, C1 4.5, D1 100, E1 20, F1 30, G1 60, H1 8"

    result = author_from_document(client, "Rapid barcoding", profile, tmp_path / "out", ask=ask, examples=False)
    assert result.stage == "done", (result.stage, result.error)
    assert len(client.calls) == 1 and "sample sheet" in asked[0][0]
    namespace: dict = {}
    exec(compile(result.source, str(tmp_path / "d.py"), "exec"), namespace)
    wt = namespace["build_worktable"]()
    wt.simulate()
    final = wt.snapshots[-1].labware("norm_Plate")
    volumes = [sum(layer.volume_ul for layer in final.well(f"{row}1").layers) for row in "ABCDEFGH"]
    assert all(abs(v - 9) < 0.01 for v in volumes), volumes
    # "50 ng in 9 ul per sample" is met in the normalised plate, not the stock.
    from fluentvibe.authoring.requirements import Requirement, verify_all

    (verdict,) = verify_all(wt, [Requirement("r", "50 ng in 9 ul", "sample_volume", {"ul": 9})])
    assert verdict.status == "pass" and "norm_Plate" in verdict.evidence, verdict


def test_a_repeat_the_operator_does_is_not_an_empty_loop(profile, tmp_path):
    """Seen on ONT: the ethanol wash after pooling is operator work; its two
    passes have no deck code and must not become `with wt.loop(): <nothing>`."""
    raw = {
        "title": "Rapid barcoding", "sample_count": 16, "sample_volume_ul": 9,
        "reagents": [{"id": "dna", "name": "Amplicon DNA", "role": "sample"},
                     {"id": "etoh", "name": "80% ethanol", "role": "wash"}],
        "steps": [
            {"id": "pool", "op": "pool", "text": "Pool all samples", "location": "deck", "volume_ul": 8},
            {"id": "wash", "op": "add", "text": "Wash with 1.5 ml ethanol", "location": "deck", "reagent": "etoh",
             "volume_ul": 150},
            {"id": "rem", "op": "remove", "text": "Remove the ethanol", "location": "deck"},
            {"id": "rep", "op": "repeat", "text": "Repeat the wash", "location": "deck", "first_step": "wash",
             "last_step": "rem", "times": 1},
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "wt.loop(" not in source
    compile(source, "d.py", "exec")
