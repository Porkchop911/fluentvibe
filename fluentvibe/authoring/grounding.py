"""Installed-data-only grounding helpers for prompt authoring."""

from __future__ import annotations

import os
import re
import runpy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from pprint import pformat
from typing import Any

import yaml

from ..catalog import (
    find_components,
    find_grip_modes,
    find_labware_for_site,
    find_legal_stacks,
    find_workspaces_using,
    open_index,
    resolve_by_name,
    resolve_liquid_class_by_name,
)
from ..catalog.catalog import resolve_workspace_by_guid, resolve_workspace_by_name
from ..catalog.xcmp import load_xwsp
from .models import FailureCategory, WorkspaceBinding

CURRENT_WORKTABLE_SCHEMA_VERSION = 1
CURRENT_WORKTABLE_ENV = "FLUENTVIBE_CURRENT_WORKTABLE_PATH"


@dataclass(frozen=True)
class GroundingError(Exception):
    category: FailureCategory
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class GroundedLabware:
    family: str
    python_class: str
    catalog_name: str


@dataclass(frozen=True)
class GroundingBundle:
    workspace: WorkspaceBinding
    valid_slots: frozenset[tuple[str, int]]
    labware_defaults: dict[str, str]
    layout_defaults: dict[str, dict[str, Any]]
    liquid_class_name: str

    def require_slot(self, location: str, position: int) -> tuple[str, int]:
        slot = (location, position)
        if slot not in self.valid_slots:
            raise GroundingError(
                FailureCategory.WORKSPACE_SLOT_INVALIDITY,
                f"Configured slot {slot!r} is not valid on workspace {self.workspace.name!r}.",
            )
        return slot

    def slot_for_role(self, role: str) -> tuple[str, int]:
        cfg = self.layout_defaults.get(role)
        if cfg is None:
            raise GroundingError(
                FailureCategory.WORKSPACE_SLOT_INVALIDITY,
                f"No configured layout slot exists for role {role!r}.",
            )
        return self.require_slot(str(cfg["location"]), int(cfg["site"]))

    def resolve_labware_family(self, family: str) -> GroundedLabware:
        alias_map = {
            "mca_tipbox_small": "mca96_tipbox_small",
            "mca_tipbox_medium": "mca96_tipbox_medium",
            "mca_tipbox_large": "mca96_tipbox_large",
        }
        resolved_family = alias_map.get(family, family)
        catalog_name = self.labware_defaults.get(resolved_family)
        if not catalog_name:
            raise GroundingError(
                FailureCategory.MISSING_CATALOG_ITEM,
                f"No catalog grounding is configured for labware family {family!r}.",
            )
        entry = resolve_by_name(catalog_name)
        if entry is None:
            raise GroundingError(
                FailureCategory.MISSING_CATALOG_ITEM,
                f"Configured catalog name {catalog_name!r} is not installed locally.",
            )
        python_class = {
            "plate_96": "Plate96",
            "plate_384": "Plate384",
            "magnet_plate_96": "MagnetRack",
            "reservoir_standard": "Trough25mL",
            "reservoir_ethanol": "Trough25mL",
            "waste_reservoir": "Waste",
            "fca_tipbox_small": "FCA200Box",
            "fca_tipbox_large": "FCA1000Box",
            "mca_tipbox_small": "MCA100Box",
            "mca_tipbox_medium": "MCA200Box",
            "mca_tipbox_large": "MCA500Box",
        }.get(family)
        if python_class is None:
            raise GroundingError(
                FailureCategory.MISSING_CATALOG_ITEM,
                f"Labware family {family!r} is not supported by the authoring code generator.",
            )
        return GroundedLabware(
            family=family,
            python_class=python_class,
            catalog_name=catalog_name,
        )

    def resolve_liquid_class(self, name: str | None = None) -> str:
        candidate = (name or self.liquid_class_name).strip()
        entry = resolve_liquid_class_by_name(candidate)
        if entry is None:
            raise GroundingError(
                FailureCategory.LIQUID_CLASS_RESOLUTION_FAILURE,
                f"Liquid class {candidate!r} is not installed locally.",
            )
        return entry.name

    def exact_catalog_for_pattern(self, pattern: str, *, expected_category: str | None = None) -> str:
        rows = find_components(pattern)
        for row in rows:
            if expected_category is None or row.category == expected_category:
                return row.name
        raise GroundingError(
            FailureCategory.MISSING_CATALOG_ITEM,
            f"No installed catalog entry matched pattern {pattern!r}.",
        )


