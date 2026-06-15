"""Resolve a workspace-app profile into the inputs generation needs.

The workspace setup app (``fluentvibe workspace-app`` →
``workspace_app.service.save_profile``) writes a profile directory, e.g.
``build/workspaces/sat_1080_test/``, containing:

- ``workspace_profile.json``   — workspace name/guid + base deck,
- ``current_worktable.py``     — the grounding snapshot,
- ``generation.profile.yaml``  — the labware/liquid whitelist (+ optional
                                  ``deck_rules``),
- ``deck-<name>.md``           — the ``--lab-scope skills`` deck skill.

This module is the single place that turns that directory into a
:class:`ResolvedProfile`, so the CLI, the authoring session, and the lab-scope
loader all read the profile the same way. Selection is explicit (a ``--profile``
flag) or via the ``FLUENTVIBE_PROFILE_DIR`` env var — the same env-channel
pattern as ``FLUENTVIBE_CURRENT_WORKTABLE_PATH`` in :mod:`grounding`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROFILE_DIR_ENV = "FLUENTVIBE_PROFILE_DIR"


@dataclass(frozen=True)
class ResolvedProfile:
    """A workspace-app profile, parsed into generation inputs."""

    root: Path
    workspace_name: str
    workspace_guid: str
    current_worktable: Path
    deck_skill: Path | None
    labware: frozenset[str] = field(default_factory=frozenset)
    labware_classes: dict[str, str] = field(default_factory=dict)
    liquid_classes: frozenset[str] = field(default_factory=frozenset)
    deck_rules: dict[str, Any] = field(default_factory=dict)


def resolve_profile(profile_dir: Path | str) -> ResolvedProfile:
    """Parse a profile directory. Raises ``ValueError`` on a malformed profile."""
    root = Path(profile_dir)
    profile_json = root / "workspace_profile.json"
    if not profile_json.exists():
        raise ValueError(f"Not a workspace profile (no workspace_profile.json): {root}")
    try:
        profile = json.loads(profile_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {profile_json}: {exc}") from exc

    workspace = profile.get("workspace") or {}
    name = str(workspace.get("name") or "").strip()
    guid = str(workspace.get("guid") or "").strip()
    if not name or not guid:
        raise ValueError(f"Profile {root} is missing workspace.name/guid")

    labware, labware_classes, liquid_classes, raw_deck_rules = _read_generation_profile(root)
    # generation.profile.yaml keys deck_rules by workspace name (same shape as
    # the shipped generation.yaml); extract this workspace's flat rule set.
    deck_rules = raw_deck_rules.get(name, {}) if isinstance(raw_deck_rules, dict) else {}
    if not isinstance(deck_rules, dict):
        deck_rules = {}
    decks = sorted(root.glob("deck-*.md"))

    return ResolvedProfile(
        root=root,
        workspace_name=name,
        workspace_guid=guid,
        current_worktable=root / "current_worktable.py",
        deck_skill=decks[0] if decks else None,
        labware=labware,
        labware_classes=labware_classes,
        liquid_classes=liquid_classes,
        deck_rules=deck_rules,
    )


def profile_from_env() -> ResolvedProfile | None:
    """Resolve the profile named by ``FLUENTVIBE_PROFILE_DIR``, if any.

    Returns ``None`` (never raises) when the env var is unset or points at a
    directory that is not a valid profile, so callers can consult it
    unconditionally — mirroring ``load_current_worktable_snapshot``.
    """
    raw = os.environ.get(PROFILE_DIR_ENV)
    if not raw:
        return None
    try:
        return resolve_profile(Path(raw))
    except ValueError:
        return None


def _read_generation_profile(
    root: Path,
) -> tuple[frozenset[str], dict[str, str], frozenset[str], dict[str, Any]]:
    path = root / "generation.profile.yaml"
    if not path.exists():
        return frozenset(), {}, frozenset(), {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return frozenset(), frozenset(), {}
    lab_scope = data.get("lab_scope") or {}
    labware = frozenset(
        str(x).strip() for x in (lab_scope.get("labware") or []) if str(x).strip()
    )
    profile_block = data.get("workspace_profile") if isinstance(data.get("workspace_profile"), dict) else {}
    labware_classes = {
        str(item.get("catalog_name") or "").strip(): str(item.get("python_class") or "").strip()
        for item in (profile_block.get("common_labware") or [])
        if isinstance(item, dict)
        and str(item.get("catalog_name") or "").strip()
        and str(item.get("python_class") or "").strip()
    }
    liquid_classes = frozenset(
        str(x).strip() for x in (lab_scope.get("liquid_classes") or []) if str(x).strip()
    )
    deck_rules = data.get("deck_rules") if isinstance(data.get("deck_rules"), dict) else {}
    return labware, labware_classes, liquid_classes, deck_rules
