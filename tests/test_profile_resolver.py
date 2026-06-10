"""`authoring.profile` — resolve a workspace-app profile dir into inputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.authoring.profile import (  # noqa: E402
    PROFILE_DIR_ENV,
    profile_from_env,
    resolve_profile,
)

WS_NAME = "Test_Deck_X"
WS_GUID = "abcdef00-1111-2222-3333-444455556666"


def _write_profile(root: Path) -> Path:
    """Write a minimal but well-formed workspace-app profile."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace_profile.json").write_text(
        json.dumps({
            "workspace": {
                "name": WS_NAME,
                "guid": WS_GUID,
                "base_worktable_name": "X Base Unit",
            }
        }),
        encoding="utf-8",
    )
    (root / "generation.profile.yaml").write_text(
        yaml.safe_dump({
            "lab_scope": {
                "labware": ["Foo Plate", "Bar Tips"],
                "liquid_classes": ["Test Liquid"],
            },
            "deck_rules": {
                WS_NAME: {
                    "trough_locations": ["WS_50ml_"],
                    "require_fca_tipbox": True,
                    "check_mix_section": True,
                }
            },
        }),
        encoding="utf-8",
    )
    (root / f"deck-{WS_NAME.lower()}.md").write_text(
        "---\n"
        f"name: deck-{WS_NAME.lower()}\n"
        "axis: deck\n"
        "description: synthetic test deck\n"
        "always_on: true\n"
        "---\n"
        f'Always: `Worktable.from_workspace("{WS_NAME}", workspace_guid="{WS_GUID}")`\n',
        encoding="utf-8",
    )
    (root / "current_worktable.py").write_text("SCHEMA_VERSION = 1\n", encoding="utf-8")
    return root


def test_resolve_profile_parses_all_fields(tmp_path: Path) -> None:
    root = _write_profile(tmp_path / "prof")
    rp = resolve_profile(root)

    assert rp.workspace_name == WS_NAME
    assert rp.workspace_guid == WS_GUID
    assert rp.current_worktable == root / "current_worktable.py"
    assert rp.deck_skill is not None and rp.deck_skill.name == f"deck-{WS_NAME.lower()}.md"
    assert rp.labware == frozenset({"Foo Plate", "Bar Tips"})
    assert rp.liquid_classes == frozenset({"Test Liquid"})
    # deck_rules are extracted (un-keyed) for this workspace
    assert rp.deck_rules["trough_locations"] == ["WS_50ml_"]
    assert rp.deck_rules["require_fca_tipbox"] is True


def test_resolve_profile_rejects_non_profile_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace_profile.json"):
        resolve_profile(tmp_path)


def test_profile_from_env_roundtrip(tmp_path: Path, monkeypatch) -> None:
    root = _write_profile(tmp_path / "prof")
    monkeypatch.setenv(PROFILE_DIR_ENV, str(root))
    rp = profile_from_env()
    assert rp is not None and rp.workspace_name == WS_NAME


def test_profile_from_env_is_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv(PROFILE_DIR_ENV, raising=False)
    assert profile_from_env() is None


def test_profile_from_env_is_none_on_bad_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "nope"))
    assert profile_from_env() is None
