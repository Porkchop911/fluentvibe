"""
Renderer for converting IR to Tecan XML format.

Takes validated Protocol IR and renders it to .xscr XML format
using templates and command definitions from the reference.
"""

import difflib
import re
import uuid
import yaml
def sanitize_text(text: str) -> str:
    """Sanitize text for XML - replace problematic characters."""
    if not text:
        return ""
    # Replace ampersand with "and" (Fluent Control doesn't like &)
    text = text.replace("&", "and")
    # Replace angle brackets with alternatives
    text = text.replace("<", "(").replace(">", ")")
    return text
from pathlib import Path
from typing import Optional, Dict

from ..catalog.fc_install import rewrite_checksum_in_place
from ..ir.schema import (
    Protocol, Group, Step, StepType, STEP_TO_COMMAND_ID,
    AddLabwareStep, RemoveLabwareStep,
    GetHeadAdapterStep, DropHeadAdapterStep,
    PickUpTipsStep, SetTipsBackStep,
    AspirateStep, DispenseStep,
    RgaTransferLabwareStep, CgaGetFingersStep, CgaDropFingersStep,
    Mca384MixStep, WaitStep, LoopStep,
    ConditionalStep,
    SetVariableStep, CalculateVariableStep,
    Mca384EmptyTipsStep,
    Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep,
    LihaAspirateStep, LihaDispenseStep, LihaMixStep,
    LihaGetTipsStep, LihaDropTipsStep, LihaEmptyTipsStep,
    ExportVariableStep, ImportVariableStep, QueryVariableStep,
    ExecuteApplicationStep, DelayStep, SetLocationStep, SubRoutineStep,
    VariableMapping, GenericStep, ScriptGroupStep,
    WorklistImportStep, LoadWorklistStep, ExecuteWorklistStep
)


_EVA_CONFIG = {
    "name": "EVA",
    "display_name": "EVA (Extended Volume)",
    "x_count": 12,
    "y_count": 8,
    "x_spacing": 9,
    "y_spacing": 9,
    "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi96.ExtVol",
    "can_mount_tecan_ditis": True,
    "tip_type": "MCA96",
    "partial_columns": 12,
    "partial_rows": 8,
    "last_tip_x": 12,
    "last_tip_y": 8,
}

_384_COMBO_CONFIG = {
    "name": "384_Combo",
    "display_name": "384 Tips Combo (Partial Tips)",
    "x_count": 24,
    "y_count": 16,
    "x_spacing": 4.5,
    "y_spacing": 4.5,
    "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi384.Combo",
    "can_mount_tecan_ditis": False,
    "tip_type": "MCA384",
    "partial_columns": 24,
    "partial_rows": 16,
    "last_tip_x": 24,
    "last_tip_y": 16,
}

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_valid_variable_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    candidate = name.strip()
    if not candidate:
        return False
    return bool(_VARIABLE_NAME_RE.fullmatch(candidate))


def _get_adapter_config(labware_name: str) -> Dict:
    """Get adapter configuration from database by labware name."""
    # Fast path: recognize EVA by labware name
    if labware_name and "eva" in labware_name.lower():
        return dict(_EVA_CONFIG)

    try:
        from ..catalog.database import get_database
        db = get_database()
        config = db.get_adapter_config(labware_name)
        # Database returns 384 Combo default when adapter not found;
        # detect EVA patterns even if DB doesn't know the labware name.
        if config.get("name") == "384_Combo" and labware_name and "eva" in labware_name.lower():
            return dict(_EVA_CONFIG)
        return config
    except Exception:
        return dict(_384_COMBO_CONFIG)


class RenderError(Exception):
    """Raised when rendering fails."""
    pass


