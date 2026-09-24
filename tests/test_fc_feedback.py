"""FluentControl InfoPad feedback for the authoring loop (no FluentControl needed)."""

from __future__ import annotations

from types import SimpleNamespace

from fluentvibe.authoring import fc_feedback
from fluentvibe.authoring.eval_rubric import build_worktable_from_source
from fluentvibe.authoring.fc_feedback import (
    check_in_fluentcontrol,
    explain_infopad,
    fc_findings_message,
)

SOURCE = '''
from fluentvibe import MCA200Box, Plate96, Reagent, Trough25mL, Worktable


def build_worktable():
    wt = Worktable(name="p")
    wt.group("Labware Placement")
    tips = wt.place(MCA200Box("Tips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 1)
    plate = wt.place(Plate96("Plate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    buffer = wt.place(Trough25mL("Buffer", catalog="25ml_short"), "WS_100ml_1", 2)
    buffer.fill_all(Reagent("Buffer"), 20000.0)
    wt.group("Add buffer")
    mca = wt.mca96
    mca.mount_adapter()
    mca.pick_up(tips)
    mca.aspirate(buffer, 10.0, liquid_class="Water Free Single")
    mca.dispense(plate, 10.0, liquid_class="Water Free Single")
    mca.return_tips(tips)
    return wt
'''


def _worktable(tmp_path):
    path = tmp_path / "draft.py"
    path.write_text(SOURCE, encoding="utf-8")
    return build_worktable_from_source(SOURCE, str(path))


def _fc_line_of(wt, kind):
    for group in wt.to_protocol().groups:
        for step in group.steps:
            if type(step).__name__ == kind:
                return step.line_number
    raise AssertionError(kind)


def test_infopad_lines_become_findings_at_python_lines(tmp_path):
    wt = _worktable(tmp_path)
    line = _fc_line_of(wt, "AspirateStep")
    errors = [
        f"{line:03d}: Buffer out of range. Arm cannot move to position. Place object in an area the arm can access.",
        f"{line + 1:03d}: The volume to pipette is higher than the available liquid volume in the selected tip. "
        "The available liquid volume is: 0.",
        "007: 'Close prompt after' exceeds lower range limit. Please enter a value between 1 and 7200.",
    ]
    findings = [f.to_dict() for f in explain_infopad(errors, wt)]
    kinds = [f["kind"] for f in findings]
    assert kinds == ["out_of_reach", "prompt_timeout"]  # the follow-on error is folded in
    reach = findings[0]
    aspirate_line = SOURCE.splitlines().index('    mca.aspirate(buffer, 10.0, liquid_class="Water Free Single")') + 1
    assert reach["python_lines"] == [aspirate_line]
    assert reach["groups"] == ["Add buffer"] and reach["labware"] == "Buffer"
    assert "SBS reservoir" in reach["hint"]


def test_check_reports_clean_and_failing_scripts(tmp_path):
    def clean(_path):
        return SimpleNamespace(opened=True, load_failed=False, error_lines=[])

    def failing(_path):
        return SimpleNamespace(opened=True, load_failed=False, error_lines=["012: Labware name already exists"])

    ok = check_in_fluentcontrol(tmp_path / "x.xscr", validator=clean)
    assert ok["ok"] is True and ok["findings"] == []
    bad = check_in_fluentcontrol(tmp_path / "x.xscr", validator=failing)
    assert bad["ok"] is False and bad["findings"][0]["kind"] == "duplicate_labware_name"
    assert "duplicate_labware_name" in fc_findings_message(bad)


def test_unavailable_fluentcontrol_is_not_a_protocol_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(fc_feedback, "fluentcontrol_available", lambda: (False, "not running"))
    result = check_in_fluentcontrol(tmp_path / "x.xscr")
    assert result["ok"] is None and result["available"] is False


def test_graph_asks_for_a_repair_only_when_enabled_and_within_budget(tmp_path, monkeypatch):
    from fluentvibe.authoring.graph import _Nodes

    verdict = {"ok": False, "available": True, "findings": [
        {"kind": "out_of_reach", "message": "Buffer out of range.", "count": 1, "python_lines": [12],
         "groups": ["Add buffer"], "labware": "Buffer", "hint": "move it"}]}
    monkeypatch.setattr(fc_feedback, "check_in_fluentcontrol", lambda *a, **k: verdict)
    recorded = []
    node = SimpleNamespace(registry=SimpleNamespace(record_fc_check=recorded.append))
    compiled = {"xscr_path": str(tmp_path / "x.xscr"), "python_path": str(tmp_path / "x.py")}

    monkeypatch.delenv(fc_feedback.FC_CHECK_ENV, raising=False)
    assert _Nodes._fluentcontrol_findings(node, compiled, "src", 0) is None and not recorded

    monkeypatch.setenv(fc_feedback.FC_CHECK_ENV, "1")
    message = _Nodes._fluentcontrol_findings(node, compiled, "src", 0)
    assert message is not None and "line 12" in message.content and recorded == [verdict]
    assert _Nodes._fluentcontrol_findings(node, compiled, "src", fc_feedback.FC_CHECK_BUDGET) is None
