"""Native tool surface exposed to the LM protocol author."""

from __future__ import annotations

import ast
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..catalog import (
    find_components,
    find_components_by_metadata,
    get_database,
    load_xlqc,
    load_xcmp,
    open_index,
    retrieve_dsl_recipes,
    resolve_by_name,
    resolve_liquid_class_by_name,
)
from ..catalog.inference import component_taxonomy
from .grounding import GroundingError, load_grounding_bundle
from .models import (
    FailureCategory,
    FunctionalGroupPlan,
    IntentSpec,
    ProtocolWorkflowPlan,
    VariableBinding,
)
from .repair_policy import resolve_repair_policy
from .validator import AuthoringValidator
from .fluentcontrol_shell import (
    DEFAULT_SHELL_XSCR,
    validate_generated_xscr_via_shell,
    validate_xscr_direct,
)


ToolFn = Callable[..., dict[str, Any]]

_EXPORTED_TECANLAB_CLASSES: frozenset[str] = frozenset({
    "Worktable", "Gripper",
    "MCA96Head", "LiHa", "Tip",
    "Labware", "ExternalLabware", "Layer", "Well",
    "Plate", "Plate96", "Plate96Deep", "Plate384",
    "Trough", "Trough25mL", "Trough100mL", "Waste",
    "TipBox", "MCA100Box", "MCA200Box", "MCA500Box",
    "FCA50Box", "FCA200Box", "FCA1000Box",
    "EvaAdapter", "MagnetRack",
    "TubeRack", "WashStation", "WasteChute", "Hotel", "Adapter", "FixedDeck",
})

_CATALOG_BACKED_CLASSES: frozenset[str] = frozenset({
    "Plate", "Plate96", "Plate96Deep", "Plate384",
    "Trough", "Trough25mL", "Trough100mL", "Waste",
    "TipBox", "MCA100Box", "MCA200Box", "MCA500Box",
    "FCA50Box", "FCA200Box", "FCA1000Box",
    "EvaAdapter", "MagnetRack",
    "TubeRack", "WashStation", "WasteChute", "Hotel", "Adapter", "FixedDeck",
})

_MCA_TIP_CLASSES: frozenset[str] = frozenset({"MCA100Box", "MCA200Box", "MCA500Box"})
_FCA_TIP_CLASSES: frozenset[str] = frozenset({"FCA50Box", "FCA200Box", "FCA1000Box"})
_TIP_CLASS_CAPACITY_UL: dict[str, float] = {
    "MCA100Box": 100.0,
    "MCA200Box": 200.0,
    "MCA500Box": 500.0,
    "FCA50Box": 50.0,
    "FCA200Box": 200.0,
    "FCA1000Box": 1000.0,
}
_TIP_RECOMMENDATIONS: tuple[tuple[float, str, str], ...] = (
    (50.0, "FCA50Box", "FCA, 50ul SBS"),
    (100.0, "MCA100Box", "MCA96, 100ul, Box"),
    (200.0, "MCA200Box", "MCA96, 200ul, Box"),
    (500.0, "MCA500Box", "MCA96, 500ul, Box"),
    (1000.0, "FCA1000Box", "FCA, 1000ul SBS"),
)
_CATALOG_SEMANTIC_OVERRIDES: dict[str, dict[str, Any]] = {
    "lv_alpaqua_a000350": {"category": "magnet_rack", "python_class": "MagnetRack", "layout": "96"},
    "lv_alpaqua_a000350_1": {"category": "magnet_rack", "python_class": "MagnetRack", "layout": "96"},
    "lv_alpaqua_384": {"category": "magnet_rack", "python_class": "MagnetRack", "layout": "384"},
    "24 magnet plate": {"category": "magnet_rack", "python_class": "MagnetRack", "layout": "24"},
    "landscape nest magnet teleshake segment": {"category": "fixed_deck", "python_class": "FixedDeck"},
    "2 landscape 7mm nest magnet teleshake segment": {"category": "fixed_deck", "python_class": "FixedDeck"},
}

# ── Deck layout suggestion types ───────────────────────────────────

@dataclass(frozen=True)
class _DeckResource:
    """A resource the model wants placed on the deck."""
    label: str
    category: str | None = None       # e.g. plate, tip_box, trough, magnet_rack
    catalog_name: str | None = None   # exact FC catalog name (optional)
    role: str | None = None           # hint: source, destination, tips, trough, magnet, waste
    expected_waste_ul: float | None = None
    capacity_ul: float | None = None

@dataclass(frozen=True)
class _DeckPlacement:
    """A proposed placement for a single resource."""
    label: str
    location: str
    position: int
    role: str | None = None

@dataclass(frozen=True)
class _Collision:
    """Two resources that would share the same slot without stacking intent."""
    labels: tuple[str, ...]
    location: str
    position: int

# Role → preferred (location, positions) mapping.
# These are generic platform defaults; actual valid slots come from workspace grounding.
_ROLE_PREFERENCES: dict[str, list[tuple[str, int]]] = {
    "source": [("Nest61mm_Pos", 1)],
    "destination": [("Nest61mm_Pos", 2)],
    "dest": [("Nest61mm_Pos", 2)],
    "tips": [("Nest61mm_Pos", 4), ("Nest61mm_Pos", 11), ("Nest61mm_Pos", 12)],
    "mca_tips": [("Nest61mm_Pos", 4), ("Nest61mm_Pos", 11), ("Nest61mm_Pos", 12)],
    "fca_tips": [("Nest61mm_Pos", 6)],
    "trough": [("WS_100ml_1", 1)],
    "reservoir": [("WS_100ml_1", 1)],
    "magnet": [("Nest61mm_Pos", 3), ("RGA", 1)],
    "tip_waste": [("Nest61mm_Pos", 5)],
    "liquid_waste": [("Nest61mm_Pos", 5), ("WS_100ml_1", 1)],
    "waste": [("Nest61mm_Pos", 5)],
}

# Categories that should NOT share a slot unless stacking is explicit.
# Magnet racks can be stacked under plates, but only via gripper.move(onto=…).
_NON_STACKABLE_CATEGORIES: set[str] = {
    "plate", "trough", "tip_box", "tube_rack",
}


