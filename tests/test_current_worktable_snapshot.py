from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fluentvibe.authoring.grounding as grounding


def _config(name: str = "Workspace A", guid: str = "guid-a") -> dict:
    return {
        "worktable": {"name": name, "guid": guid},
        "grounding_defaults": {
            "labware": {"plate_96": "plate"},
            "layout": {"sample_plate": {"location": "Loc", "site": 1}},
        },
        "liquid_class": {"name": "Water Free Single"},
    }


def test_current_worktable_snapshot_is_written_and_reused(monkeypatch, tmp_path: Path) -> None:
    snapshot_path = tmp_path / "current_worktable.py"
    monkeypatch.setenv(grounding.CURRENT_WORKTABLE_ENV, str(snapshot_path))
    monkeypatch.setattr(grounding, "load_generation_config", lambda: _config())
    monkeypatch.setattr(
        grounding,
        "resolve_workspace_by_guid",
        lambda guid: SimpleNamespace(file_path=tmp_path / f"{guid}.xwsp"),
    )

    calls = {"load_xwsp": 0}

    def fake_load_xwsp(path: Path):
        calls["load_xwsp"] += 1
        return SimpleNamespace(
            name="Workspace A",
            guid="guid-a",
            available_sites=[
                ((0,), "Loc"),
                ((1,), "Loc"),
                ((2,), "Other"),
            ],
        )

    monkeypatch.setattr(grounding, "load_xwsp", fake_load_xwsp)

    first = grounding.load_grounding_bundle()
    assert first.workspace.name == "Workspace A"
    assert ("Loc", 1) in first.valid_slots
    assert ("Loc", 2) in first.valid_slots
    assert snapshot_path.exists()
    assert calls["load_xwsp"] == 1

    monkeypatch.setattr(
        grounding,
        "load_xwsp",
        lambda path: (_ for _ in ()).throw(AssertionError("should use snapshot")),
    )
    second = grounding.load_grounding_bundle()
    assert second.workspace.guid == "guid-a"
    assert second.valid_slots == first.valid_slots
    assert calls["load_xwsp"] == 1


def test_current_worktable_snapshot_rebuilds_when_workspace_changes(monkeypatch, tmp_path: Path) -> None:
    snapshot_path = tmp_path / "current_worktable.py"
    monkeypatch.setenv(grounding.CURRENT_WORKTABLE_ENV, str(snapshot_path))
    monkeypatch.setattr(grounding, "load_generation_config", lambda: _config())
    monkeypatch.setattr(
        grounding,
        "resolve_workspace_by_guid",
        lambda guid: SimpleNamespace(file_path=tmp_path / f"{guid}.xwsp"),
    )

    def fake_load_xwsp(path: Path):
        guid = path.stem
        return SimpleNamespace(
            name="Workspace B" if guid == "guid-b" else "Workspace A",
            guid=guid,
            available_sites=[((0,), "ChangedLoc" if guid == "guid-b" else "Loc")],
        )

    monkeypatch.setattr(grounding, "load_xwsp", fake_load_xwsp)

    first = grounding.load_grounding_bundle()
    changed = grounding.load_grounding_bundle(
        workspace_name="Workspace B",
        workspace_guid="guid-b",
    )

    assert first.workspace.guid == "guid-a"
    assert changed.workspace.guid == "guid-b"
    assert ("ChangedLoc", 1) in changed.valid_slots
    text = snapshot_path.read_text(encoding="utf-8")
    assert "Workspace B" in text
    assert "guid-b" in text