@lru_cache(maxsize=1)
def load_generation_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "_assets" / "config" / "generation.yaml"
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def load_grounding_bundle(
    *,
    workspace_name: str | None = None,
    workspace_guid: str | None = None,
) -> GroundingBundle:
    config = load_generation_config()
    cfg_workspace = config.get("worktable", {})
    chosen_name = workspace_name or str(cfg_workspace.get("name") or "")
    chosen_guid = workspace_guid or str(cfg_workspace.get("guid") or "")
    labware_defaults = dict(config.get("grounding_defaults", {}).get("labware", {}))
    layout_defaults = dict(config.get("grounding_defaults", {}).get("layout", {}))
    liquid_class_name = str(config.get("liquid_class", {}).get("name") or "Water Free Single")
    placement_locations = _placement_location_allowlist(config)

    snapshot = _load_current_worktable_snapshot(_current_worktable_path())
    if snapshot is not None and _snapshot_matches(snapshot, name=chosen_name, guid=chosen_guid):
        return GroundingBundle(
            workspace=WorkspaceBinding(
                name=str(snapshot["workspace"]["name"]),
                guid=str(snapshot["workspace"]["guid"]),
            ),
            valid_slots=frozenset(
                (str(location), int(position))
                for location, position in snapshot["valid_slots"]
            ),
            labware_defaults=labware_defaults,
            layout_defaults=layout_defaults,
            liquid_class_name=liquid_class_name,
        )

    bundle, workspace = _build_grounding_bundle_from_catalog(
        workspace_name=chosen_name,
        workspace_guid=chosen_guid,
        labware_defaults=labware_defaults,
        layout_defaults=layout_defaults,
        liquid_class_name=liquid_class_name,
    )
    bundle = GroundingBundle(
        workspace=bundle.workspace,
        valid_slots=frozenset(_placement_slots(bundle.valid_slots, placement_locations)),
        labware_defaults=bundle.labware_defaults,
        layout_defaults=bundle.layout_defaults,
        liquid_class_name=bundle.liquid_class_name,
    )
    _write_current_worktable_snapshot(
        _current_worktable_path(),
        bundle,
        workspace=workspace,
        placement_locations=placement_locations,
    )
    return bundle


def _build_grounding_bundle_from_catalog(
    *,
    workspace_name: str,
    workspace_guid: str,
    labware_defaults: dict[str, str],
    layout_defaults: dict[str, dict[str, Any]],
    liquid_class_name: str,
) -> tuple[GroundingBundle, Any]:
    """Build the current worktable snapshot from the indexed `.xwsp` once."""

    ws_entry = resolve_workspace_by_guid(workspace_guid) if workspace_guid else None
    if ws_entry is None and workspace_name:
        ws_entry = resolve_workspace_by_name(workspace_name)
    if ws_entry is None:
        raise GroundingError(
            FailureCategory.WORKSPACE_SLOT_INVALIDITY,
            f"Workspace binding could not be resolved locally: name={workspace_name!r}, guid={workspace_guid!r}.",
        )

    workspace = load_xwsp(ws_entry.file_path)
    valid_slots: set[tuple[str, int]] = set()
    counters: dict[str, int] = {}
    seen: set[tuple[tuple[int, ...], str]] = set()
    for site_path, location_name in workspace.available_sites:
        if not site_path or not location_name:
            continue
        key = (site_path, location_name)
        if key in seen:
            continue
        seen.add(key)
        counters[location_name] = counters.get(location_name, 0) + 1
        valid_slots.add((location_name, counters[location_name]))

    return GroundingBundle(
        workspace=WorkspaceBinding(name=workspace.name, guid=workspace.guid),
        valid_slots=frozenset(valid_slots),
        labware_defaults=labware_defaults,
        layout_defaults=layout_defaults,
        liquid_class_name=liquid_class_name,
    ), workspace


