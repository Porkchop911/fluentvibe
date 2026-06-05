"""Parse a FluentControl .xscr into a Pydantic ``Protocol`` IR.

Walks the XML tree, identifies each ``<Object>``'s command type, and
emits the corresponding Step subclass. Inverse of
``fluentvibe.compiler.renderer``.

Scope (v1.1): the steps fluentvibe itself emits — AddLabware,
GetHeadAdapter / DropHeadAdapter, PickUpTips / SetTipsBack, Aspirate /
Dispense, Loop, Conditional, RGA gripper transfers, CGA finger
operations, basic variable / wait / comment ops. Anything else lands
as a ``GenericStep`` with the raw type retained, so round-trip parity
flags it explicitly rather than silently corrupting.

Field extraction is intentionally minimal: only the fields the IR Step
classes actually carry. Defaults (line numbers, device aliases, blowout
airgaps) are recomputed by the renderer at re-emit time.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator, Optional, Union

from ..catalog.xcmp import _find, _local, _text
from ..ir.schema import (
    AddLabwareStep, AspirateStep, CgaDropFingersStep, CgaGetFingersStep,
    CommentStep, ConditionalStep, DispenseStep, DropHeadAdapterStep,
    ExecuteApplicationStep, ExportVariableStep, GenericStep, GetHeadAdapterStep,
    Group, ImportVariableStep, LihaAspirateStep,
    LihaDispenseStep, LihaDropTipsStep, LihaEmptyTipsStep, LihaGetTipsStep,
    LihaMixStep, LoopStep, Mca384DropTipsStep, Mca384EmptyTipsStep,
    Mca384GetTipsStep, Mca384MixStep, Mca384MoveArmStep, PickUpTipsStep,
    Protocol, QueryVariableStep, RemoveLabwareStep, RgaTransferLabwareStep,
    ScriptGroupStep, SetLocationStep, SetTipsBackStep, SetVariableStep,
    StartTimerStep, STEP_TO_COMMAND_ID, Step, StepType, UserPromptStep,
    WaitForTimerStep, WaitStep, WorklistColumnMapping, WorklistImportStep,
    LoadWorklistStep, ExecuteWorklistStep, LegacyDriverMacroStep,
)


# Reverse mapping: command-id string -> StepType enum. Preserve the first
# mapping so "SetVariable" resolves to SET_VARIABLE instead of the secondary
# CALCULATE_VARIABLE renderer alias.
COMMAND_ID_TO_STEP_TYPE: dict[str, StepType] = {}
for _step_type, _command_id in STEP_TO_COMMAND_ID.items():
    COMMAND_ID_TO_STEP_TYPE.setdefault(_command_id, _step_type)

COMMAND_ID_ALIASES = {
    "LihaPickUp": "LihaGetTips",
    "LihaSetTipsBack": "LihaDropTips",
    "CommentStatement": "Comment",
}

RAW_PRESERVE_COMMAND_IDS = {
    "ApplicationDriverMacro",
    "Mca384PickUpTips",
    "Mca384SetTipsBack",
    "Mca384Aspirate",
    "Mca384Dispense",
    "Mca384Mix",
    "Mca384EmptyTips",
    "Mca384GetTips",
    "Mca384DropTips",
}

# Suffix-strip regex: '<Cmd>ScriptCommandDataV2' → '<Cmd>'
_CMD_ID_RE = re.compile(r"(ScriptCommand)?DataV?\d*$")


def parse_xscr(path: Union[Path, str]) -> Protocol:
    """Parse a .xscr file into a ``Protocol``."""
    tree = ET.parse(str(path))
    root = tree.getroot()

    payload = _find(root, "Payload")
    name = _text(_find(payload, "ObjectName")) or "Untitled Protocol"
    comment = _text(_find(payload, "Comment")) or ""
    worktable_guid, worktable_name = _parse_worktable_reference(payload)
    file_references = _parse_file_references(payload)

    variables, variable_defaults = _parse_variable_declarations(root)

    groups: list[Group] = []
    script_group = next(
        (e for e in root.iter()
         if isinstance(e.tag, str) and _local(e.tag) == "ScriptGroup"),
        None,
    )
    if script_group is not None:
        objects = _find(script_group, "Objects")
        if objects is not None:
            object_children = [
                obj for obj in objects
                if isinstance(obj.tag, str) and _local(obj.tag) == "Object"
            ]
            if object_children and any(
                "ScriptGroupData" in (obj.attrib.get("Type") or "")
                for obj in object_children
            ):
                current_group: Optional[Group] = None
                for group_obj in object_children:
                    if "ScriptGroupData" in (group_obj.attrib.get("Type") or ""):
                        group_name = _extract_field(group_obj, "Name") or "Steps"
                        stmts = _find_inner_statements(group_obj)
                        steps = _parse_statements(stmts) if stmts is not None else []
                        current_group = Group(name=group_name, steps=steps)
                        groups.append(current_group)
                        continue
                    if current_group is None:
                        current_group = Group(name="Steps", steps=[])
                        groups.append(current_group)
                    step = _parse_step_object(group_obj)
                    if step is not None:
                        current_group.steps.append(step)
            elif object_children:
                groups.append(Group(
                    name=_text(_find(script_group, "Name")) or "Steps",
                    steps=_parse_statements(objects),
                ))

    return Protocol(
        name=name,
        comment=comment,
        variables=variables,
        variable_defaults=variable_defaults,
        groups=groups,
        worktable_guid=worktable_guid,
        worktable_name=worktable_name,
        file_references=file_references,
    )


def _parse_worktable_reference(payload: Optional[ET.Element]) -> tuple[Optional[str], Optional[str]]:
    if payload is None:
        return None, None
    for child in list(payload):
        if not isinstance(child.tag, str) or _local(child.tag) != "Reference":
            continue
        if _text(_find(child, "TypeId")) != "WorktableWorkspace":
            continue
        return _text(_find(child, "Guid")), _text(_find(child, "ObjectName"))
    return None, None


def _parse_file_references(payload: Optional[ET.Element]) -> list[str]:
    if payload is None:
        return []
    refs: list[str] = []
    for child in list(payload):
        if not isinstance(child.tag, str) or _local(child.tag) != "FileReference":
            continue
        file_text = _text(_find(child, "File"))
        if file_text:
            refs.append(file_text)
    return refs


def _parse_variable_declarations(
    root: ET.Element,
) -> tuple[list[str], dict[str, Union[float, int, str]]]:
    """Read ``<VariableDeclarations>`` (double-nested) entries.

    Each ``<anyType>`` block carries ``<Name>``, ``<TypeName>``, and
    ``<Values>/<string>`` (the default). FluentControl uses
    ``Floating Point``/``Integer``/``String`` as type names; coerce
    to the matching Python type.
    """
    var_root = next(
        (e for e in root.iter()
         if isinstance(e.tag, str) and _local(e.tag) == "VariableDeclarations"),
        None,
    )
    if var_root is None:
        return [], {}

    variables: list[str] = []
    defaults: dict[str, Union[float, int, str]] = {}
    for elem in var_root.iter():
        if not isinstance(elem.tag, str):
            continue
        if _local(elem.tag) != "anyType":
            continue
        name_el = next((c for c in elem.iter()
                        if isinstance(c.tag, str) and _local(c.tag) == "Name"), None)
        type_el = next((c for c in elem.iter()
                        if isinstance(c.tag, str) and _local(c.tag) == "TypeName"), None)
        values_el = next((c for c in elem.iter()
                          if isinstance(c.tag, str) and _local(c.tag) == "Values"), None)
        if name_el is None or not name_el.text:
            continue
        var_name = name_el.text.strip()
        type_name = (type_el.text or "").strip() if type_el is not None else ""
        default_text = ""
        if values_el is not None:
            for sub in values_el:
                if isinstance(sub.tag, str) and _local(sub.tag) == "string":
                    default_text = (sub.text or "").strip()
                    break
        default_value = _coerce_variable_default(default_text, type_name)
        variables.append(var_name)
        defaults[var_name] = default_value
    return variables, defaults


def _coerce_variable_default(text: str, type_name: str) -> Union[float, int, str]:
    if not text:
        return ""
    t = type_name.lower()
    if "integer" in t:
        try:
            return int(float(text))
        except ValueError:
            return text
    if "float" in t or "double" in t or "point" in t:
        try:
            return float(text)
        except ValueError:
            return text
    return text


# ── Step parsing ────────────────────────────────────────────────────


def _parse_statements(stmts: ET.Element) -> list[Step]:
    out: list[Step] = []
    pending_conditional: Optional[ConditionalStep] = None
    for child in stmts:
        if not isinstance(child.tag, str):
            continue
        if _local(child.tag) != "Object":
            continue
        if _is_command_suffix(child, "AlternateGroup") and pending_conditional is not None:
            pending_conditional.else_steps = _parse_body(child)
            pending_conditional = None
            continue
        step = _parse_step_object(child)
        if step is not None:
            out.append(step)
            pending_conditional = step if isinstance(step, ConditionalStep) else None
    return out


def _parse_step_object(obj: ET.Element) -> Optional[Step]:
    type_attr = obj.attrib.get("Type") or ""
    suffix = type_attr.rsplit(".", 1)[-1]

    if "LoopGroup" in suffix:
        return _raw_step("LoopGroup", type_attr, obj)
    if "ConditionalGroup" in suffix:
        return _raw_step("ConditionalGroup", type_attr, obj)
    if "AlternateGroup" in suffix:
        return _raw_step("AlternateGroup", type_attr, obj)
    if "ScriptGroupData" in suffix:
        return ScriptGroupStep(
            name=_extract_field(obj, "Name") or "Steps",
            steps=_parse_body(obj),
        )

    raw_command_id = _CMD_ID_RE.sub("", suffix)
    command_id = _normalise_command_id(suffix)
    if command_id in RAW_PRESERVE_COMMAND_IDS:
        return _raw_step(command_id, type_attr, obj)
    step_type = COMMAND_ID_TO_STEP_TYPE.get(command_id)

    if step_type == StepType.ADD_LABWARE:
        return AddLabwareStep(
            labware_type=_extract_field(obj, "LabwareType") or "",
            label=_extract_field(obj, "LabwareLable") or "",
            location=_extract_field(obj, "Location") or "Site",
            position=int(_extract_field(obj, "Position") or "1"),
        )
    if step_type == StepType.REMOVE_LABWARE:
        return RemoveLabwareStep(labware_name=_extract_field(obj, "LabwareName") or "")
    if step_type == StepType.GET_HEAD_ADAPTER:
        return GetHeadAdapterStep(
            labware_name=_extract_field(obj, "LabwareName") or "EVA1",
        )
    if step_type == StepType.DROP_HEAD_ADAPTER:
        return DropHeadAdapterStep(
            labware_name=_extract_field(obj, "LabwareName"),
        )
    if step_type == StepType.PICK_UP_TIPS:
        return PickUpTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
        )
    if step_type == StepType.SET_TIPS_BACK:
        return SetTipsBackStep(
            labware_name=_extract_field(obj, "LabwareName"),
        )
    if step_type == StepType.ASPIRATE:
        return AspirateStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_extract_field(obj, "Volume")),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.DISPENSE:
        return DispenseStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_extract_field(obj, "Volume")),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.RGA_TRANSFER_LABWARE:
        return _parse_rga_transfer(obj)
    if step_type == StepType.LEGACY_DRIVER_MACRO:
        return _parse_legacy_driver_macro(obj)
    if step_type == StepType.CGA_GET_FINGERS:
        return CgaGetFingersStep(labware_name=_extract_field(obj, "LabwareName"))
    if step_type == StepType.CGA_DROP_FINGERS:
        return CgaDropFingersStep()
    if step_type == StepType.SET_VARIABLE:
        return SetVariableStep(
            variable_name=_extract_field(obj, "VariableName") or _extract_field(obj, "Name") or "",
            value=_coerce_scalar(_strip_wrapping_quotes(_extract_field(obj, "Value") or "")),
        )
    if step_type == StepType.COMMENT:
        return CommentStep(comment=_extract_field(obj, "Text") or _extract_field(obj, "Comment") or "")
    if step_type == StepType.WAIT:
        seconds_text = _extract_field(obj, "Seconds") or _extract_field(obj, "Duration")
        return WaitStep(duration_seconds=_parse_volume(seconds_text))
    if step_type == StepType.USER_PROMPT:
        return UserPromptStep(
            prompt=_extract_field(obj, "Prompt") or "",
            timeout=_parse_int(_extract_field(obj, "Timeout"), default=0),
        )
    if step_type == StepType.START_TIMER:
        return StartTimerStep(timer=_parse_int(_extract_field(obj, "Timer"), default=1))
    if step_type == StepType.WAIT_FOR_TIMER:
        return WaitForTimerStep(
            timer=_parse_int(_extract_field(obj, "Timer"), default=1),
            duration_seconds=_parse_volume(_extract_field(obj, "Duration")),
        )
    if step_type == StepType.EXPORT_VARIABLE:
        return ExportVariableStep(
            variables=_list_values(obj, "Variables"),
            export_file=_strip_wrapping_quotes(_extract_field(obj, "ExportFile") or ""),
            write_header=_parse_bool(_extract_field(obj, "WriteHeader")),
            replace_existing_file=_parse_bool(_extract_field(obj, "ReplaceExistingFile")),
            export_strings_with_quotes=_parse_bool(_extract_field(obj, "ExportStringsWithQuotes")),
            delimiter_code=_parse_int(_extract_field(obj, "DelimiterCode"), default=59),
        )
    if step_type == StepType.IMPORT_VARIABLE:
        return ImportVariableStep(
            variables=_list_values(obj, "Variables"),
            import_file=_strip_wrapping_quotes(_extract_field(obj, "ImportFile") or ""),
            read_line=_parse_bool(_extract_field(obj, "ReadLine")),
            line=_parse_int(_extract_field(obj, "Line"), default=1),
            start_in_column=_parse_bool(_extract_field(obj, "StartInColumn")),
            column=_parse_int(_extract_field(obj, "Column"), default=1),
            has_header=_parse_bool(_extract_field(obj, "HasHeader")),
            delimiter_code=_parse_int(_extract_field(obj, "DelimiterCode"), default=59),
        )
    if step_type == StepType.QUERY_VARIABLE:
        return QueryVariableStep(
            variable_name=_extract_field(obj, "VariableName") or _extract_field(obj, "Name") or "",
            query_prompt=_extract_field(obj, "QueryPrompt") or "",
            limit_range=_parse_bool(_extract_field(obj, "LimitRange")),
        )
    if step_type == StepType.EXECUTE_APPLICATION:
        return ExecuteApplicationStep(
            application=_extract_field(obj, "Application") or "",
            arguments=_extract_field(obj, "Arguments") or "",
            wait=_parse_bool(_extract_field(obj, "Wait"), default=True),
            store_return=_parse_bool(_extract_field(obj, "StoreReturn")),
            variable=_extract_field(obj, "Variable") or "",
        )
    if step_type == StepType.SET_LOCATION:
        return SetLocationStep(
            labware=_extract_field(obj, "Labware") or "",
            location=_extract_field(obj, "Location") or "Site",
            site=_parse_int(_extract_field(obj, "Site"), default=1),
            rotation=_parse_int(_extract_field(obj, "Rotation"), default=0),
        )
    if step_type == StepType.WORKLIST_IMPORT:
        return WorklistImportStep(
            csv_path=_strip_wrapping_quotes(_extract_field(obj, "CsvExpressionOrFilename") or ""),
            gwl_path=_strip_wrapping_quotes(_extract_field(obj, "GwlExpressionOrFilename") or ""),
            start_line=_parse_int(_extract_field(obj, "StartLinenumber"), default=1),
            stop_with_last_line=_parse_bool(_extract_field(obj, "IsStopWithLastLine"), default=True),
            stop_with_line=_parse_int(_extract_field(obj, "StopWithLine"), default=1),
            separator=_extract_field(obj, "SelectedColumnSeperator") or ",",
            columns=_parse_worklist_columns(obj),
        )
    if step_type == StepType.LOAD_WORKLIST:
        well_numeric = _parse_bool(_extract_field(obj, "UseWellIndexNumbers"), default=True)
        return LoadWorklistStep(
            gwl_path=_strip_wrapping_quotes(_extract_field(obj, "WorklistPath") or ""),
            liquid_class=_extract_field(obj, "LiquidClassName") or None,
            diti_type=_extract_field(obj, "DitiType") or "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 50ul SBS",
            selected_tips=_list_int_values(obj, "SelectedTips") or list(range(8)),
            handle_missing_labware=_extract_field(obj, "HandleMissingLabwareOptionEnum") or "SkipWithoutWarning",
            skip_initial_wash=_parse_bool(_extract_field(obj, "SkipInitialWash")),
            waste_labware=_strip_wrapping_quotes(_first_field_after_parent(obj, "DropDiTiParameters", "LabwareName") or "FCA Thru Deck Waste Chute_1"),
            empty_tips_liquid_class=_extract_field(obj, "EmptyTipsLiquidClassNameBySelection") or "Empty Tip",
            use_legacy_gwl_file_format=_parse_bool(_extract_field(obj, "UseLegacyGwlFileFormat")),
            ignore_filename_until_run=_parse_bool(_extract_field(obj, "IgnoreFilenameUntilRun"), default=True),
            device_alias=_extract_field(obj, "DeviceAlias") or None,
            well_positions="numeric" if well_numeric else "alphanumeric",
            dynamic_diti_table=_extract_field(obj, "DynamicDiTiTable") or "",
            dynamic_diti_handling=_parse_bool(_extract_field(obj, "IsDynamicDiTiHandling")),
            airgap_speed=_parse_int(_extract_field(obj, "AirgapSpeed"), default=70),
            airgap_volume=_parse_int(_extract_field(obj, "AirgapVolume"), default=10),
        )
    if step_type == StepType.EXECUTE_WORKLIST:
        return ExecuteWorklistStep(
            delete_gwl_scripts=_parse_bool(_extract_field(obj, "DeleteGwlScripts")),
        )
    if step_type == StepType.LIHA_GET_TIPS:
        if raw_command_id == "LihaPickUp" or _liha_get_tips_requires_raw(obj):
            return _raw_step(command_id, type_attr, obj)
        return LihaGetTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or None,
        )
    if step_type == StepType.LIHA_DROP_TIPS:
        return LihaDropTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or None,
        )
    if step_type == StepType.LIHA_ASPIRATE:
        if _liha_pipette_requires_raw(obj):
            return _raw_step(command_id, type_attr, obj)
        return LihaAspirateStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_first_list_value(obj, "Volumes")),
            liquid_class=_extract_field(obj, "LiquidClassName"),
            well_offset=_parse_offset(_extract_field(obj, "WellOffset")),
        )
    if step_type == StepType.LIHA_DISPENSE:
        if _liha_pipette_requires_raw(obj):
            return _raw_step(command_id, type_attr, obj)
        return LihaDispenseStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_first_list_value(obj, "Volumes")),
            liquid_class=_extract_field(obj, "LiquidClassName"),
            well_offset=_parse_offset(_extract_field(obj, "WellOffset")),
        )
    if step_type == StepType.LIHA_MIX:
        if _liha_pipette_requires_raw(obj):
            return _raw_step(command_id, type_attr, obj)
        return LihaMixStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_first_list_value(obj, "Volumes")),
            cycles=_parse_volume(_extract_field(obj, "Cycles") or "10"),
            liquid_class=_extract_field(obj, "LiquidClassName"),
            well_offset=_parse_offset(_extract_field(obj, "WellOffset")),
        )
    if step_type == StepType.LIHA_EMPTY_TIPS:
        return LihaEmptyTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_extract_field(obj, "Volume")),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.MCA384_GET_TIPS:
        return Mca384GetTipsStep(labware_name=_extract_field(obj, "LabwareName") or None)
    if step_type == StepType.MCA384_DROP_TIPS:
        return Mca384DropTipsStep(labware_name=_extract_field(obj, "LabwareName") or None)
    if step_type == StepType.MCA384_MOVE_ARM:
        return Mca384MoveArmStep(
            movement_type=_extract_field(obj, "MovementType") or "GlobalZTravel",
            labware_name=_extract_field(obj, "LabwareName") or None,
        )
    if step_type == StepType.MCA384_MIX:
        return Mca384MixStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_extract_field(obj, "Volume")),
            cycles=_coerce_scalar(_extract_field(obj, "Cycles") or "10"),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.MCA384_EMPTY_TIPS:
        return Mca384EmptyTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_volume(_extract_field(obj, "Volume")),
            liquid_class=_extract_field(obj, "LiquidClassName") or "Empty Tip",
        )

    return _raw_step(command_id or suffix, type_attr, obj)


def _raw_step(command_id: str, type_attr: str, obj: ET.Element) -> GenericStep:
    return GenericStep(
        step_type=command_id,
        parameters={
            "raw_type": type_attr,
            "raw_xml": ET.tostring(obj, encoding="unicode"),
        },
    )


def _liha_get_tips_requires_raw(obj: ET.Element) -> bool:
    diti_type = _extract_diti_type_available_id(obj)
    if diti_type and diti_type != "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS":
        return True
    return False


def _liha_pipette_requires_raw(obj: ET.Element) -> bool:
    if _parse_bool(_extract_field(obj, "IsLiquidClassNameByExpressionEnabled")):
        return True
    if _extract_field(obj, "LiquidClassNameByExpression"):
        return True
    if _extract_field(obj, "LiquidClassSelectionMode") in {"SingleByExpression", "MultiByExpression"}:
        return True
    tip_spacing = _extract_field(obj, "TipSpacing")
    if tip_spacing not in (None, "", "9"):
        return True
    selected = _extract_field(obj, "SelectedWellsString")
    if selected not in (None, "", "A1 - H1", "A1"):
        return True
    serialized = _extract_field(obj, "SerializedWellIndexes")
    if serialized not in (None, "", "0>1>7;", "0;"):
        return True
    volumes = _list_values(obj, "Volumes")
    if len(set(volumes)) > 1:
        return True
    return False


def _extract_diti_type_available_id(obj: ET.Element) -> Optional[str]:
    for elem in _walk_skipping_objects(obj):
        if not isinstance(elem.tag, str) or _local(elem.tag) != "DitiType":
            continue
        for child in elem.iter():
            if isinstance(child.tag, str) and _local(child.tag) == "AvailableID" and child.text:
                return child.text.strip()
    return None


def _parse_loop(obj: ET.Element) -> LoopStep:
    """``<LoopGroup>`` carries: ``<Name>``, ``<LoopVariable>``,
    ``<NumberOfLoops>``, and ``<Objects>`` (the body — direct child
    step Objects, no wrapping ``<Statements>``).
    """
    name_field = _extract_field(obj, "Name") or "Loop"
    loop_var = _extract_field(obj, "LoopVariable") or None
    iter_text = (
        _extract_field(obj, "NumberOfLoops")
        or _extract_field(obj, "NumberOfIterations")
        or _extract_field(obj, "Iterations")
        or "1"
    )
    body = _parse_body(obj)

    try:
        iterations = max(1, int(iter_text))
        number_of_loops: Union[int, str] = iterations
    except (ValueError, TypeError):
        iterations = 1
        number_of_loops = iter_text

    # If a LoopVariable string is present, prefer it as number_of_loops over
    # the literal — that's what fluentvibe's authoring API encodes when
    # `wt.loop(times='cycles')` is used.
    if loop_var:
        number_of_loops = loop_var

    return LoopStep(
        name=name_field,
        iterations=iterations,
        loop_variable=loop_var,
        number_of_loops=number_of_loops,
        steps=body,
    )


def _parse_conditional(obj: ET.Element) -> ConditionalStep:
    """``<ConditionalGroup>`` carries ``<Name>``, ``<Condition>`` (a
    single string like ``'ph>=7'``), and ``<Objects>`` for the
    then-branch. else-branches live in a sibling ``<AlternateGroup>``
    in the rendered XML; v1.1 doesn't author those.
    """
    name_field = _extract_field(obj, "Name") or "If"
    condition = _extract_field(obj, "Condition") or ""
    left, op, right_value = _parse_condition_string(condition)
    body = _parse_body(obj)

    return ConditionalStep(
        name=name_field,
        left_variable=left,
        operator=op,
        right_value=right_value,
        right_is_variable=False,
        then_steps=body,
        else_steps=[],
    )


_CONDITION_RE = re.compile(r"^\s*(\w+)\s*(==|!=|<=|>=|<|>|=)\s*(.+?)\s*$")


def _parse_condition_string(text: str) -> tuple[str, str, Union[str, int, float, bool]]:
    """Split ``'ph>=7'`` into ``('ph', '>=', 7)``."""
    if not text:
        return "", "==", ""
    m = _CONDITION_RE.match(text)
    if not m:
        return text, "==", ""
    left, op, right = m.group(1), m.group(2), m.group(3)
    if op == "=":
        op = "=="
    coerced: Union[str, int, float, bool]
    try:
        coerced = int(right)
    except ValueError:
        try:
            coerced = float(right)
        except ValueError:
            stripped = right.strip().strip("'\"")
            if stripped.lower() in ("true", "false"):
                coerced = stripped.lower() == "true"
            else:
                coerced = stripped
    return left, op, coerced


def _normalise_command_id(suffix: str) -> str:
    command_id = _CMD_ID_RE.sub("", suffix)
    command_id = COMMAND_ID_ALIASES.get(command_id, command_id)
    if command_id.endswith("Statement"):
        base = command_id.removesuffix("Statement")
        if base in COMMAND_ID_TO_STEP_TYPE:
            return base
    return command_id


def _is_command_suffix(obj: ET.Element, command_id: str) -> bool:
    type_attr = obj.attrib.get("Type") or ""
    suffix = type_attr.rsplit(".", 1)[-1]
    return _normalise_command_id(suffix) == command_id or suffix == command_id


def _parse_body(obj: ET.Element) -> list[Step]:
    """Loop / conditional body lives in ``<Objects>`` (direct child step
    Objects). Plain groups use ``<Statements>``. Try both."""
    body_container = _find_first(obj, "Objects")
    if body_container is None:
        body_container = _find_first(obj, "Statements")
    if body_container is None:
        return []
    return _parse_statements(body_container)


def _parse_rga_transfer(obj: ET.Element) -> RgaTransferLabwareStep:
    """RGA transfer is rendered as ``ApplicationDriverMacro`` with the
    actual labware / location / site stored as an XML-encoded string
    inside ``<ExecutionSettings>`` (a ``TransferLabwareCommandParameters``
    block). Parse the inner XML to recover them.

    For ``gripper.move(plate, onto=other)``, the renderer emits cover-
    site macros like ``GetCoverSiteName("Other")`` /
    ``GetCoverSiteIndex("Other")``. These are kept verbatim in
    ``destination_location`` so the renderer re-emits them byte-equal;
    ``destination_site`` falls back to ``1`` when the site value is a
    macro expression rather than a literal int.
    """
    inner_text = _extract_field(obj, "ExecutionSettings") or ""
    labware_name = ""
    dest_loc = "Site"
    dest_pos = 1
    if inner_text:
        # The inner XML is stored HTML-entity-escaped as character data
        # (`&lt;TransferLabwareCommandParameters&gt;…`).  Unescape before
        # parsing.
        from html import unescape as _html_unescape
        unescaped = _html_unescape(inner_text)
        try:
            inner = ET.fromstring(unescaped)
            for el in inner.iter():
                if not isinstance(el.tag, str):
                    continue
                tag = _local(el.tag)
                if tag == "Labware" and el.text:
                    labware_name = el.text.strip()
                elif tag == "Location" and el.text:
                    dest_loc = el.text.strip()
                elif tag == "Site" and el.text:
                    raw = el.text.strip()
                    try:
                        dest_pos = max(1, int(raw))
                    except (ValueError, TypeError):
                        dest_pos = 1  # macro expression — codegen recovers via dest_loc
        except ET.ParseError:
            pass
    return RgaTransferLabwareStep(
        labware_name=labware_name,
        destination_location=dest_loc,
        destination_site=dest_pos,
    )


def _parse_legacy_driver_macro(obj: ET.Element) -> LegacyDriverMacroStep:
    """A ``LegacyDriverMacro`` (e.g. Inheco ODTC ``SiLA-ODTC`` commands).

    ``Name`` / ``ModuleName`` are *attributes* of the inner ``<LegacyDriverMacro>``
    element; the payload is the ``<ExecutionSettings>`` text (absent/self-closing
    when empty).
    """
    name = ""
    module_name = ""
    for el in obj.iter():
        if isinstance(el.tag, str) and _local(el.tag) == "LegacyDriverMacro":
            name = el.attrib.get("Name", "") or ""
            module_name = el.attrib.get("ModuleName", "") or ""
            break
    settings = _extract_field(obj, "ExecutionSettings")
    return LegacyDriverMacroStep(
        name=name,
        module_name=module_name,
        execution_settings=settings or None,
    )


def _parse_worklist_columns(obj: ET.Element) -> list[WorklistColumnMapping]:
    out: list[WorklistColumnMapping] = []
    for sub in obj.iter():
        if not isinstance(sub.tag, str) or _local(sub.tag) != "InputParameter":
            continue
        out.append(WorklistColumnMapping(
            column_name=_child_text(sub, "ColumnName") or "",
            column_index=_parse_int(_child_text(sub, "ColumnIndex"), default=0),
            gwl_index=_child_text(sub, "GwlIndex") or "",
        ))
    return out


# ── XML helpers ─────────────────────────────────────────────────────


def _extract_field(obj: ET.Element, field_name: str) -> Optional[str]:
    """Return the text of the first descendant with the given local name,
    *not* descending into nested step ``<Object>`` siblings of the body.
    """
    for sub in _walk_skipping_objects(obj):
        if not isinstance(sub.tag, str):
            continue
        if _local(sub.tag) == field_name:
            txt = sub.text
            if txt is not None:
                return txt.strip()
    return None


def _find_inner_statements(obj: ET.Element) -> Optional[ET.Element]:
    """First ``<Statements>`` block inside ``obj`` that belongs to ``obj``
    itself, not to a nested step's loop/conditional body."""
    return _find_first(obj, "Statements")


