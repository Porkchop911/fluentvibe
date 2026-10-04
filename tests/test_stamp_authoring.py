"""Regression coverage for the author's shifted partial MCA stamp."""
import json
import xml.etree.ElementTree as ET

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from fluentvibe import MCA100Box, Plate96, Reagent, Worktable
from fluentvibe.authoring.lab_scope import load_lab_scope
from fluentvibe.authoring.lab_skills import build_initial_scope_message, select_skills
from fluentvibe.authoring.models import IntentSpec
from fluentvibe.authoring.models import AuthoringStatus
from fluentvibe.authoring.service import SYSTEM_PROMPT, system_prompt_for_scope
from fluentvibe.authoring.session import PromptAuthoringSession
from fluentvibe.authoring.tools import AuthoringToolRegistry
from fluentvibe.authoring.trace import ModelTraceConfig, ModelTraceRecorder
from fluentvibe.authoring.validator import AuthoringValidator, _check_intent_against_final_labware
from fluentvibe.simulator.invariants import MissingTipsError

PROMPT = "make a protocol that stamps 50 ul from the left half of plate a to the center of plate b with the mca"


class Selector:
    def __init__(self, names):
        self.names = names
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages[-1].content)
        return AIMessage(content=json.dumps(self.names))


def stamp_source(source_cols=None, dest_cols=None, volume=50):
    source_cols = list(range(1, 7)) if source_cols is None else source_cols
    dest_cols = list(range(4, 10)) if dest_cols is None else dest_cols
    return f'''from fluentvibe import Worktable, Plate96, MCA100Box, Reagent
def build_worktable() -> Worktable:
    wt = Worktable.from_workspace("SAT_Fluent_780_Rev3", workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096", auto_place=False)
    wt.declare_variable("TARGET_VOLUME_UL", {volume})
    wt.set_sim_value("TARGET_VOLUME_UL", {volume})
    wt.declare_variable("LIQUID_CLASS_TRANSFER", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_TRANSFER", "Water Free Single")
    wt.group("Labware Placement")
    src = wt.place(Plate96("PlateA", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dst = wt.place(Plate96("PlateB", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    for col in range(1, 7):
        for row in "ABCDEFGH":
            src.wells[f"{{row}}{{col}}"].add_layer(Reagent(f"Sample_{{row}}{{col}}"), 150)
    wt.group("Stamp")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips, columns={source_cols!r})
    head.aspirate(src, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER", columns={source_cols!r})
    head.dispense(dst, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER", columns={dest_cols!r})
    head.return_tips(tips, columns={source_cols!r})
    head.drop_adapter()
    return wt
'''


def test_centered_stamp_compiles_and_preserves_all_48_sample_identities(tmp_path):
    report = AuthoringValidator().validate(stamp_source(), output_dir=tmp_path, stem="stamp", attempt_index=1, prompt=PROMPT)
    assert report.success, report.failure_message
    assert report.intent_check_ok
    dest = report.final_labware["PlateB"]["wells"]
    source = report.final_labware["PlateA"]["wells"]
    assert len(dest) == len(source) == 48
    for col in range(1, 7):
        for row in "ABCDEFGH":
            well = dest[f"{row}{col+3}"]
            assert well["volume_ul"] == 50
            assert well["layers"] == [{"reagent": f"Sample_{row}{col}", "volume_ul": 50}]
            assert source[f"{row}{col}"]["volume_ul"] == 100
    xml = report.xscr_path.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    dispense = next(
        obj for obj in root.iter("Object")
        if obj.attrib.get("Type", "").endswith("Mca384DispenseScriptCommandDataV2")
    )
    # Mounted tips 1–6 reach destination columns 4–9 with a three-column shift.
    assert dispense.findtext(".//FirstTipXPosition") == "1"
    assert dispense.findtext(".//LastTipXPosition") == "6"
    assert dispense.findtext(".//Column") == "3"
    assert "<PartialColumns>6</PartialColumns>" in xml


@pytest.mark.parametrize("cols", [list(range(7, 13)), list(range(1, 13))])
def test_right_half_or_whole_plate_cannot_pass_for_center(tmp_path, cols):
    result = AuthoringToolRegistry(output_dir=tmp_path, current_prompt=PROMPT).simulate_python_draft(stamp_source(dest_cols=cols))
    assert not result["ok"]
    assert result["category"] == "intent_not_satisfied"
    assert "destination columns [4,5,6,7,8,9]" in result["message"]


def test_wrong_stamp_volume_fails_even_without_declared_intent(tmp_path):
    result = AuthoringToolRegistry(output_dir=tmp_path, current_prompt=PROMPT).compile_and_simulate(stamp_source(volume=75))
    assert not result["ok"]
    assert result["simulation_failure_category"] == "intent_not_satisfied"


def test_volume_correction_uses_latest_user_value(tmp_path):
    result = AuthoringToolRegistry(output_dir=tmp_path, current_prompt=PROMPT + "\nUse 75 ul instead.").compile_and_simulate(stamp_source(volume=75))
    assert result["ok"], result.get("failure_message")


