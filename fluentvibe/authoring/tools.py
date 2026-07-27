"""Native tool surface exposed to the LM protocol author."""

from __future__ import annotations

import ast
import inspect
import json
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..catalog import (
    CatalogSchemaOutOfDate,
    find_components,
    find_components_by_metadata,
    find_grip_modes,
    find_legal_stacks,
    find_sites_for,
    find_workspaces_using,
    get_database,
    load_xcmp,
    load_xlqc,
    open_index,
    resolve_by_name,
    resolve_liquid_class_by_name,
    retrieve_dsl_recipes,
)
from ..catalog.inference import component_taxonomy
from ..reagent import ROLES as _REAGENT_ROLES
from .fluentcontrol_shell import (
    DEFAULT_SHELL_XSCR,
    validate_generated_xscr_via_shell,
    validate_xscr_direct,
)
from .grounding import (
    GroundingBundle,
    GroundingError,
    load_current_worktable_snapshot,
    load_grounding_bundle,
)
from .lab_scope import LabScope
from .models import (
    FailureCategory,
    FunctionalGroupPlan,
    IntentSpec,
    ProtocolWorkflowPlan,
    VariableBinding,
)
from .repair_policy import resolve_repair_policy
from .validator import AuthoringValidator
from .workspace_modules import WorkspaceModule, copy_workspace_modules

ToolFn = Callable[..., dict[str, Any]]

