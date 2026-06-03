"""Service layer for the local workspace setup helper.

The functions in this module are intentionally UI-agnostic so the HTTP
wrapper and tests exercise the same catalog/profile behavior.
"""

from __future__ import annotations

import json
import hashlib
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..authoring.grounding import (
    _build_grounding_bundle_from_catalog,
    _current_worktable_payload,
    _placement_location_allowlist,
    _placement_slots,
    _write_current_worktable_snapshot,
    load_generation_config,
)
from ..authoring.tools import _catalog_entry, _python_class_for, _semantic_category
from ..catalog.catalog import (
    find_components,
    list_by_category,
    open_index,
    resolve_by_name,
    resolve_workspace_by_guid,
    resolve_workspace_by_name,
)


PROFILE_SCHEMA_VERSION = 1
DEFAULT_FC_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\DataBase")
DEFAULT_INSTRUMENT_CONFIG_DIR = Path(r"C:\ProgramData\Tecan\VisionX\InstrumentConfigurations")
COMMON_LABWARE_CATEGORIES: tuple[dict[str, Any], ...] = (
    {"name": "", "label": "All labware"},
    {"name": "plate", "label": "Plates"},
    {"name": "tip_box", "label": "Tip boxes"},
    {"name": "trough", "label": "Reservoirs and troughs"},
    {"name": "magnet_rack", "label": "Magnet racks"},
    {"name": "tube_rack", "label": "Tube racks"},
    {"name": "waste_chute", "label": "Waste chutes"},
)


@dataclass(frozen=True)
class ProfilePaths:
    root: Path
    profile_json: Path
    current_worktable: Path
    generation_yaml: Path
    deck_skill: Path
    readme: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "profile_json": str(self.profile_json),
            "current_worktable": str(self.current_worktable),
            "generation_yaml": str(self.generation_yaml),
            "deck_skill": str(self.deck_skill),
            "readme": str(self.readme),
        }


@dataclass(frozen=True)
class _ArrangementTemplateGeometry:
    internal_id: str
    sites_in_x: int
    sites_in_y: int
    sites_in_z: int
    site_spacing_mm: tuple[float, float, float]
    position_in_parent_mm: tuple[float, float, float]
    site_offsets_mm: dict[int, tuple[float, float, float]]
    site_template_guids: dict[int, str]


def list_workspaces() -> dict[str, Any]:
    with open_index() as conn:
        rows = conn.execute(
            "SELECT name, guid FROM workspaces ORDER BY name"
        ).fetchall()
    return {
        "ok": True,
        "workspaces": [{"name": row["name"], "guid": row["guid"]} for row in rows],
    }


def list_configurations(install_path: Path | str | None = None) -> dict[str, Any]:
    root = _configuration_dir(install_path)
    configs = []
    for path in sorted(root.glob("*.config")):
        summary = _configuration_summary(path)
        if summary is not None:
            configs.append(summary)
    preferred = next(
        (item["guid"] for item in configs if "2203009762" in item["name"]),
        configs[0]["guid"] if configs else None,
    )
    return {"ok": True, "preferred_guid": preferred, "configurations": configs}


def configuration_detail(
    guid: str | None = None,
    *,
    install_path: Path | str | None = None,
) -> dict[str, Any]:
    root = _configuration_dir(install_path)
    path = _configuration_path(root, guid)
    summary = _configuration_summary(path)
    if summary is None:
        raise ValueError(f"Configuration {guid!r} could not be parsed")
    devices = _instrument_devices(path)
    summary.update({
        "devices": devices,
        "device_count": len(devices),
    })
    return {"ok": True, "configuration": summary}


def workspace_detail(name: str | None = None, guid: str | None = None) -> dict[str, Any]:
    bundle, workspace = _workspace_bundle(name=name, guid=guid)
    config = load_generation_config()
    placement_locations = _placement_location_allowlist(config)
    slots = _placement_slots(bundle.valid_slots, placement_locations)
    payload = _current_worktable_payload(bundle, slots, workspace=workspace)
    payload["ok"] = True
    payload["valid_slots"] = slots
    payload["deck_coordinates"] = _deck_coordinates(payload, workspace=workspace)
    payload["deck_frame"] = _deck_frame(workspace)
    payload["workspace_source"] = _workspace_source_metadata(workspace)
    payload["deck_blocks"] = _deck_blocks(payload)
    payload["common_labware_categories"] = list(COMMON_LABWARE_CATEGORIES)
    payload["default_common_labware"] = suggest_common_labware(
        workspace_name=bundle.workspace.name,
        workspace_guid=bundle.workspace.guid,
    )["common_labware"]
    payload["liquid_class"] = str(config.get("liquid_class", {}).get("name") or "Water Free Single")
    return payload


