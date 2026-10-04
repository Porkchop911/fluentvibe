import json

from fluentvibe.cli import main
from fluentvibe.authoring.fluentcontrol_shell import UiResult


def test_fc_open_compiles_current_python_before_validation(tmp_path, monkeypatch, capsys):
    events = []
    class Worktable:
        def simulate(self, **kwargs):
            events.append(("simulate", kwargs))
        def compile(self, path):
            events.append(("compile", path))
    monkeypatch.setattr("fluentvibe.cli._load_protocol", lambda path: Worktable())
    def validate(path, **kwargs):
        events.append(("validate", kwargs))
        return UiResult(True, ["12: Invalid placement"], ["12: Invalid placement"])
    monkeypatch.setattr("fluentvibe.authoring.fluentcontrol_shell.validate_generated_xscr_via_shell", validate)
    assert main(["fc-open", str(tmp_path / "protocol.py"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [event[0] for event in events] == ["simulate", "compile", "validate"]
    assert events[0][1] == {"strict": True}
    assert events[2][1] == {"restore_shell": True, "backup": True}
    assert result["opened"] and len(result["findings"]) == 1
    assert result["findings"][0]["python_lines"] == []


def test_fc_open_build_failure_is_structured_and_does_not_open_ui(monkeypatch, capsys):
    def fail(path):
        raise ValueError("Missing return wt")
    monkeypatch.setattr("fluentvibe.cli._load_protocol", fail)
    assert main(["fc-open", "broken.py", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["opened"]
    assert result["load_error"] == "Missing return wt"
    assert result["findings"] == []