def _current_worktable_path() -> Path:
    configured = os.environ.get(CURRENT_WORKTABLE_ENV)
    if configured:
        return Path(configured)
    return Path.cwd() / "current_worktable.py"


def _load_current_worktable_snapshot(path: Path) -> dict[str, Any] | None:
    return load_current_worktable_snapshot(path)


def load_current_worktable_snapshot(path: Path | None = None) -> dict[str, Any] | None:
    """Load the authoring-time current worktable snapshot, if present.

    The returned payload keeps the rich metadata written to
    ``current_worktable.py`` while normalizing the core fields used by
    workspace grounding.
    """
    if path is None:
        path = _current_worktable_path()
    if not path.exists():
        return None
    try:
        data = runpy.run_path(str(path))
    except Exception:
        return None
    if data.get("SCHEMA_VERSION") != CURRENT_WORKTABLE_SCHEMA_VERSION:
        return None
    workspace = data.get("WORKSPACE")
    valid_slots = data.get("VALID_SLOTS")
    if not isinstance(workspace, dict) or not isinstance(valid_slots, (list, tuple)):
        return None
    if not workspace.get("name") or not workspace.get("guid"):
        return None
    parsed_slots: list[tuple[str, int]] = []
    try:
        for item in valid_slots:
            location, position = item
            parsed_slots.append((str(location), int(position)))
    except Exception:
        return None
    grouped: dict[str, list[int]] = {}
    for location, position in parsed_slots:
        grouped.setdefault(location, []).append(position)
    for positions in grouped.values():
        positions.sort()
    return {
        "workspace": dict(workspace),
        "valid_slots": tuple(parsed_slots),
        "position_summary_by_location": dict(
            data.get("POSITION_SUMMARY_BY_LOCATION") or grouped
        ),
        "positions": list(data.get("POSITIONS") or ()),
        "occupants": list(data.get("OCCUPANTS") or ()),
        "compatibility_by_occupant": dict(data.get("COMPATIBILITY_BY_OCCUPANT") or {}),
        "accepts_by_occupied_carrier_site": dict(
            data.get("ACCEPTS_BY_OCCUPIED_CARRIER_SITE") or {}
        ),
    }


def _snapshot_matches(snapshot: dict[str, Any], *, name: str, guid: str) -> bool:
    workspace = snapshot.get("workspace") or {}
    snapshot_name = str(workspace.get("name") or "")
    snapshot_guid = str(workspace.get("guid") or "")
    if guid and guid != snapshot_guid:
        return False
    if name and name != snapshot_name:
        return False
    return True