def test_explicit_geometry_correction_supersedes_center_default(tmp_path):
    validator = AuthoringValidator()
    path = tmp_path / "correction.py"
    path.write_text(stamp_source(dest_cols=list(range(7, 13))), encoding="utf-8")
    wt = validator._load_protocol(path)
    assert validator.partial_stamp_intent(wt, PROMPT + "\nUse the right half instead.") is None


def test_split_stamp_preserves_geometry_and_total_volume(tmp_path):
    source = stamp_source(volume=25)
    operations = source[source.index('    head.aspirate'):source.index('    head.return_tips')]
    source = source.replace(operations, operations + operations)
    result = AuthoringToolRegistry(output_dir=tmp_path, current_prompt=PROMPT).compile_and_simulate(source)
    assert result["ok"], result.get("failure_message")


def test_stamp_requires_actual_liquid_handling():
    assert AuthoringValidator()._check_prompt_intent("no liquid handling", PROMPT)


@pytest.mark.parametrize("wells", [
    {"A4": {"volume_ul": 100}},
    {"A4": {"volume_ul": 50}, "A1": {"volume_ul": 50}},
])
def test_intent_rejects_overfill_and_extra_wells(wells):
    intent = IntentSpec(target_volume_ul=50, destination_label="B", destination_wells=("A4",))
    assert _check_intent_against_final_labware(intent, {"B": {"wells": wells}})


def test_selector_cannot_omit_explicit_mca_or_family():
    scope = load_lab_scope("skills")
    names = select_skills(PROMPT, scope.skill_catalog, Selector(["head-liha"]))
    assert "head-mca96" in names
    assert "family-simple-transfer" in names


def test_selected_skills_are_recorded_in_readable_trace(tmp_path):
    selector = Selector(["family-simple-transfer"])
    selector.trace_recorder = ModelTraceRecorder(ModelTraceConfig(enabled=True, output_dir=tmp_path))
    build_initial_scope_message(load_lab_scope("skills"), PROMPT, selector)
    event = json.loads(selector.trace_recorder.current_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event"] == "skill_selection"
    assert "head-mca96" in event["selected_skills"]
    assert "Skill Selection" in selector.trace_recorder.current_readable_path.read_text(encoding="utf-8")


def test_scope_prompt_has_one_workflow():
    prompt = system_prompt_for_scope(load_lab_scope("skills"))
    assert "present_object_draft" not in prompt
    assert "ask_user" not in prompt
    assert "profile's default liquid class" in prompt
    assert system_prompt_for_scope(load_lab_scope("off")) == SYSTEM_PROMPT


def test_skill_context_refreshes_without_duplicating_system_messages(tmp_path):
    session = PromptAuthoringSession(output_dir=tmp_path, lab_scope="skills")
    selector = Selector(["head-mca96", "family-simple-transfer"])
    session._client = selector
    session._user_turns = [PROMPT]
    session._inject_skill_context(PROMPT)
    selector.names = ["head-liha", "family-plate-reformatting"]
    session._user_turns.append("use LiHa instead")
    session._inject_skill_context("\n".join(session._user_turns))
    assert len([m for m in session._messages if isinstance(m, SystemMessage)]) == 2
    assert "wt.liha" in session._messages[1].content
    assert "use LiHa instead" in selector.prompts[-1]


def test_skills_session_generates_without_default_liquid_class_question(tmp_path):
    class Client:
        def __init__(self):
            self.calls = []
            self.draft_count = 0

        def complete(self, *, messages, tools):
            self.calls.append((messages, tools))
            if "You select which curated lab skills" in messages[0]["content"]:
                assert tools == []
                return {"content": '["head-mca96", "family-simple-transfer"]'}
            self.draft_count += 1
            if self.draft_count <= 2:
                name = "simulate_python_draft" if self.draft_count == 1 else "compile_and_simulate"
                return {"content": "", "tool_calls": [{"id": str(self.draft_count), "function": {
                    "name": name, "arguments": json.dumps({"source": stamp_source()}),
                }}]}
            return {"content": stamp_source()}

    client = Client()
    session = PromptAuthoringSession(output_dir=tmp_path, lab_scope="skills", client=client)
    result = session.send(PROMPT)
    assert result.status == AuthoringStatus.SUCCESS, result.failure_message
    assert not result.clarification_questions
    for messages, tools in client.calls[1:]:
        assert {t["function"]["name"] for t in tools} == {"simulate_python_draft", "compile_and_simulate"}
        assert not any("intent axes are still missing" in m.get("content", "") for m in messages)


@pytest.mark.parametrize("columns", [[], [0], [13], [1, 1], [2, 1], [1.5], [True]])
def test_mca_rejects_invalid_columns(columns):
    wt = Worktable(name="invalid selection")
    with pytest.raises(ValueError):
        wt.mca96.aspirate("Source", 50, liquid_class="Water Free Single", columns=columns)


def test_partial_tips_cannot_silently_cover_a_full_plate():
    wt = Worktable(name="tip count")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 2)
    src.fill_all(Reagent("Water"), 100)
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips, columns=[1])
    wt.mca96.aspirate(src, 50, liquid_class="Water Free Single")
    with pytest.raises(MissingTipsError, match="exceed the mounted tip count"):
        wt.simulate()
