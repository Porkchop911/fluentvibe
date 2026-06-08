"""Codegen — emit a fluentvibe Python protocol from a Pydantic Protocol IR.

The output is a self-contained ``.py`` with a ``build_worktable()``
factory that, when executed, produces an IR equivalent to the input
(modulo the random ``WorkspaceDelta`` GUID and the FC checksum).

Conventions:

- Each placed Plate / Trough is filled with a stand-in
  ``default_reagent`` so simulation runs cleanly. Reagent identity is
  not recovered from the .xscr (never serialized); the user replaces
  the stand-in with real ``Reagent`` objects to model real liquids.
- ``EVA[*]`` AddLabware steps are skipped — ``head.mount_adapter()``
  triggers them implicitly and the install-bundle's checksum rewrite
  re-injects the labware row.
- The ``CgaGet/RgaTransfer/CgaDrop`` triplet that ``gripper.move(...)``
  emits is collapsed back into a single ``wt.gripper.move(...)`` call.
- ``GetCoverSiteName("X")`` destination locations are recognised as
  ``onto=X`` instead of ``to=("Site", N)``.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Union

from ..catalog.catalog import index_exists, resolve_by_name
from ..ir.schema import (
    AddLabwareStep, AspirateStep, CgaDropFingersStep, CgaGetFingersStep,
    CommentStep, ConditionalStep, DispenseStep, DropHeadAdapterStep,
    ExecuteApplicationStep, ExportVariableStep, GenericStep, GetHeadAdapterStep,
    Group, ImportVariableStep, LihaAspirateStep, LihaDispenseStep,
    LihaDropTipsStep, LihaEmptyTipsStep, LihaGetTipsStep, LihaMixStep,
    LoopStep, Mca384DropTipsStep, Mca384EmptyTipsStep, Mca384GetTipsStep,
    Mca384MixStep, Mca384MoveArmStep, PickUpTipsStep, Protocol,
    QueryVariableStep, RemoveLabwareStep, RgaTransferLabwareStep,
    ScriptGroupStep, SetLocationStep, SetTipsBackStep, SetVariableStep,
    StartTimerStep, Step, UserPromptStep, WaitForTimerStep, WaitStep,
    WorklistImportStep, LoadWorklistStep, ExecuteWorklistStep,
    LegacyDriverMacroStep,
)


# Default fill volumes (µL) per labware family — heuristic stand-ins.
_FILL_BY_CATEGORY: dict[str, float] = {
    "plate":       200.0,
    "trough":      20_000.0,
    # tip_box, magnet_rack, etc. don't carry liquid; no fill_all emitted.
}

# CATEGORY_TO_CLASS gives the family base class. Codegen refines plates
# by grid: 8×12 → Plate96, 16×24 → Plate384, otherwise the base.
_PLATE_GRID_TO_CLASS: dict[tuple[int, int], str] = {
    (8, 12): "Plate96",
    (16, 24): "Plate384",
}

_CATEGORY_TO_BASE_CLASS: dict[str, str] = {
    "plate":         "Plate",
    "trough":        "Trough",
    "tip_box":       "TipBox",
    "magnet_rack":   "MagnetRack",
    "tube_rack":     "TubeRack",
    "wash_station":  "WashStation",
    "waste_chute":   "WasteChute",
    "hotel":         "Hotel",
    "adapter":       "Adapter",
    "fixed_deck":    "FixedDeck",
}

_GET_COVER_SITE_RE = re.compile(r'GetCoverSiteName\("([^"]+)"\)')


def emit_python(protocol: Protocol, *, source_xscr: Optional[str] = None) -> str:
    """Render a ``Protocol`` to a Python source string."""
    classes_used: set[str] = set()
    label_to_var: dict[str, str] = {}
    placed_labware: list[tuple[str, str]] = []  # (var_name, category)

    body_lines: list[str] = []

    worktable_ctor = _emit_worktable_ctor(protocol)
    body_lines.append(f"    {worktable_ctor}")
    body_lines.append("")
    body_lines.append('    default_reagent = Reagent("liquid")')
    body_lines.append("    # Stand-in reagent — replace with real Reagent(...) instances")
    body_lines.append('    # to model identity (e.g. beads with role="bead_carrier").')
    classes_used.update({"Worktable", "Reagent"})

    # Emit protocol variables. set_sim_value is also seeded with the default
    # so the simulator has a value for any loop/conditional that references
    # this variable; the user can override before calling simulate().
    if protocol.variables:
        body_lines.append("")
        for var_name in protocol.variables:
            default = protocol.variable_defaults.get(var_name, 0)
            body_lines.append(f"    wt.declare_variable({var_name!r}, {default!r})")
            body_lines.append(f"    wt.set_sim_value({var_name!r}, {default!r})")

    # Walk groups; collapse RGA triplets; skip EVA AddLabware.
    for group in protocol.groups:
        body_lines.append("")
        body_lines.append(f'    wt.group({group.name!r})')
        _emit_steps(
            group.steps,
            indent="    ",
            out=body_lines,
            classes_used=classes_used,
            label_to_var=label_to_var,
            placed_labware=placed_labware,
        )

    body_lines.append("")
    body_lines.append("    return wt")

    # Header. Use only the basename in the docstring to keep it path-safe
    # on Windows (full paths embed unescaped backslash sequences which
    # Python parses as escape codes).
    if source_xscr:
        basename = source_xscr.replace("\\", "/").rsplit("/", 1)[-1]
    else:
        basename = ".xscr"
    header_lines: list[str] = []
    header_lines.append('"""Auto-decompiled from {} — DO NOT hand-edit."""'.format(basename))
    header_lines.append("")
    header_lines.append(_format_imports(classes_used))
    header_lines.append("")
    header_lines.append("")
    header_lines.append("def build_worktable() -> Worktable:")

    footer_lines = [
        "",
        "",
        'if __name__ == "__main__":',
        "    wt = build_worktable()",
        "    out = wt.compile({!r})".format(
            (source_xscr.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].replace(".xscr", "_recompiled.xscr"))
            if source_xscr else "decompiled.xscr"
        ),
        "    print(f'Wrote {out}')",
    ]

    return "\n".join(header_lines + body_lines + footer_lines) + "\n"


def _emit_worktable_ctor(protocol: Protocol) -> str:
    if protocol.worktable_name or protocol.worktable_guid:
        auto_place = not any(
            isinstance(step, AddLabwareStep)
            for group in protocol.groups
            for step in _iter_steps(group.steps)
        )
        return (
            f"wt = Worktable.from_workspace({(protocol.worktable_name or '')!r}, "
            f"workspace_guid={protocol.worktable_guid!r}, "
            f"auto_place={auto_place!r}, "
            f"protocol_name={protocol.name!r}, comment={protocol.comment!r})"
        )

    message = (
        "Decompiled protocol is missing its WorktableWorkspace reference. "
        "Bind a specific workspace before building, simulating, or compiling it."
    )
    return f"raise RuntimeError({message!r})"


def _iter_steps(steps: Iterable[Step]) -> Iterable[Step]:
    for step in steps:
        yield step
        if isinstance(step, ScriptGroupStep):
            yield from _iter_steps(step.steps)
        elif isinstance(step, LoopStep):
            yield from _iter_steps(step.steps)
        elif isinstance(step, ConditionalStep):
            yield from _iter_steps(step.then_steps)
            yield from _iter_steps(step.else_steps)


def _emit_steps(
    steps: Iterable[Step],
    *,
    indent: str,
    out: list[str],
    classes_used: set[str],
    label_to_var: dict[str, str],
    placed_labware: list[tuple[str, str]],
) -> None:
    head_var_emitted = False
    steps_list = list(steps)
    i = 0
    while i < len(steps_list):
        step = steps_list[i]

        if (
            isinstance(step, WorklistImportStep)
            and i + 2 < len(steps_list)
            and isinstance(steps_list[i + 1], LoadWorklistStep)
            and isinstance(steps_list[i + 2], ExecuteWorklistStep)
            and steps_list[i + 1].gwl_path == step.gwl_path
        ):
            out.append(indent + _emit_worklist(step, steps_list[i + 1], steps_list[i + 2], source_path=step.csv_path))
            i += 3
            continue

        if (
            isinstance(step, LoadWorklistStep)
            and i + 1 < len(steps_list)
            and isinstance(steps_list[i + 1], ExecuteWorklistStep)
        ):
            out.append(indent + _emit_worklist(None, step, steps_list[i + 1], source_path=step.gwl_path))
            i += 2
            continue

        # Collapse Cga(Get|Drop)Fingers + RgaTransferLabware triplet into
        # a single gripper.move(...) call.
        if (
            isinstance(step, CgaGetFingersStep)
            and i + 2 < len(steps_list)
            and isinstance(steps_list[i + 1], RgaTransferLabwareStep)
            and isinstance(steps_list[i + 2], CgaDropFingersStep)
        ):
            rga = steps_list[i + 1]
            assert isinstance(rga, RgaTransferLabwareStep)
            out.append(_emit_gripper_move(rga, indent=indent, label_to_var=label_to_var))
            i += 3
            continue

        if isinstance(step, AddLabwareStep):
            if step.labware_type.startswith("EVA["):
                # EVA AddLabware is auto-injected by the install-bundle
                # checksum rewrite; mount_adapter() handles the IR side.
                i += 1
                continue
            line, resolved = _emit_add_labware(step, classes_used, label_to_var, placed_labware)
            out.append(indent + line)
            if resolved:
                fill = _emit_fill_all(step, label_to_var, placed_labware)
                if fill is not None:
                    out.append(indent + fill)
            i += 1
            continue

        if isinstance(step, GetHeadAdapterStep):
            if not head_var_emitted:
                out.append(indent + "head = wt.mca96")
                head_var_emitted = True
            out.append(indent + "head.mount_adapter()")
            i += 1
            continue

        if isinstance(step, DropHeadAdapterStep):
            out.append(indent + "head.drop_adapter()")
            i += 1
            continue

        if isinstance(step, PickUpTipsStep):
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            out.append(indent + f"head.pick_up({target})")
            i += 1
            continue

        if isinstance(step, SetTipsBackStep):
            if step.labware_name:
                target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
                out.append(indent + f"head.return_tips({target})")
            else:
                out.append(indent + "head.return_tips(None)")
            i += 1
            continue

        if isinstance(step, AspirateStep):
            out.append(indent + _emit_aspirate(step, label_to_var))
            i += 1
            continue

        if isinstance(step, DispenseStep):
            out.append(indent + _emit_dispense(step, label_to_var))
            i += 1
            continue

        if isinstance(step, RgaTransferLabwareStep):
            # Stray RGA without surrounding Cga* — emit standalone gripper.move.
            out.append(_emit_gripper_move(step, indent=indent, label_to_var=label_to_var))
            i += 1
            continue

        if isinstance(step, CgaGetFingersStep) or isinstance(step, CgaDropFingersStep):
            # Without an adjacent RGA, drop CGA steps; gripper.move recreates them.
            i += 1
            continue

        if isinstance(step, RemoveLabwareStep):
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            out.append(indent + f"wt.remove({target})")
            i += 1
            continue

        if isinstance(step, LoopStep):
            times_repr = _times_repr(step)
            out.append(indent + f"with wt.loop(times={times_repr}, name={step.name!r}):")
            _emit_steps(
                step.steps,
                indent=indent + "    ",
                out=out,
                classes_used=classes_used,
                label_to_var=label_to_var,
                placed_labware=placed_labware,
            )
            _ensure_block_body(out, indent + "    ")
            i += 1
            continue

        if isinstance(step, ConditionalStep):
            right = step.right_value
            right_repr = repr(right)
            cond_var = f"_cond_{i}"
            out.append(
                indent + f"with wt.conditional(left={step.left_variable!r}, "
                f"op={step.operator!r}, right={right_repr}, name={step.name!r}):"
            )
            if step.else_steps:
                out[-1] = out[-1].replace("):", f") as {cond_var}:")
            _emit_steps(
                step.then_steps,
                indent=indent + "    ",
                out=out,
                classes_used=classes_used,
                label_to_var=label_to_var,
                placed_labware=placed_labware,
            )
            _ensure_block_body(out, indent + "    ")
            if step.else_steps:
                out.append(indent + f"with wt.else_branch({cond_var}):")
                _emit_steps(
                    step.else_steps,
                    indent=indent + "    ",
                    out=out,
                    classes_used=classes_used,
                    label_to_var=label_to_var,
                    placed_labware=placed_labware,
                )
                _ensure_block_body(out, indent + "    ")
            i += 1
            continue

        # Unsupported steps — emit as a comment so round-trip won't break
        # silently. The user (or v1.2) replaces with proper API.
        if isinstance(step, ScriptGroupStep):
            out.append(indent + f"with wt.nested_group({step.name!r}):")
            _emit_steps(
                step.steps,
                indent=indent + "    ",
                out=out,
                classes_used=classes_used,
                label_to_var=label_to_var,
                placed_labware=placed_labware,
            )
            _ensure_block_body(out, indent + "    ")
            i += 1
            continue

        if isinstance(step, LihaGetTipsStep):
            out.append(indent + "liha = wt.liha")
            arg = _label_arg(step.labware_name, label_to_var)
            out.append(indent + f"liha.get_tips({arg})" if arg else indent + "liha.get_tips()")
            i += 1
            continue

        if isinstance(step, LihaDropTipsStep):
            out.append(indent + "liha = wt.liha")
            arg = _label_arg(step.labware_name, label_to_var)
            out.append(indent + f"liha.drop_tips({arg})" if arg else indent + "liha.drop_tips()")
            i += 1
            continue

        if isinstance(step, LihaAspirateStep):
            out.append(indent + "liha = wt.liha")
            out.append(indent + _emit_liha_pipette("aspirate", step, label_to_var))
            i += 1
            continue

        if isinstance(step, LihaDispenseStep):
            out.append(indent + "liha = wt.liha")
            out.append(indent + _emit_liha_pipette("dispense", step, label_to_var))
            i += 1
            continue

        if isinstance(step, LihaMixStep):
            out.append(indent + "liha = wt.liha")
            out.append(indent + _emit_liha_pipette("mix", step, label_to_var, cycles=step.cycles))
            i += 1
            continue

        if isinstance(step, LihaEmptyTipsStep):
            out.append(indent + "liha = wt.liha")
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            parts = [target, repr(step.volume)]
            if step.liquid_class:
                parts.append(f"liquid_class={step.liquid_class!r}")
            out.append(indent + f"liha.empty_tips({', '.join(parts)})")
            i += 1
            continue

        if isinstance(step, Mca384MixStep):
            if not head_var_emitted:
                out.append(indent + "head = wt.mca96")
                head_var_emitted = True
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            parts = [target, repr(step.volume), f"cycles={step.cycles!r}"]
            parts.append(f"liquid_class={(step.liquid_class or 'Water Mix')!r}")
            out.append(indent + f"head.mix({', '.join(parts)})")
            i += 1
            continue

        if isinstance(step, Mca384EmptyTipsStep):
            if not head_var_emitted:
                out.append(indent + "head = wt.mca96")
                head_var_emitted = True
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            parts = [target, repr(step.volume)]
            if step.liquid_class:
                parts.append(f"liquid_class={step.liquid_class!r}")
            out.append(indent + f"head.empty_tips({', '.join(parts)})")
            i += 1
            continue

        if isinstance(step, SetVariableStep):
            out.append(indent + f"wt.set_variable({step.variable_name!r}, {step.value!r})")
            i += 1
            continue

        if isinstance(step, WaitStep):
            out.append(indent + f"wt.wait({step.duration_seconds!r})")
            i += 1
            continue

        if isinstance(step, CommentStep):
            out.append(indent + f"wt.add_comment({step.comment!r})")
            i += 1
            continue

        if isinstance(step, UserPromptStep):
            out.append(indent + f"wt.user_prompt({step.prompt!r}, timeout={step.timeout!r})")
            i += 1
            continue

        if isinstance(step, StartTimerStep):
            out.append(indent + f"wt.start_timer({step.timer!r})")
            i += 1
            continue

        if isinstance(step, WaitForTimerStep):
            out.append(indent + f"wt.wait_for_timer({step.timer!r}, {step.duration_seconds!r})")
            i += 1
            continue

        if isinstance(step, ExportVariableStep):
            out.append(indent + _emit_export_variables(step))
            i += 1
            continue

        if isinstance(step, ImportVariableStep):
            out.append(indent + _emit_import_variables(step))
            i += 1
            continue

        if isinstance(step, QueryVariableStep):
            out.append(indent + (
                f"wt.query_variable({step.variable_name!r}, {step.query_prompt!r}, "
                f"limit_range={step.limit_range!r})"
            ))
            i += 1
            continue

        if isinstance(step, ExecuteApplicationStep):
            out.append(indent + _emit_execute_application(step))
            i += 1
            continue

        if isinstance(step, WorklistImportStep):
            out.append(indent + _emit_convert_csv_to_gwl(step))
            i += 1
            continue

        if isinstance(step, LoadWorklistStep):
            out.append(indent + _emit_load_worklist(step))
            i += 1
            continue

        if isinstance(step, ExecuteWorklistStep):
            out.append(indent + _emit_execute_worklist(step))
            i += 1
            continue

        if isinstance(step, SetLocationStep):
            out.append(indent + (
                f"wt.set_location({step.labware!r}, {step.location!r}, "
                f"{step.site!r}, rotation={step.rotation!r})"
            ))
            i += 1
            continue

        if isinstance(step, (Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep)):
            out.append(indent + _emit_mca384_generic_step(step))
            i += 1
            continue

        if isinstance(step, LegacyDriverMacroStep):
            out.append(indent + _emit_legacy_driver_macro(step))
            i += 1
            continue

        if isinstance(step, GenericStep):
            out.append(indent + _emit_generic_step(step))
            i += 1
            continue

        out.append(indent + f"# [decompiler] unsupported step: {type(step).__name__}")
        i += 1


def _emit_add_labware(
    step: AddLabwareStep,
    classes_used: set[str],
    label_to_var: dict[str, str],
    placed_labware: list[tuple[str, str]],
) -> tuple[str, bool]:
    cls_name, category, error = _resolve_class_for_catalog(step.labware_type)
    if error is not None:
        message = (
            f"Decompiled labware {step.label!r} references catalog {step.labware_type!r}, "
            f"but {error} Replace it with an exact installed labware before "
            "simulating or compiling this protocol."
        )
        return f"raise RuntimeError({message!r})", False
    classes_used.add(cls_name)
    var_name = _allocate_var_name(step.label, label_to_var)
    placed_labware.append((var_name, category))
    return (
        f"{var_name} = wt.place("
        f"{cls_name}({step.label!r}, catalog={step.labware_type!r}), "
        f"{step.location!r}, {step.position}, allow_occupied=True)"
    ), True


def _emit_fill_all(
    step: AddLabwareStep,
    label_to_var: dict[str, str],
    placed_labware: list[tuple[str, str]],
) -> Optional[str]:
    var = label_to_var.get(step.label)
    if var is None:
        return None
    _, category = placed_labware[-1] if placed_labware else (var, "")
    fill = _FILL_BY_CATEGORY.get(category)
    if fill is None:
        return None
    return f"{var}.fill_all(default_reagent, {fill})"


def _emit_aspirate(step: AspirateStep, label_to_var: dict[str, str]) -> str:
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    parts = [target, repr(step.volume)]
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    if step.columns:
        parts.append(f"columns={list(step.columns)!r}")
    return f"head.aspirate({', '.join(parts)})"


def _emit_dispense(step: DispenseStep, label_to_var: dict[str, str]) -> str:
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    parts = [target, repr(step.volume)]
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    if step.columns:
        parts.append(f"columns={list(step.columns)!r}")
    return f"head.dispense({', '.join(parts)})"


def _label_arg(label: Optional[str], label_to_var: dict[str, str]) -> Optional[str]:
    if not label:
        return None
    return label_to_var.get(label, repr(label))


def _ensure_block_body(out: list[str], indent: str) -> None:
    if not out or not out[-1].startswith(indent):
        out.append(indent + "pass")


def _emit_liha_pipette(
    method: str,
    step: Union[LihaAspirateStep, LihaDispenseStep, LihaMixStep],
    label_to_var: dict[str, str],
    *,
    cycles: Optional[Union[int, str]] = None,
) -> str:
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    parts = [target, repr(step.volume)]
    if cycles is not None:
        parts.append(f"cycles={cycles!r}")
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    if step.well_offset is not None:
        parts.append(f"well_offset={step.well_offset!r}")
    return f"liha.{method}({', '.join(parts)})"


def _emit_export_variables(step: ExportVariableStep) -> str:
    parts = [repr(step.variables), repr(step.export_file)]
    if step.write_header:
        parts.append(f"write_header={step.write_header!r}")
    if step.replace_existing_file:
        parts.append(f"replace_existing_file={step.replace_existing_file!r}")
    if step.export_strings_with_quotes:
        parts.append(f"export_strings_with_quotes={step.export_strings_with_quotes!r}")
    if step.delimiter_code != 59:
        parts.append(f"delimiter_code={step.delimiter_code!r}")
    return f"wt.export_variables({', '.join(parts)})"


def _emit_import_variables(step: ImportVariableStep) -> str:
    parts = [repr(step.variables), repr(step.import_file)]
    if step.read_line:
        parts.append(f"read_line={step.read_line!r}")
    if step.line != 1:
        parts.append(f"line={step.line!r}")
    if step.start_in_column:
        parts.append(f"start_in_column={step.start_in_column!r}")
    if step.column != 1:
        parts.append(f"column={step.column!r}")
    if step.has_header:
        parts.append(f"has_header={step.has_header!r}")
    if step.delimiter_code != 59:
        parts.append(f"delimiter_code={step.delimiter_code!r}")
    return f"wt.import_variables({', '.join(parts)})"


def _emit_execute_application(step: ExecuteApplicationStep) -> str:
    parts = [repr(step.application)]
    if step.arguments:
        parts.append(f"arguments={step.arguments!r}")
    if not step.wait:
        parts.append(f"wait={step.wait!r}")
    if step.store_return:
        parts.append(f"store_return={step.store_return!r}")
    if step.variable:
        parts.append(f"variable={step.variable!r}")
    return f"wt.execute_application({', '.join(parts)})"


def _emit_worklist(
    import_step: WorklistImportStep | None,
    load_step: LoadWorklistStep,
    execute_step: ExecuteWorklistStep,
    *,
    source_path: str,
) -> str:
    parts = [repr(source_path)]
    if import_step is not None:
        parts.append(f"gwl_path={import_step.gwl_path!r}")
        if import_step.start_line != 2:
            parts.append(f"start_line={import_step.start_line!r}")
        if import_step.separator != ",":
            parts.append(f"separator={import_step.separator!r}")
        columns = _columns_dict(import_step)
        if columns != {"A": "SourceLabel", "B": "SourcePosition", "C": "DestLabel", "D": "DestPosition", "E": "Volume"}:
            parts.append(f"columns={columns!r}")
    parts.extend(_load_worklist_kwargs(load_step))
    if execute_step.delete_gwl_scripts:
        return "; ".join([
            f"wt.worklist({', '.join(parts)}, execute=False)",
            f"wt.execute_worklist(delete_gwl_scripts={execute_step.delete_gwl_scripts!r})",
        ])
    return f"wt.worklist({', '.join(parts)})"


def _emit_convert_csv_to_gwl(step: WorklistImportStep) -> str:
    parts = [repr(step.csv_path), repr(step.gwl_path)]
    if step.start_line != 1:
        parts.append(f"start_line={step.start_line!r}")
    if not step.stop_with_last_line:
        parts.append(f"stop_with_last_line={step.stop_with_last_line!r}")
    if step.stop_with_line != 1:
        parts.append(f"stop_with_line={step.stop_with_line!r}")
    if step.separator != ",":
        parts.append(f"separator={step.separator!r}")
    parts.append(f"columns={_columns_dict(step)!r}")
    return f"wt.convert_csv_to_gwl({', '.join(parts)})"


def _emit_load_worklist(step: LoadWorklistStep) -> str:
    parts = [repr(step.gwl_path), *_load_worklist_kwargs(step)]
    return f"wt.load_worklist({', '.join(parts)})"


def _emit_execute_worklist(step: ExecuteWorklistStep) -> str:
    if step.delete_gwl_scripts:
        return f"wt.execute_worklist(delete_gwl_scripts={step.delete_gwl_scripts!r})"
    return "wt.execute_worklist()"


def _load_worklist_kwargs(step: LoadWorklistStep) -> list[str]:
    parts: list[str] = []
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    if step.diti_type != "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 50ul SBS":
        parts.append(f"diti_type={step.diti_type!r}")
    if step.selected_tips != list(range(8)):
        parts.append(f"selected_tips={step.selected_tips!r}")
    if step.well_positions != "numeric":
        parts.append(f"well_positions={step.well_positions!r}")
    if step.handle_missing_labware != "SkipWithoutWarning":
        parts.append(f"handle_missing_labware={step.handle_missing_labware!r}")
    if step.skip_initial_wash:
        parts.append(f"skip_initial_wash={step.skip_initial_wash!r}")
    if step.waste_labware != "FCA Thru Deck Waste Chute_1":
        parts.append(f"waste_labware={step.waste_labware!r}")
    if not step.ignore_filename_until_run:
        parts.append(f"ignore_filename_until_run={step.ignore_filename_until_run!r}")
    return parts


def _columns_dict(step: WorklistImportStep) -> dict[str, str]:
    return {col.column_name: col.gwl_index for col in step.columns}


_ODTC_MACRO_TO_HELPER = {
    "SiLA-ODTC_OpenDoor": ("odtc_open_door", False),
    "SiLA-ODTC_CloseDoor": ("odtc_close_door", False),
    "SiLA-ODTC_GetParameters": ("odtc_get_parameters", False),
    "SiLA-ODTC_ExecuteMethod": ("odtc_execute_method", True),
}

_ODTC_SETPARAMS_PREFIX = "Parameter:MethodsXML:String:File:"


def _emit_legacy_driver_macro(step: LegacyDriverMacroStep) -> str:
    """Prefer the typed ``wt.odtc_*`` helpers for SiLA-ODTC; otherwise emit the
    generic ``wt.legacy_driver_macro(...)`` call."""
    if step.module_name == "SiLA-ODTC":
        if (
            step.name == "SiLA-ODTC_SetParameters"
            and step.execution_settings
            and step.execution_settings.startswith(_ODTC_SETPARAMS_PREFIX)
        ):
            file_arg = step.execution_settings[len(_ODTC_SETPARAMS_PREFIX):]
            return f"wt.odtc_set_parameters({file_arg!r})"
        helper = _ODTC_MACRO_TO_HELPER.get(step.name)
        if helper:
            method, takes_arg = helper
            if takes_arg:
                return f"wt.{method}({(step.execution_settings or '')!r})"
            return f"wt.{method}()"
    if step.execution_settings is None:
        return f"wt.legacy_driver_macro({step.name!r}, {step.module_name!r})"
    return (
        f"wt.legacy_driver_macro({step.name!r}, {step.module_name!r}, "
        f"{step.execution_settings!r})"
    )


def _emit_generic_step(step: GenericStep) -> str:
    raw_xml = step.parameters.get("raw_xml")
    if raw_xml:
        return f"wt.raw_xml_step({step.step_type!r}, {raw_xml!r})"
    params = {
        k: v for k, v in step.parameters.items()
        if k not in {"raw_type"} and isinstance(k, str)
    }
    args = ", ".join(f"{k}={v!r}" for k, v in sorted(params.items()))
    if args:
        return f"wt.generic_step({step.step_type!r}, {args})"
    return f"wt.generic_step({step.step_type!r})"


def _emit_mca384_generic_step(
    step: Union[Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep],
) -> str:
    if isinstance(step, Mca384GetTipsStep):
        params = {}
        if step.labware_name:
            params["labware_name"] = step.labware_name
        return _generic_call("Mca384GetTips", params)
    if isinstance(step, Mca384DropTipsStep):
        params = {}
        if step.labware_name:
            params["labware_name"] = step.labware_name
        return _generic_call("Mca384DropTips", params)
    params = {"movement_type": step.movement_type}
    if step.labware_name:
        params["labware_name"] = step.labware_name
    return _generic_call("Mca384MoveArm", params)


def _generic_call(step_type: str, params: dict[str, object]) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in sorted(params.items()))
    if args:
        return f"wt.generic_step({step_type!r}, {args})"
    return f"wt.generic_step({step_type!r})"


def _emit_gripper_move(
    step: RgaTransferLabwareStep,
    *,
    indent: str,
    label_to_var: dict[str, str],
) -> str:
    src_var = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    cover_match = _GET_COVER_SITE_RE.search(step.destination_location or "")
    if cover_match:
        target_label = cover_match.group(1)
        target_var = label_to_var.get(target_label)
        if target_var:
            return indent + f"wt.gripper.move({src_var}, onto={target_var})"
    loc = step.destination_location
    pos = step.destination_site
    return indent + f"wt.gripper.move({src_var}, to=({loc!r}, {pos}))"


def _times_repr(step: LoopStep) -> str:
    """Best LoopStep → ``times=`` argument."""
    if step.loop_variable:
        return repr(step.loop_variable)
    if isinstance(step.number_of_loops, str):
        return repr(step.number_of_loops)
    if step.number_of_loops is not None:
        return repr(step.number_of_loops)
    return repr(step.iterations)


def _resolve_class_for_catalog(catalog_name: str) -> tuple[str, str, str | None]:
    """Map a catalog name to (Python class name, category, error)."""
    if not index_exists():
        return "", "external", "the local fluentvibe catalog index is not built."
    entry = resolve_by_name(catalog_name)
    if entry is None:
        return "", "external", "that catalog name is not installed in the local fluentvibe catalog index."
    category = entry.category
    base = _CATEGORY_TO_BASE_CLASS.get(category, "FixedDeck")
    if category == "plate":
        grid = (entry.grid_y or 0, entry.grid_x or 0)
        if grid in _PLATE_GRID_TO_CLASS:
            return _PLATE_GRID_TO_CLASS[grid], category, None
    return base, category, None


def _allocate_var_name(label: str, label_to_var: dict[str, str]) -> str:
    if label in label_to_var:
        return label_to_var[label]
    base = _to_var_name(label)
    var = base
    n = 1
    while var in label_to_var.values():
        n += 1
        var = f"{base}_{n}"
    label_to_var[label] = var
    return var


def _to_var_name(label: str) -> str:
    """Make a Python identifier from a labware label."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", label)
    if s and s[0].isdigit():
        s = "_" + s
    if not s:
        s = "lw"
    return s.lower() if s[:1].isupper() else s


def _format_imports(classes_used: set[str]) -> str:
    """Emit a `from fluentvibe import (...)` block listing only what's used."""
    ordered = sorted(classes_used)
    if len(ordered) <= 5:
        return f"from fluentvibe import {', '.join(ordered)}"
    lines = ["from fluentvibe import ("]
    for cls in ordered:
        lines.append(f"    {cls},")
    lines.append(")")
    return "\n".join(lines)