def _write_current_worktable_snapshot(
    path: Path,
    bundle: GroundingBundle,
    *,
    workspace: Any | None = None,
    placement_locations: set[str] | None = None,
) -> None:
    slots = _placement_slots(bundle.valid_slots, placement_locations or set())
    payload = _current_worktable_payload(bundle, slots, workspace=workspace)
    content = (
        '"""Current FluentControl worktable snapshot for authoring.\n\n'
        "Regenerated automatically when a different workspace name or GUID is supplied.\n"
        '"""\n\n'
        f"SCHEMA_VERSION = {CURRENT_WORKTABLE_SCHEMA_VERSION}\n"
        f"WORKSPACE = {pformat(payload['workspace'], width=100)}\n"
        f"VALID_SLOTS = {pformat(slots, width=100)}\n"
        f"POSITION_SUMMARY_BY_LOCATION = {pformat(payload['position_summary_by_location'], width=100)}\n"
        f"POSITIONS = {pformat(payload['positions'], width=100)}\n"
        f"OCCUPANTS = {pformat(payload['occupants'], width=100)}\n"
        f"COMPATIBILITY_BY_OCCUPANT = {pformat(payload['compatibility_by_occupant'], width=100)}\n"
        f"ACCEPTS_BY_OCCUPIED_CARRIER_SITE = {pformat(payload['accepts_by_occupied_carrier_site'], width=100)}\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Snapshotting is an optimization and operator convenience; the catalog
        # build above has already produced the authoritative in-memory bundle.
        return


def _placement_location_allowlist(config: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for item in config.get("grounding_defaults", {}).get("layout", {}).values():
        if isinstance(item, dict) and item.get("location"):
            out.add(str(item["location"]))
    placement = config.get("labware_placement", {})
    for group in placement.values():
        if not isinstance(group, dict):
            continue
        for rule in group.values():
            if not isinstance(rule, dict):
                continue
            for location in rule.get("valid_locations") or ():
                out.add(str(location))
    return out


def _placement_slots(
    valid_slots: frozenset[tuple[str, int]],
    placement_locations: set[str],
) -> list[tuple[str, int]]:
    filtered = sorted(
        slot
        for slot in valid_slots
        if _is_author_facing_location(slot[0], placement_locations)
    )
    return filtered or sorted(valid_slots)


def _is_author_facing_location(location: str, placement_locations: set[str]) -> bool:
    if location in placement_locations:
        return True
    if location.endswith("_Pos"):
        return True
    if re.fullmatch(r"WS_\d+ml_\d+", location):
        return True
    if location in {
        "MCA384_Diti_ActiveNest",
        "ThruDeckWaste_Pos",
        "Regrip_Pos",
        "InfiniteM200_Pos",
    }:
        return True
    return False


def _current_worktable_payload(
    bundle: GroundingBundle,
    slots: list[tuple[str, int]],
    *,
    workspace: Any | None = None,
) -> dict[str, Any]:
    if workspace is None:
        workspace_entry = (
            resolve_workspace_by_guid(bundle.workspace.guid)
            or resolve_workspace_by_name(bundle.workspace.name)
        )
        workspace = load_xwsp(workspace_entry.file_path) if workspace_entry is not None else None
    occupants = list(getattr(workspace, "occupants", ()) or ())
    site_paths_by_slot = _site_paths_by_slot(workspace)
    occupant_by_site_path = {tuple(occ.site_path): occ for occ in occupants}
    occupant_by_slot: dict[tuple[str, int], Any] = {}
    for occ in occupants:
        loc = occ.base_location_identifier
        if not loc:
            continue
        pos = _position_for_site_path(tuple(occ.site_path))
        occupant_by_slot[(loc, pos)] = occ

    positions: list[dict[str, Any]] = []
    for location, position in slots:
        site_path = site_paths_by_slot.get((location, position))
        occ = occupant_by_slot.get((location, position))
        if occ is None and site_path is not None:
            occ = occupant_by_site_path.get(site_path)
        positions.append({
            "location": location,
            "position": position,
            "site_path": _site_path_string(site_path),
            "occupied_by": getattr(occ, "catalog_name", None) if occ is not None else None,
        })

    occupant_rows = [_occupant_payload(occ) for occ in occupants]
    names = sorted({row["resolved_catalog_name"] for row in occupant_rows if row.get("resolved_catalog_name")})
    compatibility_by_occupant = {
        name: _component_compatibility_payload(name)
        for name in names
    }
    accepts_by_carrier_site = {
        name: _component_site_acceptance_payload(name)
        for name in names
        if _component_site_acceptance_payload(name)
    }
    return {
        "workspace": {
            "name": bundle.workspace.name,
            "guid": bundle.workspace.guid,
            "base_worktable_name": getattr(workspace, "base_worktable_name", None),
            "base_worktable_guid": getattr(workspace, "base_worktable_guid", None),
        },
        "position_summary_by_location": _position_summary(slots),
        "positions": positions,
        "occupants": occupant_rows,
        "compatibility_by_occupant": compatibility_by_occupant,
        "accepts_by_occupied_carrier_site": accepts_by_carrier_site,
    }


def _site_paths_by_slot(workspace: Any) -> dict[tuple[str, int], tuple[int, ...]]:
    if workspace is None:
        return {}
    counters: dict[str, int] = {}
    seen: set[tuple[tuple[int, ...], str]] = set()
    out: dict[tuple[str, int], tuple[int, ...]] = {}
    for site_path, location_name in workspace.available_sites:
        if not site_path or not location_name:
            continue
        key = (tuple(site_path), str(location_name))
        if key in seen:
            continue
        seen.add(key)
        counters[str(location_name)] = counters.get(str(location_name), 0) + 1
        out[(str(location_name), counters[str(location_name)])] = tuple(site_path)
    return out


def _position_for_site_path(site_path: tuple[int, ...]) -> int:
    if len(site_path) >= 3 and site_path[-1] == 0:
        return site_path[-2] + 1
    return site_path[-1] + 1


def _site_path_string(site_path: tuple[int, ...] | None) -> str | None:
    if site_path is None:
        return None
    return "/".join(str(part) for part in site_path)


def _occupant_payload(occ: Any) -> dict[str, Any]:
    resolved = _resolve_catalog_name(occ.catalog_name)
    return {
        "catalog_name": occ.catalog_name,
        "resolved_catalog_name": resolved,
        "location": occ.base_location_identifier,
        "position": _position_for_site_path(tuple(occ.site_path)),
        "site_path": _site_path_string(tuple(occ.site_path)),
        "site_index": occ.site_index,
        "base_location_connector_identifier": occ.base_location_connector_identifier,
    }


def _position_summary(slots: list[tuple[str, int]]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for location, position in slots:
        out.setdefault(location, []).append(position)
    return out


def _component_compatibility_payload(name: str) -> dict[str, Any]:
    resolved_name = _resolve_catalog_name(name) or name
    row = resolve_by_name(resolved_name)
    stacks = find_legal_stacks(resolved_name)
    return {
        "catalog_name": resolved_name,
        "category": getattr(row, "category", None),
        "footprint": getattr(row, "footprint", None),
        "grip_modes": find_grip_modes(resolved_name),
        "stacks_above": [
            {"name": entry.name, "category": entry.category, "footprint": entry.footprint}
            for entry in stacks.get("above", [])
        ],
        "stacks_below": [
            {"name": entry.name, "category": entry.category, "footprint": entry.footprint}
            for entry in stacks.get("below", [])
        ],
        "used_in_workspaces": [
            {"name": workspace.name, "guid": workspace.guid}
            for workspace in find_workspaces_using(resolved_name)
        ],
    }


def _component_site_acceptance_payload(name: str) -> list[dict[str, Any]]:
    resolved_name = _resolve_catalog_name(name) or name
    row = resolve_by_name(resolved_name)
    if row is None:
        return []
    site_indices = _component_site_indices(resolved_name)
    out: list[dict[str, Any]] = []
    for site_index, footprint, grip_modes in site_indices:
        accepted = find_labware_for_site(resolved_name, site_index)
        out.append({
            "site_index": site_index,
            "accepted_footprint": footprint,
            "grip_modes": list(grip_modes),
            "compatible_labware": [
                {
                    "name": entry.name,
                    "category": entry.category,
                    "footprint": entry.footprint,
                }
                for entry in accepted
            ],
        })
    return out


def _component_site_indices(name: str) -> list[tuple[int, str | None, tuple[str, ...]]]:
    resolved_name = _resolve_catalog_name(name) or name
    with open_index() as conn:
        row = conn.execute(
            "SELECT guid FROM components WHERE name = ? LIMIT 1",
            (resolved_name,),
        ).fetchone()
        if row is None:
            return []
        rows = conn.execute(
            """
            SELECT site_index, footprint, grip_modes
            FROM component_sites
            WHERE component_guid = ?
            ORDER BY site_index
            """,
            (row["guid"],),
        ).fetchall()
    out: list[tuple[int, str | None, tuple[str, ...]]] = []
    for item in rows:
        out.append((
            int(item["site_index"]),
            item["footprint"],
            _json_tuple(item["grip_modes"]),
        ))
    return out


def _resolve_catalog_name(name: str | None) -> str | None:
    if not name:
        return None
    if resolve_by_name(name) is not None:
        return name
    stripped = re.sub(r"\[\d+\]$", "", name).strip()
    if stripped and resolve_by_name(stripped) is not None:
        return stripped
    return None


def _json_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        parsed = yaml.safe_load(value)
    except Exception:
        return ()
    if isinstance(parsed, list):
        return tuple(str(item) for item in parsed)
    return ()