_EXPORTED_FLUENTVIBE_CLASSES: frozenset[str] = frozenset({
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

# Enforce-mode deterministic role → (labware family, layout role) resolution.
# `family` keys index generation.yaml grounding_defaults.labware; `layout`
# keys index grounding_defaults.layout. First matching rule wins. Used only
# by `_autoground_object_draft_enforce` (lab_scope.enforces) — off/cheatsheet
# never reach this table, keeping the baseline byte-identical.
_ENFORCE_FAMILY_PYCLASS: dict[str, str] = {
    "plate_96": "Plate96",
    "plate_384": "Plate384",
    "magnet_plate_96": "MagnetRack",
    "waste_reservoir": "Waste",
    "reservoir_standard": "Trough25mL",
    "reservoir_ethanol": "Trough100mL",
    "fca_tipbox_small": "FCA200Box",
    "fca_tipbox_large": "FCA1000Box",
    "mca96_tipbox_small": "MCA100Box",
    "mca96_tipbox_medium": "MCA200Box",
    "mca96_tipbox_large": "MCA500Box",
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
            {"name": "from_workspace", "signature": "Worktable.from_workspace(name, *, workspace_guid, auto_place=False, protocol_name=None, comment=None)", "examples": ["wt = Worktable.from_workspace('SAT_Fluent_780_Rev3', workspace_guid='291ba293-6361-4f8f-aa8d-7c2643d3f096', auto_place=False)"]},
            {"name": "place", "signature": "wt.place(labware, location, position)", "examples": ["plate = wt.place(Plate96('DestPlate', catalog='96_ABgene_SuperPlate_Thermo_AB2800'), 'Nest61mm_Pos', 2)"]},
            {"name": "group", "signature": "wt.group(name)", "examples": ["wt.group('Transfer')"]},
            {"name": "loop", "signature": "with wt.loop(times, *, name='Loop', loop_variable=None): ...", "examples": ["# native FluentControl loop — do NOT unroll with a Python for-loop", "with wt.loop(times=12, name='Dispense columns', loop_variable='col'):", "    head.aspirate(trough, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS')", "    head.dispense(plate, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS', well_offset='(col-1)*8')"]},
            {"name": "declare_variable", "signature": "wt.declare_variable(name, default)", "examples": ["wt.declare_variable('RunId', 'demo')"]},
            {"name": "set_sim_value", "signature": "wt.set_sim_value(name, value)", "examples": ["wt.set_sim_value('RunId', 'demo')"]},
            {"name": "set_variable", "signature": "wt.set_variable(name, value)", "examples": ["wt.set_variable('RunId', 'demo')"]},
            {"name": "wait", "signature": "wt.wait(duration_seconds)", "examples": ["wt.wait(30)"]},
            {"name": "add_comment", "signature": "wt.add_comment(text)", "examples": ["wt.add_comment('Incubate at room temperature')"]},
            {"name": "worklist", "signature": "wt.worklist(source_path, *, gwl_path=None, liquid_class=None, diti_type='TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 50ul SBS', selected_tips=range(8), execute=True)", "examples": ["wt.group('Worklist')", "wt.worklist(r'C:\\ProgramData\\Tecan\\VisionX\\Worklists\\TEMP_Tier6.gwl', liquid_class='Water Free Single')", "# CSV sources are also accepted; fluentvibe emits Convert CSV to GWL before loading."]},
            {"name": "execute_worklist", "signature": "wt.execute_worklist(delete_gwl_scripts=False)", "examples": ["wt.worklist('a.gwl', execute=False)", "wt.worklist('b.gwl', execute=False)", "wt.execute_worklist()"]},
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
        "note": "wt.liha is the only fixed-channel pipetting head exposed by fluentvibe. Use it for FCA-style operations (trough-to-plate dispenses, single-channel or per-column transfers, individual well aspirate/dispense). Use wt.mca96 only for true 96-channel plate-to-plate moves. PASS DECLARED VARIABLES BY NAME (a string) for volume and liquid_class — `'BEAD_VOLUME_UL'`, not the Python value — or the rendered protocol bakes in a literal and the FC variable is dead. To cover all 12 columns, wrap a single aspirate/dispense in `with wt.loop(times=12, loop_variable='col')` and address columns with `well_offset='(col-1)*8'`; NEVER unroll with a Python `for` loop.",
        "methods": [
            {"name": "get_tips", "signature": "get_tips(labware=None)", "examples": ["head = wt.liha", "head.get_tips(tips)"]},
            {"name": "aspirate", "signature": "aspirate(labware, volume, *, liquid_class=None, well_offset=None)", "examples": ["head.aspirate(source, 'TARGET_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER')"]},
            {"name": "dispense", "signature": "dispense(labware, volume, *, liquid_class=None, well_offset=None)", "examples": ["with wt.loop(times=12, loop_variable='col'):", "    head.dispense(dest, 'TARGET_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER', well_offset='(col-1)*8')"]},
            {"name": "mix", "signature": "mix(labware, volume, *, cycles=10, liquid_class=None, well_offset=None)", "examples": ["head.mix(plate, 'MIX_VOLUME_UL', cycles=10, liquid_class='LIQUID_CLASS_MIX')"]},
            {"name": "empty_tips", "signature": "empty_tips(labware, volume=0, *, liquid_class=None)", "examples": ["head.empty_tips(waste, 'SUPERNATANT_VOLUME_UL')"]},
            {"name": "drop_tips", "signature": "drop_tips(labware=None)", "examples": ["head.drop_tips()", "head.drop_tips(tips)"]},
        ],
        "forbidden_common_mistakes": ["pick_up", "return_tips", "mount_adapter", "drop_adapter"],
    },
    "wt.fca": {
        "object": "wt.fca",
        "note": "fluentvibe does NOT expose wt.fca as a runtime head. FCA-style fixed-channel pipetting (single-channel, per-column, trough-to-plate dispenses) is authored through wt.liha. Call lookup_api('wt.liha') for the full method surface. Use wt.mca96 only for true 96-channel plate-to-plate operations.",
        "aliased_to": "wt.liha",
        "forbidden_common_mistakes": ["wt.fca.aspirate", "wt.fca.dispense", "wt.fca.pick_up"],
    },
    "wt.mca96": {
        "object": "wt.mca96",
        "note": "True 96-channel head: one aspirate/dispense/mix touches all 96 wells at once — no per-column loop. PASS DECLARED VARIABLES BY NAME (a string) for the volume and liquid_class (e.g. 'SUPERNATANT_ASPIRATE_UL', 'LIQUID_CLASS_SUPERNATANT'); passing the Python value bakes a literal into the protocol and leaves the FC variable unused.",
        "methods": [
            {"name": "mount_adapter", "signature": "mount_adapter(adapter=None)", "examples": ["head = wt.mca96", "head.mount_adapter()"]},
            {"name": "pick_up", "signature": "pick_up(tip_box)", "examples": ["head.pick_up(tips)"]},
            {"name": "aspirate", "signature": "aspirate(target, volume_ul, *, liquid_class)", "examples": ["head.aspirate(source, 'SUPERNATANT_ASPIRATE_UL', liquid_class='LIQUID_CLASS_SUPERNATANT')"]},
            {"name": "dispense", "signature": "dispense(target, volume_ul, *, liquid_class)", "examples": ["head.dispense(dest, 'TRANSFER_VOLUME_UL', liquid_class='LIQUID_CLASS_ELUATE')"]},
            {"name": "mix", "signature": "mix(target, volume_ul, *, cycles=10, liquid_class)", "examples": ["head.mix(plate, 'MIX_VOLUME_UL', cycles=10, liquid_class='LIQUID_CLASS_BEADS')"]},
            {"name": "empty_tips", "signature": "empty_tips(target, volume_ul, *, liquid_class='Empty Tip')", "examples": ["head.empty_tips(waste, 'SUPERNATANT_ASPIRATE_UL')"]},
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


# Common arg-name slips small local models make, mapped onto the canonical
# parameter. Only applied when the canonical name is a real parameter of the
# target method and the model didn't already supply it.
_TOOL_ARG_ALIASES = {
    "message": "question",
    "prompt": "question",
    "query": "question",
    "code": "source",
    "python": "source",
    "script": "source",
    "source_code": "source",
    "python_source": "source",
    "draft": "source",
}


def reconcile_tool_args(
    fn: Callable[..., Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Best-effort align LM-supplied kwargs with a tool method's real signature.

    Small local models routinely misname args (``message`` for ``question``)
    or pass junk keys, which would raise an opaque ``TypeError`` deep in the
    call. Filter to the parameters the method actually accepts and remap known
    aliases onto missing parameters, so a fumbled call still lands (or fails
    with a clean, recoverable message rather than a crash).
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(payload)
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return dict(payload)  # method takes **kwargs — don't second-guess it
    accepted = {
        n for n, p in params.items()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    cleaned: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, value in payload.items():
        (cleaned if key in accepted else unknown)[key] = value
    for key, value in unknown.items():
        target = _TOOL_ARG_ALIASES.get(key)
        if target and target in accepted and target not in cleaned:
            cleaned[target] = value
    return cleaned


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
        _tool("present_source_protocol_plan",
              "When the user provides attached file context, present the source "
              "protocol you extracted from that document BEFORE object drafting. "
              "Every document step must be classified as automated, manual_off_deck, "
              "or unsupported. Include key volumes, reagents, labware, incubations, "
              "and page/source references when available. Wait for user approval or "
              "requested changes before calling present_object_draft.", {
            "protocol_title": {"type": "string"},
            "summary": {"type": "string"},
            "source_files": {"type": "array", "items": {"type": "object"}},
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Ordered source-document steps with description, classification, "
                               "volumes, reagents, labware, incubations, and source_ref/page.",
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        }, required=("protocol_title", "summary", "source_files", "steps")),
        _tool("declare_protocol_workflow",
              "Declare the high-level protocol plan before drafting Python. "
              "The first two groups must be exactly Variables and Labware Placement. "
              "After those scaffold groups, include every requested functional stage; "
              "at least one non-scaffold group is required. "
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
                "description": "At least three ordered groups. The first two must be Variables and Labware Placement; the remaining groups name every requested protocol stage.",
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
        _tool("lookup_api", "Return supported fluentvibe public API methods and examples for an object or class.", {
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
        _tool("get_labware", "Return exact installed labware metadata and suggested fluentvibe Python class. "
              "Includes a compact compatibility block (sites that accept this footprint, grip modes, "
              "stack candidates, workspaces using this labware) when the catalog index is current.", {
            "name": {"type": "string"},
        }),
        _tool("lookup_compatibility",
              "Compatibility query against the catalog index. Returns ALL "
              "(workspace, site) entries whose footprint matches the named "
              "labware, gripper-mode whitelist, legal stack candidates, and "
              "workspaces that already use this component. Prefer this over "
              "chained search_labware/get_labware when you want to know "
              "where a given labware can sit, what can grip it, what stacks "
              "with it, or which workspaces reference it.", {
            "name": {"type": "string", "description": "Exact catalog component name."},
            "kind": {
                "type": "string",
                "enum": ["sites", "grip_modes", "stacks", "workspaces", "all"],
                "description": "Which slice of compatibility to return; defaults to 'all'.",
            },
        }, required=("name",)),
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
        _tool("ground_in_parallel",
              "Fan out grounding across multiple focused subagents in parallel. "
              "Use AFTER clarifications and intent are resolved, when you know "
              "which labware/liquid-class domains the protocol needs. Each named "
              "category spawns a focused LM subagent that grounds its domain; "
              "results are cached so subsequent search_labware / get_labware / "
              "lookup_workspace / lookup_rules / lookup_liquid_class calls return "
              "instantly. Pick only the categories your protocol actually needs — "
              "typical: 3–5.", {
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Category names to ground. Available: workspace, rules, "
                    "plates, pcr_plates, deep_well_plates, troughs, mca_tips, "
                    "fca_tips, magnets, tube_racks, waste, adapters, "
                    "filter_plates, liquid_classes."
                ),
            },
            "extra_prompt_terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional extra context appended to each subagent's user prompt.",
            },
        }, required=("categories",)),
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


# Tools that are pure read-only catalog/heuristic lookups: no registry-state
# mutation, no early-return semantics, safe to execute concurrently. Only
# `repair_lock.observe_tool_result` results from `simulate_python_draft` /
# `compile_and_simulate` mutate lock state — neither tool is in this set.
PARALLEL_SAFE_TOOLS: frozenset[str] = frozenset({
    "lookup_api",
    "lookup_workspace",
    "list_valid_positions",
    "search_labware",
    "get_labware",
    "lookup_liquid_class",
    "lookup_rules",
    "plan_protocol_resources",
    "suggest_deck_layout",
    "lookup_compatibility",
})


def _freeze_arguments(payload: Any) -> Any:
    """Recursively convert dict/list payloads into hashable keys for caching."""
    if isinstance(payload, dict):
        return tuple(sorted((k, _freeze_arguments(v)) for k, v in payload.items()))
    if isinstance(payload, (list, tuple)):
        return tuple(_freeze_arguments(v) for v in payload)
    return payload


class AuthoringToolRegistry:
    def __init__(
        self,
        *,
        output_dir: Path,
        workspace_name: str | None = None,
        workspace_guid: str | None = None,
        current_prompt: str | None = None,
        workspace_modules: tuple[WorkspaceModule, ...] = (),
    ) -> None:
        self.output_dir = output_dir
        self.workspace_name = workspace_name
        self.workspace_guid = workspace_guid
        self.current_prompt = current_prompt
        self.original_prompt = current_prompt or ""
        self.latest_user_text = current_prompt or ""
        self.user_history_text = current_prompt or ""
        self.workspace_modules = tuple(workspace_modules)
        self.validator = AuthoringValidator(workspace_modules=self.workspace_modules)
        self.calls: list[dict[str, Any]] = []
        self.model_turns: list[dict[str, Any]] = []
        self._compile_attempt = 0
        self.current_intent: IntentSpec = IntentSpec()
        self.workflow_plan: ProtocolWorkflowPlan | None = None
        # Set once declare_protocol_workflow succeeds: True ⇒ enforce per-group
        # staged checkpoints; False ⇒ the declared plan is simple enough to draft
        # in one pass (skills mode only — see graph._should_stage).
        self.staged_drafting: bool = False
        self.source_protocol_plan: dict[str, Any] | None = None
        self.source_protocol_plan_approved: bool = False
        self.object_draft: dict[str, Any] | None = None
        self.object_draft_approved: bool = False
        self.functional_group_plan_approved: bool = False
        self.pending_approval_kind: str | None = None
        self._grounding_cache: dict[tuple[str, Any], dict[str, Any]] = {}
        self._lock = threading.RLock()
        # Set by session/service after the registry is constructed so
        # `ground_in_parallel` has an LM client to fan subagents out with.
        # If left None, the tool returns a structured "not configured"
        # error instead of crashing.
        self._subagent_client: Any | None = None
        self._subagent_pool_size: int = 8
        self._subagent_timeout_s: float = 240.0
        self._grounding_runs: set[str] = set()
        # Narrowed-scope experiment. Inert default (mode "off" ⇒ baseline);
        # session/service overwrite this when --lab-scope/env is set.
        self.lab_scope: LabScope = LabScope()
        if workspace_name is None and workspace_guid is None:
            self._bind_current_worktable_snapshot()

    def __deepcopy__(self, memo: dict) -> "AuthoringToolRegistry":
        # threading.RLock is not deepcopy-safe. Rebuild lock + cache; deepcopy
        # the rest. Tests rely on deepcopy(registry) to clone state for
        # parity checks against bound LangChain tools.
        import copy as _copy

        clone = self.__class__.__new__(self.__class__)
        memo[id(self)] = clone
        for key, value in self.__dict__.items():
            if key == "_lock":
                continue
            if key == "_grounding_cache":
                # Cache is process-local optimization; safe to start fresh.
                clone.__dict__[key] = {}
                continue
            clone.__dict__[key] = _copy.deepcopy(value, memo)
        clone._lock = threading.RLock()
        return clone

    def dispatch(self, name: str, arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        payload = self._parse_arguments(arguments)
        t0 = time.monotonic()
        result = self._dispatch_pure(name, payload)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        with self._lock:
            self.calls.append(self._call_entry(
                name,
                payload,
                result,
                elapsed_ms=elapsed_ms,
                dispatch_source="live",
            ))
        return result

    def _dispatch_pure(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool without appending to `self.calls`.

        Used by the parallel tool-dispatch path so concurrent workers do not
        race on the call log. Callers that want the call recorded must follow
        up with `_record_call(name, payload, result)` on the main thread.
        """
        fn = self.functions().get(name)
        if fn is None:
            return _compact({"ok": False, "category": "unknown_tool", "message": f"Unknown tool {name!r}."})
        payload = reconcile_tool_args(fn, payload)
        try:
            result = fn(**payload)
        except TypeError as exc:
            result = {
                "ok": False,
                "category": "bad_tool_arguments",
                "message": f"{exc}. `{name}` accepts: {self._tool_arg_names(name)}. "
                           "Resend the call with the correct argument names.",
            }
        except Exception as exc:
            result = {"ok": False, "category": "tool_error", "message": str(exc)}
        return _compact(result)

    @staticmethod
    @lru_cache(maxsize=64)
    def _tool_arg_names(name: str) -> str:
        """Comma-joined parameter names a tool accepts, for actionable errors."""
        for td in tool_definitions():
            fn = td["function"]
            if fn["name"] == name:
                props = (fn.get("parameters") or {}).get("properties") or {}
                req = set((fn.get("parameters") or {}).get("required") or [])
                return ", ".join(
                    f"{k} (required)" if k in req else k for k in props
                ) or "(no arguments)"
        return "(unknown tool)"

    def _record_call(
        self,
        name: str,
        payload: dict[str, Any],
        result: dict[str, Any],
        *,
        elapsed_ms: float = 0.0,
        dispatch_source: str = "live",
    ) -> dict[str, Any]:
        with self._lock:
            self.calls.append(self._call_entry(
                name,
                payload,
                result,
                elapsed_ms=elapsed_ms,
                dispatch_source=dispatch_source,
            ))
        return result

    def _call_entry(
        self,
        name: str,
        payload: dict[str, Any],
        result: dict[str, Any],
        *,
        elapsed_ms: float,
        dispatch_source: str,
    ) -> dict[str, Any]:
        category = result.get("category") or result.get("stage") or result.get("failure_category")
        return {
            "name": name,
            "arguments": payload,
            "result": result,
            "ok": result.get("ok"),
            "category": category,
            "elapsed_ms": max(0.0, float(elapsed_ms)),
            "dispatch_source": dispatch_source,
            "result_summary": _result_size_summary(result),
        }

    def record_model_turn(
        self,
        *,
        iteration: int,
        elapsed_ms: float,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self.model_turns.append({
                "iteration": iteration,
                "elapsed_ms": max(0.0, float(elapsed_ms)),
                "tool_calls": [
                    {
                        "name": str(call.get("name") or ""),
                        "arguments": dict(call.get("args") or call.get("arguments") or {}),
                        "id": call.get("id"),
                    }
                    for call in tool_calls
                ],
            })

    def cache_lookup(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any] | None:
        if name not in PARALLEL_SAFE_TOOLS:
            return None
        key = (name, _freeze_arguments(arguments or {}))
        with self._lock:
            return self._grounding_cache.get(key)

    def cache_store(self, name: str, arguments: dict[str, Any] | None, result: dict[str, Any]) -> None:
        if name not in PARALLEL_SAFE_TOOLS:
            return
        key = (name, _freeze_arguments(arguments or {}))
        with self._lock:
            self._grounding_cache.setdefault(key, result)

    def _bind_current_worktable_snapshot(self) -> None:
        snapshot = load_current_worktable_snapshot()
        if snapshot is None:
            return
        workspace = snapshot.get("workspace") or {}
        name = str(workspace.get("name") or "").strip()
        guid = str(workspace.get("guid") or "").strip()
        if not name or not guid:
            return

        self.workspace_name = name
        self.workspace_guid = guid
        try:
            bundle = load_grounding_bundle(workspace_name=name, workspace_guid=guid)
            valid_slots = bundle.valid_slots
            default_layout = bundle.layout_defaults
        except Exception:
            valid_slots = frozenset(
                (str(location), int(position))
                for location, position in snapshot.get("valid_slots", ())
            )
            default_layout = {}
        valid_positions = _slot_summary(valid_slots)
        # Lean result, byte-identical to the live lookup_workspace tool
        # (cache-hit == cache-miss). The full current_worktable snapshot
        # (occupants / compatibility_by_occupant /
        # accepts_by_occupied_carrier_site / positions, ~112 KB) was echoed
        # to the model with zero authoring-decision value and dominated
        # ~50% of per-turn context; it is not consumed anywhere. The model
        # places by role from valid_positions + default_layout.
        workspace_result = {
            "ok": True,
            "workspace": {
                "name": name,
                "guid": guid,
            },
            "valid_positions": valid_positions,
            "default_layout": default_layout,
        }
        for args in (
            {},
            {"name_or_guid": ""},
            {"name_or_guid": name},
            {"name_or_guid": guid},
        ):
            self.cache_store("lookup_workspace", args, workspace_result)
        for location, positions in valid_positions.items():
            self.cache_store(
                "list_valid_positions",
                {"location": location},
                {
                    "ok": bool(positions),
                    "location": location,
                    "positions": list(positions),
                },
            )

    def configure_subagent_client(
        self,
        client: Any,
        *,
        pool_size: int = 8,
        timeout_s: float = 240.0,
    ) -> None:
        """Wire an LM client into the registry so `ground_in_parallel` can
        fan out category subagents. Intended to be called once by
        PromptAuthoringService/Session after constructing the registry.
        """
        self._subagent_client = client
        self._subagent_pool_size = max(2, pool_size)
        self._subagent_timeout_s = max(15.0, timeout_s)

    def set_authoring_context(
        self,
        *,
        original_prompt: str | None = None,
        latest_user_text: str | None = None,
        user_history_text: str | None = None,
    ) -> None:
        if original_prompt is not None:
            self.original_prompt = original_prompt
        if latest_user_text is not None:
            self.latest_user_text = latest_user_text
        if user_history_text is not None:
            self.user_history_text = user_history_text
        context_text = self.user_history_text or self.latest_user_text or self.original_prompt
        self.current_prompt = context_text

    def authoring_context(self):
        from .grounding_coordinator import AuthoringContext

        return AuthoringContext(
            original_prompt=self.original_prompt or self.current_prompt or "",
            latest_user_text=self.latest_user_text or "",
            user_history_text=(
                self.user_history_text
                or self.latest_user_text
                or self.original_prompt
                or self.current_prompt
                or ""
            ),
            workspace_name=self.workspace_name,
            workspace_guid=self.workspace_guid,
            intent=(
                self.current_intent.to_dict()
                if self.current_intent.is_specified()
                else None
            ),
            pending_approval_kind=self.pending_approval_kind,
        )

    def functions(self) -> dict[str, ToolFn]:
        return {
            "ask_user": self.ask_user,
            "declare_intent": self.declare_intent,
            "present_source_protocol_plan": self.present_source_protocol_plan,
            "declare_protocol_workflow": self.declare_protocol_workflow,
            "present_object_draft": self.present_object_draft,
            "present_functional_group_plan": self.present_functional_group_plan,
            "lookup_api": self.lookup_api,
            "lookup_workspace": self.lookup_workspace,
            "list_valid_positions": self.list_valid_positions,
            "search_labware": self.search_labware,
            "get_labware": self.get_labware,
            "lookup_compatibility": self.lookup_compatibility,
            "lookup_liquid_class": self.lookup_liquid_class,
            "lookup_rules": self.lookup_rules,
            "plan_protocol_resources": self.plan_protocol_resources,
            "suggest_deck_layout": self.suggest_deck_layout,
            "ground_in_parallel": self.ground_in_parallel,
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

    def requires_source_protocol_plan(self) -> bool:
        text = self.current_prompt or self.user_history_text or self.latest_user_text or ""
        return "Attached file context:" in text

    def _has_source_document_context(self) -> bool:
        """True when the current prompt carries attached source-document text.

        Used to compute document adherence in skills/enforce mode, where the
        `present_source_protocol_plan` checkpoint never runs but the source
        document text is still present in the prompt.
        """
        return "Attached file context:" in (self.current_prompt or "")

    def present_source_protocol_plan(
        self,
        protocol_title: str,
        summary: str,
        source_files: list[dict[str, Any]] | None,
        steps: list[dict[str, Any]] | None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        plan = {
            "protocol_title": str(protocol_title or "").strip() or "Source Protocol",
            "summary": str(summary or "").strip(),
            "source_files": [dict(item) for item in (source_files or ()) if isinstance(item, dict)],
            "steps": [dict(item) for item in (steps or ()) if isinstance(item, dict)],
            "warnings": [str(item) for item in (warnings or ()) if str(item).strip()],
        }
        errors: list[dict[str, Any]] = []
        if not plan["source_files"]:
            errors.append({"field": "source_files", "message": "At least one source file must be listed."})
        if not plan["steps"]:
            errors.append({"field": "steps", "message": "At least one source-document protocol step must be listed."})
        valid = {"automated", "manual_off_deck", "unsupported"}
        for index, step in enumerate(plan["steps"]):
            description = str(step.get("description") or step.get("name") or "").strip()
            classification = str(step.get("classification") or "").strip()
            if not description:
                errors.append({
                    "field": f"steps[{index}].description",
                    "message": "Each source step needs a description.",
                })
            if classification not in valid:
                errors.append({
                    "field": f"steps[{index}].classification",
                    "received": classification,
                    "message": "classification must be one of automated, manual_off_deck, unsupported.",
                })
        if errors:
            self.source_protocol_plan = plan
            self.source_protocol_plan_approved = False
            self.object_draft_approved = False
            self.functional_group_plan_approved = False
            self.pending_approval_kind = "source_protocol"
            return {
                "ok": False,
                "category": "source_protocol_plan_invalid",
                "message": "Source protocol plan is not ready for user approval.",
                "errors": errors,
                "next_checkpoint": "Revise the source protocol plan and call present_source_protocol_plan again.",
            }
        self.source_protocol_plan = plan
        self.source_protocol_plan_approved = False
        self.object_draft_approved = False
        self.functional_group_plan_approved = False
        self.pending_approval_kind = "source_protocol"
        return {
            "ok": True,
            "status": "needs_approval",
            "approval": {
                "kind": "source_protocol",
                "title": "Approve Source Protocol Plan",
                "summary": plan["summary"],
                "payload": plan,
                "question": (
                    "Approve this extraction of the source document, or reply with "
                    "missing/incorrect steps before labware planning."
                ),
            },
        }

    def declare_protocol_workflow(
        self,
        protocol_name: str,
        summary: str,
        variables: list[dict[str, Any]] | None,
        labware: list[dict[str, Any]] | None,
        groups: list[dict[str, Any] | str] | None,
    ) -> dict[str, Any]:
        group_plans: tuple[FunctionalGroupPlan, ...] = tuple(
            (
                FunctionalGroupPlan(name=group.strip())
                if isinstance(group, str)
                else FunctionalGroupPlan(
                    name=str(group.get("name") or "").strip(),
                    objective=str(group.get("objective") or "").strip(),
                    expected_steps=tuple(
                        str(item) for item in (group.get("expected_steps") or ())
                    ),
                )
            )
            for group in (groups or ())
            if isinstance(group, dict) or (isinstance(group, str) and group.strip())
        )
        group_names = [group.name for group in group_plans]
        if len(group_names) < 3:
            return {
                "ok": False,
                "category": "workflow_plan_invalid",
                "message": (
                    "declare_protocol_workflow requires `Variables`, `Labware Placement`, "
                    "and at least one functional protocol group. Add every requested "
                    "stage after the two scaffold groups before drafting."
                ),
                "received_groups": group_names,
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
            default = item.get("default", item.get("default_value"))
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

    def _enforce_catalog_for_role(
        self, role_text: str
    ) -> tuple[str | None, str | None, str | None]:
        """Enforce-only deterministic role → (catalog, python_class, layout
        role) resolution. `role_text` is the lowered "<role> <label>" of an
        object-draft labware item. Returns the exact whitelist catalog name,
        the correct fluentvibe python_class, and the grounding_defaults.layout
        role key — any of which may be ``None`` when the role is
        unrecognised (the model's own value is then kept). Catalog is
        returned only when it is in `lab_scope.labware`.
        """
        text = (role_text or "").lower()

        def has(*words: str) -> bool:
            return any(w in text for w in words)

        family: str | None = None
        layout_role: str | None = None
        is_plate = "plate" in text
        if has("magnet"):
            family, layout_role = "magnet_plate_96", "magnet_plate"
        elif has("waste", "trash") and not is_plate:
            family, layout_role = "waste_reservoir", "waste"
        elif has("tip", "tips", "diti"):
            if has("mca"):
                if "500" in text:
                    family = "mca96_tipbox_large"
                elif "200" in text:
                    family = "mca96_tipbox_medium"
                else:
                    family = "mca96_tipbox_small"
                layout_role = "mca_tips"
            else:  # fca / liha fixed-channel tips
                family = "fca_tipbox_small" if "200" in text else "fca_tipbox_large"
                layout_role = "fca_tips"
        elif not is_plate and has("ethanol", "etoh", "wash"):
            family, layout_role = "reservoir_ethanol", "reservoir_start"
        elif not is_plate and has(
            "reservoir", "trough", "reagent", "bead", "buffer", "elution buffer", "water"
        ):
            family, layout_role = "reservoir_standard", "reservoir_start"
        elif is_plate or has("sample", "source", "destination", "elution", "final", "eluate"):
            if "384" in text:
                family = "plate_384"
            else:
                family = "plate_96"
            if has("elution", "final", "dest", "output", "clean", "eluate", "target"):
                layout_role = "final_plate"
            else:
                layout_role = "sample_plate"

        if family is None:
            return None, None, None

        try:
            bundle = load_grounding_bundle(
                workspace_name=self.workspace_name,
                workspace_guid=self.workspace_guid,
            )
        except Exception:
            return None, None, layout_role
        catalog = bundle.labware_defaults.get(family)
        if not catalog or catalog not in set(self.lab_scope.labware):
            return None, None, layout_role

        python_class = _ENFORCE_FAMILY_PYCLASS.get(family)
        row = resolve_by_name(catalog)
        if row is not None and python_class is not None:
            semantic = _semantic_category(row.name, row.category)
            if not _catalog_class_compatible(python_class, row.category, row.name):
                python_class = _python_class_for(row.name, str(semantic))
        return catalog, python_class, layout_role

    def _enforce_complete_labware_payloads(
        self, labware: list[dict[str, Any]] | None
    ) -> None:
        """Enforce-only. Fill/repair each object-draft labware item's
        catalog_name, python_class and deck slot from the curated whitelist +
        grounding defaults, so the first `present_object_draft` carries a
        payload `_validate_object_draft` accepts instead of the model
        rediscovering deterministic values by trial. Only fills blanks and
        corrects values validation would reject; never overwrites a valid
        model-supplied value. Best-effort; mutates items in place.
        """
        if not self.lab_scope.enforces:
            return
        import sys as _sys

        try:
            bundle = load_grounding_bundle(
                workspace_name=self.workspace_name,
                workspace_guid=self.workspace_guid,
            )
        except Exception:
            bundle = None

        for item in labware or ():
            if not isinstance(item, dict):
                continue
            label = _object_label(item)
            role = str(item.get("role") or item.get("category") or "").strip()
            cur_catalog = _object_catalog_name(item)
            cur_class = _object_python_class(item)

            resolved_catalog, resolved_class, layout_role = self._enforce_catalog_for_role(
                f"{role} {label}"
            )

            catalog = cur_catalog
            if not catalog and resolved_catalog:
                catalog = resolved_catalog
                item["catalog_name"] = catalog
                item.pop("catalog", None)

            # python_class: fill when missing, or correct when the supplied
            # one would be rejected by _catalog_class_compatible.
            target_class = cur_class
            row = resolve_by_name(catalog) if catalog else None
            if row is not None:
                semantic = _semantic_category(row.name, row.category)
                needs_class = not cur_class or not _catalog_class_compatible(
                    cur_class, row.category, row.name
                )
                if needs_class:
                    target_class = resolved_class or _python_class_for(
                        row.name, str(semantic)
                    )
                    if target_class:
                        item["python_class"] = target_class
            elif not cur_class and resolved_class:
                target_class = resolved_class
                item["python_class"] = target_class

            # location/site: snap to the grounding default for this role only
            # when the model left it blank (suggest_deck_layout may already
            # have set it above).
            if not item.get("location") and bundle is not None and layout_role:
                cfg = bundle.layout_defaults.get(layout_role)
                if isinstance(cfg, dict) and cfg.get("location"):
                    item["location"] = str(cfg["location"])
                    if not item.get("site") and not item.get("position"):
                        site = cfg.get("site")
                        item["site"] = site
                        item["position"] = site

            if catalog != cur_catalog or target_class != cur_class:
                print(
                    f"[autoground] payload: {label or '?'} "
                    f"python_class={item.get('python_class')} "
                    f"catalog={item.get('catalog_name')} "
                    f"@ {item.get('location')}/{item.get('site')}",
                    file=_sys.stderr,
                    flush=True,
                )

    def _enforce_object_draft_volumes(self, variables: list[dict[str, Any]] | None) -> None:
        """Enforce-only. Derive dependent bead-cleanup volumes from their
        primitive inputs so the two assay-logic errors the simulator cannot
        catch (supernatant aspirate, eluate transfer) are fixed at authoring
        time instead of being left to the model's guess:

            SUPERNATANT_ASPIRATE_UL = SAMPLE_VOLUME_UL + BEAD_VOLUME_UL
                                      - RETAIN_VOLUME_UL
            TRANSFER_VOLUME_UL / ELUATE_*_UL = ELUTION_VOLUME_UL
                                      - RETAIN_VOLUME_UL

        Conservative: only overrides when every required primitive is present
        and numeric; otherwise keeps the model's value. Best-effort, never
        raises, mutates items in place.
        """
        if not self.lab_scope.enforces:
            return
        import sys as _sys

        try:
            items = [v for v in (variables or ()) if isinstance(v, dict)]

            def _vname(v: dict[str, Any]) -> str:
                return str(
                    v.get("name") or v.get("variable") or v.get("variable_name") or ""
                ).strip().upper()

            def _vnum(v: dict[str, Any]) -> float | None:
                for key in ("default_value", "default", "value"):
                    if key in v and v[key] is not None:
                        try:
                            return float(v[key])
                        except (TypeError, ValueError):
                            return None
                return None

            by_name = {_vname(v): v for v in items if _vname(v)}

            def num(name: str) -> float | None:
                v = by_name.get(name)
                return _vnum(v) if v is not None else None

            # The lab cheatsheet's canonical AMPure workflow names the
            # input/target quantity TARGET_VOLUME_UL; accept it as a synonym
            # for SAMPLE_VOLUME_UL (same physical quantity by the lab's own
            # naming — not an inferred value).
            sample = num("SAMPLE_VOLUME_UL")
            if sample is None:
                sample = num("TARGET_VOLUME_UL")
            beads = num("BEAD_VOLUME_UL")
            retain = num("RETAIN_VOLUME_UL")
            elution = num("ELUTION_VOLUME_UL")

            def _apply(item: dict[str, Any], derived: float) -> None:
                old = _vnum(item)
                wrote = False
                for key in ("default_value", "default", "value"):
                    if key in item:
                        item[key] = derived
                        wrote = True
                if not wrote:
                    item["default_value"] = derived
                if "sim_value" in item:
                    item["sim_value"] = derived
                print(
                    f"[autoground] volume: {_vname(item)} {old}→{derived}",
                    file=_sys.stderr,
                    flush=True,
                )

            for item in items:
                name = _vname(item)
                if name == "SUPERNATANT_ASPIRATE_UL":
                    if None not in (sample, beads, retain):
                        _apply(item, float(sample) + float(beads) - float(retain))
                    else:
                        print(
                            f"[autoground] volume: {name} kept (inputs incomplete)",
                            file=_sys.stderr,
                            flush=True,
                        )
                elif name == "TRANSFER_VOLUME_UL" or (
                    name.startswith("ELUATE") and name.endswith("_UL")
                ):
                    if None not in (elution, retain):
                        _apply(item, float(elution) - float(retain))
                    else:
                        print(
                            f"[autoground] volume: {name} kept (inputs incomplete)",
                            file=_sys.stderr,
                            flush=True,
                        )
        except Exception as exc:  # volume derivation is best-effort only
            print(f"[autoground] volume: skipped: {exc!r}", file=_sys.stderr, flush=True)

    def _autoground_object_draft_enforce(self, labware: list[dict[str, Any]] | None) -> None:
        """Bet 1 — enforce only. The object-draft gate requires prior
        successful `lookup_workspace` and (for >1 deck object)
        `suggest_deck_layout` calls. In enforce mode both are fully
        deterministic (fixed workspace + curated labware), yet the model
        rediscovers them by trial-and-error — ~16 failed `present_object_draft`
        turns in the trace. Satisfy them server-side so the first present
        succeeds. No-op unless `lab_scope.enforces`, so off/cheatsheet/
        baseline are byte-identical. Never raises into the caller.
        """
        if not self.lab_scope.enforces:
            return
        import sys as _sys

        try:
            if not (
                self._has_successful_call("lookup_workspace")
                or self._has_successful_call("list_valid_positions")
            ):
                ws = self.lookup_workspace()
                if isinstance(ws, dict) and ws.get("ok"):
                    self._record_call("lookup_workspace", {}, ws, dispatch_source="autoground")
                    print("[autoground] lookup_workspace (enforce)", file=_sys.stderr, flush=True)

            items = [dict(x) for x in (labware or ()) if isinstance(x, dict)]
            if len(items) > 1 and not self._has_successful_call("suggest_deck_layout"):
                resources = [
                    {
                        "label": _object_label(it, default=f"labware[{i}]"),
                        "catalog_name": _object_catalog_name(it) or None,
                        "role": str(it.get("role") or it.get("category") or "").strip() or None,
                    }
                    for i, it in enumerate(items)
                ]
                layout = self.suggest_deck_layout(resources)
                if isinstance(layout, dict) and layout.get("ok"):
                    self._record_call(
                        "suggest_deck_layout",
                        {"resources": resources},
                        layout,
                        dispatch_source="autoground",
                    )
                    by_label = {
                        str(p.get("label")): p
                        for p in layout.get("placements", [])
                        if isinstance(p, dict)
                    }
                    for src in labware or ():
                        if not isinstance(src, dict):
                            continue
                        placement = by_label.get(_object_label(src))
                        if placement is None:
                            continue
                        src.setdefault("location", placement.get("location"))
                        if not src.get("site") and not src.get("position"):
                            src["site"] = placement.get("position")
                            src["position"] = placement.get("position")
                    print(
                        f"[autoground] suggest_deck_layout (enforce): "
                        f"{len(by_label)} placement(s)",
                        file=_sys.stderr,
                        flush=True,
                    )

            self._enforce_complete_labware_payloads(labware)
        except Exception as exc:  # autogrounding is best-effort only
            print(f"[autoground] skipped: {exc!r}", file=_sys.stderr, flush=True)

    def _autoground_reagent_roles_enforce(self, reagents: list[dict[str, Any]] | None) -> None:
        """Bet 1 — enforce only. Coerce an invalid reagent ``role`` to a valid
        one in place rather than rejecting the draft.

        The model reliably tags ethanol/buffers with ``role="wash"``, which is
        not in ``Reagent.ROLES``. Rejecting it at the object-draft gate just
        makes the model resubmit the same draft until the gate-loop exhausts
        (observed: present_object_draft looping every turn). The special roles
        the simulator cares about (``bead_carrier``/``analyte``/``eluent``) are
        the ones the model gets right; anything else is ordinary liquid, so
        coercing an unknown role to ``plain`` is safe and unambiguous. No-op
        unless ``lab_scope.enforces``; never raises into the caller.
        """
        if not self.lab_scope.enforces:
            return
        import sys as _sys

        try:
            for item in reagents or ():
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip()
                if not role or role in _REAGENT_ROLES:
                    continue
                item["role"] = "plain"
                print(
                    f"[autoground] reagent role: {role!r} -> 'plain' "
                    f"({item.get('name')!r})",
                    file=_sys.stderr,
                    flush=True,
                )
        except Exception as exc:  # autogrounding is best-effort only
            print(f"[autoground] reagent role skipped: {exc!r}", file=_sys.stderr, flush=True)

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
        self._autoground_object_draft_enforce(labware)
        self._autoground_reagent_roles_enforce(reagents)
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
        self._enforce_object_draft_volumes(object_draft["variables"])
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
        profile_classes = dict(getattr(self.lab_scope, "labware_classes", {}) or {})

        for index, item in enumerate(object_draft["labware"]):
            label = _object_label(item, default=f"labware[{index}]")
            python_class = _object_python_class(item)
            catalog_name = _object_catalog_name(item)
            role = str(item.get("role") or item.get("category") or "").strip().lower()

            if not python_class:
                errors.append({
                    "field": f"labware[{index}].python_class",
                    "label": label,
                    "message": "Each labware object must include a fluentvibe python_class.",
                    "fix": "Set python_class to an exported class returned by lookup_api/get_labware.",
                    "valid_classes": sorted(_CATALOG_BACKED_CLASSES),
                })
                continue
            if python_class not in _EXPORTED_FLUENTVIBE_CLASSES:
                errors.append({
                    "field": f"labware[{index}].python_class",
                    "label": label,
                    "received": python_class,
                    "message": f"{python_class!r} is not exported by fluentvibe.",
                    "fix": "Use an exported fluentvibe class; for FCA tips use FCA1000Box/FCA200Box/FCA50Box.",
                    "valid_classes": sorted(_EXPORTED_FLUENTVIBE_CLASSES),
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
                expected_profile_class = profile_classes.get(catalog_name)
                if expected_profile_class and python_class != expected_profile_class:
                    errors.append({
                        "field": f"labware[{index}].python_class",
                        "label": label,
                        "catalog_name": catalog_name,
                        "received": python_class,
                        "expected_python_class": expected_profile_class,
                        "message": "Profile labware class contract violation.",
                        "fix": (
                            f"Use {expected_profile_class} for profile catalog "
                            f"{catalog_name!r}; do not substitute {python_class!r}."
                        ),
                    })
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

        # The object-draft reagent ``role`` is display metadata and is not
        # necessarily the Python ``Reagent.role`` enum (off/cheatsheet have
        # always passed free-text role labels like "source liquid" through
        # untouched). Only validate it under enforce, where role vocabulary is
        # curated; gating here keeps off/cheatsheet byte-identical to baseline.
        for index, item in enumerate(object_draft["reagents"] if self.lab_scope.enforces else []):
            role = str(item.get("role") or "").strip()
            if role and role not in _REAGENT_ROLES:
                errors.append({
                    "field": f"reagents[{index}].role",
                    "name": item.get("name"),
                    "received": role,
                    "message": f"Reagent role {role!r} is not one of {_REAGENT_ROLES}.",
                    "fix": "Use 'plain' for buffers/ethanol/sample, 'bead_carrier' "
                           "for bead suspensions, 'analyte' for the captured "
                           "species, 'eluent' for release buffer.",
                    "valid_roles": list(_REAGENT_ROLES),
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
        # enforce mode: the curated whitelist replaces get_labware as the
        # grounding source (the search tools are removed from the model).
        if self.lab_scope.enforces:
            names |= set(self.lab_scope.labware)
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
        if self.lab_scope.enforces:
            names |= set(self.lab_scope.liquid_classes)
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
        groups: list[dict[str, Any] | str] | None,
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
        if kind == "source_protocol" and self.pending_approval_kind == "source_protocol":
            self.source_protocol_plan_approved = True
            self.pending_approval_kind = None
            return
        if kind == "objects" and self.pending_approval_kind == "objects":
            self.object_draft_approved = True
            self.pending_approval_kind = None
            return
        if kind == "functional_groups" and self.pending_approval_kind == "functional_groups":
            self.functional_group_plan_approved = True
            self.pending_approval_kind = None
            return

    def reopen_pending(self, kind: str) -> None:
        if kind == "source_protocol":
            self.source_protocol_plan_approved = False
            self.object_draft_approved = False
            self.functional_group_plan_approved = False
            self.pending_approval_kind = "source_protocol"
        elif kind == "objects":
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
        # Narrowed-scope Lever B: when enforcing, restrict matches to the
        # curated whitelist. No-op (byte-identical) unless mode == "enforce".
        if self.lab_scope.enforces:
            scoped = [row for row in rows if self.lab_scope.allows_labware(row.name)]
            return {
                "ok": True,
                "lab_scope": "enforce",
                "matches": [_catalog_entry(row) for row in scoped[:limit]],
                "note": (
                    "Results restricted to this lab's curated labware. If "
                    "nothing here fits the request, say so explicitly rather "
                    "than substituting an off-list component."
                ),
            }
        return {"ok": True, "matches": [_catalog_entry(row) for row in rows[:limit]]}

    def get_labware(self, name: str) -> dict[str, Any]:
        row = resolve_by_name(name)
        if row is None:
            return {"ok": False, "category": FailureCategory.MISSING_CATALOG_ITEM.value, "message": f"Catalog labware {name!r} is not installed."}
        metadata = _catalog_entry(row)
        metadata["python_class"] = _python_class_for(row.name, str(metadata["category"]))
        if row.footprint:
            metadata["footprint"] = row.footprint
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
        try:
            metadata["compatibility"] = _compact_compatibility(name)
        except CatalogSchemaOutOfDate as exc:
            metadata["compatibility_warning"] = str(exc)
        except Exception:
            # Compatibility is best-effort enrichment; never fail the
            # whole get_labware response over a join error.
            pass
        return {"ok": True, "labware": metadata}

    def lookup_compatibility(
        self,
        name: str,
        kind: str = "all",
    ) -> dict[str, Any]:
        """Compatibility queries against the catalog index.

        ``kind`` ∈ {"sites", "grip_modes", "stacks", "workspaces", "all"}.
        Read-only, PARALLEL_SAFE — answers are derived from the indexed
        compatibility tables and don't touch any registry state.
        """
        valid_kinds = {"sites", "grip_modes", "stacks", "workspaces", "all"}
        kind_norm = (kind or "all").lower().strip()
        if kind_norm not in valid_kinds:
            return {
                "ok": False,
                "category": "invalid_argument",
                "message": (
                    f"kind={kind!r} is not one of {sorted(valid_kinds)}"
                ),
            }

        row = resolve_by_name(name)
        if row is None:
            return {
                "ok": False,
                "category": FailureCategory.MISSING_CATALOG_ITEM.value,
                "message": f"Catalog labware {name!r} is not installed.",
            }

        try:
            payload = _full_compatibility(name, kind_norm)
        except CatalogSchemaOutOfDate as exc:
            return {
                "ok": False,
                "category": "catalog_schema_out_of_date",
                "message": str(exc),
            }
        return {"ok": True, "name": row.name, **payload}

    def lookup_liquid_class(self, name: str, device_type: str | None = None) -> dict[str, Any]:
        if not name.strip():
            name = "Water Free Single"
        # Narrowed-scope Lever B: reject off-whitelist liquid classes before
        # resolving. No-op unless mode == "enforce".
        if self.lab_scope.enforces and not self.lab_scope.allows_liquid_class(name):
            return {
                "ok": False,
                "lab_scope": "enforce",
                "category": FailureCategory.LIQUID_CLASS_RESOLUTION_FAILURE.value,
                "message": (
                    f"Liquid class {name!r} is outside this lab's curated "
                    f"scope. Allowed: {sorted(self.lab_scope.liquid_classes)}."
                ),
                "default": "Water Free Single",
            }
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

    def _log_ground_in_parallel(self, msg: str) -> None:
        import sys
        print(f"[ground_in_parallel] {msg}", file=sys.stderr, flush=True)

    def ground_in_parallel(
        self,
        categories: list[str],
        extra_prompt_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fan out grounding across N category subagents in parallel.

        Use AFTER clarifications are resolved and you know which domains
        the protocol needs grounded. Each named category spawns a focused
        LM subagent with a restricted toolset that grounds its domain;
        results are cached so subsequent search_labware / get_labware /
        lookup_workspace / lookup_rules / lookup_liquid_class calls return
        instantly.

        Args:
            categories: list of category names. Available:
                workspace, rules, plates, pcr_plates, deep_well_plates,
                troughs, mca_tips, fca_tips, magnets, tube_racks, waste,
                adapters, filter_plates, liquid_classes.
            extra_prompt_terms: optional extra context to append to the
                user prompt seen by each subagent (e.g. "20 uL transfer").

        Returns:
            Summary dict with `ok`, `categories_run`, `cache_writes`, and
            per-category results.
        """
        self._log_ground_in_parallel(f"called with categories={list(categories or [])!r}")

        if self._subagent_client is None:
            self._log_ground_in_parallel("ABORT — no LM client wired (subagent_unavailable)")
            return {
                "ok": False,
                "category": "subagent_unavailable",
                "message": (
                    "ground_in_parallel requires a configured LM client. "
                    "This usually means the tool was invoked outside an "
                    "authoring session (e.g. in tests without service wiring)."
                ),
            }

        from .category_agents import DEFAULT_CATEGORIES
        from .grounding_coordinator import AuthoringContext, GroundingCoordinator

        requested = {str(c).strip() for c in (categories or []) if str(c).strip()}
        if not requested:
            self._log_ground_in_parallel("ABORT — empty `categories` list")
            return {
                "ok": False,
                "category": "bad_tool_arguments",
                "message": "ground_in_parallel requires a non-empty `categories` list.",
            }

        by_name = {c.name: c for c in DEFAULT_CATEGORIES}
        unknown = sorted(requested - by_name.keys())
        selected = [by_name[n] for n in requested if n in by_name]
        if unknown:
            self._log_ground_in_parallel(
                f"unknown categories ignored: {unknown!r}"
            )
        if not selected:
            self._log_ground_in_parallel(
                f"ABORT — no recognised categories. Valid: {sorted(by_name.keys())!r}"
            )
            return {
                "ok": False,
                "category": "bad_tool_arguments",
                "message": (
                    "ground_in_parallel: none of the requested category names "
                    f"are recognised. Got {sorted(requested)!r}; valid: "
                    f"{sorted(by_name.keys())!r}."
                ),
            }
        self._log_ground_in_parallel(
            f"firing {len(selected)} subagent(s) in parallel: "
            f"{[c.name for c in selected]!r}"
        )

        context = self.authoring_context()
        if extra_prompt_terms:
            extras = " ".join(str(t).strip() for t in extra_prompt_terms if str(t).strip())
            if extras:
                context = AuthoringContext(
                    original_prompt=context.original_prompt,
                    latest_user_text=context.latest_user_text,
                    user_history_text=f"{context.user_history_text}\n\nAdditional context: {extras}".strip(),
                    workspace_name=context.workspace_name,
                    workspace_guid=context.workspace_guid,
                    intent=context.intent,
                    pending_approval_kind=context.pending_approval_kind,
                )

        result = GroundingCoordinator(
            registry=self,
            client=self._subagent_client,
            pool_size=self._subagent_pool_size,
            timeout_s=self._subagent_timeout_s,
        ).run(
            context,
            categories=[c.name for c in selected],
            force=True,
        )

        ok_count = sum(1 for item in (result.get("per_category") or []) if item.get("ok"))
        total_writes = int(result.get("cache_writes") or 0)
        self._log_ground_in_parallel(
            f"done: {ok_count}/{len(selected)} agents ok, "
            f"{total_writes} cache writes"
        )
        result = dict(result)
        result.setdefault(
            "next_checkpoint",
            (
                "Grounded categories cached. Call search_labware / get_labware / "
                "lookup_workspace / lookup_rules / lookup_liquid_class as needed; "
                "their results will return instantly from cache."
            ),
        )
        return result

    def plan_protocol_resources(self, phases: list[dict[str, Any]]) -> dict[str, Any]:
        return plan_protocol_resources(phases)

    def _is_staged_subdraft(self, source: str) -> bool:
        """True if `source` looks like a staged sub-draft that hasn't reached the terminal stage.

        The staged authoring loop builds a draft up one functional group at a
        time (see _workflow_next_group_message). Early checkpoints have only
        Variables + Labware Placement and lack pipetting by design, so the
        `_check_prompt_intent` gate (which requires aspirate+dispense whenever
        the prompt mentions transfer) would reject them. Defer that gate until
        the source carries every approved functional group; the terminal
        `compile_and_simulate` path still enforces it via validator.validate.
        """
        plan = self.workflow_plan
        if plan is None or not plan.groups:
            return False
        group_calls = re.findall(r"wt\.group\(\s*['\"]([^'\"]+)['\"]\s*\)", source)
        expected_terminal_groups = max(0, len(plan.groups) - 1)
        return len(group_calls) < expected_terminal_groups

    def simulate_python_draft(self, source: str, strict: bool = True) -> dict[str, Any]:
        volume_rewrites: list[dict[str, Any]] = []
        class_rewrites: list[dict[str, Any]] = []
        source, class_rewrites = _autoground_labware_classes(
            source, dict(getattr(self.lab_scope, "labware_classes", {}) or {})
        )
        for rewrite in class_rewrites:
            print(
                f"[autoground] labware class: {rewrite['from']} -> {rewrite['to']} "
                f"for catalog {rewrite['catalog']!r} (line {rewrite['line']})"
            )
        if self.lab_scope.enforces and self.object_draft_approved and self.object_draft:
            source, volume_rewrites = _autoground_pipetting_volume_literals(source, self.object_draft)
            for rewrite in volume_rewrites:
                print(
                    f"[autoground] pipetting volume: {rewrite['value']!r} -> "
                    f"{rewrite['variable']} (line {rewrite['line']})"
                )
        with tempfile.TemporaryDirectory(prefix="fluentvibe-authoring-sim-") as tmp:
            path = Path(tmp) / "draft.py"
            copy_workspace_modules(self.workspace_modules, Path(tmp))
            path.write_text(source, encoding="utf-8")
            contract_error = self.validator._check_contract(source)
            if contract_error is None and not self._is_staged_subdraft(source):
                contract_error = self.validator._check_prompt_intent(source, self.current_prompt)
            if contract_error is None:
                contract_error = _check_source_against_profile_labware_classes(
                    source,
                    dict(getattr(self.lab_scope, "labware_classes", {}) or {}),
                )
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
        result: dict[str, Any] = {
            "ok": True,
            "stage": "strict_simulation",
            "message": "Draft built and strict simulation passed.",
            "state_summary": _simulation_state_summary(report),
        }
        if volume_rewrites or class_rewrites:
            # Surface the autogrounded source so the graph carries the
            # corrected version forward into compile_and_simulate, not the
            # model's original draft.
            result["source"] = source
            autoground: dict[str, Any] = {}
            if volume_rewrites:
                autoground["pipetting_volumes"] = volume_rewrites
            if class_rewrites:
                autoground["labware_classes"] = class_rewrites
            result["autoground"] = autoground
        return result

    def compile_and_simulate(self, source: str) -> dict[str, Any]:
        self._compile_attempt += 1
        profile_classes = dict(getattr(self.lab_scope, "labware_classes", {}) or {})
        source, class_rewrites = _autoground_labware_classes(source, profile_classes)
        for rewrite in class_rewrites:
            print(
                f"[autoground] labware class: {rewrite['from']} -> {rewrite['to']} "
                f"for catalog {rewrite['catalog']!r} (line {rewrite['line']})"
            )
        profile_contract_error = _check_source_against_profile_labware_classes(
            source,
            profile_classes,
        )
        if profile_contract_error:
            return {
                "success": False,
                "ok": False,
                "stage": "contract",
                "category": FailureCategory.PYTHON_BUILD_FAILURE.value,
                "python_build_ok": False,
                "compile_ok": False,
                "strict_simulation_ok": False,
                "failure_category": FailureCategory.PYTHON_BUILD_FAILURE.value,
                "message": profile_contract_error,
                "failure_message": profile_contract_error,
            }
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
        if class_rewrites:
            # Surface the corrected source/import so callers carry the
            # profile-pinned classes forward, not the model's generic draft.
            payload["source"] = source
            payload.setdefault("autoground", {})["labware_classes"] = class_rewrites
        # Compute source-document adherence whenever a source document is in
        # play — either via an approved plan (staged flow) or an attached file
        # context (skills/enforce flow, where no plan checkpoint runs). Without
        # this, profile-mode runs never compute adherence and silently accept
        # protocols that drop automatable source stages.
        if self.source_protocol_plan is not None or self._has_source_document_context():
            from .document_adherence import document_adherence_report

            payload["document_adherence"] = document_adherence_report(
                source_text=self.current_prompt or "",
                protocol_source=source,
                source_name="attached file context",
                approved_plan=self.source_protocol_plan,
            )
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


def _format_compat_site(site) -> dict[str, Any]:
    return {
        "workspace": site.workspace_name,
        "carrier": site.component_name,
        "site_index": site.site_index,
        "footprint": site.footprint,
        "grip_modes": list(site.grip_modes),
        "base_location": site.base_location,
    }


def _compact_compatibility(name: str) -> dict[str, Any]:
    """Top-N compatibility view used by `get_labware`. Best-effort."""
    sites = find_sites_for(name)
    grip_modes = find_grip_modes(name)
    stacks = find_legal_stacks(name)
    workspaces = find_workspaces_using(name)
    return {
        "fits_on_sites": [_format_compat_site(s) for s in sites[:8]],
        "grip_modes": grip_modes,
        "stacks_above": [r.name for r in stacks.get("above", [])[:4]],
        "stacks_below": [r.name for r in stacks.get("below", [])[:4]],
        "used_in_workspaces": [w.name for w in workspaces[:4]],
    }


def _full_compatibility(name: str, kind: str) -> dict[str, Any]:
    """Untruncated compatibility view used by `lookup_compatibility`."""
    out: dict[str, Any] = {}
    if kind in ("sites", "all"):
        out["sites"] = [_format_compat_site(s) for s in find_sites_for(name)]
    if kind in ("grip_modes", "all"):
        out["grip_modes"] = find_grip_modes(name)
    if kind in ("stacks", "all"):
        stacks = find_legal_stacks(name)
        out["stacks"] = {
            "above": [
                {"name": r.name, "category": r.category, "footprint": r.footprint}
                for r in stacks.get("above", [])
            ],
            "below": [
                {"name": r.name, "category": r.category, "footprint": r.footprint}
                for r in stacks.get("below", [])
            ],
        }
    if kind in ("workspaces", "all"):
        out["workspaces"] = [w.name for w in find_workspaces_using(name)]
    return out


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


def _autoground_pipetting_volume_literals(
    source: str, object_draft: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite hardcoded pipetting volume literals to their approved variable.

    The volume contract (`_check_source_against_approved_objects`) rejects a
    draft that passes a numeric literal where an approved phase volume must
    flow through its declared variable, but it never repairs it — the model
    can resubmit the same literal until the no-progress guard fires. In
    enforce mode we instead rewrite the literal to the variable, the same way
    `_autoground_object_draft_enforce` corrects the object draft.

    Only safe rewrites are applied: the literal's value must map to exactly
    one approved variable (no ambiguous bind), and that variable must be
    declared in the draft (no NameError). Anything ambiguous or undeclared is
    left for the contract check to reject. Replacement is surgical span
    editing so the model's formatting and comments survive.
    """
    resource_plan = object_draft.get("_resource_plan") or {}
    value_to_vars: dict[float, set[str]] = {}
    for required in resource_plan.get("required_variables", []):
        if not isinstance(required, dict) or required.get("kind") not in {"volume", "split_volume"}:
            continue
        variable = str(required.get("variable") or "").strip()
        value = required.get("value")
        if not variable or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        value_to_vars.setdefault(float(value), set()).add(variable)
    unique = {value: next(iter(vars_)) for value, vars_ in value_to_vars.items() if len(vars_) == 1}
    if not unique:
        return source, []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []

    declared = set(_extract_numeric_variable_defaults(tree))
    pipetting = {"aspirate", "dispense", "mix", "empty_tips"}
    replacements: list[tuple[int, int, int, str, float]] = []  # lineno, col, end_col, var, value
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
        if volume_node is None or isinstance(volume_node, ast.Name):
            continue
        literal = _literal_arg(volume_node)
        if not isinstance(literal, (int, float)) or isinstance(literal, bool):
            continue
        variable = unique.get(float(literal))
        if not variable or variable not in declared:
            continue
        if volume_node.lineno != volume_node.end_lineno:
            continue
        replacements.append(
            (volume_node.lineno, volume_node.col_offset, volume_node.end_col_offset, variable, float(literal))
        )
    if not replacements:
        return source, []

    lines = source.splitlines(keepends=True)
    rewrites: list[dict[str, Any]] = []
    # Apply bottom-up so earlier edits don't shift later spans.
    for lineno, col, end_col, variable, value in sorted(replacements, reverse=True):
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:col] + variable + line[end_col:]
        rewrites.append({"variable": variable, "value": value, "line": lineno})
    return "".join(lines), list(reversed(rewrites))


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
        # Accept ANY declared liquid-class variable, not only the specific
        # name from the object draft. The model legitimately picks semantic
        # names (LIQUID_CLASS_BEADS/ETHANOL/ELUTION) rather than echoing the
        # one approved name; the real contract is "pass a variable, don't
        # hardcode the class string literal".
        any_variable_used = any(kind == "name" for kind, _ in liquid_class_args)
        for name, variable in liquid_variables.items():
            literal_used = any(kind == "literal" and value == name for kind, value in liquid_class_args)
            # Defer the "must be used through variable" half until the
            # staged source actually has pipetting calls — the group that
            # uses the variable is not authored yet on the first staged
            # draft (Variables + Labware Placement only). Hardcoded literals
            # in a real pipetting call are still rejected.
            if literal_used or (liquid_class_args and not any_variable_used):
                return (
                    f"Approved liquid class {name!r} must be passed via a "
                    f"declared liquid-class variable, not hardcoded as a "
                    f"string literal in pipetting calls."
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
            # Defer the "must be used through variable" half until pipetting
            # calls exist (see liquid-class note above). The first staged
            # draft has no aspirate/dispense yet, so demanding variable use
            # there is unsatisfiable.
            if literal_used or (volume_args and not variable_used):
                return (
                    f"Approved phase volume {value!r} must be used through variable "
                    f"{variable!r}, not hardcoded in pipetting calls."
                )

    sources_plan = [s for s in (resource_plan.get("sources") or []) if isinstance(s, dict)]
    if sources_plan:
        var_to_label = _extract_place_var_to_label(tree)
        label_to_var = {label: var for var, label in var_to_label.items()}
        aspirate_targets = _extract_aspirate_target_vars(tree)
        fills = _extract_fill_all_calls(tree)
        variable_defaults = _extract_numeric_variable_defaults(tree)
        for source in sources_plan:
            label = str(source.get("source_label") or "").strip()
            if not label:
                continue
            python_class = (placements.get(label) or {}).get("python_class", "")
            if python_class not in _SINGLE_WELL_CONTAINERS:
                continue
            required = _float_arg(source.get("required_volume_ul"), default=0.0)
            if required <= 0:
                continue
            python_var = label_to_var.get(label)
            if not python_var or python_var not in aspirate_targets:
                continue
            recommended = _float_arg(source.get("recommended_fill_volume_ul"), default=required)
            covered = False
            for kind, value in fills.get(python_var, []):
                resolved = value if kind == "literal" else variable_defaults.get(value)
                if resolved is not None and float(resolved) >= required:
                    covered = True
                    break
            if not covered:
                return (
                    f"Source {label!r} is aspirated by the draft but no sufficient "
                    f"{python_var}.fill_all(...) call covers it: "
                    f"requires {required:.1f} uL across all phases. "
                    f"Add `{python_var}.fill_all(<reagent>, {recommended:.1f})` "
                    f"(includes dead-volume buffer) before the first aspirate."
                )
    return None


def _check_source_against_profile_labware_classes(
    source: str,
    profile_classes: dict[str, str],
) -> str | None:
    if not profile_classes:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "place":
            continue
        if not node.args:
            continue
        labware_call = node.args[0]
        if not isinstance(labware_call, ast.Call):
            continue
        python_class = _call_name(labware_call.func)
        if not python_class:
            continue
        catalog_name = _catalog_kwarg_value(labware_call)
        if not catalog_name:
            continue
        expected = profile_classes.get(catalog_name)
        if expected and python_class != expected:
            return (
                "Profile labware class contract violation: "
                f"catalog {catalog_name!r} must use {expected}, got {python_class} "
                f"on line {getattr(labware_call, 'lineno', '?')}."
            )
    return None


def _labware_class_swap_is_safe(used: str, required: str) -> bool:
    """True when swapping ``used`` -> ``required`` is behaviour-preserving.

    The profile-pinned classes (FCA1000Box, Trough25mL, MCA100Box, ...) are
    drop-in subclasses of the generic constructors the model reaches for
    (TipBox, Trough, ...) with identical ``__init__`` signatures — they only
    pin geometry/capacity. A swap is safe precisely when the two classes are in
    a subclass relationship (either direction). When the model used a wholly
    unrelated class (e.g. ``Plate96`` for a trough catalog) the classes are not
    related, so we do not silently rewrite and the contract check still fires.
    """
    try:
        import fluentvibe as _fv

        used_cls = getattr(_fv, used, None)
        required_cls = getattr(_fv, required, None)
    except Exception:
        return False
    if not isinstance(used_cls, type) or not isinstance(required_cls, type):
        return False
    return issubclass(required_cls, used_cls) or issubclass(used_cls, required_cls)


def _autoground_labware_classes(
    source: str, profile_classes: dict[str, str]
) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite a generic labware class to the profile's required subclass.

    The profile labware-class contract (`_check_source_against_profile_labware_classes`)
    rejects ``wt.place(TipBox(..., catalog="FCA, 1000ul SBS"), ...)`` because the
    profile pins that catalog to ``FCA1000Box``. The required class is a drop-in
    subclass with an identical constructor, so the mismatch is a mechanical
    rename — but as a hard PYTHON_BUILD_FAILURE it pushes the model to drop the
    labware (and its protocol steps) entirely rather than swap the name. Instead
    we rewrite the constructor (and import) to the required class, the same way
    `_autoground_pipetting_volume_literals` repairs volume literals.

    Only safe swaps are applied (see `_labware_class_swap_is_safe`); an
    unrelated class is left for the contract check to reject. Replacement is
    surgical span editing so the model's formatting and comments survive.
    """
    if not profile_classes:
        return source, []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []

    # (lineno, col, end_col, new_name) for each constructor identifier to swap.
    replacements: list[tuple[int, int, int, str]] = []
    introduced: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "place":
            continue
        if not node.args or not isinstance(node.args[0], ast.Call):
            continue
        labware_call = node.args[0]
        used = _call_name(labware_call.func)
        catalog_name = _catalog_kwarg_value(labware_call)
        if not used or not catalog_name:
            continue
        required = profile_classes.get(catalog_name)
        if not required or required == used:
            continue
        if not _labware_class_swap_is_safe(used, required):
            continue
        func = labware_call.func
        if isinstance(func, ast.Name):
            target = func
        elif isinstance(func, ast.Attribute):
            # Rewrite the trailing attribute identifier (e.g. fv.TipBox).
            target = func  # span handled below via end_col of the attribute
        else:
            continue
        if target.lineno != target.end_lineno:
            continue
        # For an attribute the identifier is the last `.attr`; recompute its
        # start as end_col_offset - len(attr).
        if isinstance(func, ast.Attribute):
            start_col = func.end_col_offset - len(func.attr)
        else:
            start_col = target.col_offset
        replacements.append((target.lineno, start_col, target.end_col_offset, required))
        introduced.append({"catalog": catalog_name, "from": used, "to": required, "line": target.lineno})

    if not replacements:
        return source, []

    lines = source.splitlines(keepends=True)
    # Apply class-name swaps bottom-up so earlier edits don't shift later spans.
    for lineno, col, end_col, new_name in sorted(replacements, reverse=True):
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:col] + new_name + line[end_col:]
    rewritten = "".join(lines)
    rewritten = _ensure_fluentvibe_imports(rewritten, [item["to"] for item in introduced])
    return rewritten, introduced


def _ensure_fluentvibe_imports(source: str, names: list[str]) -> str:
    """Add ``names`` to the ``from fluentvibe import ...`` statement if absent."""
    wanted = list(dict.fromkeys(names))
    if not wanted:
        return source
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    import_node: ast.ImportFrom | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fluentvibe":
            import_node = node
            break
    if import_node is None:
        missing = [n for n in wanted]
        if not missing:
            return source
        return f"from fluentvibe import {', '.join(missing)}\n" + source
    already = {alias.name for alias in import_node.names}
    missing = [n for n in wanted if n not in already]
    if not missing:
        return source
    lines = source.splitlines(keepends=True)
    # Only edit single-line imports surgically; otherwise prepend a new import.
    if import_node.lineno == import_node.end_lineno:
        idx = import_node.lineno - 1
        line = lines[idx]
        newline = "\n" if line.endswith("\n") else ""
        stripped = line.rstrip("\n").rstrip()
        lines[idx] = f"{stripped}, {', '.join(missing)}{newline}"
        return "".join(lines)
    return f"from fluentvibe import {', '.join(missing)}\n" + source


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _catalog_kwarg_value(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "catalog" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return str(value).strip() if isinstance(value, str) else None
    return None


_SINGLE_WELL_CONTAINERS = frozenset({"Trough25mL", "Trough100mL", "Waste"})


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


def _extract_place_var_to_label(tree: ast.AST) -> dict[str, str]:
    var_to_label: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "place"
            and isinstance(func.value, ast.Name)
            and func.value.id == "wt"
        ):
            continue
        if not call.args or not isinstance(call.args[0], ast.Call):
            continue
        ctor = call.args[0]
        if not ctor.args:
            continue
        label = _literal_arg(ctor.args[0])
        if isinstance(label, str):
            var_to_label[target.id] = label
    return var_to_label


def _extract_aspirate_target_vars(tree: ast.AST) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "aspirate":
            continue
        if node.args and isinstance(node.args[0], ast.Name):
            targets.add(node.args[0].id)
    return targets


def _extract_fill_all_calls(tree: ast.AST) -> dict[str, list[tuple[str, Any]]]:
    fills: dict[str, list[tuple[str, Any]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "fill_all"
            and isinstance(func.value, ast.Name)
        ):
            continue
        if len(node.args) < 2:
            continue
        volume_node = node.args[1]
        if isinstance(volume_node, ast.Name):
            fills.setdefault(func.value.id, []).append(("name", volume_node.id))
        else:
            literal = _literal_arg(volume_node)
            if isinstance(literal, (int, float)):
                fills.setdefault(func.value.id, []).append(("literal", float(literal)))
    return fills


def _extract_numeric_variable_defaults(tree: ast.AST) -> dict[str, float]:
    defaults: dict[str, float] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            literal = _literal_arg(node.value)
            if isinstance(literal, (int, float)) and not isinstance(literal, bool):
                defaults[node.targets[0].id] = float(literal)
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "declare_variable"
            and isinstance(func.value, ast.Name)
            and func.value.id == "wt"
        ):
            continue
        if len(node.args) < 2:
            continue
        name_lit = _literal_arg(node.args[0])
        value_lit = _literal_arg(node.args[1])
        if isinstance(name_lit, str) and isinstance(value_lit, (int, float)) and not isinstance(value_lit, bool):
            defaults[name_lit] = float(value_lit)
    return defaults


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
        "fca": "wt.fca",
        "wt.fca": "wt.fca",
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
            "details": {"valid_exported_classes": sorted(_EXPORTED_FLUENTVIBE_CLASSES)},
            "valid_exported_classes": sorted(_EXPORTED_FLUENTVIBE_CLASSES),
            "repair_options": ["use_exported_fluentvibe_class", "call_lookup_api_for_unknown_symbol"],
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
    # Previously appended a redundant `_summary` = first 1200 chars of the
    # SAME serialized dict (plus `_truncated`), which only made large
    # results bigger and was consumed nowhere. Pass results through
    # unchanged; real economization happens at the result-construction
    # sites (lean lookup_workspace, superseded draft echoes).
    return value


def _result_size_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Compact cardinalities useful for lookup eval reports."""
    summary: dict[str, Any] = {}
    for key in (
        "matches",
        "recipes",
        "rules",
        "modules",
        "patterns",
        "positions",
        "sites",
        "grip_modes",
        "stacks",
        "workspaces",
    ):
        item = value.get(key)
        if isinstance(item, (list, tuple, dict, set)):
            summary[f"{key}_count"] = len(item)
    api = value.get("api")
    if isinstance(api, dict):
        methods = api.get("methods")
        recipes = api.get("recipes")
        if isinstance(methods, (list, tuple)):
            summary["api_methods_count"] = len(methods)
        if isinstance(recipes, (list, tuple)):
            summary["api_recipes_count"] = len(recipes)
    labware = value.get("labware")
    if isinstance(labware, dict):
        if labware.get("name"):
            summary["labware_name"] = labware.get("name")
        pipettable = labware.get("pipettable")
        if isinstance(pipettable, dict) and pipettable.get("well_count") is not None:
            summary["well_count"] = pipettable.get("well_count")
    liquid = value.get("liquid_class")
    if isinstance(liquid, dict) and liquid.get("name"):
        summary["liquid_class_name"] = liquid.get("name")
    workspace = value.get("workspace")
    if isinstance(workspace, dict) and workspace.get("name"):
        summary["workspace_name"] = workspace.get("name")
    try:
        summary["json_bytes"] = len(json.dumps(value, default=str))
    except TypeError:
        summary["json_bytes"] = 0
    return summary
