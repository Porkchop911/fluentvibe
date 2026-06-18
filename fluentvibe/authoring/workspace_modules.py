"""Profile-scoped reusable Python modules for authoring.

Workspace setup can save vetted helper functions beside a profile. During
authoring those helpers are copied next to generated drafts so protocols can
import and call known-good routines instead of re-authoring complex stages.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


MANIFEST_NAME = "workspace_modules.yaml"
MODULES_DIR_NAME = "modules"


@dataclass(frozen=True)
class WorkspaceModule:
    name: str
    description: str
    source_path: Path
    import_name: str
    function: str
    triggers: tuple[str, ...] = ()
    approved: bool = False
    validation_status: str = "unknown"
    parameters: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()

    @property
    def is_usable(self) -> bool:
        return self.approved and self.validation_status == "passed" and self.source_path.exists()

    @property
    def import_line(self) -> str:
        return f"from {self.import_name} import {self.function}"


def load_workspace_modules(profile_root: Path) -> tuple[WorkspaceModule, ...]:
    manifest = profile_root / MANIFEST_NAME
    if not manifest.exists():
        return ()
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    entries = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return ()
    out: list[WorkspaceModule] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        source = str(raw.get("source") or "").strip()
        function = str(raw.get("function") or "").strip()
        if not name or not source or not function:
            continue
        import_name = str(raw.get("import_name") or Path(source).stem).strip()
        out.append(
            WorkspaceModule(
                name=name,
                description=str(raw.get("description") or "").strip(),
                source_path=(profile_root / source).resolve(),
                import_name=import_name,
                function=function,
                triggers=tuple(_strings(raw.get("triggers"))),
                approved=bool(raw.get("approved", False)),
                validation_status=str(raw.get("validation_status") or "unknown").strip(),
                parameters=tuple(_strings(raw.get("parameters"))),
                required_roles=tuple(_strings(raw.get("required_roles"))),
            )
        )
    return tuple(out)


def usable_workspace_modules(modules: tuple[WorkspaceModule, ...]) -> tuple[WorkspaceModule, ...]:
    return tuple(m for m in modules if m.is_usable)


def copy_workspace_modules(modules: tuple[WorkspaceModule, ...], dest_dir: Path) -> list[Path]:
    """Copy approved module files into ``dest_dir`` and return copied paths."""
    copied: list[Path] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    for module in usable_workspace_modules(modules):
        dest = dest_dir / module.source_path.name
        if module.source_path.resolve() != dest.resolve():
            shutil.copyfile(module.source_path, dest)
        copied.append(dest)
    return copied


def render_workspace_module_context(modules: tuple[WorkspaceModule, ...]) -> str | None:
    usable = usable_workspace_modules(modules)
    if not usable:
        return None
    lines = [
        "## Available workspace modules",
        "",
        "Prefer these approved profile-local Python helpers over re-authoring matching complex stages.",
        "Import and call the helper; do not copy its implementation into the protocol.",
        "",
    ]
    for module in usable:
        lines.extend(
            [
                f"### {module.name}",
                f"- Import: `{module.import_line}`",
                f"- Callable: `{module.function}(...)`",
                f"- Description: {module.description or 'Approved workspace helper.'}",
            ]
        )
        if module.triggers:
            lines.append(f"- Use when prompt mentions: {', '.join(f'`{t}`' for t in module.triggers)}")
        if module.required_roles:
            lines.append(f"- Required roles/labware: {', '.join(f'`{r}`' for r in module.required_roles)}")
        if module.parameters:
            lines.append(f"- Key parameters: {', '.join(f'`{p}`' for p in module.parameters)}")
        lines.append("")
    return "\n".join(lines).strip()


def module_manifest_entry(
    *,
    name: str,
    description: str,
    source: str,
    import_name: str,
    function: str,
    triggers: list[str],
    parameters: list[str],
    required_roles: list[str],
    approved: bool = True,
    validation_status: str = "passed",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "source": source,
        "import_name": import_name,
        "function": function,
        "triggers": list(triggers),
        "parameters": list(parameters),
        "required_roles": list(required_roles),
        "approved": bool(approved),
        "validation_status": validation_status,
    }


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


SPRI_MODULE_SOURCE = '''"""Approved workspace helper: SPRI / AMPure-style cleanup."""

from __future__ import annotations

from fluentvibe import Layer, Reagent, Worktable


def _ensure_analyte_marker(sample_plate, analyte: Reagent, volume_ul: float) -> float:
    """Ensure every sample well contains a small free analyte marker."""
    marker = max(0.5, min(2.0, float(volume_ul) * 0.1))
    for well in sample_plate.wells.values():
        if not any(layer.reagent is analyte or layer.reagent.name == analyte.name for layer in well.layers):
            well.layers.append(Layer(reagent=analyte, volume_ul=marker))
    return marker


def spri_cleanup(
    wt: Worktable,
    *,
    sample_plate,
    magnet,
    bead_source,
    elution_source,
    waste_labware,
    eluate_plate,
    cleanup_tips,
    eluate_tips,
    analyte: Reagent,
    beads: Reagent,
    eluent: Reagent,
    liquid_class: str,
    sample_volume_ul: float,
    bead_volume_ul: float,
    elution_volume_ul: float,
    retain_volume_ul: float = 2.0,
    mix_volume_ul: float | None = None,
) -> None:
    """Run a bead cleanup with required bind -> elute -> recover round-trip.

    Preconditions: sample_plate contains bulk sample liquid; bead_source and
    elution_source are seeded for all active wells; cleanup_tips and eluate_tips
    are separate tip boxes.
    """
    if cleanup_tips is eluate_tips:
        raise ValueError("spri_cleanup requires separate cleanup_tips and eluate_tips")
    if not getattr(analyte, "is_analyte", False):
        raise ValueError("analyte reagent must use role='analyte'")
    if not getattr(beads, "carries_beads", False):
        raise ValueError("beads reagent must use role='bead_carrier'")
    if not getattr(eluent, "is_eluent", False):
        raise ValueError("eluent reagent must use role='eluent'")

    marker_volume_ul = _ensure_analyte_marker(sample_plate, analyte, sample_volume_ul)
    bind_mix_ul = float(mix_volume_ul or min(sample_volume_ul + bead_volume_ul, 80.0))
    supernatant_ul = float(sample_volume_ul + bead_volume_ul - retain_volume_ul)
    eluate_transfer_ul = float(elution_volume_ul + marker_volume_ul - retain_volume_ul)

    head = wt.mca96
    wt.group("SPRI cleanup - bind")
    head.mount_adapter()
    head.pick_up(cleanup_tips)
    head.aspirate(bead_source, bead_volume_ul, liquid_class=liquid_class)
    head.dispense(sample_plate, bead_volume_ul, liquid_class=liquid_class)
    head.mix(sample_plate, bind_mix_ul, liquid_class=liquid_class)

    wt.group("SPRI cleanup - magnetise and remove supernatant")
    wt.gripper.move(sample_plate, onto=magnet)
    head.aspirate(sample_plate, supernatant_ul, liquid_class=liquid_class)
    head.dispense(waste_labware, supernatant_ul, liquid_class=liquid_class)
    wt.gripper.move(sample_plate, to=sample_plate.slot)

    wt.group("SPRI cleanup - elute off magnet")
    head.aspirate(elution_source, elution_volume_ul, liquid_class=liquid_class)
    head.dispense(sample_plate, elution_volume_ul, liquid_class=liquid_class)
    head.mix(sample_plate, min(elution_volume_ul, bind_mix_ul), liquid_class=liquid_class)
    head.return_tips(cleanup_tips)

    wt.group("SPRI cleanup - recover eluate")
    head.pick_up(eluate_tips)
    wt.gripper.move(sample_plate, onto=magnet)
    head.aspirate(sample_plate, eluate_transfer_ul, liquid_class=liquid_class)
    head.dispense(eluate_plate, eluate_transfer_ul, liquid_class=liquid_class)
    wt.gripper.move(sample_plate, to=sample_plate.slot)
    head.return_tips(eluate_tips)
    head.drop_adapter()
'''


SPRI_MODULE_ENTRY = module_manifest_entry(
    name="spri_cleanup",
    description="Validated SPRI/AMPure cleanup helper with magnet round-trip and clean eluate recovery.",
    source=f"{MODULES_DIR_NAME}/workspace_modules.py",
    import_name="workspace_modules",
    function="spri_cleanup",
    triggers=["spri", "ampure", "bead", "beads", "magnetic", "cleanup"],
    parameters=[
        "sample_plate",
        "magnet",
        "bead_source",
        "elution_source",
        "waste_labware",
        "eluate_plate",
        "cleanup_tips",
        "eluate_tips",
        "sample_volume_ul",
        "bead_volume_ul",
        "elution_volume_ul",
    ],
    required_roles=[
        "analyte role='analyte'",
        "beads role='bead_carrier'",
        "eluent role='eluent'",
        "separate cleanup and eluate tip boxes",
    ],
)