def _find_first(obj: ET.Element, local_name: str) -> Optional[ET.Element]:
    """First descendant of ``obj`` (in document order) with the given
    local name, *not* descending into nested step ``<Object>`` siblings.
    """
    for child in _walk_skipping_objects(obj):
        if isinstance(child.tag, str) and _local(child.tag) == local_name:
            return child
    return None


def _child_text(obj: ET.Element, local_name: str) -> Optional[str]:
    for child in obj:
        if isinstance(child.tag, str) and _local(child.tag) == local_name:
            return (child.text or "").strip()
    return None


def _first_field_after_parent(obj: ET.Element, parent_name: str, field_name: str) -> Optional[str]:
    for parent in obj.iter():
        if not isinstance(parent.tag, str) or _local(parent.tag) != parent_name:
            continue
        for sub in parent.iter():
            if isinstance(sub.tag, str) and _local(sub.tag) == field_name and sub.text is not None:
                return sub.text.strip()
    return None


def _walk_skipping_objects(root: ET.Element) -> Iterator[ET.Element]:
    """Pre-order walk of ``root``'s descendants that does not enter any
    child ``<Object>`` element. Use to scan a step Object's payload
    fields without recursing into its loop/conditional body's child
    step Objects.
    """
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if _local(child.tag) == "Object" and child is not root:
            continue
        yield child
        yield from _walk_skipping_objects(child)