def plan_protocol_resources(phases: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute deterministic resource pressure from generic protocol phases.

    The planner is deliberately protocol-agnostic: callers describe liquid
    movements with source/destination/waste labels, per-operation volume,
    well count, repetition count, and optional split volume.
    """
    sources: dict[str, dict[str, Any]] = {}
    waste: dict[str, dict[str, Any]] = {}
    destinations: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    max_tip_volume = 0.0
    max_single_operation_volume = 0.0
    required_variables: list[dict[str, Any]] = []
    split_plan_required: list[dict[str, Any]] = []

    for index, phase in enumerate(phases):
        volume = _float_arg(phase.get("volume_ul"), default=0.0)
        split_volume = _float_arg(phase.get("split_volume_ul"), default=volume)
        well_count = max(0, int(phase.get("well_count") or 0))
        repetitions = max(1, int(phase.get("repetitions") or 1))
        total_volume = volume * well_count * repetitions
        role = str(phase.get("role") or phase.get("phase") or "").lower()
        max_tip_volume = max(max_tip_volume, split_volume or volume)
        max_single_operation_volume = max(max_single_operation_volume, volume)
        required_variables.extend(_phase_required_variables(index, phase))

        source_label = _optional_label(phase.get("source_label"))
        if source_label:
            entry = sources.setdefault(source_label, {
                "source_label": source_label,
                "required_volume_ul": 0.0,
                "dead_volume_buffer_ul": 0.0,
                "recommended_fill_volume_ul": 0.0,
                "phase_indices": [],
            })
            entry["required_volume_ul"] += total_volume
            entry["phase_indices"].append(index)

            fill_volume = phase.get("source_fill_volume_ul")
            if fill_volume is not None:
                available = _float_arg(fill_volume, default=0.0) * max(1, well_count)
                if available < entry["required_volume_ul"]:
                    warnings.append({
                        "category": "source_underfilled",
                        "source_label": source_label,
                        "available_volume_ul": available,
                        "required_volume_ul": entry["required_volume_ul"],
                        "short_by_ul": entry["required_volume_ul"] - available,
                    })

        destination_label = _optional_label(phase.get("destination_label"))
        if destination_label:
            entry = destinations.setdefault(destination_label, {
                "destination_label": destination_label,
                "well_count": well_count,
                "total_volume_in_ul": 0.0,
                "max_per_well_requested_ul": 0.0,
                "phase_indices": [],
            })
            entry["well_count"] = max(entry["well_count"], well_count)
            entry["total_volume_in_ul"] += total_volume
            entry["max_per_well_requested_ul"] += volume * repetitions
            entry["phase_indices"].append(index)
            capacity = phase.get("destination_capacity_ul") or phase.get("capacity_ul")
            if capacity is not None and entry["max_per_well_requested_ul"] > _float_arg(capacity, default=0.0):
                warnings.append({
                    "category": "destination_capacity_risk",
                    "destination_label": destination_label,
                    "max_per_well_requested_ul": entry["max_per_well_requested_ul"],
                    "capacity_ul": _float_arg(capacity, default=0.0),
                })

        waste_label = _optional_label(phase.get("waste_label"))
        if waste_label:
            entry = waste.setdefault(waste_label, {
                "waste_label": waste_label,
                "expected_waste_ul": 0.0,
                "well_count": well_count,
                "max_per_well_requested_ul": 0.0,
                "phase_indices": [],
            })
            entry["expected_waste_ul"] += total_volume
            entry["well_count"] = max(entry["well_count"], well_count)
            entry["max_per_well_requested_ul"] += volume * repetitions
            entry["phase_indices"].append(index)
            capacity = phase.get("waste_capacity_ul") or phase.get("capacity_ul")
            if capacity is not None and entry["max_per_well_requested_ul"] > _float_arg(capacity, default=0.0):
                warnings.append({
                    "category": "waste_capacity_risk",
                    "waste_label": waste_label,
                    "expected_waste_ul": entry["expected_waste_ul"],
                    "max_per_well_requested_ul": entry["max_per_well_requested_ul"],
                    "capacity_ul": _float_arg(capacity, default=0.0),
                    "guidance": "Do not use a shallow 96-well plate as waste if predicted waste exceeds capacity.",
                })

        tip_capacity = _float_arg(phase.get("tip_capacity_ul"), default=0.0)
        effective_tip_volume = split_volume or volume
        if volume > effective_tip_volume:
            split_plan_required.append({
                "phase_index": index,
                "phase": phase.get("phase") or phase.get("role"),
                "operation_volume_ul": volume,
                "split_volume_ul": effective_tip_volume,
                "split_volume_variable": phase.get("split_volume_variable"),
            })
        if effective_tip_volume > 500.0 or ("tip_capacity_ul" in phase and effective_tip_volume > tip_capacity):
            warnings.append({
                "category": "tip_capacity_risk",
                "phase_index": index,
                "requested_volume_ul": effective_tip_volume,
                "tip_capacity_ul": phase.get("tip_capacity_ul"),
                "guidance": "Use higher-capacity tips or split the operation volume.",
            })

        if volume > 0.0 and not _optional_label(phase.get("volume_variable")):
            warnings.append({
                "category": "missing_phase_variable",
                "phase_index": index,
                "field": "volume_variable",
                "volume_ul": volume,
                "guidance": "Declare a variable for phase volumes that affect planning or generated code.",
            })
        if phase.get("source_fill_volume_ul") is not None and not _optional_label(phase.get("source_fill_variable")):
            warnings.append({
                "category": "missing_phase_variable",
                "phase_index": index,
                "field": "source_fill_variable",
                "guidance": "Declare a variable for source fill volumes used by resource planning.",
            })
        if volume > effective_tip_volume and not _optional_label(phase.get("split_volume_variable")):
            warnings.append({
                "category": "missing_phase_variable",
                "phase_index": index,
                "field": "split_volume_variable",
                "guidance": "Declare a split-volume variable when splitting a larger operation across tips.",
            })
        if phase.get("liquid_class") is not None and not _optional_label(phase.get("liquid_class_variable")):
            warnings.append({
                "category": "missing_phase_variable",
                "phase_index": index,
                "field": "liquid_class_variable",
                "guidance": "Declare a role/mode liquid-class variable instead of hardcoding assay-specific names.",
            })

        if role == "waste" and not waste_label:
            warnings.append({
                "category": "missing_waste_label",
                "phase_index": index,
                "guidance": "Waste-producing phases should identify a waste_label.",
            })

    for entry in sources.values():
        buffer = max(10.0, entry["required_volume_ul"] * 0.1) if entry["required_volume_ul"] else 0.0
        entry["dead_volume_buffer_ul"] = buffer
        entry["recommended_fill_volume_ul"] = entry["required_volume_ul"] + buffer

    recommendation = _recommend_tip_for_capacity(max_tip_volume)
    return {
        "ok": True,
        "sources": list(sources.values()),
        "waste": list(waste.values()),
        "destinations": list(destinations.values()),
        "max_single_operation_volume_ul": max_single_operation_volume,
        "minimum_tip_capacity_ul": max_tip_volume,
        "recommended_tip_class": recommendation.get("python_class"),
        "recommended_tip_catalog": recommendation.get("catalog_name"),
        "required_split_plan": split_plan_required,
        "required_variables": required_variables,
        "warnings": warnings,
    }


def suggest_deck_layout(
    resources: list[dict[str, Any]],
    *,
    workspace_name: str | None = None,
    workspace_guid: str | None = None,
) -> dict[str, Any]:
    """Propose deck placements for a set of labware resources.

    Args:
        resources: List of dicts with keys:
            - label (str): Author-given identifier (required)
            - category (str | None): Labware category hint (plate, tip_box, trough, …)
            - catalog_name (str | None): Exact FC catalog name
            - role (str | None): Role hint (source, destination, tips, trough, magnet, waste)
        workspace_name: Workspace to resolve valid slots from.
        workspace_guid: Alternative workspace identifier.

    Returns:
        Dict with keys:
            - ok: bool
            - placements: list of {label, location, position, role}
            - collisions: list of {labels, location, position}
            - unplaced: list of label strings that could not be placed
            - advice: optional guidance string
    """
    try:
        bundle = load_grounding_bundle(
            workspace_name=workspace_name,
            workspace_guid=workspace_guid,
        )
    except GroundingError as exc:
        return {
            "ok": False,
            "category": exc.category.value,
            "message": exc.message,
            "placements": [],
            "collisions": [],
            "unplaced": [r.get("label", "?") for r in resources],
        }

    parsed = [_parse_resource(r) for r in resources]
    valid_slots = set(bundle.valid_slots)

    # Build a priority-ordered list of candidate slots per resource.
    placements: list[_DeckPlacement] = []
    assigned: dict[tuple[str, int], str] = {}  # slot → label (first claim wins)
    unplaced: list[str] = []
    warnings: list[dict[str, Any]] = []

    for res in parsed:
        waste_warning = _waste_capacity_warning(res)
        if waste_warning is not None:
            warnings.append(waste_warning)
            unplaced.append(res.label)
            continue
        candidates = _candidate_slots(res, bundle, valid_slots)
        placed = False
        for loc, pos in candidates:
            if not _resource_fits_location(res, loc):
                continue
            slot = (loc, pos)
            if slot not in assigned:
                placements.append(_DeckPlacement(
                    label=res.label,
                    location=loc,
                    position=pos,
                    role=res.role,
                ))
                assigned[slot] = res.label
                placed = True
                break
        if not placed:
            unplaced.append(res.label)

    # Detect collisions: resources that would share a slot without stacking intent.
    collisions = _detect_collisions(parsed, placements)

    advice_parts: list[str] = []
    if collisions:
        labels_str = ", ".join(
            f"{c.labels[0]} vs {c.labels[1]}" for c in collisions[:3]
        )
        advice_parts.append(
            f"Collisions detected ({labels_str}). "
            f"Use gripper.move(onto=…) to stack intentionally, or choose different slots."
        )
    if unplaced:
        advice_parts.append(
            f"Could not place: {', '.join(unplaced)}. "
            f"Call list_valid_positions for available locations."
        )
    if warnings:
        advice_parts.append(
            "Do not use a shallow 96-well plate as waste if predicted waste exceeds capacity; "
            "prefer waste_chute, Waste, or a high-capacity trough-like waste resource."
        )

    return {
        "ok": len(unplaced) == 0,
        "placements": [
            {"label": p.label, "location": p.location, "position": p.position, "role": p.role}
            for p in placements
        ],
        "collisions": [
            {"labels": list(c.labels), "location": c.location, "position": c.position}
            for c in collisions
        ],
        "unplaced": unplaced,
        "warnings": warnings,
        "advice": "; ".join(advice_parts) if advice_parts else None,
    }


def _parse_resource(r: dict[str, Any]) -> _DeckResource:
    label = str(r.get("label", r.get("name", "")))
    category = r.get("category") or None
    catalog_name = r.get("catalog_name") or r.get("catalog") or None
    role = r.get("role") or None
    if catalog_name:
        category = _semantic_category(catalog_name, category)
    expected_waste_ul = (
        _float_arg(r.get("expected_waste_ul"), default=0.0)
        if r.get("expected_waste_ul") is not None
        else None
    )
    capacity_ul = (
        _float_arg(r.get("capacity_ul") or r.get("waste_capacity_ul"), default=0.0)
        if r.get("capacity_ul") is not None or r.get("waste_capacity_ul") is not None
        else None
    )
    # Infer category from role if not provided.
    if category is None and role:
        role_lower = role.lower()
        if "tip" in role_lower:
            category = "tip_box"
        elif role_lower in ("source", "destination", "dest"):
            category = "plate"
        elif role_lower in ("trough", "reservoir", "liquid_waste"):
            category = "trough"
        elif "magnet" in role_lower:
            category = "magnet_rack"
        elif role_lower in ("waste", "tip_waste"):
            category = "waste_chute"
    return _DeckResource(
        label=label,
        category=category,
        catalog_name=catalog_name,
        role=role,
        expected_waste_ul=expected_waste_ul,
        capacity_ul=capacity_ul,
    )


def _float_arg(value: Any, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_label(value: Any) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    return label or None


def _phase_required_variables(index: int, phase: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for field, variable_field, kind in (
        ("volume_ul", "volume_variable", "volume"),
        ("source_fill_volume_ul", "source_fill_variable", "source_fill"),
        ("split_volume_ul", "split_volume_variable", "split_volume"),
        ("liquid_class", "liquid_class_variable", "liquid_class"),
    ):
        variable = _optional_label(phase.get(variable_field))
        if variable is None:
            continue
        out.append({
            "phase_index": index,
            "phase": phase.get("phase") or phase.get("role"),
            "kind": kind,
            "field": field,
            "variable": variable,
            "value": phase.get(field),
        })
    return out


def _recommend_tip_for_capacity(required_ul: float) -> dict[str, Any]:
    for capacity, python_class, catalog_name in _TIP_RECOMMENDATIONS:
        if required_ul <= capacity:
            return {
                "capacity_ul": capacity,
                "python_class": python_class,
                "catalog_name": catalog_name,
            }
    return {
        "capacity_ul": required_ul,
        "python_class": None,
        "catalog_name": None,
    }


def _waste_capacity_warning(res: _DeckResource) -> dict[str, Any] | None:
    role = (res.role or "").lower()
    if role not in {"waste", "liquid_waste"}:
        return None
    if role == "liquid_waste" and (res.category or "").lower() == "waste_chute":
        return {
            "category": "liquid_waste_sink_invalid",
            "label": res.label,
            "guidance": "WasteChute is only a tip-waste/deck-disposal target; use a high-capacity liquid waste reservoir.",
        }
    category = (res.category or "").lower()
    catalog = (res.catalog_name or "").lower()
    looks_plate_like = category == "plate" or "96" in catalog and "plate" in catalog
    if not looks_plate_like:
        return None
    if res.expected_waste_ul is None or res.capacity_ul is None:
        return None
    if res.expected_waste_ul <= res.capacity_ul:
        return None
    return {
        "category": "waste_capacity_risk",
        "label": res.label,
        "expected_waste_ul": res.expected_waste_ul,
        "capacity_ul": res.capacity_ul,
        "guidance": "Use waste_chute, Waste, or high-capacity trough-like waste instead of a shallow plate.",
    }


def _candidate_slots(
    res: _DeckResource,
    bundle: GroundingBundle,
    valid_slots: set[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Return ordered candidate slots for a resource."""
    candidates: list[tuple[str, int]] = []

    # 1. Role-based preference (from _ROLE_PREFERENCES).
    if res.role:
        preferred = _ROLE_PREFERENCES.get(res.role.lower(), [])
        for loc, pos in preferred:
            slot = (loc, pos)
            if slot in valid_slots and slot not in candidates:
                candidates.append(slot)

    # 2. Layout defaults from grounding bundle.
    if res.role:
        layout_cfg = bundle.layout_defaults.get(res.role.lower())
        if layout_cfg:
            loc = str(layout_cfg.get("location", ""))
            pos = int(layout_cfg.get("site", 0))
            slot = (loc, pos)
            if slot in valid_slots and slot not in candidates:
                candidates.append(slot)

    # 3. Category-appropriate locations from generation config.
    category_locations = _category_preferred_locations(res.category)
    for loc in category_locations:
        # Find first available position at this location.
        for pos in sorted(p for l, p in valid_slots if l == loc):
            slot = (loc, pos)
            if slot not in candidates:
                candidates.append(slot)

    # 4. Fallback: all remaining valid slots, ordered by (location, position).
    existing = set(candidates)
    for slot in sorted(valid_slots - existing):
        candidates.append(slot)

    return candidates


def _category_preferred_locations(category: str | None) -> list[str]:
    """Return preferred location names for a labware category."""
    if category == "tip_box":
        return ["Nest61mm_Pos"]
    elif category in ("plate", "magnet_rack"):
        return ["Nest61mm_Pos", "RGA"]
    elif category == "trough":
        return ["WS_100ml_1"]
    elif category == "waste_chute":
        return ["Nest61mm_Pos"]
    else:
        return ["Nest61mm_Pos"]  # generic fallback


def _resource_fits_location(res: _DeckResource, location: str) -> bool:
    catalog = (res.catalog_name or "").strip().lower()
    role = (res.role or "").strip().lower()
    category = (res.category or "").strip().lower()
    if location == "Nest61mm_Pos":
        if catalog in {"100ml trough 156mm"}:
            return False
        if category == "trough" and "300ml sbs" not in catalog:
            return False
    if location.startswith("WS_100ml") and _is_sbs_footprint_catalog(catalog):
        return False
    return True


def _detect_collisions(
    resources: list[_DeckResource],
    placements: list[_DeckPlacement],
) -> list[_Collision]:
    """Detect cases where non-stackable labware shares a slot."""
    collisions: list[_Collision] = []

    # Build reverse map: slot → list of placed items.
    placement_by_slot: dict[tuple[str, int], list[_DeckPlacement]] = {}
    for p in placements:
        slot = (p.location, p.position)
        placement_by_slot.setdefault(slot, []).append(p)

    # Build category lookup.
    cat_map: dict[str, str | None] = {r.label: r.category for r in resources}

    for slot, placed_list in placement_by_slot.items():
        if len(placed_list) < 2:
            continue
        cats = {cat_map.get(p.label) for p in placed_list}
        # If items are non-stackable categories and none is a magnet_rack,
        # flag as collision.
        non_stack_cats = cats & _NON_STACKABLE_CATEGORIES
        has_magnet = "magnet_rack" in cats
        if non_stack_cats and not has_magnet:
            labels = tuple(p.label for p in placed_list)
            collisions.append(_Collision(
                labels=labels,
                location=slot[0],
                position=slot[1],
            ))
    return collisions



_API_LOOKUPS: dict[str, dict[str, Any]] = {
    "worktable": {
        "object": "Worktable",
        "methods": [
            {"name": "from_workspace", "signature": "Worktable.from_workspace(name, *, workspace_guid, auto_place=False, protocol_name=None, comment=None)", "examples": ["wt = Worktable.from_workspace('WORKSPACE_NAME_FROM_TOOLS', workspace_guid='WORKSPACE_GUID_FROM_TOOLS', auto_place=False)"]},
            {"name": "place", "signature": "wt.place(labware, location, position)", "examples": ["plate = wt.place(Plate96('DestPlate', catalog='96_ABgene_SuperPlate_Thermo_AB2800'), 'Nest61mm_Pos', 2)"]},
            {"name": "group", "signature": "wt.group(name)", "examples": ["wt.group('Transfer')"]},
            {"name": "declare_variable", "signature": "wt.declare_variable(name, default)", "examples": ["wt.declare_variable('RunId', 'demo')"]},
            {"name": "set_sim_value", "signature": "wt.set_sim_value(name, value)", "examples": ["wt.set_sim_value('RunId', 'demo')"]},
            {"name": "set_variable", "signature": "wt.set_variable(name, value)", "examples": ["wt.set_variable('RunId', 'demo')"]},
            {"name": "wait", "signature": "wt.wait(duration_seconds)", "examples": ["wt.wait(30)"]},
            {"name": "add_comment", "signature": "wt.add_comment(text)", "examples": ["wt.add_comment('Incubate at room temperature')"]},
        ],
        "attributes": ["liha", "mca96", "gripper"],
        "forbidden_common_mistakes": ["wt.pick_up(...)", "wt.aspirate(...)", "wt.dispense(...)"],
    },
    "wt.gripper": {
        "object": "wt.gripper",
        "methods": [
            {"name": "move", "signature": "move(labware, *, to=None, onto=None)", "examples": ["wt.gripper.move(plate, to=('Nest61mm_Pos', 2))", "wt.gripper.move(plate, onto=magnet)"]},
        ],
        "forbidden_common_mistakes": ["pick_up", "drop", "place", "aspirate", "dispense"],
    },
    "wt.liha": {
        "object": "wt.liha",
        "methods": [
            {"name": "get_tips", "signature": "get_tips(labware=None)", "examples": ["head = wt.liha", "head.get_tips(tips)"]},
            {"name": "aspirate", "signature": "aspirate(labware, volume, *, liquid_class=None, well_offset=None)", "examples": ["head.aspirate(source, 20.0, liquid_class='Water Free Single')"]},
            {"name": "dispense", "signature": "dispense(labware, volume, *, liquid_class=None, well_offset=None)", "examples": ["head.dispense(dest, 20.0, liquid_class='Water Free Single', well_offset=col * 8)"]},
            {"name": "mix", "signature": "mix(labware, volume, *, cycles=10, liquid_class=None, well_offset=None)", "examples": ["head.mix(plate, 30.0, cycles=10, liquid_class='Water Mix')"]},
            {"name": "empty_tips", "signature": "empty_tips(labware, volume=0, *, liquid_class=None)", "examples": ["head.empty_tips(waste, 20.0)"]},
            {"name": "drop_tips", "signature": "drop_tips(labware=None)", "examples": ["head.drop_tips()", "head.drop_tips(tips)"]},
        ],
        "forbidden_common_mistakes": ["pick_up", "return_tips", "mount_adapter", "drop_adapter"],
    },
    "wt.mca96": {
        "object": "wt.mca96",
        "methods": [
            {"name": "mount_adapter", "signature": "mount_adapter(adapter=None)", "examples": ["head = wt.mca96", "head.mount_adapter()"]},
            {"name": "pick_up", "signature": "pick_up(tip_box)", "examples": ["head.pick_up(tips)"]},
            {"name": "aspirate", "signature": "aspirate(target, volume_ul, *, liquid_class)", "examples": ["head.aspirate(source, 20.0, liquid_class='Water Free Single')"]},
            {"name": "dispense", "signature": "dispense(target, volume_ul, *, liquid_class)", "examples": ["head.dispense(dest, 20.0, liquid_class='Water Free Single')"]},
            {"name": "mix", "signature": "mix(target, volume_ul, *, cycles=10, liquid_class)", "examples": ["head.mix(plate, 20.0, cycles=10, liquid_class='Water Mix')"]},
            {"name": "empty_tips", "signature": "empty_tips(target, volume_ul, *, liquid_class='Empty Tip')", "examples": ["head.empty_tips(waste, 20.0)"]},
            {"name": "return_tips", "signature": "return_tips(tip_box=None)", "examples": ["head.return_tips(tips)"]},
            {"name": "drop_adapter", "signature": "drop_adapter(adapter=None)", "examples": ["head.drop_adapter()"]},
        ],
        "forbidden_common_mistakes": ["get_tips", "drop_tips"],
    },
    "labware": {
        "object": "Labware",
        "methods": [
            {"name": "well", "signature": "well(address)", "examples": ["plate.well('A1').add_layer(sample, 20.0)"]},
            {"name": "all_wells", "signature": "all_wells()", "examples": ["for well in plate.all_wells(): ..."]},
            {"name": "column", "signature": "column(idx)", "examples": ["plate.column(1)"]},
            {"name": "row", "signature": "row(letter)", "examples": ["plate.row('A')"]},
            {"name": "fill_all", "signature": "fill_all(reagent, volume_ul)", "examples": ["source.fill_all(water, 80.0)"]},
        ],
        "attributes": ["label", "wells", "catalog_name", "slot", "is_magnetized"],
        "forbidden_common_mistakes": ["fill", "plate['A1']"],
    },
    "plate96": {
        "object": "Plate96",
        "constructor": "Plate96(label, *, catalog, max_well_volume_ul=None)",
        "examples": ["Plate96('DestPlate', catalog='96_ABgene_SuperPlate_Thermo_AB2800')"],
        "inherits": "Labware",
    },
    "trough100ml": {
        "object": "Trough100mL",
        "constructor": "Trough100mL(label, *, catalog, max_well_volume_ul=None)",
        "examples": ["Trough100mL('SourceTrough', catalog='100ml Trough 156mm')", "source_trough.fill_all(water, 5000.0)"],
        "inherits": "Labware",
    },
    "fca1000box": {
        "object": "FCA1000Box",
        "constructor": "FCA1000Box(label, *, catalog)",
        "examples": ["FCA1000Box('Tips', catalog='FCA, 1000ul SBS')"],
        "attributes": ["capacity_ul", "is_full"],
        "inherits": "TipBox",
    },
}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        _tool("ask_user",
              "Ask the user a focused clarifying question BEFORE drafting Python. "
              "Use this when the prompt leaves any of {target volume, source labware, "
              "destination labware, well coverage, liquid class} ambiguous. The chat "
              "loop pauses and surfaces the question to the user. Do not guess.", {
            "question": {"type": "string", "description": "A single, focused, plain-English question."},
            "axes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Which intent axes this question resolves, e.g. "
                               "['target_volume_ul'], ['destination_labware'], ['well_coverage'].",
            },
        }, required=("question",)),
        _tool("declare_intent",
              "Declare the resolved authoring intent before final code. Once set, "
              "post-simulation validation checks that destination wells received the "
              "declared per-well volume.", {
            "target_volume_ul": {"type": "number", "description": "Per-well transfer volume in microliters."},
            "source_label": {"type": "string", "description": "Worktable label of the source labware."},
            "destination_label": {"type": "string", "description": "Worktable label of the destination labware."},
            "destination_wells": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit destination well addresses (e.g. ['A1','A2',...]). "
                               "Leave empty/null to validate against all wells of the destination.",
            },
            "liquid_class": {"type": "string"},
        }, required=()),
        _tool("declare_protocol_workflow",
              "Declare the high-level protocol plan before drafting Python. "
              "The first two groups must be exactly Variables and Labware Placement. "
              "Variables are emitted as wt.declare_variable and wt.set_sim_value "
              "before any wt.group call.", {
            "protocol_name": {"type": "string"},
            "summary": {"type": "string", "description": "High-level protocol intent and strategy."},
            "variables": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Runtime/protocol variables to declare before labware placement.",
            },
            "labware": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Planned labware resources with label, role, type/class, catalog, and deck location when known.",
            },
            "groups": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Ordered functional groups. The first two must be Variables and Labware Placement.",
            },
        }, required=("protocol_name", "summary", "variables", "labware", "groups")),
        _tool("present_object_draft",
              "Present the planned worktable objects for user approval before "
              "functional-group planning or Python drafting. Reference the exact "
              "workspace being used and include variables, reagents/samples, liquid "
              "classes, labware labels, Python classes, catalog names, roles, and "
              "deck locations when known.", {
            "protocol_name": {"type": "string"},
            "summary": {"type": "string"},
            "workspace": {"type": "object", "description": "Workspace name/GUID and any deck context."},
            "variables": {"type": "array", "items": {"type": "object"}},
            "reagents": {"type": "array", "items": {"type": "object"}},
            "liquid_classes": {"type": "array", "items": {"type": "object"}},
            "labware": {"type": "array", "items": {"type": "object"}},
        }, required=("protocol_name", "summary", "workspace", "labware")),
        _tool("present_functional_group_plan",
              "Present the ordered functional groups for user approval after the "
              "object draft is approved. The first two groups must be exactly "
              "Variables and Labware Placement.", {
            "protocol_name": {"type": "string"},
            "summary": {"type": "string"},
            "variables": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Runtime/protocol variables used by the mandatory Variables group.",
            },
            "labware": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Approved object draft labware resources.",
            },
            "groups": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Ordered functional groups; first two are Variables and Labware Placement.",
            },
        }, required=("protocol_name", "summary", "variables", "labware", "groups")),
        _tool("lookup_api", "Return supported tecanlab public API methods and examples for an object or class.", {
            "object_or_class": {
                "type": "string",
                "description": "Object or class name such as Worktable, wt.gripper, wt.liha, wt.mca96, Labware, Plate96, Trough100mL, FCA1000Box.",
            },
        }),
        _tool("lookup_workspace", "Resolve the canonical or named workspace and summarize valid deck slots.", {
            "name_or_guid": {"type": "string", "description": "Workspace name or GUID. Use empty string for the configured canonical workspace."},
        }, required=()),
        _tool("list_valid_positions", "Return valid site numbers for a workspace location.", {
            "location": {"type": "string"},
        }),
        _tool("search_labware", "Search installed catalog labware/components by substring.", {
            "query": {"type": "string"},
            "category": {"type": "string", "description": "Optional category such as plate, tip_box, trough, magnet_rack."},
            "component_kind": {"type": "string", "description": "Optional major component kind such as carrier, labware, or unknown."},
            "component_subtype": {"type": "string", "description": "Optional normalized component subtype such as runner, nest, microplate, or trough."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, required=("query",)),
        _tool("get_labware", "Return exact installed labware metadata and suggested tecanlab Python class.", {
            "name": {"type": "string"},
        }),
        _tool("lookup_liquid_class", "Resolve an installed liquid class by exact name.", {
            "name": {"type": "string"},
            "device_type": {"type": "string", "description": "Optional legacy device type filter."},
        }, required=("name",)),
        _tool("lookup_rules", "Return compact learned rules/modules/patterns from tecan.db.", {
            "protocol_type": {"type": "string"},
            "category": {"type": "string"},
        }, required=()),
        _tool("plan_protocol_resources",
              "Compute deterministic source, waste, destination, variable, split-plan, and tip-capacity pressure "
              "from generic protocol phases before drafting or while repairing resource failures.", {
            "phases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_label": {"type": "string"},
                        "destination_label": {"type": "string"},
                        "waste_label": {"type": "string"},
                        "volume_ul": {"type": "number"},
                        "well_count": {"type": "integer"},
                        "repetitions": {"type": "integer"},
                        "split_volume_ul": {"type": "number"},
                        "source_fill_volume_ul": {"type": "number"},
                        "volume_variable": {"type": "string"},
                        "source_fill_variable": {"type": "string"},
                        "split_volume_variable": {"type": "string"},
                        "liquid_class_variable": {"type": "string"},
                        "liquid_class": {"type": "string"},
                        "destination_capacity_ul": {"type": "number"},
                        "waste_capacity_ul": {"type": "number"},
                        "tip_capacity_ul": {"type": "number"},
                        "role": {"type": "string"},
                    },
                },
            },
        }, required=("phases",)),
        _tool("suggest_deck_layout",
              "Propose deck placements for a set of labware resources. Call this after "
              "slot_occupied failures or when planning where to place multiple items. "
              "Returns distinct valid slots per resource, detects collisions, and flags "
              "unplaceable items.", {
            "resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Author-given identifier (required)."},
                        "category": {"type": "string", "description": "Labware category: plate, tip_box, trough, magnet_rack, waste_chute."},
                        "catalog_name": {"type": "string", "description": "Exact FC catalog name (optional)."},
                        "role": {"type": "string", "description": "Role hint: source, destination, tips, trough, magnet, tip_waste, liquid_waste."},
                        "expected_waste_ul": {"type": "number", "description": "Predicted waste per final sink well or container."},
                        "capacity_ul": {"type": "number", "description": "Capacity of the proposed waste sink well or container."},
                    },
                },
            },
        }, required=("resources",)),
        _tool("simulate_python_draft", "Import build_worktable() and run strict simulation on a Python draft.", {
            "source": {"type": "string"},
            "strict": {"type": "boolean"},
        }, required=("source",)),
        _tool("compile_and_simulate", "Run the full gate: contract, import/build, compile .xscr, strict simulation.", {
            "source": {"type": "string"},
        }),
        _tool("validate_fluentcontrol_shell", "Patch/open the FluentControl shell script and scrape InfoPad validation errors.", {
            "source": {"type": "string", "description": "Optional Python draft. If provided, compile_and_simulate runs first and the resulting .xscr is shell-validated."},
            "xscr_path": {"type": "string", "description": "Optional existing .xscr path to validate if source is not supplied."},
            "shell_xscr": {"type": "string", "description": "Optional UserSpecific shell .xscr path."},
            "process_id": {"type": "integer", "description": "Optional SystemSW.exe/FluentControl process id."},
            "restore_shell": {"type": "boolean", "description": "Restore shell content after validation. Default false."},
            "backup": {"type": "boolean", "description": "Create a shell backup before patching. Default false."},
            "open_direct": {"type": "boolean", "description": "Open xscr directly instead of patching shell. Default false."},
        }, required=()),
    ]


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required if required is not None else properties),
            },
        },
    }


