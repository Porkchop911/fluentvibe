"""Shared helpers for building blocks."""

from __future__ import annotations

from typing import Iterable

# Mix steps need a liquid class with a Mix micro-script section; FluentControl
# rejects e.g. "Water Free Single" for mixing.
DEFAULT_MIX_LIQUID_CLASS = "Water Mix"
# Emptying tips into waste uses the dedicated empty-tip class.
DEFAULT_EMPTY_TIP_LIQUID_CLASS = "Empty Tip"


class BlockError(ValueError):
    """A block's preconditions do not hold. The message says how to fix it."""


def roles_in(labware) -> set[str]:
    """Reagent roles present anywhere in ``labware``'s authored initial state."""
    roles: set[str] = set()
    for well in getattr(labware, "wells", {}).values():
        for layer in well.layers:
            roles.add(getattr(layer.reagent, "role", "plain"))
    return roles


def received_liquid(wt, labware) -> bool:
    """True when a step recorded so far dispenses into ``labware`` (its contents
    then come from the protocol itself, e.g. the eluate of an earlier clean-up)."""
    label = getattr(labware, "label", labware)

    def walk(steps) -> bool:
        for step in steps:
            if "Dispense" in type(step).__name__ and getattr(step, "labware_name", None) == label:
                return True
            if walk(getattr(step, "steps", None) or ()):
                return True
        return False

    groups = list(getattr(wt, "_groups", ()))
    stacked = [steps for steps in getattr(wt, "_emit_target_stack", ())]
    return any(walk(group.steps) for group in groups) or any(walk(steps) for steps in stacked)


def require_role(labware, role: str, *, param: str, block: str, wt=None) -> None:
    """``labware`` must hold ``role`` initially, or (with ``wt``) have been filled
    by an earlier step, in which case the simulator checks what it holds."""
    if role in roles_in(labware) or (wt is not None and received_liquid(wt, labware)):
        return
    if role not in roles_in(labware):
        present = sorted({
            f"{layer.reagent.name!r} (role={getattr(layer.reagent, 'role', 'plain')!r})"
            for well in getattr(labware, "wells", {}).values()
            for layer in well.layers
        })
        contents = ", ".join(present) if present else "nothing"
        raise BlockError(
            f"{block}: {param} ({getattr(labware, 'label', labware)!r}) must be filled "
            f"with a Reagent(..., role={role!r}) before calling {block}(), but it holds "
            f"{contents}. Fill it with the {role} reagent itself, e.g. "
            f"`{param}.fill_all(Reagent('<name>', role={role!r}), <volume>)`; the block "
            f"handles marker/matrix bookkeeping, so no separate plain 'matrix' fill is needed."
        )


def ensure_analyte_marker(plate, *, max_fraction: float = 0.1, max_marker_ul: float = 2.0) -> None:
    """Keep the analyte a small marker layer inside bulk sample liquid.

    The simulator moves a bound analyte's volume into the bead phase, where it
    no longer counts as liquid. Physically DNA adds no volume, so a well filled
    entirely with an ``analyte`` reagent would "lose" most of its liquid on
    binding and break the supernatant arithmetic. When the analyte is more than
    ``max_fraction`` of a well, the excess is re-expressed as a plain
    ``"<analyte> matrix"`` layer. The well's total volume is unchanged; only
    the authored initial state is touched, never the emitted steps.
    """
    from ..labware.base import Layer
    from ..reagent import Reagent

    matrices: dict[str, Reagent] = {}
    for well in getattr(plate, "wells", {}).values():
        analyte = [layer for layer in well.layers if getattr(layer.reagent, "role", None) == "analyte"]
        if not analyte:
            continue
        total = sum(layer.volume_ul for layer in well.layers)
        analyte_ul = sum(layer.volume_ul for layer in analyte)
        if total <= 0 or analyte_ul <= max_fraction * total:
            continue
        marker_ul = min(max_marker_ul, max_fraction * total)
        reagent = analyte[0].reagent
        matrix = matrices.setdefault(reagent.name, Reagent(f"{reagent.name} matrix"))
        others = [layer for layer in well.layers if layer not in analyte]
        well.layers = [
            *others,
            Layer(reagent=matrix, volume_ul=analyte_ul - marker_ul),
            Layer(reagent=reagent, volume_ul=marker_ul),
        ]


def variable_prefix(name: str | None) -> str | None:
    """FluentControl variable prefix for a block call, e.g. ``"PCR clean-up"`` →
    ``"PCR_CLEAN_UP"``; ``None`` when the block has no name."""
    if not name:
        return None
    slug = "".join(ch if ch.isalnum() else "_" for ch in str(name).upper())
    slug = "_".join(part for part in slug.split("_") if part)
    return slug or None


class BlockVariables:
    """Declares a block's values as FluentControl variables.

    With a prefix, ``ref("BEAD_VOLUME_UL", 36.0)`` declares
    ``<PREFIX>_BEAD_VOLUME_UL`` (default and sim value 36.0) and returns the
    variable name, so the emitted step references the variable and the value
    stays editable in FluentControl. Without a prefix it returns the value
    unchanged (a literal). Re-declaring a name with a different value raises:
    two blocks with the same ``name`` would otherwise overwrite each other.
    """

    def __init__(self, wt, prefix: str | None, *, block: str) -> None:
        self.wt = wt
        self.prefix = prefix
        self.block = block

    def ref(self, key: str, value):
        if self.prefix is None:
            return value
        name = f"{self.prefix}_{key}"
        existing = self.wt.protocol_variables.get(name)
        if existing is not None and existing != value:
            raise BlockError(
                f"{self.block}: variable {name} already holds {existing!r}; give this "
                f"{self.block}() call a different name= (e.g. name='Library clean-up')."
            )
        self.wt.declare_variable(name, value)
        self.wt.set_sim_value(name, value)
        return name


def require_distinct(block: str, **tip_boxes) -> None:
    seen: dict[int, str] = {}
    for name, box in tip_boxes.items():
        if box is None:
            continue
        other = seen.get(id(box))
        if other is not None:
            raise BlockError(
                f"{block}: {other} and {name} must be different tip boxes "
                f"(sample-touching and reagent/eluate tips must not be shared)."
            )
        seen[id(box)] = name


def require_positive(block: str, **values: float) -> None:
    for name, value in values.items():
        if value is None or float(value) <= 0:
            raise BlockError(f"{block}: {name} must be a positive number, got {value!r}.")


def mca_columns(columns: Iterable[int] | None) -> list[int] | None:
    """Plate columns for an MCA96 partial-plate call: ``None`` for the full plate.

    The same 1-based numbers address the tip box (``pick_up`` / ``return_tips``
    peel them from the box's left edge) and the plate (``aspirate`` /
    ``dispense`` / ``mix``), so the picked tips line up with the addressed columns.
    """
    if columns is None:
        return None
    cols = sorted({int(c) for c in columns})
    if not cols or any(not 1 <= c <= 12 for c in cols):
        raise BlockError(f"columns must be 1..12 plate columns, got {cols!r}.")
    return None if cols == list(range(1, 13)) else cols


def columns_or_all(columns: Iterable[int] | None) -> list[int]:
    cols = list(range(1, 13)) if columns is None else [int(c) for c in columns]
    bad = [c for c in cols if not 1 <= c <= 12]
    if bad or not cols:
        raise BlockError(f"columns must be 1..12 plate columns, got {cols!r}.")
    return cols