def _parse_volume(text: Optional[str]) -> Union[float, str]:
    if text is None:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return text  # variable name reference


def _coerce_scalar(text: str) -> Union[int, float, str]:
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _parse_int(text: Optional[str], *, default: int) -> int:
    if text is None or text == "":
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _parse_bool(text: Optional[str], *, default: bool = False) -> bool:
    if text is None or text == "":
        return default
    return text.strip().lower() == "true"


def _strip_wrapping_quotes(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


def _parse_offset(text: Optional[str]) -> Optional[Union[int, str]]:
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return text


def _first_list_value(obj: ET.Element, list_name: str) -> Optional[str]:
    container = _find_first(obj, list_name)
    if container is None:
        return None
    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) in {"string", "double", "int"} and el.text is not None:
            return el.text.strip()
    return None


def _list_values(obj: ET.Element, list_name: str) -> list[str]:
    container = _find_first(obj, list_name)
    if container is None:
        return []
    values: list[str] = []
    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) in {"string", "String"} and el.text is not None:
            values.append(el.text.strip())
    return values


def _list_int_values(obj: ET.Element, list_name: str) -> list[int]:
    container = _find_first(obj, list_name)
    if container is None:
        return []
    values: list[int] = []
    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) == "int" and el.text is not None:
            try:
                values.append(int(el.text.strip()))
            except ValueError:
                pass
    return values