class AuthoringToolRegistry:
    def __init__(
        self,
        *,
        output_dir: Path,
        workspace_name: str | None = None,
        workspace_guid: str | None = None,
        current_prompt: str | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.workspace_name = workspace_name
        self.workspace_guid = workspace_guid
        self.current_prompt = current_prompt
        self.validator = AuthoringValidator()
        self.calls: list[dict[str, Any]] = []
        self._compile_attempt = 0
        self.current_intent: IntentSpec = IntentSpec()
        self.workflow_plan: ProtocolWorkflowPlan | None = None
        self.object_draft: dict[str, Any] | None = None
        self.object_draft_approved: bool = False
        self.functional_group_plan_approved: bool = False
        self.pending_approval_kind: str | None = None

    def dispatch(self, name: str, arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        payload = self._parse_arguments(arguments)
        fn = self.functions().get(name)
        if fn is None:
            result = {"ok": False, "category": "unknown_tool", "message": f"Unknown tool {name!r}."}
        else:
            try:
                result = fn(**payload)
            except TypeError as exc:
                result = {"ok": False, "category": "bad_tool_arguments", "message": str(exc)}
            except Exception as exc:
                result = {"ok": False, "category": "tool_error", "message": str(exc)}
        self.calls.append({"name": name, "arguments": payload, "result": _compact(result)})
        return _compact(result)

    def functions(self) -> dict[str, ToolFn]:
        return {
            "ask_user": self.ask_user,
            "declare_intent": self.declare_intent,
            "declare_protocol_workflow": self.declare_protocol_workflow,
            "present_object_draft": self.present_object_draft,
            "present_functional_group_plan": self.present_functional_group_plan,
            "lookup_api": self.lookup_api,
            "lookup_workspace": self.lookup_workspace,
            "list_valid_positions": self.list_valid_positions,
            "search_labware": self.search_labware,
            "get_labware": self.get_labware,
            "lookup_liquid_class": self.lookup_liquid_class,
            "lookup_rules": self.lookup_rules,
            "plan_protocol_resources": self.plan_protocol_resources,
            "suggest_deck_layout": self.suggest_deck_layout,
            "simulate_python_draft": self.simulate_python_draft,
            "compile_and_simulate": self.compile_and_simulate,
            "validate_fluentcontrol_shell": self.validate_fluentcontrol_shell,
        }

    def ask_user(self, question: str, axes: list[str] | None = None) -> dict[str, Any]:
        cleaned = (question or "").strip()
        if not cleaned:
            return {
                "ok": False,
                "category": "bad_tool_arguments",
                "message": "ask_user requires a non-empty `question`.",
            }
        return {
            "ok": True,
            "status": "needs_user",
            "question": cleaned,
            "axes": list(axes or ()),
        }

    def declare_intent(
        self,
        target_volume_ul: float | None = None,
        source_label: str | None = None,
        destination_label: str | None = None,
        destination_wells: list[str] | None = None,
        liquid_class: str | None = None,
    ) -> dict[str, Any]:
        merged = IntentSpec(
            target_volume_ul=(
                float(target_volume_ul)
                if target_volume_ul is not None
                else self.current_intent.target_volume_ul
            ),
            source_label=source_label or self.current_intent.source_label,
            destination_label=destination_label or self.current_intent.destination_label,
            destination_wells=(
                tuple(destination_wells)
                if destination_wells
                else self.current_intent.destination_wells
            ),
            liquid_class=liquid_class or self.current_intent.liquid_class,
        )
        self.current_intent = merged
        return {"ok": True, "intent": merged.to_dict()}

    def declare_protocol_workflow(
        self,
        protocol_name: str,
        summary: str,
        variables: list[dict[str, Any]] | None,
        labware: list[dict[str, Any]] | None,
        groups: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        group_plans = tuple(
            FunctionalGroupPlan(
                name=str(group.get("name") or "").strip(),
                objective=str(group.get("objective") or "").strip(),
                expected_steps=tuple(str(item) for item in (group.get("expected_steps") or ())),
            )
            for group in (groups or ())
            if isinstance(group, dict)
        )
        group_names = [group.name for group in group_plans]
        if len(group_names) < 2:
            return {
                "ok": False,
                "category": "workflow_plan_invalid",
                "message": "declare_protocol_workflow requires at least Variables and Labware Placement groups.",
            }
        if group_names[:2] != ["Variables", "Labware Placement"]:
            return {
                "ok": False,
                "category": "workflow_plan_invalid",
                "message": (
                    "The first two functional groups must be exactly "
                    "`Variables` then `Labware Placement`."
                ),
                "received_groups": group_names,
            }
        if len(set(group_names)) != len(group_names):
            return {
                "ok": False,
                "category": "workflow_plan_invalid",
                "message": "Functional group names must be unique.",
                "received_groups": group_names,
            }

        variable_bindings: list[VariableBinding] = []
        for item in variables or ():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            default = item.get("default")
            sim_value = item.get("sim_value", default)
            variable_bindings.append(VariableBinding(name=name, default=default, sim_value=sim_value))
        if not variable_bindings:
            return {
                "ok": False,
                "category": "workflow_plan_invalid",
                "message": "At least one protocol variable must be declared for the mandatory Variables phase.",
            }

        plan = ProtocolWorkflowPlan(
            protocol_name=str(protocol_name or "").strip() or "Authored Protocol",
            summary=str(summary or "").strip(),
            variables=tuple(variable_bindings),
            labware=tuple(dict(item) for item in (labware or ()) if isinstance(item, dict)),
            groups=group_plans,
        )
        self.workflow_plan = plan
        return {
            "ok": True,
            "workflow": plan.to_dict(),
            "next_checkpoint": "Draft Variables and Labware Placement together, then call simulate_python_draft.",
        }

    def present_object_draft(
        self,
        protocol_name: str,
        summary: str,
        workspace: dict[str, Any] | None,
        labware: list[dict[str, Any]] | None,
        variables: list[dict[str, Any]] | None = None,
        reagents: list[dict[str, Any]] | None = None,
        liquid_classes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not any(call["name"] in {"lookup_workspace", "list_valid_positions"} for call in self.calls):
            return {
                "ok": False,
                "category": "workspace_grounding_required",
                "message": "Call lookup_workspace or list_valid_positions before presenting the object draft.",
            }
        object_draft = {
            "protocol_name": str(protocol_name or "").strip() or "Authored Protocol",
            "summary": str(summary or "").strip(),
            "workspace": dict(workspace or {}),
            "variables": [dict(item) for item in (variables or ()) if isinstance(item, dict)],
            "reagents": [dict(item) for item in (reagents or ()) if isinstance(item, dict)],
            "liquid_classes": [dict(item) for item in (liquid_classes or ()) if isinstance(item, dict)],
            "labware": [dict(item) for item in (labware or ()) if isinstance(item, dict)],
        }
        if not object_draft["labware"]:
            return {
                "ok": False,
                "category": "object_draft_invalid",
                "message": "present_object_draft requires at least one planned labware object.",
            }
        validation_error = self._validate_object_draft(object_draft)
        if validation_error is not None:
            self.object_draft = object_draft
            self.object_draft_approved = False
            self.functional_group_plan_approved = False
            self.pending_approval_kind = "objects"
            return validation_error
        latest_resource_plan = self._latest_successful_result("plan_protocol_resources")
        if latest_resource_plan:
            object_draft["_resource_plan"] = latest_resource_plan
        self.object_draft = object_draft
        self.object_draft_approved = False
        self.functional_group_plan_approved = False
        self.pending_approval_kind = "objects"
        return {
            "ok": True,
            "status": "needs_approval",
            "approval": {
                "kind": "objects",
                "title": "Approve Worktable Objects",
                "summary": object_draft["summary"],
                "payload": object_draft,
                "question": "Approve these worktable objects, or reply with changes.",
            },
        }

    def _validate_object_draft(self, object_draft: dict[str, Any]) -> dict[str, Any] | None:
        errors: list[dict[str, Any]] = []
        confirmed_catalogs = self._confirmed_catalog_names()
        confirmed_liquid_classes = self._confirmed_liquid_class_names()

        for index, item in enumerate(object_draft["labware"]):
            label = _object_label(item, default=f"labware[{index}]")
            python_class = _object_python_class(item)
            catalog_name = _object_catalog_name(item)
            role = str(item.get("role") or item.get("category") or "").strip().lower()

            if not python_class:
                errors.append({
                    "field": f"labware[{index}].python_class",
                    "label": label,
                    "message": "Each labware object must include a tecanlab python_class.",
                    "fix": "Set python_class to an exported class returned by lookup_api/get_labware.",
                    "valid_classes": sorted(_CATALOG_BACKED_CLASSES),
                })
                continue
            if python_class not in _EXPORTED_TECANLAB_CLASSES:
                errors.append({
                    "field": f"labware[{index}].python_class",
                    "label": label,
                    "received": python_class,
                    "message": f"{python_class!r} is not exported by tecanlab.",
                    "fix": "Use an exported tecanlab class; for FCA tips use FCA1000Box/FCA200Box/FCA50Box.",
                    "valid_classes": sorted(_EXPORTED_TECANLAB_CLASSES),
                })
                continue
            if python_class in _CATALOG_BACKED_CLASSES and not catalog_name:
                errors.append({
                    "field": f"labware[{index}].catalog_name",
                    "label": label,
                    "python_class": python_class,
                    "message": "Catalog-backed labware requires an exact installed catalog_name.",
                    "fix": "Call search_labware, then get_labware for the exact replacement before retrying.",
                })
                continue
            if catalog_name:
                row = resolve_by_name(catalog_name)
                if row is None:
                    errors.append({
                        "field": f"labware[{index}].catalog_name",
                        "label": label,
                        "catalog_name": catalog_name,
                        "message": f"Catalog labware {catalog_name!r} is not installed.",
                        "fix": "Call search_labware, then get_labware for an installed exact catalog name.",
                    })
                    continue
                if catalog_name not in confirmed_catalogs:
                    errors.append({
                        "field": f"labware[{index}].catalog_name",
                        "label": label,
                        "catalog_name": catalog_name,
                        "message": "Object approval requires exact catalog grounding with get_labware.",
                        "fix": f"Call get_labware(name={catalog_name!r}) successfully before presenting this draft.",
                    })
                semantic_category = _semantic_category(row.name, row.category)
                functional_group, component_kind, component_subtype = _component_identity(row)
                compatible = _catalog_class_compatible(python_class, row.category, row.name)
                if not compatible:
                    errors.append({
                        "field": f"labware[{index}].python_class",
                        "label": label,
                        "python_class": python_class,
                        "catalog_name": catalog_name,
                        "catalog_category": row.category,
                        "semantic_category": semantic_category,
                        "message": "python_class does not match the installed catalog category/name.",
                        "fix": "Use the python_class suggested by get_labware or choose a matching catalog.",
                        "suggested_python_class": _python_class_for(row.name, str(semantic_category)),
                    })
                if python_class == "MagnetRack" and semantic_category != "magnet_rack" and component_kind == "carrier":
                    errors.append({
                        "field": f"labware[{index}].catalog_name",
                        "label": label,
                        "python_class": python_class,
                        "catalog_name": catalog_name,
                        "catalog_category": row.category,
                        "semantic_category": semantic_category,
                        "functional_group": functional_group,
                        "component_kind": component_kind,
                        "component_subtype": component_subtype,
                        "message": "Carrier infrastructure cannot be approved as protocol MagnetRack labware.",
                        "fix": "Use the python_class suggested by get_labware, or choose an installed magnet_rack catalog.",
                        "suggested_python_class": _python_class_for(row.name, str(semantic_category)),
                    })
                layout_item = item
                if python_class == "MagnetRack" and not _requested_layout(item):
                    plate_layout = _object_draft_plate_layout(object_draft)
                    if plate_layout:
                        layout_item = {**item, "layout": plate_layout}
                layout_error = _catalog_layout_error(label=label, item=layout_item, catalog_name=row.name)
                if layout_error is not None:
                    errors.append({"field": f"labware[{index}].layout", **layout_error})
                fit_error = _placement_fit_error(
                    label=label,
                    role=role,
                    python_class=python_class,
                    catalog_name=catalog_name,
                    catalog_category=str(semantic_category),
                    location=str(item.get("location") or ""),
                )
                if fit_error is not None:
                    errors.append({"field": f"labware[{index}].location", **fit_error})
                tip_error = _tip_resource_error(label=label, role=role, python_class=python_class, catalog_name=catalog_name)
                if tip_error is not None:
                    errors.append({"field": f"labware[{index}].python_class", **tip_error})
            if ("waste" in role or "waste" in label.lower()) and not catalog_name:
                errors.append({
                    "field": f"labware[{index}].catalog_name",
                    "label": label,
                    "message": "Waste/bulk liquid waste resources require an exact supported waste catalog.",
                    "fix": "Use get_labware for a supported waste catalog such as an installed waste chute/trough.",
                })
            waste_error = _waste_role_error(
                label=label,
                role=role,
                python_class=python_class,
                catalog_name=catalog_name,
            )
            if waste_error is not None:
                errors.append({"field": f"labware[{index}].role", **waste_error})

        for index, item in enumerate(object_draft["liquid_classes"]):
            name = _liquid_class_name(item)
            if not name:
                errors.append({
                    "field": f"liquid_classes[{index}].name",
                    "message": "Each liquid-class default must include an exact name.",
                    "fix": "Call lookup_liquid_class and include the resolved name.",
                })
                continue
            if name not in confirmed_liquid_classes:
                errors.append({
                    "field": f"liquid_classes[{index}].name",
                    "name": name,
                    "message": "Object approval requires lookup_liquid_class for every liquid-class default.",
                    "fix": f"Call lookup_liquid_class(name={name!r}) successfully before presenting this draft.",
                })

        if len(object_draft["labware"]) > 1 and not self._has_successful_call("suggest_deck_layout"):
            errors.append({
                "field": "labware",
                "message": "Object drafts with more than one deck object require deck-layout grounding.",
                "fix": "Call suggest_deck_layout with the planned resources before presenting the object draft.",
            })

        if _requires_resource_plan(object_draft) and not self._has_successful_call("plan_protocol_resources"):
            errors.append({
                "field": "summary",
                "message": "Multi-step cleanup/wash protocols require resource planning before object approval.",
                "fix": "Call plan_protocol_resources and revise source fill, waste, and split-volume choices before retrying.",
            })

        latest_resource_plan = self._latest_successful_result("plan_protocol_resources")
        if latest_resource_plan:
            risk_warnings = [
                warning for warning in latest_resource_plan.get("warnings", [])
                if warning.get("category") in {
                    "source_underfilled",
                    "waste_capacity_risk",
                    "tip_capacity_risk",
                    "missing_phase_variable",
                    "liquid_waste_sink_invalid",
                }
            ]
            if risk_warnings:
                errors.append({
                    "field": "resources",
                    "message": "Resource planning found source, waste, or tip-capacity risks.",
                    "fix": "Revise fill volumes, waste capacity, or split/tip choices before presenting the object draft.",
                    "warnings": risk_warnings,
                })
            variable_errors = _object_variable_errors(object_draft, latest_resource_plan)
            errors.extend(variable_errors)
            tip_error = _object_tip_capacity_error(object_draft, latest_resource_plan)
            if tip_error is not None:
                errors.append(tip_error)

        if self._plans_both_mca_and_liha():
            classes = {_object_python_class(item) for item in object_draft["labware"]}
            if not classes.intersection(_MCA_TIP_CLASSES) or not classes.intersection(_FCA_TIP_CLASSES):
                errors.append({
                    "field": "labware",
                    "message": "Drafts using both wt.mca96 and wt.liha require separate MCA and FCA tip resources.",
                    "fix": "Include at least one MCA tip box and one FCA tip box with exact installed catalogs.",
                })

        if not errors:
            return None
        return {
            "ok": False,
            "category": "object_draft_invalid",
            "message": "Worktable object draft is not ready for user approval.",
            "errors": errors,
            "next_checkpoint": "Revise the object draft and call present_object_draft again.",
        }

    def _confirmed_catalog_names(self) -> set[str]:
        names: set[str] = set()
        for call in self.calls:
            if call.get("name") != "get_labware":
                continue
            result = call.get("result") or {}
            labware = result.get("labware") or {}
            if result.get("ok") is True and isinstance(labware, dict) and labware.get("name"):
                names.add(str(labware["name"]))
        return names

    def _confirmed_liquid_class_names(self) -> set[str]:
        names: set[str] = set()
        for call in self.calls:
            if call.get("name") != "lookup_liquid_class":
                continue
            result = call.get("result") or {}
            liquid_class = result.get("liquid_class") or {}
            if result.get("ok") is True and isinstance(liquid_class, dict) and liquid_class.get("name"):
                names.add(str(liquid_class["name"]))
        return names

    def _has_successful_call(self, name: str) -> bool:
        return any(call.get("name") == name and (call.get("result") or {}).get("ok") is True for call in self.calls)

    def _latest_successful_result(self, name: str) -> dict[str, Any] | None:
        for call in reversed(self.calls):
            if call.get("name") == name and (call.get("result") or {}).get("ok") is True:
                result = call.get("result")
                return result if isinstance(result, dict) else None
        return None

    def _plans_both_mca_and_liha(self) -> bool:
        looked_up = {
            _normalize_api_lookup(str((call.get("arguments") or {}).get("object_or_class") or ""))
            for call in self.calls
            if call.get("name") == "lookup_api" and (call.get("result") or {}).get("ok") is True
        }
        return {"wt.mca96", "wt.liha"}.issubset(looked_up)

    def present_functional_group_plan(
        self,
        protocol_name: str,
        summary: str,
        variables: list[dict[str, Any]] | None,
        labware: list[dict[str, Any]] | None,
        groups: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        if not self.object_draft_approved:
            return {
                "ok": False,
                "category": "object_approval_required",
                "message": "Present the worktable object draft and wait for user approval before functional groups.",
            }
        result = self.declare_protocol_workflow(
            protocol_name=protocol_name,
            summary=summary,
            variables=variables,
            labware=labware,
            groups=groups,
        )
        if result.get("ok") is not True:
            return result
        self.functional_group_plan_approved = False
        self.pending_approval_kind = "functional_groups"
        return {
            "ok": True,
            "status": "needs_approval",
            "approval": {
                "kind": "functional_groups",
                "title": "Approve Functional Groups",
                "summary": str(summary or "").strip(),
                "payload": result["workflow"],
                "question": "Approve this functional group plan, or reply with changes.",
            },
        }

    def approve_pending(self, kind: str) -> None:
        if kind == "objects" and self.pending_approval_kind == "objects":
            self.object_draft_approved = True
            self.pending_approval_kind = None
            return
        if kind == "functional_groups" and self.pending_approval_kind == "functional_groups":
            self.functional_group_plan_approved = True
            self.pending_approval_kind = None
            return

    def reopen_pending(self, kind: str) -> None:
        if kind == "objects":
            self.object_draft_approved = False
            self.functional_group_plan_approved = False
            self.pending_approval_kind = "objects"
        elif kind == "functional_groups":
            self.functional_group_plan_approved = False
            self.pending_approval_kind = "functional_groups"

    def lookup_api(self, object_or_class: str) -> dict[str, Any]:
        key = _normalize_api_lookup(object_or_class)
        entry = _API_LOOKUPS.get(key)
        if entry is None:
            return {
                "ok": False,
                "category": "unknown_api_object",
                "message": f"No API grounding entry for {object_or_class!r}.",
                "available": sorted(_API_LOOKUPS),
            }
        api = dict(entry)
        recipes = retrieve_dsl_recipes(
            get_database(),
            object_or_class,
            object_key=key,
            context_text=self.current_prompt,
            limit=4,
        )
        api["recipes"] = recipes
        return {"ok": True, "api": api}

    def lookup_workspace(self, name_or_guid: str = "") -> dict[str, Any]:
        name = self.workspace_name
        guid = self.workspace_guid
        candidate = (name_or_guid or "").strip()
        if candidate:
            if _looks_like_guid(candidate):
                guid = candidate
            else:
                name = candidate
        try:
            bundle = load_grounding_bundle(workspace_name=name, workspace_guid=guid)
        except GroundingError as exc:
            return {"ok": False, "category": exc.category.value, "message": exc.message}
        return {
            "ok": True,
            "workspace": {
                "name": bundle.workspace.name,
                "guid": bundle.workspace.guid,
            },
            "valid_positions": _slot_summary(bundle.valid_slots),
            "default_layout": bundle.layout_defaults,
        }

    def list_valid_positions(self, location: str) -> dict[str, Any]:
        bundle = load_grounding_bundle(workspace_name=self.workspace_name, workspace_guid=self.workspace_guid)
        positions = sorted(position for loc, position in bundle.valid_slots if loc == location)
        return {"ok": bool(positions), "location": location, "positions": positions}

    def search_labware(
        self,
        query: str,
        category: str | None = None,
        limit: int = 20,
        component_kind: str | None = None,
        component_subtype: str | None = None,
    ) -> dict[str, Any]:
        kind = _normalize_component_filter(component_kind)
        subtype = _normalize_component_filter(component_subtype)
        rows = find_components_by_metadata(query, component_kind=kind, component_subtype=subtype) if kind or subtype else find_components(query)
        if category:
            rows = [row for row in rows if _semantic_category(row.name, row.category) == category]
        return {"ok": True, "matches": [_catalog_entry(row) for row in rows[:limit]]}

    def get_labware(self, name: str) -> dict[str, Any]:
        row = resolve_by_name(name)
        if row is None:
            return {"ok": False, "category": FailureCategory.MISSING_CATALOG_ITEM.value, "message": f"Catalog labware {name!r} is not installed."}
        metadata = _catalog_entry(row)
        metadata["python_class"] = _python_class_for(row.name, str(metadata["category"]))
        try:
            component = load_xcmp(row.file_path)
            if not metadata.get("functional_group"):
                functional_group, component_kind, component_subtype = component_taxonomy(component.functional_group)
                metadata["functional_group"] = functional_group
                metadata["component_kind"] = component_kind
                metadata["component_subtype"] = component_subtype
            if component.arrangement:
                metadata["arrangement"] = {
                    "sites_in_x": component.arrangement.sites_in_x,
                    "sites_in_y": component.arrangement.sites_in_y,
                    "sites_in_z": component.arrangement.sites_in_z,
                    "site_spacing_mm": component.arrangement.site_spacing_mm,
                    "site_count": component.arrangement.site_count,
                }
            if component.pipettable:
                metadata["pipettable"] = {
                    "rows": component.pipettable.y_wells,
                    "columns": component.pipettable.x_wells,
                    "x_spacing_mm": component.pipettable.x_spacing_mm,
                    "y_spacing_mm": component.pipettable.y_spacing_mm,
                    "well_count": component.pipettable.well_count,
                    "cavity_volume_ul": component.pipettable.cavity.volume_ul if component.pipettable.cavity else None,
                }
        except Exception as exc:
            metadata["parse_warning"] = str(exc)
        return {"ok": True, "labware": metadata}

    def lookup_liquid_class(self, name: str, device_type: str | None = None) -> dict[str, Any]:
        if not name.strip():
            name = "Water Free Single"
        row = resolve_liquid_class_by_name(name)
        if row is not None:
            supported_heads = _liquid_class_supported_heads(row)
            payload = {
                "ok": True,
                "liquid_class": {
                    "name": row.name,
                    "guid": row.guid,
                    "head": row.head,
                    "supported_heads": supported_heads,
                },
            }
            if device_type:
                payload["requested_device_type"] = device_type
                if supported_heads and not _device_type_compatible(device_type, supported_heads):
                    return {
                        "ok": False,
                        "category": FailureCategory.LIQUID_CLASS_RESOLUTION_FAILURE.value,
                        "message": (
                            f"Liquid class {name!r} is not marked compatible with "
                            f"{device_type!r} in its .xlqc metadata."
                        ),
                        "liquid_class": payload["liquid_class"],
                        "supported_heads": supported_heads,
                    }
            return payload
        legacy = get_database().get_liquid_class(name, device_type=device_type)
        if legacy:
            return {"ok": True, "liquid_class": {k: legacy.get(k) for k in ("name", "device_type", "description", "key_parameters")}}
        return {
            "ok": False,
            "category": FailureCategory.LIQUID_CLASS_RESOLUTION_FAILURE.value,
            "message": f"Liquid class {name!r} is not installed.",
            "suggestions": _liquid_class_suggestions(name),
            "default": "Water Free Single",
        }

    def lookup_rules(self, protocol_type: str | None = None, category: str | None = None) -> dict[str, Any]:
        db = get_database()
        rules = db.get_rules_by_category(category, protocol_type=protocol_type) if category else db.get_all_rules(protocol_type=protocol_type)
        modules = db.get_all_modules() if hasattr(db, "get_all_modules") else []
        patterns = db.find_patterns(min_frequency=1)
        builtin = _filter_builtin_patterns(protocol_type=protocol_type, category=category)
        return {
            "ok": True,
            "rules": [_rule(row) for row in rules[:20]],
            "modules": [_module(row) for row in modules[:10]],
            "patterns": [_pattern(row) for row in patterns[:10]] + builtin,
        }

    def suggest_deck_layout(self, resources: list[dict[str, Any]]) -> dict[str, Any]:
        return suggest_deck_layout(
            resources,
            workspace_name=self.workspace_name,
            workspace_guid=self.workspace_guid,
        )

    def plan_protocol_resources(self, phases: list[dict[str, Any]]) -> dict[str, Any]:
        return plan_protocol_resources(phases)

    def simulate_python_draft(self, source: str, strict: bool = True) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="tecanlab-authoring-sim-") as tmp:
            path = Path(tmp) / "draft.py"
            path.write_text(source, encoding="utf-8")
            contract_error = self.validator._check_contract(source)
            if contract_error is None:
                contract_error = self.validator._check_prompt_intent(source, self.current_prompt)
            if contract_error is None and self.object_draft_approved and self.object_draft:
                contract_error = _check_source_against_approved_objects(source, self.object_draft)
            if contract_error:
                return {"ok": False, "stage": "contract", "category": FailureCategory.PYTHON_BUILD_FAILURE.value, "message": contract_error}
            try:
                wt = self.validator._load_protocol(path)
                wt.simulate(strict=strict)
            except Exception as exc:
                report = getattr(locals().get("wt", None), "simulation_report", None)
                structured_failure = _simulation_failure(report)
                if structured_failure is None:
                    structured_failure = _python_build_failure(exc)
                failure_category = structured_failure.get("category") if structured_failure else None
                policy = resolve_repair_policy(category=failure_category, message=_failure_message(report) or str(exc))
                return {
                    "ok": False,
                    "stage": "strict_simulation" if "wt" in locals() else "python_build",
                    "category": failure_category or FailureCategory.STRICT_SIMULATION_FAILURE.value,
                    "message": structured_failure.get("message") or str(exc),
                    "failure": structured_failure,
                    "repair_options": list(policy.options) or (structured_failure.get("repair_options") or []),
                    "state_summary": _simulation_state_summary(report),
                    "repair_hint": policy.guidance if policy.guidance else None,
                }
            report = getattr(wt, "simulation_report", None)
        return {
            "ok": True,
            "stage": "strict_simulation",
            "message": "Draft built and strict simulation passed.",
            "state_summary": _simulation_state_summary(report),
        }

    def compile_and_simulate(self, source: str) -> dict[str, Any]:
        self._compile_attempt += 1
        report = self.validator.validate(
            source,
            output_dir=self.output_dir,
            stem=f"lm_authoring_attempt{self._compile_attempt}",
            attempt_index=self._compile_attempt,
            prompt=self.current_prompt,
            intent=self.current_intent if self.current_intent.is_specified() else None,
        )
        payload = report.to_dict()
        payload["ok"] = report.success
        return payload

    def validate_fluentcontrol_shell(
        self,
        source: str | None = None,
        xscr_path: str | None = None,
        shell_xscr: str | None = None,
        process_id: int | None = None,
        restore_shell: bool = False,
        backup: bool = False,
        open_direct: bool = False,
    ) -> dict[str, Any]:
        compiled: dict[str, Any] | None = None
        resolved_xscr: Path | None = None

        if source:
            compiled = self.compile_and_simulate(source)
            if not compiled.get("ok"):
                return {
                    "ok": False,
                    "category": compiled.get("failure_category") or FailureCategory.FLUENTCONTROL_SHELL_FAILURE.value,
                    "message": "compile_and_simulate failed before FluentControl shell validation.",
                    "compile_and_simulate": compiled,
                }
            if compiled.get("xscr_path"):
                resolved_xscr = Path(compiled["xscr_path"])
        elif xscr_path:
            resolved_xscr = Path(xscr_path)
        else:
            return {
                "ok": False,
                "category": FailureCategory.FLUENTCONTROL_SHELL_FAILURE.value,
                "message": "validate_fluentcontrol_shell requires either source or xscr_path.",
            }

        if resolved_xscr is None:
            return {
                "ok": False,
                "category": FailureCategory.FLUENTCONTROL_SHELL_FAILURE.value,
                "message": "No .xscr path was available for FluentControl shell validation.",
                "compile_and_simulate": compiled,
            }

        shell_path = Path(shell_xscr) if shell_xscr else DEFAULT_SHELL_XSCR
        try:
            if open_direct:
                ui = validate_xscr_direct(resolved_xscr, process_id=process_id)
            else:
                ui = validate_generated_xscr_via_shell(
                    resolved_xscr,
                    shell_xscr=shell_path,
                    process_id=process_id,
                    restore_shell=restore_shell,
                    backup=backup,
                )
        except Exception as exc:
            return {
                "ok": False,
                "category": FailureCategory.FLUENTCONTROL_SHELL_FAILURE.value,
                "message": str(exc),
                "xscr_path": str(resolved_xscr),
                "shell_xscr": str(shell_path),
                "compile_and_simulate": compiled,
            }

        payload = ui.to_dict(xscr_path=resolved_xscr, shell_xscr=None if open_direct else shell_path)
        payload["category"] = None if payload["ok"] else FailureCategory.FLUENTCONTROL_SHELL_FAILURE.value
        payload["message"] = (
            "FluentControl shell validation passed."
            if payload["ok"]
            else "FluentControl shell validation failed."
        )
        if compiled is not None:
            payload["compile_and_simulate"] = compiled
        return payload

    def _parse_arguments(self, arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        if arguments is None or arguments == "":
            return {}
        if isinstance(arguments, dict):
            return arguments
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"_raw": arguments}


def _catalog_entry(row) -> dict[str, Any]:
    category = _semantic_category(row.name, row.category)
    functional_group, component_kind, component_subtype = _component_identity(row, parse_fallback=False)
    entry = {
        "name": row.name,
        "guid": row.guid,
        "category": category,
        "catalog_category": row.category,
        "grid_x": row.grid_x,
        "grid_y": row.grid_y,
        "dim_x_mm": row.dim_x_mm,
        "dim_y_mm": row.dim_y_mm,
        "dim_z_mm": row.dim_z_mm,
        "site_count": row.site_count,
        "functional_group": functional_group,
        "component_kind": component_kind,
        "component_subtype": component_subtype,
    }
    semantic = _catalog_semantic(row.name)
    if semantic.get("layout"):
        entry["layout"] = semantic["layout"]
    return entry


def _normalize_component_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return normalized or None


def _component_identity(row, *, parse_fallback: bool = True) -> tuple[str | None, str, str | None]:
    functional_group = getattr(row, "functional_group", None)
    component_kind = getattr(row, "component_kind", None) or "unknown"
    component_subtype = getattr(row, "component_subtype", None)
    if (functional_group or component_kind != "unknown") or not parse_fallback:
        return functional_group, component_kind, component_subtype
    try:
        component = load_xcmp(row.file_path)
    except Exception:
        return functional_group, component_kind, component_subtype
    return component_taxonomy(component.functional_group)


def _object_label(item: dict[str, Any], *, default: str = "") -> str:
    return str(item.get("label") or item.get("name") or default).strip()


def _object_python_class(item: dict[str, Any]) -> str:
    return str(item.get("python_class") or item.get("class") or item.get("type") or "").strip()


def _object_catalog_name(item: dict[str, Any]) -> str:
    return str(item.get("catalog_name") or item.get("catalog") or "").strip()


def _liquid_class_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("default") or item.get("liquid_class") or "").strip()


def _catalog_class_compatible(python_class: str, category: str, catalog_name: str) -> bool:
    if python_class not in _CATALOG_BACKED_CLASSES:
        return True
    semantic_category = _semantic_category(catalog_name, category)
    suggested = _python_class_for(catalog_name, semantic_category)
    if suggested is None:
        return False
    if python_class == suggested:
        return True
    if semantic_category == "plate" and python_class in {"Plate", "Plate96", "Plate96Deep"} and suggested == "Plate96":
        return True
    if semantic_category == "trough" and python_class in {"Trough", "Trough25mL", "Trough100mL", "Waste"}:
        return True
    if semantic_category == "tip_box":
        if suggested in _MCA_TIP_CLASSES:
            return python_class == suggested
        if suggested in _FCA_TIP_CLASSES:
            return python_class == suggested
    return False


def _catalog_semantic(catalog_name: str) -> dict[str, Any]:
    return _CATALOG_SEMANTIC_OVERRIDES.get(catalog_name.strip().lower(), {})


def _semantic_category(catalog_name: str, category: str | None) -> str | None:
    return str(_catalog_semantic(catalog_name).get("category") or category) if category is not None else _catalog_semantic(catalog_name).get("category")


def _is_sbs_footprint_catalog(catalog_lower: str) -> bool:
    return catalog_lower in {"300ml sbs", "300ml sbs_1"} or "alpaqua" in catalog_lower or "96_" in catalog_lower or "96 well" in catalog_lower


def _placement_fit_error(
    *,
    label: str,
    role: str,
    python_class: str,
    catalog_name: str,
    catalog_category: str,
    location: str,
) -> dict[str, Any] | None:
    if not location:
        return None
    res = _DeckResource(
        label=label,
        category=catalog_category,
        catalog_name=catalog_name,
        role=role,
    )
    if _resource_fits_location(res, location):
        return None
    return {
        "label": label,
        "python_class": python_class,
        "catalog_name": catalog_name,
        "location": location,
        "message": "Selected catalog does not physically fit the requested deck location.",
        "fix": "Place SBS-footprint resources on SBS nests and narrow troughs on trough/reservoir carrier locations.",
    }


def _waste_role_error(
    *,
    label: str,
    role: str,
    python_class: str,
    catalog_name: str,
) -> dict[str, Any] | None:
    role_lower = role.lower()
    catalog_lower = catalog_name.lower()
    if role_lower == "liquid_waste" and python_class == "WasteChute":
        return {
            "label": label,
            "role": role,
            "python_class": python_class,
            "message": "WasteChute cannot be used as a liquid-waste sink.",
            "fix": "Use Waste with an installed high-capacity reservoir catalog such as '300ml SBS'.",
        }
    if role_lower == "liquid_waste" and python_class == "Waste" and catalog_lower.startswith("300ml sbs"):
        return None
    if role_lower == "liquid_waste" and python_class not in {"Waste", "Trough", "Trough100mL", "Trough25mL"}:
        return {
            "label": label,
            "role": role,
            "python_class": python_class,
            "message": "Liquid waste must be a liquid-holding reservoir, not a disposal-only deck item.",
            "fix": "Use Waste/Trough with a suitable high-capacity catalog such as '300ml SBS'.",
        }
    return None


def _tip_resource_error(
    *,
    label: str,
    role: str,
    python_class: str,
    catalog_name: str,
) -> dict[str, Any] | None:
    if role.lower() in {"tips", "mca_tips"} and python_class in _FCA_TIP_CLASSES:
        return {
            "label": label,
            "role": role,
            "python_class": python_class,
            "catalog_name": catalog_name,
            "message": "96-well MCA workflows must use MCA96 tip boxes, not FCA tip boxes.",
            "fix": "Use an MCA tip catalog such as 'MCA96, 200ul, Box' or split volumes to stay within MCA tip capacity.",
        }
    return None


def _catalog_layout_error(*, label: str, item: dict[str, Any], catalog_name: str) -> dict[str, Any] | None:
    expected = _requested_layout(item)
    if not expected:
        return None
    actual = str(_catalog_semantic(catalog_name).get("layout") or "").strip().lower()
    if not actual or actual == expected:
        return None
    return {
        "label": label,
        "catalog_name": catalog_name,
        "expected_layout": expected,
        "catalog_layout": actual,
        "message": "Catalog semantic layout does not satisfy the requested labware format.",
        "fix": "Use a magnet/catalog whose layout matches the planned plate format.",
    }


def _requested_layout(item: dict[str, Any]) -> str:
    return str(
        item.get("layout")
        or item.get("format")
        or item.get("required_layout")
        or item.get("well_layout")
        or ""
    ).strip().lower()


def _object_draft_plate_layout(object_draft: dict[str, Any]) -> str | None:
    layouts: set[str] = set()
    for item in object_draft.get("labware", []):
        if not isinstance(item, dict):
            continue
        python_class = _object_python_class(item)
        catalog_name = _object_catalog_name(item)
        requested = _requested_layout(item)
        if requested in {"96", "384"}:
            layouts.add(requested)
        if python_class in {"Plate96", "Plate96Deep"}:
            layouts.add("96")
        elif python_class == "Plate384":
            layouts.add("384")
        catalog_lower = catalog_name.lower()
        if "384" in catalog_lower:
            layouts.add("384")
        elif "96" in catalog_lower or _catalog_semantic(catalog_name).get("layout") == "96":
            layouts.add("96")
    if len(layouts) == 1:
        return next(iter(layouts))
    return None


def _object_variable_errors(object_draft: dict[str, Any], resource_plan: dict[str, Any]) -> list[dict[str, Any]]:
    declared = {
        str(item.get("name") or item.get("variable") or "").strip()
        for item in object_draft.get("variables", [])
        if isinstance(item, dict)
    }
    declared.update(
        str(item.get("variable") or item.get("variable_name") or "").strip()
        for item in object_draft.get("liquid_classes", [])
        if isinstance(item, dict)
    )
    declared.discard("")
    errors: list[dict[str, Any]] = []
    for required in resource_plan.get("required_variables", []):
        variable = str(required.get("variable") or "").strip()
        if variable and variable not in declared:
            errors.append({
                "field": "variables",
                "message": "Object draft is missing a variable declared by the phase resource plan.",
                "variable": variable,
                "phase_index": required.get("phase_index"),
                "kind": required.get("kind"),
                "fix": "Include every phase volume, source-fill, split-volume, and liquid-class variable in the object draft.",
            })
    return errors


def _object_tip_capacity_error(object_draft: dict[str, Any], resource_plan: dict[str, Any]) -> dict[str, Any] | None:
    required = _float_arg(resource_plan.get("minimum_tip_capacity_ul"), default=0.0)
    if required <= 0.0:
        return None
    selected = [
        _TIP_CLASS_CAPACITY_UL.get(_object_python_class(item), 0.0)
        for item in object_draft.get("labware", [])
        if isinstance(item, dict) and _object_python_class(item) in _TIP_CLASS_CAPACITY_UL
    ]
    if not selected:
        return None
    max_selected = max(selected)
    if max_selected >= required:
        return None
    return {
        "field": "labware",
        "message": "Selected tips cannot satisfy planned operation volumes.",
        "minimum_tip_capacity_ul": required,
        "selected_max_tip_capacity_ul": max_selected,
        "recommended_tip_class": resource_plan.get("recommended_tip_class"),
        "recommended_tip_catalog": resource_plan.get("recommended_tip_catalog"),
        "fix": "Select higher-capacity tips or provide split_volume_variable/split plan so each operation fits the selected tips.",
    }


def _requires_resource_plan(object_draft: dict[str, Any]) -> bool:
    text_parts = [
        str(object_draft.get("protocol_name") or ""),
        str(object_draft.get("summary") or ""),
    ]
    for item in object_draft.get("labware", []):
        if isinstance(item, dict):
            text_parts.extend(str(item.get(key) or "") for key in ("label", "role", "category"))
    text = " ".join(text_parts).lower()
    multi_step = any(token in text for token in ("multi-step", "multistep", "cleanup", "wash", "magnet"))
    liquid_waste = any(
        isinstance(item, dict) and "waste" in str(item.get("role") or item.get("label") or "").lower()
        for item in object_draft.get("labware", [])
    )
    return multi_step and liquid_waste


def _check_source_against_approved_objects(source: str, object_draft: dict[str, Any]) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return str(exc)

    approved: dict[str, dict[str, str]] = {}
    for item in object_draft.get("labware", []):
        if not isinstance(item, dict):
            continue
        label = _object_label(item)
        if not label:
            continue
        approved[label] = {
            "python_class": _object_python_class(item),
            "catalog_name": _object_catalog_name(item),
        }

    placements = _extract_wt_placements(tree)
    placement_labels = set(placements)
    approved_labels = set(approved)
    missing = sorted(approved_labels - placement_labels)
    if missing:
        return f"Generated source omits approved labware resource(s): {', '.join(missing)}."
    added = sorted(placement_labels - approved_labels)
    if added:
        return f"Generated source adds unapproved labware resource(s): {', '.join(added)}."
    for label, expected in approved.items():
        actual = placements[label]
        if actual.get("python_class") != expected.get("python_class"):
            return (
                f"Generated source changes approved class for {label!r}: "
                f"expected {expected.get('python_class')!r}, got {actual.get('python_class')!r}."
            )
        if actual.get("catalog_name") != expected.get("catalog_name"):
            return (
                f"Generated source changes approved catalog for {label!r}: "
                f"expected {expected.get('catalog_name')!r}, got {actual.get('catalog_name')!r}."
            )

    liquid_variables = {
        _liquid_class_name(item): str(item.get("variable") or item.get("variable_name") or "").strip()
        for item in object_draft.get("liquid_classes", [])
        if isinstance(item, dict) and _liquid_class_name(item) and (item.get("variable") or item.get("variable_name"))
    }
    if liquid_variables:
        liquid_class_args = _extract_liquid_class_arguments(tree)
        for name, variable in liquid_variables.items():
            literal_used = any(kind == "literal" and value == name for kind, value in liquid_class_args)
            variable_used = any(kind == "name" and value == variable for kind, value in liquid_class_args)
            if literal_used or not variable_used:
                return (
                    f"Approved liquid class {name!r} must be used through variable "
                    f"{variable!r}, not hardcoded in pipetting calls."
                )
    resource_plan = object_draft.get("_resource_plan") or {}
    volume_variables = [
        required
        for required in resource_plan.get("required_variables", [])
        if isinstance(required, dict) and required.get("kind") in {"volume", "split_volume"}
    ]
    if volume_variables:
        volume_args = _extract_pipetting_volume_arguments(tree)
        for required in volume_variables:
            variable = str(required.get("variable") or "").strip()
            value = required.get("value")
            if not variable:
                continue
            literal_used = any(kind == "literal" and _float_arg(arg_value, default=-1.0) == _float_arg(value, default=-2.0) for kind, arg_value in volume_args)
            variable_used = any(kind == "name" and arg_value == variable for kind, arg_value in volume_args)
            if literal_used or not variable_used:
                return (
                    f"Approved phase volume {value!r} must be used through variable "
                    f"{variable!r}, not hardcoded in pipetting calls."
                )
    return None


def _extract_wt_placements(tree: ast.AST) -> dict[str, dict[str, str]]:
    placements: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "place"
            and isinstance(func.value, ast.Name)
            and func.value.id == "wt"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Call):
            continue
        ctor = node.args[0]
        class_name = ctor.func.id if isinstance(ctor.func, ast.Name) else None
        label = _literal_arg(ctor.args[0]) if ctor.args else None
        catalog = None
        for keyword in ctor.keywords:
            if keyword.arg == "catalog":
                catalog = _literal_arg(keyword.value)
                break
        if class_name and label:
            placements[str(label)] = {
                "python_class": str(class_name),
                "catalog_name": str(catalog or ""),
            }
    return placements


def _extract_liquid_class_arguments(tree: ast.AST) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "liquid_class":
                continue
            if isinstance(keyword.value, ast.Name):
                values.append(("name", keyword.value.id))
            else:
                literal = _literal_arg(keyword.value)
                if isinstance(literal, str):
                    values.append(("literal", literal))
    return values


def _extract_pipetting_volume_arguments(tree: ast.AST) -> list[tuple[str, Any]]:
    values: list[tuple[str, Any]] = []
    pipetting = {"aspirate", "dispense", "mix", "empty_tips"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in pipetting:
            continue
        volume_node: ast.AST | None = node.args[1] if len(node.args) > 1 else None
        if volume_node is None:
            for keyword in node.keywords:
                if keyword.arg in {"volume", "volume_ul"}:
                    volume_node = keyword.value
                    break
        if volume_node is None:
            continue
        if isinstance(volume_node, ast.Name):
            values.append(("name", volume_node.id))
        else:
            literal = _literal_arg(volume_node)
            if isinstance(literal, (int, float)):
                values.append(("literal", literal))
    return values


def _literal_arg(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _python_class_for(name: str, category: str) -> str | None:
    lowered = name.lower()
    if category == "plate":
        return "Plate384" if "384" in lowered else "Plate96"
    if category == "tip_box":
        if "mca96" in lowered and "500" in lowered:
            return "MCA500Box"
        if "mca96" in lowered and "200" in lowered:
            return "MCA200Box"
        if "mca96" in lowered:
            return "MCA100Box"
        if "1000" in lowered:
            return "FCA1000Box"
        if "200" in lowered:
            return "FCA200Box"
    if category == "trough":
        if "waste" in lowered:
            return "Waste"
        if "100ml" in lowered or "100 ml" in lowered:
            return "Trough100mL"
        return "Trough25mL"
    if category == "magnet_rack":
        return "MagnetRack"
    if category == "waste_chute":
        return "WasteChute"
    if category == "fixed_deck":
        return "FixedDeck"
    return None


def _liquid_class_suggestions(name: str, limit: int = 8) -> list[dict[str, Any]]:
    terms = [term for term in re.split(r"\W+", name) if term]
    patterns = terms or ["Water"]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with open_index() as conn:
        for pattern in patterns:
            for row in conn.execute(
                "SELECT * FROM liquid_classes WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT ?",
                (f"%{pattern}%", limit),
            ).fetchall():
                if row["name"] in seen:
                    continue
                seen.add(row["name"])
                rows.append({"name": row["name"], "guid": row["guid"], "head": row["head"]})
                if len(rows) >= limit:
                    return rows
        if not rows:
            for row in conn.execute(
                "SELECT * FROM liquid_classes WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT ?",
                ("%Water%", limit),
            ).fetchall():
                rows.append({"name": row["name"], "guid": row["guid"], "head": row["head"]})
    return rows


def _liquid_class_supported_heads(row) -> list[str]:
    heads = list(getattr(row, "supported_heads", ()) or ())
    if not heads and getattr(row, "file_path", None):
        try:
            heads = list(load_xlqc(row.file_path).supported_heads)
        except Exception:
            heads = []
    if not heads and getattr(row, "head", None):
        heads = [row.head]
    return heads


def _device_type_compatible(requested: str, supported_heads: list[str]) -> bool:
    req = requested.strip().lower()
    supported = {head.strip().lower() for head in supported_heads}
    if req in supported:
        return True
    if req == "mca":
        return any(head.startswith("mca") for head in supported)
    if req == "liha":
        return "fca" in supported or "airfca" in supported
    return False


def _slot_summary(valid_slots: frozenset[tuple[str, int]]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for location, position in sorted(valid_slots):
        out.setdefault(location, []).append(position)
    return out


def _looks_like_guid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))


def _normalize_api_lookup(value: str) -> str:
    cleaned = (value or "").strip().lower()
    aliases = {
        "gripper": "wt.gripper",
        "wt.gripper": "wt.gripper",
        "liha": "wt.liha",
        "wt.liha": "wt.liha",
        "mca96": "wt.mca96",
        "mca96head": "wt.mca96",
        "wt.mca96": "wt.mca96",
        "worktable": "worktable",
        "wt": "worktable",
        "labware": "labware",
        "plate96": "plate96",
        "trough100ml": "trough100ml",
        "fca1000box": "fca1000box",
    }
    return aliases.get(cleaned, cleaned)


def _rule(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("name", "rule_type", "category", "protocol_type", "description", "severity", "requirements")}


def _module(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("name", "domain", "description", "constraints")}


def _pattern(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("name", "description", "pattern_type", "steps", "parameters")}


# Built-in authoring patterns. These are protocol-construction recipes that
# are too specific for the global SYSTEM_PROMPT but useful when the model
# asks lookup_rules for a particular protocol_type.
_BUILTIN_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "trough_to_96_well_liha_column_fan_out",
        "pattern_type": "builtin",
        "protocol_types": ("transfer", "fill"),
        "categories": ("liquid_handling", "trough_to_plate"),
        "description": (
            "Fill every well of a Plate96 from a single trough using LiHa "
            "column-wise. The MCA-96 head cannot fan one trough well across 96 "
            "destination wells in a single aspirate (G1 in MANUAL_TEST.md), so "
            "prefer LiHa with FCA tips and an explicit per-column loop."
        ),
        "steps": [
            "Place a Trough25mL or Trough100mL at WS_100ml_1 position 1 (SAT deck).",
            "Place the Plate96 destination at Nest61mm_Pos position 2.",
            "Place FCA1000Box tips ('FCA, 1000ul SBS') at Nest61mm_Pos position 6.",
            "Fill the trough with total volume = transfer_ul * 96 + dead_volume; "
            "use source_trough.fill_all(reagent, total_ul). Trough uses fill_all(), not fill().",
            "head = wt.liha; head.get_tips(tips) once.",
            "for column_index in range(12): "
            "head.aspirate(trough, transfer_ul, liquid_class='Water Free Single'); "
            "head.dispense(plate, transfer_ul, liquid_class='Water Free Single', "
            "well_offset=column_index * 8).",
            "head.drop_tips() at the end.",
        ],
        "parameters": {
            "recommended_total_volume_for_20uL_fill_ul": 5000,
            "minimum_total_volume_for_20uL_fill_ul": 2500,
        },
        "anti_patterns": [
            "MCA aspirate/dispense with column= or wells= keyword (no such API).",
            "Subscripting labware (plate['A1']); use plate.well('A1') instead.",
            "Calling fill() on a Trough; the API is fill_all().",
        ],
    },
]


def _filter_builtin_patterns(
    *,
    protocol_type: str | None,
    category: str | None,
) -> list[dict[str, Any]]:
    pt = (protocol_type or "").strip().lower() or None
    cat = (category or "").strip().lower() or None
    out: list[dict[str, Any]] = []
    for entry in _BUILTIN_PATTERNS:
        if pt is not None and pt not in {p.lower() for p in entry.get("protocol_types", ())}:
            continue
        if cat is not None and cat not in {c.lower() for c in entry.get("categories", ())}:
            continue
        out.append(
            {
                "name": entry["name"],
                "pattern_type": entry["pattern_type"],
                "description": entry["description"],
                "steps": list(entry.get("steps", [])),
                "parameters": dict(entry.get("parameters", {})),
                "anti_patterns": list(entry.get("anti_patterns", [])),
            }
        )
    return out


def _simulation_category(report: Any) -> str | None:
    failure = getattr(report, "failure", None) if report is not None else None
    return getattr(failure, "category", None)


def _failure_message(report: Any) -> str | None:
    failure = getattr(report, "failure", None) if report is not None else None
    return getattr(failure, "message", None)


def _simulation_failure(report: Any) -> dict[str, Any] | None:
    failure = getattr(report, "failure", None) if report is not None else None
    if failure is None:
        return None
    payload = failure.to_dict() if hasattr(failure, "to_dict") else {
        "category": getattr(failure, "category", None),
        "message": getattr(failure, "message", None),
    }
    details = payload.get("details") or {}
    for key, value in details.items():
        payload.setdefault(key, value)
    return payload


def _simulation_state_summary(report: Any) -> dict[str, Any] | None:
    summary = getattr(report, "state_summary", None) if report is not None else None
    return summary if isinstance(summary, dict) else None


def _python_build_failure(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    if isinstance(exc, (ImportError, NameError)):
        return {
            "category": FailureCategory.PYTHON_BUILD_FAILURE.value,
            "exception_type": type(exc).__name__,
            "message": message,
            "details": {"valid_exported_classes": sorted(_EXPORTED_TECANLAB_CLASSES)},
            "valid_exported_classes": sorted(_EXPORTED_TECANLAB_CLASSES),
            "repair_options": ["use_exported_tecanlab_class", "call_lookup_api_for_unknown_symbol"],
        }
    if isinstance(exc, AttributeError):
        match = re.search(r"'([^']+)' object has no attribute '([^']+)'", message)
        details = {}
        if match:
            details = {"object": match.group(1), "method": match.group(2)}
        return {
            "category": "missing_method",
            "exception_type": type(exc).__name__,
            "message": message,
            "details": details,
            **details,
            "repair_options": ["call_lookup_api", "rewrite_using_supported_method"],
        }
    return {
        "category": FailureCategory.PYTHON_BUILD_FAILURE.value,
        "exception_type": type(exc).__name__,
        "message": message,
        "details": {},
        "repair_options": [],
    }


def _compact(value: dict[str, Any], *, max_text: int = 1200) -> dict[str, Any]:
    text = json.dumps(value, default=str)
    if len(text) <= max_text:
        return value
    compacted = dict(value)
    compacted["_truncated"] = True
    compacted["_summary"] = text[:max_text]
    return compacted
