"""End-to-end bridge guard: a workspace-app profile drives skills-mode scope.

The regression this locks: before the bridge work, `--lab-scope skills` always
targeted the shipped 780 deck regardless of the profile. These tests build the
lab scope from a synthetic profile and assert the injected context binds to the
profile's workspace, the active deck is the profile's, the whitelist is the
profile's, and NO trace of the default 780 deck leaks through.

Hermetic: a synthetic profile in tmp_path + the shipped skill catalog. Needs
the skills block enabled in generation.yaml (it is) but not the FC install.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.authoring.lab_scope import load_lab_scope  # noqa: E402
from fluentvibe.authoring.lab_skills import assemble_context  # noqa: E402
from fluentvibe.authoring.profile import resolve_profile  # noqa: E402

WS_NAME = "Ribbon_Test_1080"
WS_GUID = "deadbeef-1111-2222-3333-444455556666"
DEFAULT_780_NAME = "SAT_Fluent_780_Rev3"
DEFAULT_780_GUID = "291ba293-6361-4f8f-aa8d-7c2643d3f096"


def _write_profile(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace_profile.json").write_text(
        json.dumps({"workspace": {"name": WS_NAME, "guid": WS_GUID,
                                   "base_worktable_name": "1080 Base Unit"}}),
        encoding="utf-8",
    )
    (root / "generation.profile.yaml").write_text(
        yaml.safe_dump({
            "lab_scope": {
                "labware": ["96_ABgene_SuperPlate_Thermo_AB2800", "MCA96, 200ul, Box"],
                "liquid_classes": ["Water Free Single"],
            },
            "workspace_profile": {
                "common_labware": [
                    {
                        "catalog_name": "96_ABgene_SuperPlate_Thermo_AB2800",
                        "python_class": "Plate96",
                    },
                    {
                        "catalog_name": "MCA96, 200ul, Box",
                        "python_class": "MCA200Box",
                    },
                ],
            },
            "deck_rules": {WS_NAME: {"trough_locations": ["WS_100ml_"],
                                     "require_fca_tipbox": True, "check_mix_section": True}},
        }),
        encoding="utf-8",
    )
    (root / f"deck-{WS_NAME.lower()}.md").write_text(
        "---\n"
        f"name: deck-{WS_NAME.lower()}\n"
        "axis: deck\n"
        "description: synthetic 1080 test deck\n"
        "always_on: true\n"
        "---\n"
        "## Deck / workspace\n\n"
        f'- Always: `Worktable.from_workspace("{WS_NAME}", workspace_guid="{WS_GUID}", auto_place=False, ...)`\n',
        encoding="utf-8",
    )
    (root / "current_worktable.py").write_text("SCHEMA_VERSION = 1\n", encoding="utf-8")
    return root


def _skills_scope_or_skip(profile=None):
    scope = load_lab_scope("skills", profile=profile)
    if scope.mode != "skills" or not scope.skill_catalog:
        pytest.skip("skills lab scope not available in this environment")
    return scope


def test_profile_drives_deck_and_whitelist(tmp_path: Path) -> None:
    rp = resolve_profile(_write_profile(tmp_path / "prof"))
    scope = _skills_scope_or_skip(profile=rp)

    deck_names = [s.name for s in scope.skill_catalog if s.axis == "deck"]
    assert deck_names == [f"deck-{WS_NAME.lower()}"]
    assert scope.labware == rp.labware
    assert scope.labware_classes == rp.labware_classes
    assert scope.liquid_classes == rp.liquid_classes


def test_assembled_context_binds_profile_workspace_only(tmp_path: Path) -> None:
    rp = resolve_profile(_write_profile(tmp_path / "prof"))
    scope = _skills_scope_or_skip(profile=rp)

    ctx = assemble_context(scope, [s.name for s in scope.skill_catalog])
    assert ctx is not None
    # binds to the profile's workspace …
    assert WS_GUID in ctx and WS_NAME in ctx
    assert "Profile labware class contract" in ctx
    assert "| `MCA96, 200ul, Box` | `MCA200Box` |" in ctx
    # … and NOTHING of the shipped 780 deck leaks through (the regression).
    assert DEFAULT_780_NAME not in ctx
    assert DEFAULT_780_GUID not in ctx


def test_no_profile_keeps_shipped_780_default(tmp_path: Path) -> None:
    # Explicit profile=None opts out of any FLUENTVIBE_PROFILE_DIR env default.
    scope = _skills_scope_or_skip(profile=None)
    deck_names = [s.name for s in scope.skill_catalog if s.axis == "deck"]
    assert deck_names == ["deck-sat-780"]
    ctx = assemble_context(scope, [s.name for s in scope.skill_catalog])
    assert DEFAULT_780_GUID in ctx