def search_labware(
    query: str = "",
    category: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = (query or "").strip()
    category = (category or "").strip() or None
    if category and not query:
        rows = list_by_category(category)
    else:
        rows = find_components(query or "")
    if category:
        rows = [row for row in rows if _semantic_category(row.name, row.category) == category]
    matches = []
    for row in rows[: max(1, min(int(limit or 50), 200))]:
        item = _catalog_entry(row)
        py_class = _python_class_for(row.name, str(item.get("category") or row.category))
        if py_class:
            item["python_class"] = py_class
        matches.append(item)
    return {"ok": True, "matches": matches}


def list_liquid_classes() -> dict[str, Any]:
    with open_index() as conn:
        rows = conn.execute(
            "SELECT name, guid, head, supported_heads FROM liquid_classes ORDER BY name"
        ).fetchall()
    return {
        "ok": True,
        "liquid_classes": [
            {
                "name": row["name"],
                "guid": row["guid"],
                "head": row["head"],
                "supported_heads": _json_list(row["supported_heads"]),
            }
            for row in rows
        ],
    }


def suggest_common_labware(
    *,
    workspace_name: str | None = None,
    workspace_guid: str | None = None,
) -> dict[str, Any]:
    bundle, _workspace = _workspace_bundle(name=workspace_name, guid=workspace_guid)
    config = load_generation_config()
    labware_defaults = dict(config.get("grounding_defaults", {}).get("labware", {}))
    common: list[dict[str, Any]] = []
    seen: set[str] = set()
    for family, catalog_name in labware_defaults.items():
        if not catalog_name or catalog_name in seen:
            continue
        seen.add(catalog_name)
        row = resolve_by_name(catalog_name) if catalog_name else None
        if row is None:
            continue
        category = _semantic_category(row.name, row.category)
        common.append({
            "label": _label_from_family(family, row.name),
            "catalog_name": row.name,
            "category": category,
            "python_class": _python_class_for(row.name, str(category)) if category else None,
            "preferred_location": None,
            "preferred_position": None,
        })
    return {
        "ok": True,
        "workspace": {
            "name": bundle.workspace.name,
            "guid": bundle.workspace.guid,
        },
        "common_labware": common,
        "source": "deterministic_defaults",
        "lm_assist": "not_configured",
    }


def suggest_roles(
    *,
    workspace_name: str | None = None,
    workspace_guid: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for the first workspace-app iteration."""
    suggested = suggest_common_labware(
        workspace_name=workspace_name,
        workspace_guid=workspace_guid,
    )
    return {**suggested, "roles": {}}


def save_profile(payload: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, Any]:
    profile_name = _safe_profile_name(str(payload.get("profile_name") or "workspace"))
    workspace_payload = payload.get("workspace") or {}
    workspace_name = str(workspace_payload.get("name") or payload.get("workspace_name") or "")
    workspace_guid = str(workspace_payload.get("guid") or payload.get("workspace_guid") or "")
    configuration_payload = payload.get("configuration") or {}
    configuration_guid = str(configuration_payload.get("guid") or payload.get("configuration_guid") or "")
    if not workspace_name and not workspace_guid:
        raise ValueError("workspace.name or workspace.guid is required")

    bundle, workspace = _workspace_bundle(name=workspace_name, guid=workspace_guid)
    workspace_source = _workspace_source_metadata(workspace)
    _validate_workspace_source_unchanged(payload.get("workspace_source"), workspace_source)
    common_labware = _validate_common_labware(
        payload.get("common_labware") or payload.get("roles") or [],
        valid_slots=set(bundle.valid_slots),
    )
    liquid_class = str(payload.get("liquid_class") or "Water Free Single")
    root = (base_dir or Path("build") / "workspaces") / profile_name
    deck_skill_name = f"deck-{profile_name}"
    paths = ProfilePaths(
        root=root,
        profile_json=root / "workspace_profile.json",
        current_worktable=root / "current_worktable.py",
        generation_yaml=root / "generation.profile.yaml",
        deck_skill=root / f"{deck_skill_name}.md",
        readme=root / "README.md",
    )
    root.mkdir(parents=True, exist_ok=True)

    config = load_generation_config()
    placement_locations = _placement_location_allowlist(config)
    slots = _placement_slots(bundle.valid_slots, placement_locations)
    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_name": profile_name,
        "configuration": _profile_configuration(configuration_guid),
        "workspace": {
            "name": bundle.workspace.name,
            "guid": bundle.workspace.guid,
            "base_worktable_name": getattr(workspace, "base_worktable_name", None),
            "base_worktable_guid": getattr(workspace, "base_worktable_guid", None),
        },
        "workspace_source": workspace_source,
        "deck": _current_worktable_payload(bundle, slots, workspace=workspace),
        "common_labware": common_labware,
        "liquid_class": {"name": liquid_class},
    }
    paths.profile_json.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    _write_current_worktable_snapshot(
        paths.current_worktable,
        bundle,
        workspace=workspace,
        placement_locations=placement_locations,
    )
    paths.generation_yaml.write_text(
        yaml.safe_dump(_generation_profile(profile), sort_keys=False),
        encoding="utf-8",
    )
    paths.deck_skill.write_text(
        _profile_deck_skill(deck_skill_name, profile, slots),
        encoding="utf-8",
    )
    paths.readme.write_text(_profile_readme(profile, paths), encoding="utf-8")
    return {"ok": True, "profile": profile, "paths": paths.to_dict()}


def _workspace_bundle(name: str | None = None, guid: str | None = None):
    ws_by_guid = resolve_workspace_by_guid(guid) if guid else None
    ws_by_name = resolve_workspace_by_name(name) if name else None
    ws = ws_by_guid or ws_by_name
    if ws is None:
        raise ValueError(f"Workspace not found: name={name!r}, guid={guid!r}")
    config = load_generation_config()
    bundle, workspace = _build_grounding_bundle_from_catalog(
        workspace_name=ws.name,
        workspace_guid=ws.guid,
        labware_defaults=dict(config.get("grounding_defaults", {}).get("labware", {})),
        layout_defaults=dict(config.get("grounding_defaults", {}).get("layout", {})),
        liquid_class_name=str(config.get("liquid_class", {}).get("name") or "Water Free Single"),
    )
    return bundle, workspace


def _profile_configuration(guid: str) -> dict[str, Any] | None:
    if not guid:
        return None
    try:
        detail = configuration_detail(guid)["configuration"]
    except Exception:
        return {"guid": guid}
    return {
        "guid": detail["guid"],
        "name": detail["name"],
        "id_prefix": detail.get("id_prefix"),
        "file_path": detail["file_path"],
        "device_count": detail.get("device_count"),
    }


def _validate_common_labware(
    raw_items: Any,
    *,
    valid_slots: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    if isinstance(raw_items, dict):
        raw_iter = list(raw_items.values())
    else:
        raw_iter = list(raw_items or [])
    out: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_slots: dict[tuple[str, int], str] = {}
    for index, raw in enumerate(raw_iter, start=1):
        if not isinstance(raw, dict):
            continue
        catalog_name = str(raw.get("catalog_name") or "").strip()
        if not catalog_name:
            continue
        row = resolve_by_name(catalog_name)
        if row is None:
            raise ValueError(f"common labware #{index}: catalog item {catalog_name!r} is not installed")
        if row.name in seen_names:
            continue
        seen_names.add(row.name)
        preferred_location = str(raw.get("preferred_location") or raw.get("location") or "").strip() or None
        preferred_position_raw = raw.get("preferred_position", raw.get("position"))
        preferred_position: int | None = None
        if preferred_location or preferred_position_raw not in (None, ""):
            try:
                preferred_position = int(preferred_position_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{row.name}: preferred_position must be an integer") from exc
            slot = (preferred_location or "", preferred_position)
            if slot not in valid_slots:
                raise ValueError(f"{row.name}: preferred slot {slot!r} is not valid for this workspace")
            previous = seen_slots.get(slot)
            if previous is not None:
                raise ValueError(f"{row.name}: preferred slot {slot!r} is already assigned to {previous}")
            seen_slots[slot] = row.name
        category = _semantic_category(row.name, row.category)
        out.append({
            "label": str(raw.get("label") or row.name),
            "catalog_name": row.name,
            "category": category,
            "python_class": _python_class_for(row.name, str(category)) if category else None,
            "preferred_location": preferred_location,
            "preferred_position": preferred_position,
        })
    return out


def _generation_profile(profile: dict[str, Any]) -> dict[str, Any]:
    common = profile["common_labware"]
    labware_names = [item["catalog_name"] for item in common if item.get("catalog_name")]
    return {
        "worktable": {
            "name": profile["workspace"]["name"],
            "guid": profile["workspace"]["guid"],
            "type_id": "WorktableWorkspace",
        },
        "liquid_class": profile["liquid_class"],
        "configuration": profile.get("configuration"),
        "workspace_profile": {
            "common_labware": common,
            "labware_names": labware_names,
        },
        "lab_scope": {
            "labware": labware_names,
            "liquid_classes": [profile["liquid_class"]["name"]],
        },
    }


def _profile_deck_skill(
    skill_name: str,
    profile: dict[str, Any],
    slots: list[tuple[str, int]],
) -> str:
    """Render a ``--lab-scope skills`` deck skill from a saved profile.

    Mirrors the shape of the shipped ``deck-sat-780.md`` (frontmatter +
    workspace binding + valid-position table + role→slot layout) but every
    fact is driven by this workspace's profile, so authoring can target this
    deck instead of the hardcoded default. Drop it into the active skills dir
    (or point ``generation.yaml``'s ``lab_scope.skills.dir`` at it) to make it
    the deck the LM pre-pass loads.
    """
    workspace = profile.get("workspace") or {}
    name = str(workspace.get("name") or "")
    guid = str(workspace.get("guid") or "")
    base_name = workspace.get("base_worktable_name") or "?"
    base_guid = workspace.get("base_worktable_guid") or "?"

    positions_by_location: dict[str, list[int]] = {}
    for location, position in slots:
        positions_by_location.setdefault(str(location), []).append(int(position))

    position_rows = "\n".join(
        f"| `{location}` | {', '.join(str(p) for p in sorted(set(positions)))} |"
        for location, positions in sorted(positions_by_location.items())
    )

    layout_rows = "\n".join(
        f"| {item.get('label') or item.get('catalog_name')} "
        f"| `{item['preferred_location']}` | {item['preferred_position']} |"
        for item in profile.get("common_labware") or []
        if item.get("preferred_location") and item.get("preferred_position") is not None
    )
    layout_section = (
        "\n## Default layout (role → slot)\n\n"
        "| Role | Location | Site |\n|---|---|---|\n" + layout_rows + "\n"
        if layout_rows
        else ""
    )
    liquid_class = str((profile.get("liquid_class") or {}).get("name") or "Water Free Single")

    description = (
        f"The {name} deck profile ({base_name}) — workspace GUID, valid deck "
        f"positions, and the role-to-slot layout captured by the "
        f"{profile.get('profile_name')} workspace profile. Emitted by the "
        f"workspace setup app; swap this in to target this deck."
    )

    return (
        "---\n"
        f"name: {skill_name}\n"
        "axis: deck\n"
        f"description: {description}\n"
        "always_on: true\n"
        "---\n"
        "## Deck / workspace\n\n"
        f"- Workspace: `{name}`\n"
        f"- Workspace GUID: `{guid}`\n"
        f"- Base deck: `{base_name}` (`{base_guid}`)\n"
        f"- Always: `Worktable.from_workspace(\"{name}\", "
        f"workspace_guid=\"{guid}\", auto_place=False, ...)`\n\n"
        "## Valid deck positions\n\n"
        "| Location | Valid positions |\n|---|---|\n"
        f"{position_rows}\n"
        f"{layout_section}\n"
        "## Notes\n\n"
        "- Use ONLY the exact location keys above; do not invent location names.\n"
        f"- Default liquid class for this profile: `{liquid_class}`.\n"
    )


def _profile_readme(profile: dict[str, Any], paths: ProfilePaths) -> str:
    source = profile.get("workspace_source") or {}
    source_lines = ""
    if source:
        source_lines = (
            f"Workspace source: {source.get('file_path')}\n"
            f"Workspace source SHA-256: {source.get('sha256')}\n"
            f"Base deck component: {source.get('base_worktable_component_name')} / "
            f"{source.get('base_worktable_component_guid')}\n"
        )
    return (
        f"# {profile['profile_name']}\n\n"
        "Use this profile with fluentvibe authoring by setting:\n\n"
        "```powershell\n"
        f"$env:FLUENTVIBE_CURRENT_WORKTABLE_PATH=\"{paths.current_worktable}\"\n"
        "python -m fluentvibe.cli chat --lab-scope skills\n"
        "```\n\n"
        f"Workspace: {profile['workspace']['name']} / {profile['workspace']['guid']}\n"
        f"{source_lines}\n"
        f"Deck skill: `{paths.deck_skill.name}` — the `--lab-scope skills` deck\n"
        "profile for this workspace. The current-worktable snapshot only grounds\n"
        "placement; the deck the LM binds to comes from the active deck skill, so\n"
        "drop this file into the active skills dir (replacing the default deck\n"
        "skill) to author against this workspace instead of the shipped default.\n"
    )


def _configuration_dir(install_path: Path | str | None = None) -> Path:
    if install_path is not None:
        return Path(install_path)
    return DEFAULT_INSTRUMENT_CONFIG_DIR


def _configuration_path(root: Path, guid: str | None) -> Path:
    if guid:
        for suffix in (".config", ".xcfg", ".xvfg"):
            path = root / f"{guid}{suffix}"
            if path.exists():
                return path
    preferred = root / "TECAN,FLUENT,2203009762.config"
    if preferred.exists():
        return preferred
    paths = sorted(root.glob("*.config"))
    if not paths:
        raise ValueError(f"No FluentControl configuration files found in {root}")
    return paths[0]


def _configuration_summary(path: Path) -> dict[str, Any] | None:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    id_prefix = root.attrib.get("IdPrefix")
    serial = _serial_from_config(path, id_prefix)
    return {
        "guid": path.stem,
        "name": path.stem,
        "kind": _xml_local(root.tag),
        "extension": path.suffix,
        "file_path": str(path),
        "id_prefix": id_prefix,
        "serial_number": serial,
        "is_instrument_specific": True,
    }


def _instrument_devices(path: Path) -> list[dict[str, Any]]:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    id_prefix = root.attrib.get("IdPrefix") or ""
    for elem in list(root):
        if not isinstance(elem.tag, str) or _xml_local(elem.tag) != "Configuration":
            continue
        id_suffix = elem.attrib.get("IdSuffix") or ""
        type_name = elem.attrib.get("Type") or ""
        out.append({
            "id_suffix": id_suffix,
            "available_id": f"{id_prefix}/{id_suffix}" if id_prefix and id_suffix else id_suffix,
            "type": type_name,
            "simulated": elem.attrib.get("Simulated"),
            "device": _device_label(id_suffix, type_name),
        })
    return out


def _serial_from_config(path: Path, id_prefix: str | None) -> str | None:
    if id_prefix and id_prefix.startswith("USB:"):
        return id_prefix.rsplit(",", 1)[-1]
    match = re.search(r"TECAN,FLUENT,(\d+)", path.name)
    return match.group(1) if match else None


def _device_label(id_suffix: str, type_name: str) -> str:
    if ":" in id_suffix:
        return id_suffix.split(":", 1)[0]
    tail = type_name.rsplit(".", 2)[-2:] if type_name else []
    return ".".join(tail) if tail else id_suffix


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_find(elem: ET.Element | None, local_name: str) -> ET.Element | None:
    if elem is None:
        return None
    for child in elem.iter():
        if isinstance(child.tag, str) and _xml_local(child.tag) == local_name:
            return child
    return None


def _xml_text(elem: ET.Element | None) -> str | None:
    if elem is None or not elem.text:
        return None
    text = elem.text.strip()
    return text or None


def _safe_profile_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    return cleaned or "workspace"


def _label_from_family(family: str, fallback: str) -> str:
    cleaned = family.replace("mca96", "MCA96").replace("fca", "FCA")
    cleaned = cleaned.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else fallback


def _deck_coordinates(
    payload: dict[str, Any],
    *,
    workspace: Any | None = None,
) -> list[dict[str, Any]]:
    positions = list(payload.get("positions") or [])
    coords_by_key = _workspace_geometry_by_slot(workspace)
    coords_by_path_location = {
        (path, location): coord
        for (path, location, _position), coord in coords_by_key.items()
    }
    coords: list[dict[str, Any]] = []
    for row in positions:
        key = (
            str(row.get("site_path") or ""),
            str(row.get("location") or ""),
            int(row.get("position") or 0),
        )
        coord = coords_by_key.get(key)
        if coord is None:
            coord = coords_by_path_location.get((key[0], key[1]))
        if coord is None:
            coord = {
                "x_mm": None,
                "y_mm": None,
                "coordinate_source": "unresolved",
            }
        coords.append({**row, **coord})
    if not coords:
        return []
    xs = [row["x_mm"] for row in coords if row.get("x_mm") is not None]
    ys = [row["y_mm"] for row in coords if row.get("y_mm") is not None]
    if not xs or not ys:
        return _fallback_deck_coordinates(positions)
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    x_span = max(1.0, max_x - min_x)
    y_span = max(1.0, max_y - min_y)
    out: list[dict[str, Any]] = []
    for row in coords:
        x = row.get("x_mm")
        y = row.get("y_mm")
        out.append({
            **row,
            "x_pct": round(4.0 + ((x - min_x) / x_span) * 90.0, 2) if x is not None else 5.0,
            "y_pct": round(7.0 + ((max_y - y) / y_span) * 84.0, 2) if y is not None else 7.0,
        })
    return _spread_overlapping_display_positions(out)


def _fallback_deck_coordinates(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[tuple[dict[str, Any], list[int]]] = []
    for row in positions:
        parts = [
            int(part)
            for part in str(row.get("site_path") or "").split("/")
            if part.isdigit()
        ]
        parsed.append((row, parts))
    top_values = [parts[0] for _, parts in parsed if parts]
    min_top = min(top_values) if top_values else 0
    max_top = max(top_values) if top_values else 1
    span = max(1, max_top - min_top)
    lane_by_location = _lane_by_location([row for row, _ in parsed])
    out: list[dict[str, Any]] = []
    for row, parts in parsed:
        location = str(row.get("location") or "")
        top = parts[0] if parts else int(row.get("position") or 0)
        leaf = parts[-2] if len(parts) >= 3 and parts[-1] == 0 else (parts[-1] if parts else int(row.get("position") or 0))
        lane = lane_by_location.get(location, 0)
        lane_count = max(1, len(set(lane_by_location.values())))
        x_pct = 4.0 + ((top - min_top) / span) * 88.0
        y_pct = 8.0 + (lane / max(1, lane_count - 1)) * 78.0 if lane_count > 1 else 45.0
        x_pct = min(94.0, max(3.0, x_pct + (leaf % 3) * 1.5))
        out.append({
            **row,
            "x_pct": round(x_pct, 2),
            "y_pct": round(y_pct, 2),
        })
    return out


def _workspace_geometry_by_slot(workspace: Any | None) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(getattr(workspace, "file_path", "") or "")
    base_guid = str(getattr(workspace, "base_worktable_guid", "") or "")
    if not path.exists() or not base_guid:
        return {}
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}

    component_templates: dict[str, dict[str, _ArrangementTemplateGeometry]] = {}

    def templates_for(component_guid: str | None) -> dict[str, _ArrangementTemplateGeometry]:
        if not component_guid:
            return {}
        component_guid = str(component_guid)
        if component_guid not in component_templates:
            component_templates[component_guid] = _arrangement_templates_for_component(component_guid)
        return component_templates[component_guid]

    out: dict[tuple[str, str, int], dict[str, Any]] = {}

    def component_node_guid(node: ET.Element | None) -> str | None:
        guid = _xml_direct_child_text(node, "CarrierOrLabwareTemplateGUID")
        return guid or None

    def connected_or_site(value_node: ET.Element) -> ET.Element:
        connected = next(iter(_xml_direct_children(value_node, "ConnectedComponent")), None)
        if connected is not None and connected.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            connected = None
        return connected if connected is not None else value_node

    def public_location(value_node: ET.Element, component_node: ET.Element) -> str | None:
        template_guid = _xml_direct_child_text(value_node, "BaseTemplateGuid")
        if template_guid:
            site = _site_location_name(template_guid)
            if site:
                return site
        raw = _xml_direct_child_text(component_node, "BaseLocationIdentifier")
        if raw and not _looks_like_guid(raw):
            return raw
        return None

    def record_site(
        *,
        site_path: tuple[int, ...],
        value_node: ET.Element,
        component_node: ET.Element,
        origin: tuple[float, float, float],
    ) -> None:
        location = public_location(value_node, component_node)
        if not location:
            return
        position = _slot_position(site_path)
        out[("/".join(str(part) for part in site_path), location, position)] = {
            "x_mm": round(float(origin[0]), 3),
            "y_mm": round(float(origin[1]), 3),
            "z_mm": round(float(origin[2]), 3),
            **_site_dimensions(value_node),
            "coordinate_source": "workspace_arrangement_template",
        }

    def walk_sites(
        sites_node: ET.Element | None,
        *,
        parent_guid: str | None,
        arrangement: _ArrangementTemplateGeometry | None,
        parent_origin: tuple[float, float, float],
        site_path: tuple[int, ...],
    ) -> None:
        if sites_node is None:
            return
        for node in list(sites_node):
            if not isinstance(node.tag, str) or not _xml_local(node.tag).startswith("KeyValueOfintSite"):
                continue
            key = _xml_int(next(iter(_xml_direct_children(node, "Key")), None))
            value_node = next(iter(_xml_direct_children(node, "Value")), None)
            if key is None or value_node is None:
                continue
            current_path = site_path + (key,)
            site_origin = _site_origin(parent_origin, arrangement, key)
            component_node = connected_or_site(value_node)
            record_site(
                site_path=current_path,
                value_node=value_node,
                component_node=component_node,
                origin=site_origin,
            )
            child_guid = component_node_guid(component_node) or parent_guid
            walk_component_arrangements(
                component_node,
                component_guid=child_guid,
                component_origin=site_origin,
                site_path=current_path,
            )

    def walk_component_arrangements(
        component_node: ET.Element | None,
        *,
        component_guid: str | None,
        component_origin: tuple[float, float, float],
        site_path: tuple[int, ...],
    ) -> None:
        arrangements_node = next(iter(_xml_direct_children(component_node, "Arrangements")), None)
        if arrangements_node is None:
            return
        templates = templates_for(component_guid)
        ordered_templates = list(templates.values())
        arrangement_index = 0
        for arrangement_node in list(arrangements_node):
            if not isinstance(arrangement_node.tag, str) or _xml_local(arrangement_node.tag) != "Arrangement":
                continue
            template_id = _xml_direct_child_text(arrangement_node, "TemplateID")
            arrangement = templates.get(template_id or "")
            if arrangement is None and arrangement_index < len(ordered_templates):
                arrangement = ordered_templates[arrangement_index]
            walk_sites(
                _xml_find(arrangement_node, "Sites"),
                parent_guid=component_guid,
                arrangement=arrangement,
                parent_origin=component_origin,
                site_path=site_path,
            )
            arrangement_index += 1

    payload = _xml_find(root, "Payload")
    payload_data = _xml_find(payload, "PayloadData") if payload is not None else None
    worktables = next(
        (
            elem
            for elem in (payload_data.iter() if payload_data is not None else [])
            if isinstance(elem.tag, str) and _xml_local(elem.tag) == "Worktables"
        ),
        None,
    )
    if worktables is None:
        return out
    for kv in list(worktables):
        if not isinstance(kv.tag, str):
            continue
        frame = _xml_find(_xml_find(kv, "Value"), "Frame")
        walk_component_arrangements(
            frame,
            component_guid=base_guid,
            component_origin=(0.0, 0.0, 0.0),
            site_path=tuple(),
        )
    return out


def _deck_frame(workspace: Any | None) -> dict[str, Any]:
    base_guid = str(getattr(workspace, "base_worktable_guid", "") or "")
    if not base_guid:
        return {}
    with open_index() as conn:
        row = conn.execute(
            "SELECT name, file_path, dim_x_mm, dim_y_mm, dim_z_mm FROM components WHERE guid = ? LIMIT 1",
            (base_guid,),
        ).fetchone()
    if row is None:
        return {}
    return {
        "name": row["name"],
        "guid": base_guid,
        "width_mm": row["dim_x_mm"],
        "depth_mm": row["dim_y_mm"],
        "height_mm": row["dim_z_mm"],
        "grid_spacing_mm": 25.0,
        "front_grid_origin_x_mm": 84.5,
        "front_grid_y_mm": 80.0,
        "source": "base_worktable_component",
    }


def _workspace_source_metadata(workspace: Any | None) -> dict[str, Any]:
    file_path = Path(getattr(workspace, "file_path", "") or "")
    metadata: dict[str, Any] = {
        "file_path": str(file_path) if file_path else None,
        "base_worktable_component_name": getattr(workspace, "base_worktable_name", None),
        "base_worktable_component_guid": getattr(workspace, "base_worktable_guid", None),
    }
    if not file_path.exists():
        metadata.update({
            "exists": False,
            "size_bytes": None,
            "mtime": None,
            "sha256": None,
        })
        return metadata
    stat = file_path.stat()
    metadata.update({
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": _file_sha256(file_path),
    })
    return metadata


def _validate_workspace_source_unchanged(
    expected: Any,
    current: dict[str, Any],
) -> None:
    if not isinstance(expected, dict):
        return
    expected_hash = expected.get("sha256")
    current_hash = current.get("sha256")
    if not expected_hash or not current_hash:
        return
    if expected_hash == current_hash:
        return
    raise ValueError(
        "Workspace file changed since it was loaded. FluentControl may have "
        "rewritten the deck for another instrument configuration; reload the "
        "workspace before saving this profile."
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _site_origin(
    parent_origin: tuple[float, float, float],
    arrangement: _ArrangementTemplateGeometry | None,
    key: int,
) -> tuple[float, float, float]:
    if arrangement is None:
        return parent_origin
    sites_x = max(1, arrangement.sites_in_x)
    sites_y = max(1, arrangement.sites_in_y)
    x_index = key % sites_x
    y_index = (key // sites_x) % sites_y
    z_index = key // max(1, sites_x * sites_y)
    offset = arrangement.site_offsets_mm.get(key, (0.0, 0.0, 0.0))
    return (
        parent_origin[0] + arrangement.position_in_parent_mm[0] + x_index * arrangement.site_spacing_mm[0] + offset[0],
        parent_origin[1] + arrangement.position_in_parent_mm[1] + y_index * arrangement.site_spacing_mm[1] + offset[1],
        parent_origin[2] + arrangement.position_in_parent_mm[2] + z_index * arrangement.site_spacing_mm[2] + offset[2],
    )


def _slot_position(site_path: tuple[int, ...]) -> int:
    if not site_path:
        return 0
    if len(site_path) >= 3 and site_path[-1] == 0:
        return site_path[-2]
    return site_path[-1]


def _site_dimensions(value_node: ET.Element) -> dict[str, float | None]:
    template_guid = _xml_direct_child_text(value_node, "BaseTemplateGuid")
    dim = _site_dimension(template_guid) if template_guid else None
    if dim is None:
        return {"width_mm": None, "depth_mm": None, "height_mm": None}
    return {
        "width_mm": round(dim[0], 3),
        "depth_mm": round(dim[1], 3),
        "height_mm": round(dim[2], 3),
    }


@lru_cache(maxsize=2048)
def _arrangement_templates_for_component(component_guid: str) -> dict[str, _ArrangementTemplateGeometry]:
    with open_index() as conn:
        row = conn.execute(
            "SELECT file_path FROM components WHERE guid = ? LIMIT 1",
            (component_guid,),
        ).fetchone()
    if row is None:
        return {}
    path = Path(row["file_path"])
    if not path.exists():
        return {}
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}

    out: dict[str, _ArrangementTemplateGeometry] = {}
    for node in root.iter():
        if not isinstance(node.tag, str) or _xml_local(node.tag) != "ArrangementTemplate":
            continue
        internal_id = _xml_text(_xml_find(node, "InternalID"))
        if not internal_id:
            continue
        out[internal_id] = _ArrangementTemplateGeometry(
            internal_id=internal_id,
            sites_in_x=max(1, _xml_int(_xml_find(node, "SitesInX")) or 1),
            sites_in_y=max(1, _xml_int(_xml_find(node, "SitesInY")) or 1),
            sites_in_z=max(1, _xml_int(_xml_find(node, "SitesInZ")) or 1),
            site_spacing_mm=(
                _xml_float(_xml_find(node, "SiteSpacingInX")) or 0.0,
                _xml_float(_xml_find(node, "SiteSpacingInY")) or 0.0,
                _xml_float(_xml_find(node, "SiteSpacingInZ")) or 0.0,
            ),
            position_in_parent_mm=_xml_vec3(_xml_find(node, "PositionInParent")) or (0.0, 0.0, 0.0),
            site_offsets_mm=_xml_int_vec_map(_xml_find(node, "SiteOffsets")),
            site_template_guids=_xml_int_text_map(_xml_find(node, "SiteTemplateIdentifiers")),
        )
    return out


@lru_cache(maxsize=4096)
def _site_location_name(site_guid: str) -> str | None:
    site = _site_summary(site_guid)
    return str(site.get("location_group_name") or "") or None


@lru_cache(maxsize=4096)
def _site_dimension(site_guid: str | None) -> tuple[float, float, float] | None:
    if not site_guid:
        return None
    site = _site_summary(site_guid)
    dim = site.get("dimension_mm")
    return dim if isinstance(dim, tuple) else None


@lru_cache(maxsize=4096)
def _site_summary(site_guid: str) -> dict[str, Any]:
    path = DEFAULT_FC_INSTALL / "SystemSpecific" / "Worktable" / "Sites" / f"{site_guid}.xsit"
    if not path.exists():
        return {}
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}
    return {
        "location_group_name": _xml_text(_xml_find(root, "LocationGroupName")),
        "dimension_mm": _xml_vec3(_xml_find(root, "Dimension")),
    }


def _xml_direct_children(elem: ET.Element | None, local_name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [child for child in list(elem) if isinstance(child.tag, str) and _xml_local(child.tag) == local_name]


def _xml_direct_child_text(elem: ET.Element | None, local_name: str) -> str | None:
    children = _xml_direct_children(elem, local_name)
    return _xml_text(children[0]) if children else None


def _xml_int(elem: ET.Element | None) -> int | None:
    text = _xml_text(elem)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _xml_float(elem: ET.Element | None) -> float | None:
    text = _xml_text(elem)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _xml_vec3(elem: ET.Element | None) -> tuple[float, float, float] | None:
    if elem is None:
        return None
    vals: dict[str, float] = {}
    for child in list(elem):
        if not isinstance(child.tag, str) or child.text is None:
            continue
        name = _xml_local(child.tag)
        if name not in {"X", "Y", "Z"}:
            continue
        try:
            vals[name] = float(child.text)
        except ValueError:
            pass
    if {"X", "Y", "Z"}.issubset(vals):
        return (vals["X"], vals["Y"], vals["Z"])
    return None


def _xml_int_vec_map(elem: ET.Element | None) -> dict[int, tuple[float, float, float]]:
    out: dict[int, tuple[float, float, float]] = {}
    if elem is None:
        return out
    for child in list(elem):
        key = _xml_int(_xml_find(child, "Key"))
        value = _xml_vec3(_xml_find(child, "Value"))
        if key is not None and value is not None:
            out[key] = value
    return out


def _xml_int_text_map(elem: ET.Element | None) -> dict[int, str]:
    out: dict[int, str] = {}
    if elem is None:
        return out
    for child in list(elem):
        key = _xml_int(_xml_find(child, "Key"))
        value = _xml_text(_xml_find(child, "Value"))
        if key is not None and value:
            out[key] = value
    return out


def _looks_like_guid(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text))


@lru_cache(maxsize=2048)
def _connector_position(connector_guid: str | None) -> tuple[float, float, float] | None:
    if not connector_guid:
        return None
    path = DEFAULT_FC_INSTALL / "SystemSpecific" / "Worktable" / "Connectors" / f"{connector_guid}.xcon"
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    pos = _xml_find(root, "PositionInParent")
    if pos is None:
        return None
    vals: dict[str, float] = {}
    for child in pos:
        if isinstance(child.tag, str) and child.text:
            try:
                vals[_xml_local(child.tag)] = float(child.text)
            except ValueError:
                pass
    if {"X", "Y", "Z"}.issubset(vals):
        return (vals["X"], vals["Y"], vals["Z"])
    return None


def _strip_fc_instance(name: str) -> str:
    return re.sub(r"\[\d+\]$", "", name).strip()


def _spread_overlapping_display_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any], list[int]] = {}
    for idx, row in enumerate(rows):
        grouped.setdefault((row.get("x_mm"), row.get("y_mm")), []).append(idx)
    out = [dict(row) for row in rows]
    for indices in grouped.values():
        if len(indices) <= 1:
            continue
        columns = min(4, len(indices))
        for ordinal, idx in enumerate(indices):
            col = ordinal % columns
            row = ordinal // columns
            out[idx]["x_pct"] = round(min(96.0, max(3.0, float(out[idx]["x_pct"]) + (col - (columns - 1) / 2.0) * 2.4)), 2)
            out[idx]["y_pct"] = round(min(94.0, max(5.0, float(out[idx]["y_pct"]) + row * 6.0)), 2)
            out[idx]["display_offset_index"] = ordinal
    return out


def _deck_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    positions = list(payload.get("positions") or [])
    occupants = list(payload.get("occupants") or [])
    carrier_by_path = {
        str(occ.get("site_path")): occ
        for occ in occupants
        if isinstance(occ, dict)
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in positions:
        site_path = str(row.get("site_path") or "")
        parts = [part for part in site_path.split("/") if part != ""]
        top = parts[0] if parts else f"slot-{row.get('location')}"
        if top == "0":
            top = f"0:{row.get('location')}"
        grouped.setdefault(top, []).append(row)

    blocks: list[dict[str, Any]] = []
    for top, rows in grouped.items():
        carrier_key = str(top).split(":", 1)[0]
        carrier = {} if ":" in str(top) else carrier_by_path.get(carrier_key) or {}
        block_locations = sorted({str(row.get("location") or "") for row in rows})
        blocks.append({
            "top_site": _int_or_text(top),
            "carrier_name": carrier.get("catalog_name") or _carrier_label_for_rows(rows),
            "carrier_location": carrier.get("location"),
            "locations": block_locations,
            "positions": sorted(rows, key=_position_sort_key),
            "span": max(1, len(rows)),
        })
    return sorted(blocks, key=lambda block: _block_sort_key(block["top_site"], block["positions"]))


def _carrier_label_for_rows(rows: list[dict[str, Any]]) -> str:
    locations = sorted({str(row.get("location") or "") for row in rows})
    return " / ".join(locations) if locations else "Carrier"


def _position_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    site_path = str(row.get("site_path") or "")
    parts = [int(part) for part in site_path.split("/") if part.isdigit()]
    leaf = parts[-2] if len(parts) >= 3 and parts[-1] == 0 else (parts[-1] if parts else 0)
    return (leaf, int(row.get("position") or 0), str(row.get("location") or ""))


def _block_sort_key(top_site: Any, rows: list[dict[str, Any]]) -> tuple[int, int, str]:
    if isinstance(top_site, int):
        return (0, top_site, "")
    first = rows[0] if rows else {}
    return (1, 0, str(first.get("location") or top_site))


def _int_or_text(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def _lane_by_location(rows: list[dict[str, Any]]) -> dict[str, int]:
    priority = {
        "InfiniteM200_Pos": 0,
        "WS_100ml_1": 1,
        "Nest7mm_Pos": 2,
        "Nest61mm_Pos": 3,
        "MCA384_Diti_ActiveNest": 4,
        "Regrip_Pos": 5,
        "ThruDeckWaste_Pos": 6,
    }
    locations = sorted({str(row.get("location") or "") for row in rows})
    ordered = sorted(locations, key=lambda loc: (priority.get(loc, 50), loc))
    return {loc: index for index, loc in enumerate(ordered)}


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []
