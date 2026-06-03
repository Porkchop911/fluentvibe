from __future__ import annotations

import json
from pathlib import Path

from fluentvibe.authoring.grounding import load_current_worktable_snapshot
from fluentvibe.catalog.catalog import index_exists
from fluentvibe.workspace_app import service


def _workspace_name() -> str:
    workspaces = service.list_workspaces()["workspaces"]
    assert workspaces
    for item in workspaces:
        if item["name"] == "SAT_Fluent_780_Rev3":
            return item["name"]
    return workspaces[0]["name"]


def test_workspace_app_lists_workspaces() -> None:
    if not index_exists():
        return
    payload = service.list_workspaces()
    assert payload["ok"] is True
    assert payload["workspaces"]
    assert {"name", "guid"} <= set(payload["workspaces"][0])


def test_workspace_app_lists_configurations() -> None:
    if not index_exists():
        return
    payload = service.list_configurations()
    assert payload["ok"] is True
    assert payload["configurations"]
    assert any(item["name"] == "TECAN,FLUENT,2203009762" for item in payload["configurations"])
    detail = service.configuration_detail(payload["preferred_guid"])
    assert detail["ok"] is True
    assert detail["configuration"]["device_count"] > 0


def test_workspace_detail_exposes_slots_and_roles() -> None:
    if not index_exists():
        return
    payload = service.workspace_detail(name=_workspace_name())
    assert payload["ok"] is True
    assert payload["workspace"]["name"]
    assert payload["positions"]
    assert payload["valid_slots"]
    assert payload["deck_coordinates"]
    assert payload["workspace_source"]["sha256"]
    assert payload["workspace_source"]["base_worktable_component_name"]
    assert any(row.get("x_mm") is not None and row.get("y_mm") is not None for row in payload["deck_coordinates"])
    by_path = {row["site_path"]: row for row in payload["deck_coordinates"]}
    if {"3/0/0", "3/1/0", "15/0/0"} <= set(by_path):
        assert by_path["3/0/0"]["coordinate_source"] == "workspace_arrangement_template"
        assert by_path["3/0/0"]["x_mm"] < by_path["15/0/0"]["x_mm"]
        assert by_path["3/0/0"]["y_mm"] != by_path["3/1/0"]["y_mm"]
    assert payload["deck_blocks"]
    assert "plate" in {item["name"] for item in payload["common_labware_categories"]}
    assert payload["default_common_labware"]


def test_save_profile_writes_generation_artifacts(tmp_path: Path) -> None:
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    common_labware = [
        cfg for cfg in detail["default_common_labware"]
        if cfg.get("catalog_name")
    ][:3]
    saved = service.save_profile(
        {
            "profile_name": "test profile",
            "configuration": {"guid": service.list_configurations()["preferred_guid"]},
            "workspace": detail["workspace"],
            "workspace_source": detail["workspace_source"],
            "common_labware": common_labware,
            "liquid_class": detail["liquid_class"],
        },
        base_dir=tmp_path,
    )
    assert saved["ok"] is True
    paths = saved["paths"]
    profile = json.loads(Path(paths["profile_json"]).read_text(encoding="utf-8"))
    assert profile["schema_version"] == service.PROFILE_SCHEMA_VERSION
    assert profile["configuration"]
    assert profile["workspace_source"]["sha256"] == detail["workspace_source"]["sha256"]
    assert profile["common_labware"]
    assert Path(paths["generation_yaml"]).exists()
    snapshot = load_current_worktable_snapshot(Path(paths["current_worktable"]))
    assert snapshot is not None
    assert snapshot["workspace"]["guid"] == detail["workspace"]["guid"]

    # The profile also emits a --lab-scope skills deck skill, driven by this
    # workspace, so authoring can target this deck instead of the shipped one.
    from fluentvibe.authoring.lab_skills import _parse_skill

    deck_path = Path(paths["deck_skill"])
    assert deck_path.exists()
    skill = _parse_skill(deck_path)
    assert skill is not None
    assert skill.axis == "deck"
    assert skill.always_on is True
    body = deck_path.read_text(encoding="utf-8")
    # binds to THIS workspace, not a hardcoded default
    assert detail["workspace"]["guid"] in body
    assert detail["workspace"]["name"] in body


def test_save_profile_rejects_invalid_slot(tmp_path: Path) -> None:
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    item = next(
        cfg for cfg in detail["default_common_labware"]
        if cfg.get("catalog_name")
    )
    bad_item = dict(item)
    bad_item["preferred_location"] = "NotARealLocation"
    bad_item["preferred_position"] = 999
    try:
        service.save_profile(
            {
                "profile_name": "bad",
                "workspace": detail["workspace"],
                "common_labware": [bad_item],
            },
            base_dir=tmp_path,
        )
    except ValueError as exc:
        assert "not valid" in str(exc)
    else:
        raise AssertionError("invalid slot was accepted")


def test_save_profile_rejects_stale_workspace_source(tmp_path: Path) -> None:
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    stale_source = dict(detail["workspace_source"])
    stale_source["sha256"] = "0" * 64
    try:
        service.save_profile(
            {
                "profile_name": "stale",
                "workspace": detail["workspace"],
                "workspace_source": stale_source,
                "common_labware": [],
            },
            base_dir=tmp_path,
        )
    except ValueError as exc:
        assert "Workspace file changed" in str(exc)
    else:
        raise AssertionError("stale workspace source was accepted")
