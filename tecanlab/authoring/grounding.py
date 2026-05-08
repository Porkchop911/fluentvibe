"""Installed-data-only grounding helpers for prompt authoring."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..catalog import find_components, resolve_by_name, resolve_liquid_class_by_name
from ..catalog.catalog import resolve_workspace_by_guid, resolve_workspace_by_name
from ..catalog.xcmp import load_xwsp
from .models import FailureCategory, WorkspaceBinding


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

    ws_entry = resolve_workspace_by_guid(chosen_guid) if chosen_guid else None
    if ws_entry is None and chosen_name:
        ws_entry = resolve_workspace_by_name(chosen_name)
    if ws_entry is None:
        raise GroundingError(
            FailureCategory.WORKSPACE_SLOT_INVALIDITY,
            f"Workspace binding could not be resolved locally: name={chosen_name!r}, guid={chosen_guid!r}.",
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
        labware_defaults=dict(config.get("grounding_defaults", {}).get("labware", {})),
        layout_defaults=dict(config.get("grounding_defaults", {}).get("layout", {})),
        liquid_class_name=str(config.get("liquid_class", {}).get("name") or "Water Free Single"),
    )
