
from fluentvibe.workspace_app import service


def test_default_output_folders_are_dated_and_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "WORKBENCH_BASE_DIR", tmp_path)
    first = service._workbench_path(None, "authored")
    second = service._workbench_path(None, "authored")
    assert first.parent == second.parent == tmp_path / "authored"
    assert first != second
    assert len(first.name.split("-")[0]) == 8
    assert first.is_dir() and second.is_dir()


def test_explicit_output_directory_is_preserved(tmp_path):
    explicit = tmp_path / "chosen"
    assert service._workbench_path(str(explicit), "authored") == explicit


def test_fc_validation_from_the_page_is_a_human_inspection_with_backup(tmp_path, monkeypatch):
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    calls = []
    def validate(self, **kwargs):
        calls.append(kwargs)
        return {"ok": True}
    monkeypatch.setattr(service, "WORKBENCH_BASE_DIR", tmp_path)
    monkeypatch.setattr(AuthoringToolRegistry, "validate_fluentcontrol_shell", validate)
    xscr = tmp_path / "example.xscr"
    xscr.write_text("<Script/>", encoding="utf-8")
    service._job_fc_validate({"xscr_path": str(xscr)})
    assert calls[0]["by"] == "human" and calls[0]["backup"] and "restore_shell" not in calls[0]