class Renderer:
    """
    Renders Protocol IR to Tecan XML format.

    Uses templates from the templates/ directory and command
    definitions from the reference/commands.yaml file.
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        reference_path: Optional[Path] = None,
        templates_path: Optional[Path] = None
    ):
        """
        Initialize renderer.

        Args:
            config_path: Path to generation config YAML
            reference_path: Path to commands.yaml reference
            templates_path: Path to templates directory
        """
        # Asset bundle lives inside the package at fluentvibe/_assets/.
        assets_dir = Path(__file__).resolve().parent.parent / "_assets"

        self.config_path = config_path or assets_dir / "config" / "generation.yaml"
        self.reference_path = reference_path or assets_dir / "reference" / "commands.yaml"
        self.templates_path = templates_path or assets_dir / "templates"

        self.config = self._load_config()
        self.commands = self._load_commands()
        self.templates = self._load_templates()
        self.labware_reference = self._load_labware_reference()
        self._current_adapter_config: Optional[Dict] = None  # Tracks adapter state during rendering
        self._labware_types: Dict[str, str] = {}  # label -> labware_type mapping for tip type lookup
        self._labware_placements: Dict[tuple[str, int], str] = {}  # (location, position) -> label

    @staticmethod
    def _step_type_name(step) -> str:
        step_type = getattr(step, "step_type", None)
        return getattr(step_type, "value", step_type or "")

    def _load_config(self) -> dict:
        """Load generation configuration."""
        if not self.config_path.exists():
            raise RenderError(f"Config not found: {self.config_path}")
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _load_commands(self) -> dict:
        """Load command definitions from reference."""
        if not self.reference_path.exists():
            raise RenderError(f"Reference not found: {self.reference_path}")
        with open(self.reference_path) as f:
            data = yaml.safe_load(f)
        # Index commands by ID
        return {cmd["id"]: cmd for cmd in data.get("commands", [])}

    def _load_templates(self) -> dict:
        """Load XML templates."""
        templates = {}
        for template_file in self.templates_path.glob("*.xml"):
            templates[template_file.stem] = template_file.read_text(encoding="utf-8")
        return templates

    def _load_labware_reference(self) -> dict:
        """Load labware metadata (wells, category) keyed by labware name."""
        ref_path = Path(__file__).resolve().parent.parent / "_assets" / "reference" / "labware.yaml"
        if not ref_path.exists():
            return {}
        with open(ref_path) as f:
            data = yaml.safe_load(f) or {}
        labware = {}
        for entry in data.get("labware", []):
            name = entry.get("name")
            if name:
                labware[name] = {
                    "wells": entry.get("wells"),
                    "category": entry.get("category"),
                    "functional_group": entry.get("functional_group"),
                }
        for entry in data.get("other", {}).get("types", []):
            name = entry.get("name")
            if name:
                labware[name] = {
                    "wells": entry.get("wells"),
                    "category": "other",
                    "functional_group": entry.get("functional_group"),
                }
        return labware

    def render(self, protocol: Protocol) -> str:
        """
        Render a Protocol IR to XML string.

        Args:
            protocol: The protocol to render

        Returns:
            Complete XML string for .xscr file
        """
        # Reset state for this render
        self._current_adapter_config = None
        self._labware_types = {}
        self._labware_placements = {}

        # Deck-compatibility normalization for transfers onto magnet cover sites.
        # This is protocol-agnostic and only applies when a protocol includes such transfers.
        self._normalize_for_magnet_cover_site(protocol)

        # Auto-correct labware type names that are close but not exact matches
        # to known names in the database. The LLM frequently produces approximate
        # names (e.g. "50ml SBS MCA96" instead of "60ml SBS MCA96").
        self._normalize_labware_names(protocol)
        # After normalization, any add_labware whose catalog still isn't in the
        # database is a hard failure — surface a "did you mean X?" hint so the
        # authoring loop can repair on the next draft instead of producing a
        # silently-broken .xscr that FluentControl rejects at load time.
        self._validate_catalog_names_known(protocol)

        # Pre-scan set_variable steps to build variable value map
        # (needed to resolve variable references in labware types, e.g. DitiType)
        self._variable_values: Dict[str, str] = {}
        for var_name, value in (getattr(protocol, "variable_defaults", {}) or {}).items():
            self._variable_values[var_name] = str(value)
        for group in protocol.groups:
            for step in group.steps:
                if self._step_type_name(step) == "set_variable":
                    self._variable_values[step.variable_name] = str(step.value)
        self._protocol_variables = {
            str(v)
            for v in (getattr(protocol, "variables", []) or [])
            if isinstance(v, str) and _is_valid_variable_name(v)
        }
        self._protocol_variables.update(
            str(v)
            for v in (getattr(protocol, "variable_defaults", {}) or {}).keys()
            if isinstance(v, str) and _is_valid_variable_name(v)
        )

        # Assign line numbers if not already done
        protocol.assign_line_numbers()

        # Render all groups
        groups_xml = []
        for group in protocol.groups:
            group_xml = self._render_group(group, protocol)
            groups_xml.append(group_xml)

        # Build variable declarations XML
        variable_declarations_xml = ""
        declared_variables = [
            v for v in (protocol.variables or [])
            if _is_valid_variable_name(v)
        ]
        declared_defaults = {
            name: value
            for name, value in (getattr(protocol, "variable_defaults", {}) or {}).items()
            if _is_valid_variable_name(name)
        }
        for var_name in declared_defaults:
            if var_name not in declared_variables:
                declared_variables.append(var_name)
        # Auto-declare target variables from calculate_variable steps — they are
        # runtime-computed variables that FC must know about but the model often
        # omits from the protocol.variables list.
        declared_set = set(declared_variables)
        for _grp in protocol.groups:
            for _stp in _grp.steps:
                stype = self._step_type_name(_stp)
                if stype == "calculate_variable":
                    tv = (_stp.target_variable or "").strip()
                    if tv and _is_valid_variable_name(tv) and tv not in declared_set:
                        declared_variables.append(tv)
                        declared_set.add(tv)
                elif stype == "loop":
                    # Auto-declare the loop variable so it's resolvable in expressions.
                    lv = (_stp.loop_variable or "").strip()
                    if lv and _is_valid_variable_name(lv) and lv not in declared_set:
                        declared_variables.append(lv)
                        declared_set.add(lv)
                    for _inner in (_stp.steps or []):
                        if self._step_type_name(_inner) == "calculate_variable":
                            tv = (_inner.target_variable or "").strip()
                            if tv and _is_valid_variable_name(tv) and tv not in declared_set:
                                declared_variables.append(tv)
                                declared_set.add(tv)
        if declared_variables:
            # Detect variable types and initial values from declared defaults and runtime set steps
            var_info: dict[str, tuple[str, str]] = {}  # name -> (type, initial_value)

            # First pass: collect variables used in string-typed fields (labware names, types)
            string_vars: set[str] = set()
            volume_vars: set[str] = set()
            STRING_FIELDS = {'labware_name', 'labware_type', 'label', 'location',
                             'destination_location', 'liquid_class'}
            VOLUME_FIELDS = {'volume'}
            for group in protocol.groups:
                for step in group.steps:
                    for field_name in STRING_FIELDS:
                        val = getattr(step, field_name, None)
                        if isinstance(val, str) and val in declared_variables:
                            string_vars.add(val)
                    for field_name in VOLUME_FIELDS:
                        val = getattr(step, field_name, None)
                        if isinstance(val, str) and val in declared_variables:
                            volume_vars.add(val)

            def _infer_var_info(name: str, value):
                if name in string_vars:
                    return ("String", str(value) if value is not None else "")
                if name in volume_vars:
                    try:
                        return ("Floating Point", str(float(value)))
                    except (TypeError, ValueError):
                        return ("Floating Point", "0")
                if isinstance(value, float):
                    return ("Floating Point", str(value))
                if isinstance(value, int):
                    return ("Floating Point", str(float(value)))
                if isinstance(value, str) and value.replace('.', '', 1).lstrip('-').isdigit():
                    return ("Floating Point", value)
                return ("String", str(value) if value is not None else "")

            # Second pass: get types and initial values from declared defaults and runtime set_variable steps
            for var_name, value in declared_defaults.items():
                var_info[var_name] = _infer_var_info(var_name, value)

            for group in protocol.groups:
                for step in group.steps:
                    if self._step_type_name(step) == "set_variable":
                        if step.variable_name not in var_info:
                            var_info[step.variable_name] = _infer_var_info(step.variable_name, step.value)

            ns = "http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared"
            vars_list = []
            for var_name in declared_variables:
                if var_name in string_vars:
                    default_type = ("String", "")
                elif var_name in volume_vars:
                    default_type = ("Floating Point", "0")
                else:
                    default_type = ("Floating Point", "0")
                var_type, initial_value = var_info.get(var_name, default_type)
                vars_list.append(
                    f'                <d2p1:anyType xmlns:d3p1="{ns}" i:type="d3p1:VariableDefinitionHelper">\n'
                    f'                  <d3p1:IdOfParentItem>00000000-0000-0000-0000-000000000000</d3p1:IdOfParentItem>\n'
                    f'                  <d3p1:Item></d3p1:Item>\n'
                    f'                  <d3p1:Name>{var_name}</d3p1:Name>\n'
                    f'                  <d3p1:QueryOnStartup>false</d3p1:QueryOnStartup>\n'
                    f'                  <d3p1:QueryOnStartupString></d3p1:QueryOnStartupString>\n'
                    f'                  <d3p1:ReadOnly>false</d3p1:ReadOnly>\n'
                    f'                  <d3p1:Scope>Script</d3p1:Scope>\n'
                    f'                  <d3p1:TypeName>{var_type}</d3p1:TypeName>\n'
                    f'                  <d3p1:Values>\n'
                    f'                    <d2p1:string>{initial_value}</d2p1:string>\n'
                    f'                  </d3p1:Values>\n'
                    f'                </d2p1:anyType>'
                )
            variable_declarations_xml = "\n".join(vars_list)

        # Fill in script wrapper template
        wrapper = self.templates.get("script_wrapper")
        if not wrapper:
            raise RenderError("Missing script_wrapper.xml template")

        # Get config values with protocol overrides
        worktable_guid = (protocol.worktable_guid or "").strip()
        worktable_name = (protocol.worktable_name or "").strip()
        if not worktable_guid or not worktable_name:
            raise RenderError(
                "Protocol is not bound to a specific worktable workspace. "
                "Set Protocol.worktable_guid/worktable_name or build the worktable "
                "with Worktable.from_workspace(...)."
            )
        liquid_class_name = protocol.liquid_class or self.config["liquid_class"]["name"]
        liquid_class_guid = self._resolve_liquid_class_guid(liquid_class_name)

        xml = self._fill_template(wrapper, {
            "script_name": sanitize_text(protocol.name),  # Escape & < > for XML
            "comment": sanitize_text(protocol.comment or ""),
            "worktable_guid": worktable_guid,
            "worktable_name": worktable_name,
            "liquid_class_guid": liquid_class_guid,
            "liquid_class_name": liquid_class_name,
            "script_version": self.config["script"]["version"],
            "data_version": self.config["script"]["data_version"],
            "expected_duration": str(self.config["script"]["expected_duration"]),
            "workspace_delta_guid": str(uuid.uuid4()),
            "groups": "\n".join(groups_xml),
            "variable_declarations": variable_declarations_xml,
            "file_references": self._file_references_xml(getattr(protocol, "file_references", []) or []),
        })

        return xml

    def _render_group(self, group: Group, protocol: Protocol) -> str:
        """Render a single group to XML."""
        template = self.templates.get("script_group")
        if not template:
            raise RenderError("Missing script_group.xml template")

        # Render all statements in the group
        statements_xml = []
        for step in group.steps:
            step_xml = self._render_step(step, protocol, group, loop_depth=0)
            if step_xml and step_xml.strip():
                statements_xml.append(step_xml)

        return self._fill_template(template, {
            "group_name": sanitize_text(group.name),  # Escape & < > for XML
            "group_line_number": str(group.line_number or 1),
            "statements": "\n".join(statements_xml)
        })

    def _render_step(self, step: Step, protocol: Protocol, group: Group, loop_depth: int = 0) -> str:
        """Render a single step to XML using command templates."""
        stype = self._step_type_name(step)
        if isinstance(step, GenericStep) and step.parameters.get("raw_xml"):
            lines = str(step.parameters["raw_xml"]).strip().split("\n")
            return "\n".join("                        " + line for line in lines)

        if stype == "set_variable":
            var_name = (step.variable_name or "").strip()
            if not _is_valid_variable_name(var_name):
                return ""
            step.variable_name = var_name
        elif stype == "calculate_variable":
            target_var = (step.target_variable or "").strip()
            if not _is_valid_variable_name(target_var):
                return ""
            step.target_variable = target_var

        # Special handling for Loops (recursive) — must be an actual LoopStep,
        # not a GenericStep with step_type="loop" (which lacks .steps).
        if stype == "loop":
            return self._render_loop(step, protocol, group, loop_depth=loop_depth + 1)
        if stype == "conditional":
            return self._render_conditional(step, protocol, group, loop_depth=loop_depth + 1)
        if stype == "script_group":
            return self._render_script_group_step(step, protocol, group, loop_depth=loop_depth + 1)
        if stype in {"worklist_import", "load_worklist", "execute_worklist"}:
            xml = self._render_worklist_step(step, protocol)
            lines = xml.strip().split("\n")
            return "\n".join("                        " + line for line in lines)

        command_id = None

        # 1. If it's a typed step, use the explicit mapping
        if not isinstance(step, GenericStep):
            command_id = STEP_TO_COMMAND_ID.get(step.step_type)

        # 2. If it's a GenericStep, or we haven't found an ID yet...
        if not command_id:
            # Try to see if the string matches a known StepType enum value
            # This handles cases where dict_to_protocol fell back to GenericStep
            # but the type string was actually valid (just params were wrong)
            try:
                # normalize to snake_case if possible?
                # For now just try direct match
                enum_type = StepType(step.step_type)
                command_id = STEP_TO_COMMAND_ID.get(enum_type)
            except ValueError:
                pass

        # 3. If still no ID, use the snake_to_pascal conversion
        if not command_id:
            if isinstance(step, GenericStep) and step.step_type in self.commands:
                command_id = step.step_type

        if not command_id:
            command_id = self._step_type_to_command_id(step.step_type)

        if not command_id:
            raise RenderError(f"Unknown step type: {step.step_type}")

        command = self.commands.get(command_id)
        if not command:
            # Fallback: Check if it's an RGA command that maps to ApplicationDriverMacro
            if "Rga" in command_id and "Transfer" in command_id:
                 # Check if we have ApplicationDriverMacro in reference
                 if "ApplicationDriverMacro" in self.commands:
                     command_id = "ApplicationDriverMacro"
                     command = self.commands.get(command_id)

        if not command:
            raise RenderError(f"Command '{command_id}' not found in reference")

        template = command.get("template")
        if not template:
            raise RenderError(f"No template for command '{command_id}'")

        # Build parameter values from step
        params = self._step_to_params(step, protocol, group, loop_depth=loop_depth)

        # Fill in template
        xml = self._fill_template(template, params)

        # Post-process LiHa commands to fix hardcoded template values
        if self._is_liha_step(step, command_id):
            xml = self._post_process_liha_xml(xml, step, params)
        xml = self._post_process_step_xml(xml, step, params)

        # Indent for proper nesting in group
        lines = xml.strip().split("\n")
        indented = "\n".join("                        " + line for line in lines)

        return indented

    def _render_script_group_step(
        self,
        step: ScriptGroupStep,
        protocol: Protocol,
        group: Group,
        loop_depth: int = 1,
    ) -> str:
        template = self.templates.get("script_group")
        if not template:
            raise RenderError("Missing script_group.xml template")

        inner_steps_xml = []
        for inner_step in step.steps:
            inner_xml = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
            trimmed = inner_xml.strip()
            if trimmed:
                inner_steps_xml.append(trimmed)
        statements = "\n".join(
            "                        " + line
            for step_xml in inner_steps_xml
            for line in step_xml.split("\n")
        )
        xml = self._fill_template(template, {
            "group_name": sanitize_text(step.name),
            "group_line_number": str(step.line_number or 1),
            "statements": statements,
        })
        lines = xml.strip().split("\n")
        return "\n".join("                        " + line for line in lines)

    def _render_loop(self, step: LoopStep, protocol: Protocol, group: Group, loop_depth: int = 1) -> str:
        """Render a LoopStep recursively."""
        template = self.templates.get("loop_group")
        if not template:
            raise RenderError("Missing loop_group.xml template")

        # Render all steps inside the loop
        inner_steps_xml = []
        for inner_step in step.steps:
            inner_xml = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
            # Remove the base indentation added by _render_step because we'll add it ourselves
            trimmed = inner_xml.strip()
            if trimmed:
                inner_steps_xml.append(trimmed)

        # Indent inner steps for loop nesting (one level deeper than group statements)
        loop_statements = "\n".join("                              " + line 
                                   for step_xml in inner_steps_xml 
                                   for line in step_xml.split("\n"))

        # Use the loop variable name from the step so FC makes it available in-scope.
        # This allows calculate_variable expressions inside the loop to reference it.
        lv_raw = (step.loop_variable or "").strip()
        loop_variable = lv_raw if _is_valid_variable_name(lv_raw) else ""
        declared_vars = {
            v for v in (getattr(protocol, "variables", []) or [])
            if _is_valid_variable_name(v)
        }
        known_values = getattr(self, "_variable_values", {}) or {}

        if step.number_of_loops is not None:
            raw_count = str(step.number_of_loops).strip()
        else:
            raw_count = str(step.iterations)

        # FluentControl expects NumberOfLoops to be numeric or a declared variable.
        if raw_count.isdigit():
            number_of_loops = raw_count
        elif raw_count in declared_vars or raw_count in known_values:
            number_of_loops = raw_count
        else:
            number_of_loops = str(max(1, int(step.iterations or 1)))

        params = {
            "LoopName": sanitize_text(step.name),
            "NumberOfLoops": number_of_loops,
            "LoopVariable": loop_variable,
            "LineNumber": str(step.line_number or 0),
            "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
            "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
            "LoopStatements": loop_statements
        }

        xml = self._fill_template(template, params)
        
        # Indent the loop group itself to align with other steps in the group
        lines = xml.strip().split("\n")
        indented = "\n".join("                        " + line for line in lines)
        return indented

    def _render_conditional(self, step: ConditionalStep, protocol: Protocol, group: Group, loop_depth: int = 1) -> str:
        if_template = self.templates.get("conditional_group")
        else_template = self.templates.get("alternate_group")
        if not if_template or not else_template:
            raise RenderError("Missing conditional XML templates")

        then_xml = []
        for inner_step in step.then_steps:
            inner = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
            trimmed = inner.strip()
            if trimmed:
                then_xml.append(trimmed)
        then_statements = "\n".join(
            "                              " + line
            for step_xml in then_xml
            for line in step_xml.split("\n")
        )

        right_text = str(step.right_value)
        fc_operator = "=" if step.operator == "==" else step.operator
        if step.right_is_variable:
            condition = f"{step.left_variable}{fc_operator}{right_text}"
        elif isinstance(step.right_value, str) and right_text not in {"True", "False"}:
            condition = f'{step.left_variable}{fc_operator}"{self._xml_escape(right_text)}"'
        else:
            condition = f"{step.left_variable}{fc_operator}{right_text}"

        if_xml = self._fill_template(
            if_template,
            {
                "ConditionName": sanitize_text(step.name),
                "Condition": self._xml_escape(condition),
                "LineNumber": str(step.line_number or 0),
                "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
                "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
                "ThenStatements": then_statements,
            },
        )

        parts = [if_xml.strip()]
        if step.else_steps:
            else_xml = []
            for inner_step in step.else_steps:
                inner = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
                trimmed = inner.strip()
                if trimmed:
                    else_xml.append(trimmed)
            else_statements = "\n".join(
                "                              " + line
                for step_xml in else_xml
                for line in step_xml.split("\n")
            )
            parts.append(
                self._fill_template(
                    else_template,
                    {
                        "AlternateName": sanitize_text(f"{step.name} Else"),
                        "LineNumber": str(step.line_number or 0),
                        "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
                        "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
                        "ElseStatements": else_statements,
                    },
                ).strip()
            )

        lines = "\n".join(parts).split("\n")
        return "\n".join("                        " + line for line in lines)

    @staticmethod
    def _partial_tip_dims(step: Step, default_cols: int, default_rows: int) -> tuple[int, int]:
        """(PartialColumns, PartialRows) for a pickup/set-back.

        When the step names box ``columns``, ``PartialColumns`` is the column
        *span* (``max - min + 1``) of the active block and ``PartialRows`` is the
        adapter's full column height. The span — not the count — is what keeps
        the block consistent with ``FirstTip..LastTipXPosition`` and the derived
        ``PartialColumnOffset``; a sparse set (e.g. 1,4,7,10) spans 10 columns
        and is narrowed by ``SelectedRowsOrColumns``. Verified against the
        FluentControl ``PartialMCAexamples`` reference (col 1 -> span 1; cols
        7-12 -> span 6). Otherwise fall back to the legacy count fields, so full
        pickups stay byte-identical.
        """
        cols = getattr(step, "columns", None)
        if cols:
            ints = [int(c) for c in cols]
            return max(ints) - min(ints) + 1, default_rows
        pc = step.partial_columns if step.partial_columns else default_cols
        pr = step.partial_rows if step.partial_rows else default_rows
        return pc, pr

    @staticmethod
    def _partial_tip_col_offset(step: Step, default_cols: int, fallback: int) -> int:
        """``PartialColumnOffset`` for a pickup/set-back.

        FluentControl stores the offset counted from the *right* edge of the
        head: ``PartialColumnOffset = head_width - (rightmost addressed box
        column)``. So box col 1 -> offset 11, cols 7-12 -> offset 0 (verified
        against ``PartialMCAexamples``). It is therefore *derived* from the
        addressed columns and must never be passed independently — that is the
        defect that let the partial block and the well-selection disagree. When
        the step names no ``columns`` (the row-partial / legacy path) the manual
        field is used unchanged.
        """
        cols = getattr(step, "columns", None)
        if cols:
            return default_cols - max(int(c) for c in cols)
        return fallback

    def _post_process_partial_columns_xml(self, xml: str, step: Step) -> str:
        """Inject MCA partial-column well-selection into an aspirate/dispense.

        FluentControl encodes a partial-column selection on the
        ``Mca384ScriptCommandUsingWellSelectionBaseDataV6`` block:
        ``FirstTipXPosition``/``LastTipXPosition`` bound the addressed columns,
        and a non-contiguous set is enumerated in ``SelectedRowsOrColumns``.
        When ``step.columns`` is ``None`` this is a no-op, so full-plate output
        stays byte-identical.
        """
        columns = getattr(step, "columns", None)
        if not columns:
            return xml
        cols = sorted({int(c) for c in columns})
        first, last = cols[0], cols[-1]
        contiguous = cols == list(range(first, last + 1))
        xml = re.sub(
            r"<FirstTipXPosition>.*?</FirstTipXPosition>",
            lambda _: f"<FirstTipXPosition>{first}</FirstTipXPosition>",
            xml, count=1, flags=re.DOTALL,
        )
        xml = re.sub(
            r"<LastTipXPosition>.*?</LastTipXPosition>",
            lambda _: f"<LastTipXPosition>{last}</LastTipXPosition>",
            xml, count=1, flags=re.DOTALL,
        )
        if not contiguous:
            csv = ",".join(str(c) for c in cols)
            replacement = f"<SelectedRowsOrColumns>{csv}</SelectedRowsOrColumns>"
            xml = re.sub(r"<SelectedRowsOrColumns\s*/>", lambda _: replacement, xml, count=1)
            xml = re.sub(
                r"<SelectedRowsOrColumns>.*?</SelectedRowsOrColumns>",
                lambda _: replacement,
                xml, count=1, flags=re.DOTALL,
            )
        return xml

    def _post_process_step_xml(self, xml: str, step: Step, params: dict) -> str:
        stype = self._step_type_name(step)
        if "LiquidClassName" in params:
            xml = self._post_process_liquid_class_xml(xml, str(params.get("LiquidClassName") or ""))

        if stype in {"aspirate", "dispense", "pick_up_tips", "set_tips_back"}:
            xml = self._post_process_partial_columns_xml(xml, step)

        if stype in {"export_variable", "import_variable"}:
            xml = re.sub(
                r"<Variables>.*?</Variables>",
                lambda _: f"<Variables>\n{params.get('Variables', '')}\n    </Variables>",
                xml,
                count=1,
                flags=re.DOTALL,
            )
            tag = "ExportFile" if stype == "export_variable" else "ImportFile"
            for name in (tag, "WriteHeader", "ReplaceExistingFile", "ExportStringsWithQuotes", "DelimiterCode",
                         "ReadLine", "Line", "StartInColumn", "Column", "HasHeader"):
                value = params.get(name)
                if value is None:
                    continue
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name, v=value: f"<{n}>{v}</{n}>", xml, count=1, flags=re.DOTALL)

        elif stype == "query_variable":
            for name in ("Name", "QueryPrompt", "LimitRange"):
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name: f"<{n}>{params.get(n, '')}</{n}>", xml, count=1, flags=re.DOTALL)

        elif stype == "execute_application":
            for name in ("Application", "Wait", "StoreReturn", "LineNumber"):
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name: f"<{n}>{params.get(n, '')}</{n}>", xml, count=1, flags=re.DOTALL)
            arguments = params.get("Arguments", "")
            variable = params.get("Variable", "")
            xml = re.sub(r"<Arguments\s*/>", lambda _: f"<Arguments>{arguments}</Arguments>", xml, count=1)
            xml = re.sub(r"<Arguments>.*?</Arguments>", lambda _: f"<Arguments>{arguments}</Arguments>", xml, count=1, flags=re.DOTALL)
            xml = re.sub(r"<Variable\s*/>", lambda _: f"<Variable>{variable}</Variable>", xml, count=1)
            xml = re.sub(r"<Variable>.*?</Variable>", lambda _: f"<Variable>{variable}</Variable>", xml, count=1, flags=re.DOTALL)

        elif stype == "delay":
            xml = re.sub(r"<Delay>.*?</Delay>", lambda _: f"<Delay>{params.get('Delay', '')}</Delay>", xml, count=1, flags=re.DOTALL)

        elif stype == "set_location":
            for name in ("Labware", "Location", "Site", "Rotation"):
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name: f"<{n}>{params.get(n, '')}</{n}>", xml, count=1, flags=re.DOTALL)

        elif stype == "subroutine":
            xml = re.sub(r"<SubRoutine>.*?</SubRoutine>", lambda _: f"<SubRoutine>{params.get('SubRoutine', '')}</SubRoutine>", xml, count=1, flags=re.DOTALL)
            xml = re.sub(r"<ExecutionMode>.*?</ExecutionMode>", lambda _: f"<ExecutionMode>{params.get('ExecutionMode', '')}</ExecutionMode>", xml, count=1, flags=re.DOTALL)
            for name in ("VariableMappingsStart", "VariableMappingsEnd"):
                if re.search(fr"<{name}\s*/>", xml):
                    xml = re.sub(
                        fr"<{name}\s*/>",
                        lambda _, n=name: f"<{n}>\n{params.get(n, '')}\n    </{n}>",
                        xml,
                        count=1,
                    )
                xml = re.sub(
                    fr"<{name}>.*?</{name}>",
                    lambda _, n=name: f"<{n}>\n{params.get(n, '')}\n    </{n}>",
                    xml,
                    count=1,
                    flags=re.DOTALL,
                )
        return xml

    def _render_worklist_step(self, step: Step, protocol: Protocol) -> str:
        if isinstance(step, WorklistImportStep):
            return self._render_worklist_import(step)
        if isinstance(step, LoadWorklistStep):
            return self._render_load_worklist(step, protocol)
        if isinstance(step, ExecuteWorklistStep):
            return self._render_execute_worklist(step)
        raise RenderError(f"Unsupported worklist step {type(step).__name__}")

    def _render_worklist_import(self, step: WorklistImportStep) -> str:
        input_params = "\n".join(
            f'''      <Object Type="Tecan.Core.Worklist.InputParameter">
        <InputParameter>
          <ColumnName>{self._xml_escape(col.column_name)}</ColumnName>
          <ColumnIndex>{col.column_index}</ColumnIndex>
          <GwlIndex>{self._xml_escape(col.gwl_index)}</GwlIndex>
        </InputParameter>
      </Object>'''
            for col in step.columns
        )
        return f'''<Object Type="Tecan.Core.Worklist.Data.WorklistImportStatementDataV2">
  <WorklistImportStatementDataV2>
    <CsvExpressionOrFilename>"{self._xml_escape(step.csv_path)}"</CsvExpressionOrFilename>
    <StartLinenumber>{step.start_line}</StartLinenumber>
    <IsStopWithLastLine>{self._bool_text(step.stop_with_last_line)}</IsStopWithLastLine>
    <StopWithLine>{step.stop_with_line}</StopWithLine>
    <SelectedColumnSeperator>{self._xml_escape(step.separator)}</SelectedColumnSeperator>
    <SelectedSourceLabware>
      <guid>00000000-0000-0000-0000-000000000000</guid>
    </SelectedSourceLabware>
    <SelectedSourceLabwareForce>False</SelectedSourceLabwareForce>
    <SelectedDestinationLabware>
      <guid>00000000-0000-0000-0000-000000000000</guid>
    </SelectedDestinationLabware>
    <InputParameters>
{input_params}
    </InputParameters>
    <GwlExpressionOrFilename>"{self._xml_escape(step.gwl_path)}"</GwlExpressionOrFilename>
    <Data Type="Tecan.Core.Worklist.Data.WorklistStatementBaseDataV1">
      <WorklistStatementBaseDataV1>
        <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
          <ScriptStatementBaseDataV1>
            <IsBreakpoint>{self._bool_text(step.breakpoint)}</IsBreakpoint>
            <IsDisabledForExecution>{self._bool_text(step.disabled)}</IsDisabledForExecution>
            <GroupLineNumber>0</GroupLineNumber>
            <LineNumber>{step.line_number or 0}</LineNumber>
          </ScriptStatementBaseDataV1>
        </Data>
      </WorklistStatementBaseDataV1>
    </Data>
  </WorklistImportStatementDataV2>
</Object>'''

    def _render_load_worklist(self, step: LoadWorklistStep, protocol: Protocol) -> str:
        liha_config = self.config.get("liha_device", {})
        device_alias = step.device_alias or liha_config.get("alias", "Instrument=1/Device=LIHA:1")
        liquid_class = step.liquid_class or protocol.liquid_class or self.config["liquid_class"]["name"]
        selected_tips = self._int_objects_xml(step.selected_tips, indent="      ")
        return f'''<Object Type="Tecan.Core.Worklist.Data.LoadWorklistStatementDataV4">
  <LoadWorklistStatementDataV4>
    <HandleMissingLabwareOption>
      <HandleMissingLabwareOptionEnum>{self._xml_escape(step.handle_missing_labware)}</HandleMissingLabwareOptionEnum>
    </HandleMissingLabwareOption>
    <SkipInitialWash>{self._bool_text(step.skip_initial_wash)}</SkipInitialWash>
    <SelectedTips>
{selected_tips}
    </SelectedTips>
    <WashParametersList>
      <Object Type="Tecan.Core.CommandFactory.LiHa.WashParameters">
        <WashParameters>
          <WasteName />
          <CleanerName />
          <LiquidClassName />
          <Volumes />
          <LabwareName />
          <WasteVolume>3</WasteVolume>
          <CleanerVolume>4</CleanerVolume>
          <IsLiquidClassNameByExpressionEnabled>false</IsLiquidClassNameByExpressionEnabled>
        </WashParameters>
      </Object>
    </WashParametersList>
    <GetDiTiParameters Type="Tecan.Core.CommandFactory.LiHa.GetDitiParameters">
      <GetDitiParameters>
        <AirgapSpeed>{step.airgap_speed}</AirgapSpeed>
        <AirgapVolume>{step.airgap_volume}</AirgapVolume>
        <DitiType>{self._xml_escape(step.diti_type)}</DitiType>
        <IsDynamicDiTiHandling>{str(bool(step.dynamic_diti_handling)).lower()}</IsDynamicDiTiHandling>
        <DynamicDiTiTable>{self._xml_escape(step.dynamic_diti_table)}</DynamicDiTiTable>
      </GetDitiParameters>
    </GetDiTiParameters>
    <DropDiTiParameters Type="Tecan.Core.CommandFactory.LiHa.DropDitiParameters">
      <DropDitiParameters>
        <SkipIfNothingMounted>true</SkipIfNothingMounted>
        <LabwareName>"{self._xml_escape(step.waste_labware)}"</LabwareName>
      </DropDitiParameters>
    </DropDiTiParameters>
    <DecontaminationParameters>
      <DecontaminationParameters>
        <DecontWaitDurationAfterAspiration />
        <DecontLiquidClass />
        <DecontLiquidClassNameByExpression />
        <DecontSource />
        <DecontVolume />
        <IsDecontLiquidClassNameByExpressionEnabled>false</IsDecontLiquidClassNameByExpressionEnabled>
        <UseDecontaminationWash>false</UseDecontaminationWash>
      </DecontaminationParameters>
    </DecontaminationParameters>
    <EmptyTipsParameters Type="Tecan.Core.CommandFactory.LiHa.EmptyTipsParameters">
      <EmptyTipsParameters>
        <EmptyTipsLiquidClassNameBySelection>{self._xml_escape(step.empty_tips_liquid_class)}</EmptyTipsLiquidClassNameBySelection>
        <EmptyTipsLiquidClassNameByExpression />
        <IsEmptyTipsLiquidClassNameByExpressionEnabled>false</IsEmptyTipsLiquidClassNameByExpressionEnabled>
        <SelectedTipsIndexes />
        <LabwareName>"{self._xml_escape(step.waste_labware)}"</LabwareName>
        <SelectedWellIndexes />
        <WellOffset>0</WellOffset>
        <OffsetX>0</OffsetX>
        <OffsetY>0</OffsetY>
      </EmptyTipsParameters>
    </EmptyTipsParameters>
    <UseLegacyGwlFileFormat>{self._bool_text(step.use_legacy_gwl_file_format)}</UseLegacyGwlFileFormat>
    <WorklistPath>"{self._xml_escape(step.gwl_path)}"</WorklistPath>
    <IgnoreFilenameUntilRun>{self._bool_text(step.ignore_filename_until_run)}</IgnoreFilenameUntilRun>
    <LiquidClassName>{self._xml_escape(liquid_class)}</LiquidClassName>
    <IsLiquidClassNameByExpressionEnabled>False</IsLiquidClassNameByExpressionEnabled>
    <SelectedPipettingDevice Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">
      <DeviceAlias>{self._xml_escape(device_alias)}</DeviceAlias>
    </SelectedPipettingDevice>
    <Data Type="Tecan.Core.Worklist.Data.WorklistStatementBaseDataV1">
      <WorklistStatementBaseDataV1>
        <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
          <ScriptStatementBaseDataV1>
            <IsBreakpoint>{self._bool_text(step.breakpoint)}</IsBreakpoint>
            <IsDisabledForExecution>{self._bool_text(step.disabled)}</IsDisabledForExecution>
            <GroupLineNumber>0</GroupLineNumber>
            <LineNumber>{step.line_number or 0}</LineNumber>
          </ScriptStatementBaseDataV1>
        </Data>
      </WorklistStatementBaseDataV1>
    </Data>
  </LoadWorklistStatementDataV4>
</Object>'''

    def _render_execute_worklist(self, step: ExecuteWorklistStep) -> str:
        return f'''<Object Type="Tecan.Core.Worklist.Data.ExecuteWorklistStatementDataV1">
  <ExecuteWorklistStatementDataV1>
    <DeleteGwlScripts>{self._bool_text(step.delete_gwl_scripts)}</DeleteGwlScripts>
    <Data Type="Tecan.Core.Worklist.Data.WorklistStatementBaseDataV1">
      <WorklistStatementBaseDataV1>
        <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
          <ScriptStatementBaseDataV1>
            <IsBreakpoint>{self._bool_text(step.breakpoint)}</IsBreakpoint>
            <IsDisabledForExecution>{self._bool_text(step.disabled)}</IsDisabledForExecution>
            <GroupLineNumber>0</GroupLineNumber>
            <LineNumber>{step.line_number or 0}</LineNumber>
          </ScriptStatementBaseDataV1>
        </Data>
      </WorklistStatementBaseDataV1>
    </Data>
  </ExecuteWorklistStatementDataV1>
</Object>'''

    def _post_process_liquid_class_xml(self, xml: str, liquid_class: str) -> str:
        """Render liquid-class variables using FluentControl expression syntax."""
        liquid_class = (liquid_class or "").strip()
        if not liquid_class:
            return xml

        is_expression = self._is_liquid_class_expression(liquid_class)
        selection = "" if is_expression else self._xml_escape(liquid_class)
        expression = self._xml_escape(liquid_class) if is_expression else ""
        fallback_name = self._xml_escape(
            self._variable_values.get(liquid_class, liquid_class)
            if is_expression
            else liquid_class
        )
        mode = "SingleByExpression" if is_expression else "SingleByName"
        enabled = "True" if is_expression else "False"

        replacements = {
            "IsLiquidClassNameByExpressionEnabled": enabled,
            "LiquidClassNameBySelection": selection,
            "LiquidClassNameByExpression": expression,
            "LiquidClassName": fallback_name,
        }
        for tag_name, value in replacements.items():
            xml = self._replace_xml_tag(xml, tag_name, value)
        xml = re.sub(
            r"(<LiquidClassSelectionMode>\s*<LiquidClassSelectionMode>).*?(</LiquidClassSelectionMode>\s*</LiquidClassSelectionMode>)",
            lambda m: f"{m.group(1)}{mode}{m.group(2)}",
            xml,
            count=1,
            flags=re.DOTALL,
        )
        return xml

    def _is_liquid_class_expression(self, value: str) -> bool:
        value = (value or "").strip()
        if not value:
            return False
        if value in getattr(self, "_protocol_variables", set()):
            return True
        if not _is_valid_variable_name(value):
            return any(ch in value for ch in "+-*/()")
        return False

    def _replace_xml_tag(self, xml: str, tag_name: str, value: str) -> str:
        if value == "":
            # Empty value: preserve the template's element form rather than
            # injecting an empty value. A self-closing <Tag /> must stay
            # self-closing (expanding it to <Tag></Tag> diverges from
            # FluentControl's own serialization); an expanded element keeps its
            # form with its content cleared. Templates intentionally use both
            # forms in different commands, so don't normalize across them.
            if re.search(fr"<{tag_name}\s*/>", xml):
                return xml
            return re.sub(
                fr"<{tag_name}>.*?</{tag_name}>",
                lambda _: f"<{tag_name}></{tag_name}>",
                xml,
                count=1,
                flags=re.DOTALL,
            )
        if re.search(fr"<{tag_name}\s*/>", xml):
            return re.sub(
                fr"<{tag_name}\s*/>",
                f"<{tag_name}>{value}</{tag_name}>",
                xml,
                count=1,
            )
        return re.sub(
            fr"<{tag_name}>.*?</{tag_name}>",
            lambda _: f"<{tag_name}>{value}</{tag_name}>",
            xml,
            count=1,
            flags=re.DOTALL,
        )

    def _step_type_to_command_id(self, step_type: str) -> str:
        """Convert snake_case step_type to PascalCase command ID."""
        # snake_case to PascalCase: mca384_pick_up_tips -> Mca384PickUpTips
        parts = step_type.split('_')
        return ''.join(word.capitalize() for word in parts)

    @staticmethod
    def _bool_text(value: bool) -> str:
        return str(bool(value)).lower().capitalize()

    @staticmethod
    def _xml_escape(value: str) -> str:
        text = "" if value is None else str(value)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _string_objects_xml(self, values: list[str]) -> str:
        if not values:
            return ""
        lines = []
        for value in values:
            escaped = self._xml_escape(value)
            lines.append('      <Object Type="System.String">')
            lines.append(f'        <string>{escaped}</string>')
            lines.append('      </Object>')
        return "\n".join(lines)

    def _int_objects_xml(self, values: list[int], *, indent: str = "") -> str:
        lines = []
        for value in values:
            lines.append(f'{indent}<Object Type="System.Int32">')
            lines.append(f'{indent}  <int>{int(value)}</int>')
            lines.append(f'{indent}</Object>')
        return "\n".join(lines)

    def _file_references_xml(self, paths: list[str]) -> str:
        if not paths:
            return ""
        unique = []
        for path in paths:
            if path and path not in unique:
                unique.append(path)
        # Leading newline so the block starts on its own line after the
        # preceding </Reference>; when there are no references the placeholder
        # collapses to nothing (no stray blank line before <PayloadData>).
        return "\n" + "\n".join(
            f'    <FileReference>\n      <File>{self._xml_escape(path)}</File>\n    </FileReference>'
            for path in unique
        )

    def _variable_mappings_xml(self, mappings: list[VariableMapping]) -> str:
        if not mappings:
            return ""
        lines = []
        for mapping in mappings:
            lines.append('      <Object Type="Tecan.Core.Scripting.VariableMapping">')
            lines.append('        <VariableMapping>')
            lines.append(f'          <Target>{self._xml_escape(mapping.target)}</Target>')
            lines.append(f'          <Source>{self._xml_escape(mapping.source)}</Source>')
            lines.append('        </VariableMapping>')
            lines.append('      </Object>')
        return "\n".join(lines)

    def _step_to_params(self, step: Step, protocol: Protocol, group: Group, loop_depth: int = 0) -> dict:
        """Convert step to template parameters."""
        # Get defaults from config
        default_device = protocol.device_alias or self.config["device"]["alias"]
        default_available_id = getattr(protocol, "available_id", None) or self.config["device"].get("available_id", default_device)
        default_liquid_class = protocol.liquid_class or self.config["liquid_class"]["name"]

        # Normalize liquid classes to robust runtime defaults accepted by FluentControl.
        # Local model outputs frequently emit "Default Init FCA", which validates poorly.
        def _normalize_liquid_class(requested: str | None, *, mix: bool = False) -> str:
            if mix:
                # Operator convention for mixing on this setup.
                return (requested or "Water Mix").strip() or "Water Mix"

            candidate = (requested or default_liquid_class or "").strip()
            if not candidate:
                return "Water Free Single"

            bad = {
                "default init fca",
                "default init air fca",
                "default init",
            }
            if candidate.lower() in bad:
                return "Water Free Single"
            # "Water Mix" is valid for mix steps, but not reliable for aspirate/dispense.
            if candidate.lower() == "water mix":
                return "Water Free Single"
            return candidate

        def _normalize_well_offset_expr(offset_value) -> str:
            """Normalize common malformed loop-offset expressions to FC-compatible forms."""
            if offset_value is None:
                return "0"
            expr = str(offset_value).strip()
            if not expr:
                return "0"
            if re.fullmatch(r"[+-]?\d+", expr):
                return expr

            # Local-model artifact: "<loop_var> + (col-1)*8" where col is undefined.
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*\+\s*\(col-1\)\*8", expr)
            if m:
                return f"({m.group(1)})*8"

            # FC accepts arithmetic expressions here; keep loop-variable formulas intact.
            if re.search(r"[A-Za-z_][A-Za-z0-9_]*", expr) and re.search(r"[+\-*/()]", expr):
                return expr

            # Last guard: if expression is still a plain identifier, convert only known loop-ish
            # symbols and otherwise fall back to zero to avoid FC parse failures.
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
                return "0"
            return expr

        # CGA device defaults (for RGA gripper operations)
        cga_config = self.config.get("cga_device", {})
        cga_device = cga_config.get("alias", "Instrument=1/Device=CGA:1")
        cga_available_id = cga_config.get("available_id", cga_device)
        cga_get_fingers_labware = cga_config.get("get_fingers_labware", "Centric[001]")
        cga_drop_fingers_labware = cga_config.get("drop_fingers_labware", "FES Centric Nest[001]")

        # LiHa device defaults (separate from MCA384)
        liha_config = self.config.get("liha_device", {})
        liha_device = liha_config.get("alias", "Instrument=1/Device=LIHA:1")
        liha_available_id = liha_config.get("available_id", liha_device)
        liha_diti_type = liha_config.get("diti_type", "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS")
        liha_waste_labware = liha_config.get("waste_labware", "FCA Thru Deck Waste Chute_1")

        params = {
            "LineNumber": str(step.line_number or 0),
            "GroupLineNumber": "0",  # Always 0 for individual steps (matches Tecan convention)
            "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
            "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
        }

        # Handle GenericStep - use parameters dict directly
        if isinstance(step, GenericStep):
            # Check if this is a get_head_adapter step to update adapter tracking
            step_type_lower = step.step_type.lower()
            if "get_head_adapter" in step_type_lower:
                labware_name = step.parameters.get("labware_name", "")
                self._current_adapter_config = _get_adapter_config(labware_name)
            elif "drop_head_adapter" in step_type_lower:
                self._current_adapter_config = None

            for key, value in step.parameters.items():
                # Convert snake_case to PascalCase for template placeholders
                pascal_key = ''.join(word.capitalize() for word in key.split('_'))
                params[pascal_key] = str(value) if value is not None else ""

            # Add defaults if not specified
            params.setdefault("DeviceAlias", default_device)
            params.setdefault("AvailableID", default_available_id)
            params.setdefault("LiquidClassName", default_liquid_class)

            # Add adapter-aware defaults for MCA operations
            adapter_params = self._get_adapter_params(default_available_id)
            for key, value in adapter_params.items():
                params.setdefault(key, value)

            return params

        match step.step_type:
            case StepType.ADD_LABWARE:
                # Resolve variable-based labware types (e.g. "McaTipType" → "MCA384, 50ul")
                resolved_type = self._variable_values.get(step.labware_type, step.labware_type)
                # Track labware label -> type for later lookup (e.g. DitiType in GetTips)
                self._labware_types[step.label] = step.labware_type
                # Track placement so we can infer cover-site moves (e.g. placing a plate onto a magnet plate).
                try:
                    self._labware_placements[(step.location, int(step.position))] = step.label
                except Exception:
                    pass
                params.update({
                    "LabwareType": resolved_type,
                    "LabwareLable": step.label,  # Note: Tecan uses "Lable" not "Label"
                    "Location": step.location,
                    "Position": str(step.position),
                    "Rotation": str(step.rotation),
                    "HasLid": str(step.has_lid).lower().capitalize(),
                })

            case StepType.REMOVE_LABWARE:
                params.update({
                    "LabwareName": step.labware_name,
                })

            case StepType.GET_HEAD_ADAPTER:
                # Update adapter tracking
                self._current_adapter_config = _get_adapter_config(step.labware_name)
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "BlowoutAirgap": str(step.blowout_airgap),
                })

            case StepType.DROP_HEAD_ADAPTER:
                # Clear adapter tracking
                self._current_adapter_config = None
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "BlowoutAirgap": str(step.blowout_airgap),
                    "UseSourceAsBackPosition": step.back_position,
                    "AdapterAfterDrop": str(step.adapter_after_drop).lower().capitalize(),
                })

            case StepType.PICK_UP_TIPS:
                # Use adapter config for defaults if available
                adapter = self._current_adapter_config
                default_cols = adapter["partial_columns"] if adapter else 24
                default_rows = adapter["partial_rows"] if adapter else 16
                pcols, prows = self._partial_tip_dims(step, default_cols, default_rows)
                coff = self._partial_tip_col_offset(step, default_cols, step.partial_column_offset)
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "BlowoutAirgap": str(step.blowout_airgap),
                    "PartialColumns": str(pcols),
                    "PartialRows": str(prows),
                    "PartialColumnOffset": str(coff),
                    "PartialRowsOffset": str(step.partial_row_offset),
                    "HeadPosition": step.head_position,
                })

            case StepType.SET_TIPS_BACK:
                # Use adapter config for defaults if available
                adapter = self._current_adapter_config
                default_cols = adapter["partial_columns"] if adapter else 24
                default_rows = adapter["partial_rows"] if adapter else 16
                pcols, prows = self._partial_tip_dims(step, default_cols, default_rows)
                coff = self._partial_tip_col_offset(step, default_cols, step.partial_column_offset)
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "UseSourceAsBackPosition": step.back_position,
                    "PartialColumns": str(pcols),
                    "PartialRows": str(prows),
                    "PartialColumnOffset": str(coff),
                    "PartialRowsOffset": str(step.partial_row_offset),
                    "HeadPosition": step.head_position,
                })

            case StepType.ASPIRATE:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": str(int(step.volume)) if isinstance(step.volume, (int, float)) else str(step.volume),
                })

            case StepType.DISPENSE:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": str(int(step.volume)) if isinstance(step.volume, (int, float)) else str(step.volume),
                })

            case StepType.RGA_TRANSFER_LABWARE:
                # Correctly mapping to the template placeholders found in reference/commands.yaml
                # Template uses: {{LabwareName}}, {{DestinationLocation}}, {{DestinationSite}}, {{FixedSite}}, {{MoveToBase}}

                dest_location = step.destination_location
                dest_site = str(step.destination_site)
                fixed_site = str(step.fixed_site).lower()

                # If the destination coordinates are occupied by a magnet plate, move to its "cover site"
                # using VisionX helpers: GetCoverSiteName/Index(<magnet_label>).
                magnet_label = None
                try:
                    magnet_label = self._labware_placements.get((step.destination_location, int(step.destination_site)))
                except Exception:
                    magnet_label = None

                if magnet_label and magnet_label != step.labware_name:
                    meta = self._resolve_labware_meta(magnet_label)
                    magnet_type = (meta.get("type") or "").lower()
                    is_magnet = meta.get("category") == "magnet_plate" or any(
                        k in magnet_type for k in ("alpaqua", "magniflex", "magnet")
                    )
                    if is_magnet:
                        dest_location = f'GetCoverSiteName("{magnet_label}")'
                        dest_site = f'GetCoverSiteIndex("{magnet_label}")'
                        fixed_site = "true"

                params.update({
                    "LabwareName": step.labware_name,
                    "DestinationLocation": dest_location,
                    "DestinationSite": dest_site,
                    "FixedSite": fixed_site,
                    "MoveToBase": str(step.move_to_base).lower(),
                    "ModuleName": step.module_name if step.module_name else "RGA 1",
                    "AvailableID": step.available_id or default_available_id,
                    # Robotic driver macros require lowercase bools for these specific attributes
                    "IsBreakpoint": str(step.breakpoint).lower(),
                    "IsDisabledForExecution": str(step.disabled).lower(),
                })

            case StepType.LEGACY_DRIVER_MACRO:
                settings = step.execution_settings
                if settings is None or settings == "":
                    settings_element = "<ExecutionSettings />"
                else:
                    escaped = (
                        str(settings)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    settings_element = f"<ExecutionSettings>{escaped}</ExecutionSettings>"
                params.update({
                    "Name": step.name,
                    "ModuleName": step.module_name,
                    "ExecutionSettingsElement": settings_element,
                    # Driver macros require lowercase bools for these attributes
                    "IsBreakpoint": str(step.breakpoint).lower(),
                    "IsDisabledForExecution": str(step.disabled).lower(),
                })



            case StepType.CGA_GET_FINGERS:
                params.update({
                    "LabwareName": step.labware_name or cga_get_fingers_labware,
                    "DeviceAlias": step.device_alias or cga_device,
                    "AvailableID": step.available_id or cga_available_id,
                })

            case StepType.CGA_DROP_FINGERS:
                params.update({
                    "LabwareName": step.labware_name or cga_drop_fingers_labware,
                    "DeviceAlias": step.device_alias or cga_device,
                    "AvailableID": step.available_id or cga_available_id,
                    "UseSourceAsBackPosition": step.use_source_as_back_position,
                })

            case StepType.MCA384_MIX:
                # FluentControl requires a mix-capable liquid subclass for MCA mix.
                mca_mix_liquid_class = _normalize_liquid_class(step.liquid_class, mix=True)
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": mca_mix_liquid_class,
                    "Volume": str(int(step.volume)) if isinstance(step.volume, (int, float)) else str(step.volume),
                    "Cycles": str(step.cycles),
                })

            case StepType.WAIT:
                params.update({
                    "Duration": str(step.duration_seconds),
                })

            case StepType.LIHA_ASPIRATE:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": str(step.volume) if isinstance(step.volume, str) else str(float(step.volume)),
                })
                if step.well_offset is not None:
                    params["WellOffset"] = _normalize_well_offset_expr(step.well_offset)
                meta = self._resolve_labware_meta(step.labware_name)
                if meta:
                    params["_labware_type"] = meta.get("type")
                    params["_labware_wells"] = meta.get("wells")
                    params["_labware_category"] = meta.get("category")

            case StepType.LIHA_DISPENSE:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": str(step.volume) if isinstance(step.volume, str) else str(float(step.volume)),
                })
                if step.well_offset is not None:
                    params["WellOffset"] = _normalize_well_offset_expr(step.well_offset)
                meta = self._resolve_labware_meta(step.labware_name)
                if meta:
                    params["_labware_type"] = meta.get("type")
                    params["_labware_wells"] = meta.get("wells")
                    params["_labware_category"] = meta.get("category")

            case StepType.LIHA_MIX:
                # FluentControl expects a mixing-specific liquid class for LiHa mix.
                # Operator convention (current): "Water Mix".
                mix_liquid_class = _normalize_liquid_class(step.liquid_class, mix=True)
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": mix_liquid_class,
                    "Volume": str(step.volume) if isinstance(step.volume, str) else str(float(step.volume)),
                    "Cycles": str(step.cycles),
                })
                if step.well_offset is not None:
                    params["WellOffset"] = _normalize_well_offset_expr(step.well_offset)
                meta = self._resolve_labware_meta(step.labware_name)
                if meta:
                    params["_labware_type"] = meta.get("type")
                    params["_labware_wells"] = meta.get("wells")
                    params["_labware_category"] = meta.get("category")

            case StepType.LIHA_GET_TIPS:
                # GetTips: LabwareName is empty (tip identity comes from DitiType)
                # Look up tip type from the labware added to worktable
                tip_labware_type = None
                if step.labware_name:
                    raw_type = self._labware_types.get(step.labware_name)
                    if raw_type:
                        # Resolve through variable map if it's a variable reference
                        tip_labware_type = self._variable_values.get(raw_type, raw_type)
                params.update({
                    "LabwareName": "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "_tip_labware_type": tip_labware_type,  # passed to post-processing
                })

            case StepType.LIHA_DROP_TIPS:
                # DropTips always targets the waste chute/disposal target.
                #
                # Local models frequently (and incorrectly) provide the tipbox name here.
                # FluentControl expects a waste chute labware (see extracted example:
                # "FCA Thru Deck Waste Chute_1"), not a tipbox.
                params.update({
                    "LabwareName": liha_waste_labware,
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                })

            case StepType.MCA384_EMPTY_TIPS:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": step.liquid_class or "Empty Tip",
                    "Volume": str(int(step.volume)) if isinstance(step.volume, (int, float)) else str(step.volume),
                })

            case StepType.MCA384_GET_TIPS:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                })

            case StepType.MCA384_DROP_TIPS:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                })

            case StepType.MCA384_MOVE_ARM:
                params.update({
                    "MovementType": step.movement_type,
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                })

            case StepType.LIHA_EMPTY_TIPS:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": step.liquid_class or "Empty Tip",
                })

            case StepType.COMMENT:
                params.update({
                    "Comment": step.comment,
                })

            case StepType.USER_PROMPT:
                params.update({
                    "Prompt": step.prompt,
                    "AutoClose": str(step.timeout > 0),
                    "Timeout": str(step.timeout),
                })

            case StepType.START_TIMER:
                params.update({
                    "Timer": str(step.timer),
                })

            case StepType.WAIT_FOR_TIMER:
                params.update({
                    "Timer": str(step.timer),
                    "Duration": str(step.duration_seconds),
                })

            case StepType.EXPORT_VARIABLE:
                params.update({
                    "Variables": self._string_objects_xml(step.variables),
                    "ExportFile": f'"{self._xml_escape(step.export_file)}"',
                    "WriteHeader": self._bool_text(step.write_header),
                    "ReplaceExistingFile": self._bool_text(step.replace_existing_file),
                    "ExportStringsWithQuotes": self._bool_text(step.export_strings_with_quotes),
                    "DelimiterCode": str(step.delimiter_code),
                })

            case StepType.IMPORT_VARIABLE:
                params.update({
                    "Variables": self._string_objects_xml(step.variables),
                    "ImportFile": f'"{self._xml_escape(step.import_file)}"',
                    "ReadLine": self._bool_text(step.read_line),
                    "Line": str(step.line),
                    "StartInColumn": self._bool_text(step.start_in_column),
                    "Column": str(step.column),
                    "HasHeader": self._bool_text(step.has_header),
                    "DelimiterCode": str(step.delimiter_code),
                })

            case StepType.QUERY_VARIABLE:
                params.update({
                    "Name": step.variable_name,
                    "QueryPrompt": step.query_prompt,
                    "LimitRange": self._bool_text(step.limit_range),
                })

            case StepType.EXECUTE_APPLICATION:
                params.update({
                    "Application": self._xml_escape(step.application),
                    "Arguments": self._xml_escape(step.arguments),
                    "Wait": self._bool_text(step.wait),
                    "StoreReturn": self._bool_text(step.store_return),
                    "Variable": self._xml_escape(step.variable),
                })

            case StepType.DELAY:
                params.update({
                    "Delay": str(step.delay),
                })

            case StepType.SET_LOCATION:
                params.update({
                    "Labware": step.labware,
                    "Location": step.location,
                    "Site": str(step.site),
                    "Rotation": str(step.rotation),
                })

            case StepType.SUBROUTINE:
                params.update({
                    "SubRoutine": f'"{self._xml_escape(step.subroutine)}"',
                    "ExecutionMode": self._xml_escape(step.execution_mode),
                    "VariableMappingsStart": self._variable_mappings_xml(step.variable_mappings_start),
                    "VariableMappingsEnd": self._variable_mappings_xml(step.variable_mappings_end),
                })

            case StepType.SET_VARIABLE:
                # Based on extracted template: Name, Value
                # String variable values must be quoted: <Value>"96 Well Flat"</Value>
                val = step.value
                val_str = str(val)
                is_numeric = isinstance(val, (int, float))
                if not is_numeric and isinstance(val, str):
                    # Check if it's a numeric string
                    is_numeric = val.replace('.', '', 1).lstrip('-').isdigit()
                if not is_numeric:
                    val_str = f'"{val_str}"'
                params.update({
                    "Name": step.variable_name,
                    "Value": val_str,
                })

            case StepType.CALCULATE_VARIABLE:
                # Tecan has no separate CalculateVariable command — render as
                # SetVariable with an expression value that FluentControl evaluates.
                op_map = {
                    "add": "+",
                    "subtract": "-",
                    "sub": "-",
                    "multiply": "*",
                    "mul": "*",
                    "divide": "/",
                    "div": "/",
                    "+": "+",
                    "-": "-",
                    "*": "*",
                    "/": "/",
                }
                op_sym = op_map.get(str(step.operation or "").strip().lower(), "+")
                expr = f"{step.operand_a} {op_sym} {step.operand_b}"
                params.update({
                    "Name": step.target_variable,
                    "Value": expr,
                })

            case StepType.LOOP:
                # Loop rendering is handled separately in _render_step
                pass

        # Add adapter params for MCA operations that need AdapterData
        adapter_params = self._get_adapter_params(default_available_id)
        for key, value in adapter_params.items():
            params.setdefault(key, value)

        return params

    def _get_adapter_params(self, default_available_id: str) -> dict:
        """Get adapter-specific parameters for MCA commands."""
        if self._current_adapter_config:
            adapter = self._current_adapter_config
            # Build UsableTips XML based on adapter type
            if adapter["name"] == "EVA":
                usable_tips_xml = (
                    '                <UsableTips>\n'
                    '                  <UsableTips>All</UsableTips>\n'
                    '                </UsableTips>\n'
                )
                sort_number = "50"
                mount_column_row_wise = "false"
            else:
                # 384 Combo
                usable_tips_xml = (
                    '                <UsableTips>\n'
                    '                  <UsableTips>All</UsableTips>\n'
                    '                  <UsableTips>Column</UsableTips>\n'
                    '                  <UsableTips>Row</UsableTips>\n'
                    '                </UsableTips>\n'
                )
                sort_number = "10"
                mount_column_row_wise = "true"

            return {
                "BlowoutAirgap": "0",
                "HeadPosition": "Left",
                "PartialColumns": str(adapter["partial_columns"]),
                "PartialRows": str(adapter["partial_rows"]),
                "UseSourceAsBackPosition": "BackToPosition",
                "AdapterAfterDrop": "False",
                "LastTipXPosition": str(adapter["last_tip_x"]),
                "LastTipYPosition": str(adapter["last_tip_y"]),
                "AvailableID": default_available_id,
                # AdapterData block placeholders
                "AdapterName": adapter["display_name"],
                "CanMountTecanDiTis": str(adapter["can_mount_tecan_ditis"]).lower(),
                "AdapterXCount": str(adapter["x_count"]),
                "AdapterYCount": str(adapter["y_count"]),
                "AdapterXSpacing": str(adapter["x_spacing"]),
                "AdapterYSpacing": str(adapter["y_spacing"]),
                "AdapterToolId": adapter["tool_id"],
                "AdapterUsableTipsXml": usable_tips_xml,
                "AdapterSortNumber": sort_number,
                "AdapterMountColumnRowWise": mount_column_row_wise,
            }
        else:
            # No adapter mounted - use 384 Combo defaults
            return {
                "BlowoutAirgap": "0",
                "HeadPosition": "Left",
                "PartialColumns": "24",
                "PartialRows": "16",
                "UseSourceAsBackPosition": "BackToPosition",
                "AdapterAfterDrop": "False",
                "LastTipXPosition": "24",
                "LastTipYPosition": "16",
                "AvailableID": default_available_id,
                # AdapterData block placeholders (384 Combo defaults)
                "AdapterName": "384 Tips Combo (Partial Tips)",
                "CanMountTecanDiTis": "false",
                "AdapterXCount": "24",
                "AdapterYCount": "16",
                "AdapterXSpacing": "4.5",
                "AdapterYSpacing": "4.5",
                "AdapterToolId": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi384.Combo",
                "AdapterUsableTipsXml": (
                    '                <UsableTips>\n'
                    '                  <UsableTips>All</UsableTips>\n'
                    '                  <UsableTips>Column</UsableTips>\n'
                    '                  <UsableTips>Row</UsableTips>\n'
                    '                </UsableTips>\n'
                ),
                "AdapterSortNumber": "10",
                "AdapterMountColumnRowWise": "true",
            }

    # LiHa step types that need XML post-processing
    _LIHA_STEP_TYPES = {
        StepType.LIHA_ASPIRATE, StepType.LIHA_DISPENSE, StepType.LIHA_MIX,
        StepType.LIHA_GET_TIPS, StepType.LIHA_DROP_TIPS, StepType.LIHA_EMPTY_TIPS,
    }

    def _is_liha_step(self, step: Step, command_id: str | None) -> bool:
        if hasattr(step, "step_type"):
            step_type = step.step_type
            if isinstance(step_type, StepType):
                return step_type in self._LIHA_STEP_TYPES
            if isinstance(step_type, str):
                if step_type in (t.value for t in StepType):
                    return StepType(step_type) in self._LIHA_STEP_TYPES
                if step_type.startswith("liha_"):
                    return True
        return bool(command_id and command_id.startswith("Liha"))

    def _build_liha_volumes_xml(self, volume: str, num_channels: int = 8) -> str:
        """Build XML fragment for LiHa per-channel volumes."""
        entries = []
        for _ in range(num_channels):
            entries.append(
                f'          <Object Type="System.String">\n'
                f'            <string>{volume}</string>\n'
                f'          </Object>'
            )
        return "\n".join(entries)

    def _build_liha_tips_xml(self, num_tips: int = 8) -> str:
        """Build XML fragment for LiHa tip selection (all tips, consecutive)."""
        entries = []
        for i in range(num_tips):
            entries.append(
                f'                  <Object Type="System.Int32">\n'
                f'                    <int>{i}</int>\n'
                f'                  </Object>'
            )
        return "\n".join(entries)

    def _build_liha_well_selection(self, num_channels: int = 8, mode: str = "range") -> tuple:
        """Build serialized well indexes and well string for LiHa.

        Returns (SerializedWellIndexes, SelectedWellsString) for column A1-H1.
        Tecan uses range encoding: start&gt;step&gt;end;
        """
        if mode == "repeat_single":
            indexes = ";".join(["0"] * num_channels) + ";"
            wells = f"{num_channels} * A1"
            return indexes, wells

        # Default: wells 0 through num_channels-1 with step 1
        indexes = f"0&gt;1&gt;{num_channels - 1};"
        first = f"{chr(65)}1"
        last = f"{chr(65 + num_channels - 1)}1"
        wells = f"{first} - {last}"
        return indexes, wells

    def _resolve_labware_type(self, label: Optional[str]) -> Optional[str]:
        if not label:
            return None
        raw_type = self._labware_types.get(label)
        if not raw_type:
            return None
        return self._variable_values.get(raw_type, raw_type)

    def _resolve_labware_meta(self, label: Optional[str]) -> dict:
        labware_type = self._resolve_labware_type(label)
        if not labware_type:
            return {}
        meta = self.labware_reference.get(labware_type, {})
        if not meta:
            return {"type": labware_type}
        meta = dict(meta)
        meta["type"] = labware_type
        return meta

    def _post_process_liha_xml(self, xml: str, step: Step, params: dict) -> str:
        """Replace hardcoded values in LiHa template XML with actual values."""
        num_channels = 8
        volume = params.get("Volume", "10")

        # Replace hardcoded volumes block (8 entries of <string>NNN</string>)
        if step.step_type in (StepType.LIHA_ASPIRATE, StepType.LIHA_DISPENSE, StepType.LIHA_MIX):
            new_volumes = self._build_liha_volumes_xml(volume, num_channels)
            # Match the <Volumes>...</Volumes> block and replace its content
            xml = re.sub(
                r'(<Volumes>\s*)(?:<Object Type="System\.String">\s*<string>[^<]*</string>\s*</Object>\s*)+(\s*</Volumes>)',
                lambda m: m.group(1) + "\n" + new_volumes + "\n        " + m.group(2).strip(),
                xml,
                flags=re.DOTALL
            )

        # Replace hardcoded SelectedTipsIndexes block
        # First, find how many tips are in the template and replace with all 8
        new_tips = self._build_liha_tips_xml(num_channels)
        xml = re.sub(
            r'(<SelectedTipsIndexes>\s*)(?:<Object Type="System\.Int32">\s*<int>\d+</int>\s*</Object>\s*)+(\s*</SelectedTipsIndexes>)',
            lambda m: m.group(1) + "\n" + new_tips + "\n                " + m.group(2).strip(),
            xml,
            flags=re.DOTALL
        )

        # Replace hardcoded well indexes for aspirate/dispense/mix
        if step.step_type in (StepType.LIHA_ASPIRATE, StepType.LIHA_DISPENSE, StepType.LIHA_MIX):
            labware_wells = params.get("_labware_wells")
            labware_category = params.get("_labware_category")
            labware_type = str(params.get("_labware_type") or "").lower()
            # Fallback heuristic: treat trough/reservoir/waste-style labware as single well
            # even when reference metadata is missing/incomplete.
            inferred_single = any(
                token in labware_type
                for token in ("trough", "reservoir", "waste", "25ml", "100ml", "300ml")
            )
            is_single_well = labware_wells == 1 or labware_category == "reservoir" or inferred_single
            mode = "repeat_single" if is_single_well else "range"
            indexes, wells = self._build_liha_well_selection(num_channels, mode=mode)
            xml = re.sub(
                r'<SerializedWellIndexes>[^<]*</SerializedWellIndexes>',
                f'<SerializedWellIndexes>{indexes}</SerializedWellIndexes>',
                xml
            )
            xml = re.sub(
                r'<SelectedWellsString>[^<]*</SelectedWellsString>',
                f'<SelectedWellsString>{wells}</SelectedWellsString>',
                xml
            )
            # Some extracted templates contain non-zero hardcoded offsets.
            # Force explicit offset: use provided value, otherwise reset to 0.
            well_offset = params.get("WellOffset", "0")
            xml = re.sub(
                r'<WellOffset>[^<]*</WellOffset>',
                f'<WellOffset>{well_offset}</WellOffset>',
                xml,
                count=1
            )

        # Replace hardcoded cycles for mix
        if step.step_type == StepType.LIHA_MIX:
            cycles = params.get("Cycles", "10")
            xml = re.sub(
                r'<Cycles>\d+</Cycles>',
                f'<Cycles>{cycles}</Cycles>',
                xml,
                count=1
            )

        # Fix DitiType AvailableID for LiHa tips (should use TOOLTYPE identifier)
        # Derive from the tip labware type placed on worktable, fall back to config default
        if step.step_type in (StepType.LIHA_GET_TIPS, StepType.LIHA_DROP_TIPS):
            tip_labware_type = params.get("_tip_labware_type")
            if tip_labware_type:
                diti_type_id = f"TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:{tip_labware_type}"
            else:
                liha_config = self.config.get("liha_device", {})
                diti_type_id = liha_config.get("diti_type", "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS")
            xml = re.sub(
                r'(<DitiType>\s*<AvailableID>)[^<]*(</AvailableID>)',
                lambda m: m.group(1) + diti_type_id + m.group(2),
                xml
            )

        return xml

    def _fill_template(self, template: str, params: dict) -> str:
        """Fill in {{placeholder}} values in template."""
        result = template
        for key, value in params.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result

    # ------------------------------------------------------------------
    # Labware-name fuzzy correction
    # ------------------------------------------------------------------

    _KNOWN_LABWARE_NAMES: Optional[set] = None

    @classmethod
    def _get_known_labware_set(cls) -> set:
        if cls._KNOWN_LABWARE_NAMES is not None:
            return cls._KNOWN_LABWARE_NAMES
        try:
            from ..catalog.database import get_database
            db = get_database()
            all_lw = db.get_all_labware()
            cls._KNOWN_LABWARE_NAMES = {lw["name"] for lw in all_lw}
        except Exception:
            cls._KNOWN_LABWARE_NAMES = set()
        return cls._KNOWN_LABWARE_NAMES

    @classmethod
    def _fuzzy_match_labware(cls, name: str, cutoff: float = 0.6) -> Optional[str]:
        """Return the closest labware name from the DB if similarity >= cutoff."""
        known = cls._get_known_labware_set()
        if not known or not name:
            return None
        matches = difflib.get_close_matches(name, known, n=1, cutoff=cutoff)
        return matches[0] if matches else None

    def _normalize_labware_names(self, protocol: Protocol) -> None:
        """Auto-correct labware type names that are close but not exact DB matches.

        Walks all add_labware steps (literal labware_type) and set_variable steps
        (values that look like labware types) and replaces approximate names with the
        closest DB match. This fixes the most common FluentControl validation error
        category without requiring the LLM to be perfectly accurate.
        """
        known = self._get_known_labware_set()

        corrections: list[tuple[str, str]] = []  # (old, new) for logging
        variable_names = {str(v) for v in (protocol.variables or []) if isinstance(v, str)}

        def _iter_steps_recursive(steps):
            for st in steps or []:
                yield st
                if self._step_type_name(st) == "loop":
                    yield from _iter_steps_recursive(getattr(st, "steps", []) or [])

        def _try_correct(value: str) -> str:
            if not value or value in known:
                return value
            # Skip values that look like pure numbers or expressions
            stripped = value.strip()
            if not stripped or stripped.replace(".", "").replace("-", "").isdigit():
                return value
            # Skip protocol variable references (e.g., "MagnetPlateType")
            if stripped in variable_names or stripped.endswith("Type"):
                return value
            # Skip expression-like values
            if any(ch in stripped for ch in "()+-*/="):
                return value
            match = self._fuzzy_match_labware(stripped)
            if match and match != stripped:
                corrections.append((stripped, match))
                return match
            return value

        def _prefer_tip_variant(value: str, family: str) -> str:
            """Coerce common tip-type near-misses to runnable variants."""
            if not value:
                return value
            low = value.lower()
            if family == "mca":
                if "mca" not in low:
                    return value
                # Collect valid MCA96 Box candidates from known labware. Sort by
                # length so the canonical short name (e.g. "MCA96, 200ul, Box")
                # is preferred over longer variants ("MCA96, 200ul Wide, Box",
                # "MCA96, 200ul Wide Filtered, Box", etc.).
                candidates = sorted(
                    (n for n in known if "mca96" in n.lower() and "box" in n.lower()),
                    key=lambda n: (len(n), n),
                )
                # If already a valid MCA96 Box type, no correction needed
                if value in candidates:
                    return value
                # Correct MCA384 types or MCA96 types missing 'Box' suffix
                preferred_tokens = ["1000ul", "200ul", "100ul", "50ul", "10ul"]
                for tok in preferred_tokens:
                    if tok in low:
                        for cand in candidates:
                            if tok in cand.lower():
                                corrections.append((value, cand))
                                return cand
                for fallback in ("MCA96, 100ul, Box", "MCA96, 200ul, Box"):
                    if fallback in known:
                        corrections.append((value, fallback))
                        return fallback
            if family == "fca":
                if "fca" not in low or "sbs" in low:
                    return value
                # Sort candidates by length so the canonical short name (e.g.
                # "FCA, 1000ul SBS") is preferred over longer filtered/wide
                # variants.
                candidates = sorted(
                    (n for n in known if "fca" in n.lower() and "sbs" in n.lower()),
                    key=lambda n: (len(n), n),
                )
                preferred_tokens = ["1000ul", "200ul", "50ul", "10ul"]
                matched_token = next((tok for tok in preferred_tokens if tok in low), "")
                for tok in preferred_tokens:
                    if tok in low:
                        for cand in candidates:
                            if tok in cand.lower():
                                corrections.append((value, cand))
                                return cand
                for fallback in ("FCA, 1000ul SBS", "FCA, 200ul SBS"):
                    if fallback in known or (matched_token and matched_token in fallback.lower()):
                        corrections.append((value, fallback))
                        return fallback
            return value

        # Identify tip labels actually used by operations so we can coerce to compatible variants.
        mca_tip_labels: set[str] = set()
        fca_tip_labels: set[str] = set()
        for group in protocol.groups:
            for step in _iter_steps_recursive(group.steps):
                stype = self._step_type_name(step)
                label = getattr(step, "labware_name", None)
                if not isinstance(label, str) or not label:
                    continue
                if stype in {"pick_up_tips", "set_tips_back"}:
                    mca_tip_labels.add(label)
                elif stype in {"liha_get_tips", "liha_drop_tips"}:
                    fca_tip_labels.add(label)

        add_by_label: dict[str, AddLabwareStep] = {}
        set_var_by_name: dict[str, SetVariableStep] = {}
        default_var_values = getattr(protocol, "variable_defaults", {}) or {}
        for group in protocol.groups:
            for step in group.steps:
                stype = self._step_type_name(step)
                if stype == "add_labware":
                    add_by_label[step.label] = step
                elif stype == "set_variable":
                    set_var_by_name[step.variable_name] = step

        for label in mca_tip_labels:
            add_step = add_by_label.get(label)
            if not add_step:
                continue
            src = add_step.labware_type
            if src in set_var_by_name and isinstance(set_var_by_name[src].value, str):
                set_var_by_name[src].value = _prefer_tip_variant(str(set_var_by_name[src].value), "mca")
            elif src in default_var_values and isinstance(default_var_values[src], str):
                default_var_values[src] = _prefer_tip_variant(str(default_var_values[src]), "mca")
            else:
                add_step.labware_type = _prefer_tip_variant(str(src), "mca")

        for label in fca_tip_labels:
            add_step = add_by_label.get(label)
            if not add_step:
                continue
            src = add_step.labware_type
            if src in set_var_by_name and isinstance(set_var_by_name[src].value, str):
                set_var_by_name[src].value = _prefer_tip_variant(str(set_var_by_name[src].value), "fca")
            elif src in default_var_values and isinstance(default_var_values[src], str):
                default_var_values[src] = _prefer_tip_variant(str(default_var_values[src]), "fca")
            else:
                add_step.labware_type = _prefer_tip_variant(str(src), "fca")

        for group in protocol.groups:
            for step in _iter_steps_recursive(group.steps):
                stype = self._step_type_name(step)
                if stype == "add_labware" and step.labware_type:
                    step.labware_type = _try_correct(step.labware_type)
                elif stype == "set_variable" and isinstance(step.value, str):
                    var_name = (step.variable_name or "").lower()
                    if any(tok in var_name for tok in ("type", "labware", "plate", "tip", "reservoir", "trough")):
                        step.value = _try_correct(step.value)

        for var_name, value in list(default_var_values.items()):
            if not isinstance(value, str):
                continue
            low_name = str(var_name).lower()
            if any(tok in low_name for tok in ("type", "labware", "plate", "tip", "reservoir", "trough")):
                default_var_values[var_name] = _try_correct(value)

        if corrections:
            for old, new in corrections:
                print(f"  [labware-fix] '{old}' -> '{new}'")

    def _validate_catalog_names_known(self, protocol: Protocol) -> None:
        known = self._get_known_labware_set()
        if not known:
            return
        variable_names = {str(v) for v in (protocol.variables or []) if isinstance(v, str)}
        default_var_values = getattr(protocol, "variable_defaults", {}) or {}

        def _iter_steps_recursive(steps):
            for st in steps or []:
                yield st
                if self._step_type_name(st) == "loop":
                    yield from _iter_steps_recursive(getattr(st, "steps", []) or [])

        def _resolve(value: str) -> str:
            if value in variable_names:
                resolved = default_var_values.get(value)
                if isinstance(resolved, str):
                    return resolved
            return value

        def _suggestions(name: str) -> str:
            matches = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
            return ", ".join(f"'{m}'" for m in matches) if matches else ""

        for group in protocol.groups:
            for step in _iter_steps_recursive(group.steps):
                if self._step_type_name(step) != "add_labware":
                    continue
                raw = step.labware_type
                if not isinstance(raw, str) or not raw.strip():
                    continue
                resolved = _resolve(raw.strip())
                if resolved in known:
                    continue
                if any(ch in resolved for ch in "()+-*/="):
                    continue
                suggestions = _suggestions(resolved)
                label = getattr(step, "label", None) or "?"
                hint = f" Did you mean {suggestions}?" if suggestions else ""
                raise RenderError(
                    f"Unknown labware catalog {resolved!r} for placement {label!r}.{hint}"
                )

    def _normalize_for_magnet_cover_site(self, protocol: Protocol) -> None:
        """Best-effort normalization to reduce common FluentControl validation failures.

        If a protocol transfers a plate onto a magnet plate position (which the renderer turns
        into a GetCoverSiteName/GetCoverSiteIndex transfer), FluentControl may reject some plate
        types on that cover-site carrier. We force those plate types to the local known-good
        ABgene SuperPlate used in passing reference methods.
        """
        DESIRED = "96_ABgene_SuperPlate_Thermo_AB2800"

        # Find labware placements from the worktable setup group.
        setup_group = None
        for g in protocol.groups:
            if "worktable" in (g.name or "").lower():
                setup_group = g
                break
        if setup_group is None:
            return

        add_by_label: dict[str, AddLabwareStep] = {}
        magnet_sites: set[tuple[str, int]] = set()
        magnets: list[AddLabwareStep] = []
        for st in setup_group.steps:
            if self._step_type_name(st) != "add_labware":
                continue
            label = st.label or ""
            add_by_label[label] = st
            # Heuristic magnet detection (works even before variable resolution).
            lt = (st.labware_type or "").lower()
            is_magnet = ("magnet" in (label or "").lower()) or any(k in lt for k in ("alpaqua", "magniflex", "magnet", "lv_alpaqua"))
            if not is_magnet:
                continue
            try:
                magnet_sites.add((st.location, int(st.position)))
                magnets.append(st)
            except Exception:
                continue
        if not magnet_sites:
            return

        # Local fluent methods typically place the Alpaqua magnet on Nest61mm_Pos position 3.
        # If the model placed it elsewhere (commonly pos 7) the resulting cover site can be out of LiHa range.
        # Relocate it to pos 3 when possible and patch any transfers targeting the old position.
        try:
            occ_n61 = {
                (st.location, int(st.position)): st
                for st in setup_group.steps
                if self._step_type_name(st) == "add_labware" and st.location and str(st.position).isdigit()
            }
        except Exception:
            occ_n61 = {}
        for m in magnets:
            if (m.location or "") != "Nest61mm_Pos":
                continue
            try:
                old_pos = int(m.position)
            except Exception:
                continue
            preferred_pos = 3
            if old_pos == preferred_pos:
                continue
            if ("Nest61mm_Pos", preferred_pos) in occ_n61:
                continue
            # Move magnet
            m.position = preferred_pos
            # Patch transfers that moved labware onto the magnet position.
            for g in protocol.groups:
                for st in g.steps:
                    if self._step_type_name(st) == "rga_transfer_labware" and (st.destination_location or "") == "Nest61mm_Pos":
                        try:
                            if int(st.destination_site) == old_pos:
                                st.destination_site = preferred_pos
                        except Exception:
                            continue
            # Update occupancy and magnet site set.
            magnet_sites.discard(("Nest61mm_Pos", old_pos))
            magnet_sites.add(("Nest61mm_Pos", preferred_pos))

        # Collect all transfers whose destination is one of the magnet sites.
        moved_labels: set[str] = set()
        for g in protocol.groups:
            for st in g.steps:
                if self._step_type_name(st) != "rga_transfer_labware":
                    continue
                try:
                    dst = (st.destination_location, int(st.destination_site))
                except Exception:
                    continue
                if dst in magnet_sites:
                    moved_labels.add(st.labware_name)
        if not moved_labels:
            return

        # Helper: override variable assignments (or direct labware_type literals).
        def _override_type(name_or_type: str) -> None:
            # If it's a variable, prefer updating set_variable steps.
            if name_or_type in protocol.variables or name_or_type.endswith("Type"):
                for g in protocol.groups:
                    for s in g.steps:
                        if self._step_type_name(s) == "set_variable" and s.variable_name == name_or_type:
                            s.value = DESIRED
                            break
                # Ensure variable is declared even if the model forgot it.
                if name_or_type not in protocol.variables:
                    protocol.variables.append(name_or_type)
                return

        for lbl in moved_labels:
            add = add_by_label.get(lbl)
            if not add:
                continue
            lw_type = add.labware_type or ""
            if isinstance(lw_type, str):
                # If literal is the problematic default, replace directly.
                if lw_type.strip() == "96 Well Flat":
                    add.labware_type = DESIRED
                    continue
                _override_type(lw_type)

    def _resolve_liquid_class_guid(self, name: str) -> str:
        """Look up a liquid-class GUID by name.

        Source of truth is the SQL catalog index — the canonical mapping
        comes from the .xlqc files on disk. Falls back to the hardcoded
        ``generation.yaml`` value when the index is empty or the name
        isn't found, so this works on dev boxes / CI without an FC
        install.
        """
        try:
            from ..catalog import index_exists, resolve_liquid_class_by_name
            if index_exists():
                entry = resolve_liquid_class_by_name(name)
                if entry is not None:
                    return entry.guid
        except Exception:
            pass
        return self.config["liquid_class"]["guid"]

    def render_to_file(self, protocol: Protocol, output_path: Path) -> Path:
        """
        Render protocol to file.

        Args:
            protocol: The protocol to render
            output_path: Output file path

        Returns:
            Path to written file
        """
        xml = self.render(protocol)

        # Ensure .xscr extension
        if output_path.suffix != ".xscr":
            output_path = output_path.with_suffix(".xscr")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(xml, encoding="utf-8")
        rewrite_checksum_in_place(output_path)

        return output_path
