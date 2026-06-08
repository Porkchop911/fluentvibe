"""
Intermediate Representation (IR) schema for Tecan protocols.

This module defines Pydantic models that represent protocol steps
in a structured, validated format before rendering to XML.
"""

from typing import Optional, Literal, Union, Annotated
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class StepType(str, Enum):
    """Enumeration of all supported step types."""
    ADD_LABWARE = "add_labware"
    REMOVE_LABWARE = "remove_labware"
    GET_HEAD_ADAPTER = "get_head_adapter"
    DROP_HEAD_ADAPTER = "drop_head_adapter"
    PICK_UP_TIPS = "pick_up_tips"
    SET_TIPS_BACK = "set_tips_back"
    ASPIRATE = "aspirate"
    DISPENSE = "dispense"
    # RGA (Robotic Gripper Arm) step types
    RGA_TRANSFER_LABWARE = "rga_transfer_labware"
    CGA_GET_FINGERS = "cga_get_fingers"
    CGA_DROP_FINGERS = "cga_drop_fingers"
    # Mixing step type
    MCA384_MIX = "mca384_mix"
    MCA384_EMPTY_TIPS = "mca384_empty_tips"
    MCA384_GET_TIPS = "mca384_get_tips"
    MCA384_DROP_TIPS = "mca384_drop_tips"
    MCA384_MOVE_ARM = "mca384_move_arm"
    # LiHa (Liquid Handler) step types
    LIHA_ASPIRATE = "liha_aspirate"
    LIHA_DISPENSE = "liha_dispense"
    LIHA_MIX = "liha_mix"
    LIHA_GET_TIPS = "liha_get_tips"
    LIHA_DROP_TIPS = "liha_drop_tips"
    # Control flow
    SCRIPT_GROUP = "script_group"
    LOOP = "loop"
    CONDITIONAL = "conditional"
    # Wait/incubation step type
    WAIT = "wait"
    # Variable operations
    SET_VARIABLE = "set_variable"
    CALCULATE_VARIABLE = "calculate_variable"
    # Annotation and control
    COMMENT = "comment"
    USER_PROMPT = "user_prompt"
    START_TIMER = "start_timer"
    WAIT_FOR_TIMER = "wait_for_timer"
    # LiHa empty tips
    LIHA_EMPTY_TIPS = "liha_empty_tips"
    # Variable import/export and runtime query
    EXPORT_VARIABLE = "export_variable"
    IMPORT_VARIABLE = "import_variable"
    QUERY_VARIABLE = "query_variable"
    # External/runtime commands
    EXECUTE_APPLICATION = "execute_application"
    DELAY = "delay"
    # Worktable and routine commands
    SET_LOCATION = "set_location"
    SUBROUTINE = "subroutine"
    # Worklist commands
    WORKLIST_IMPORT = "worklist_import"
    LOAD_WORKLIST = "load_worklist"
    EXECUTE_WORKLIST = "execute_worklist"
    # Device / legacy driver macros (e.g. Inheco ODTC via SiLA-ODTC, inhecoMTC)
    LEGACY_DRIVER_MACRO = "legacy_driver_macro"


class BaseStep(BaseModel):
    """Base class for all protocol steps."""
    step_type: StepType
    line_number: Optional[int] = None  # Auto-assigned if not provided
    disabled: bool = False
    breakpoint: bool = False


class SetVariableStep(BaseStep):
    """Step to set a variable value."""
    step_type: Literal[StepType.SET_VARIABLE] = StepType.SET_VARIABLE
    variable_name: str = Field(..., description="Name of the variable")
    value: Union[float, int, str] = Field(..., description="Value to set (number or string)")


class CalculateVariableStep(BaseStep):
    """Step to calculate a variable value."""
    step_type: Literal[StepType.CALCULATE_VARIABLE] = StepType.CALCULATE_VARIABLE
    target_variable: str = Field(..., description="Variable to store result")
    operation: str = Field(..., description="Operation (Add, Subtract, Multiply, Divide)")
    operand_a: str = Field(..., description="First operand (variable name or value)")
    operand_b: str = Field(..., description="Second operand (variable name or value)")


class AddLabwareStep(BaseStep):
    """Step to add labware to the worktable."""
    step_type: Literal[StepType.ADD_LABWARE] = StepType.ADD_LABWARE
    labware_type: str = Field(..., description="Type of labware (e.g., 'MCA384, 50ul')")
    label: str = Field(..., description="User-defined label for this labware instance")
    location: str = Field(..., description="Location on worktable (e.g., 'Nest7mm_Pos')")
    position: int = Field(..., ge=1, description="Position index within location")
    rotation: int = Field(default=0, ge=0, lt=360, description="Rotation angle in degrees")
    has_lid: bool = Field(default=False, description="Whether labware has a lid")
    initial_volume: float = Field(default=0.0, ge=0, description="Initial volume per well in microliters")


class RemoveLabwareStep(BaseStep):
    """Step to remove labware from the worktable."""
    step_type: Literal[StepType.REMOVE_LABWARE] = StepType.REMOVE_LABWARE
    labware_name: str = Field(..., description="Label of the labware to remove")


class GetHeadAdapterStep(BaseStep):
    """Step to retrieve the head adapter."""
    step_type: Literal[StepType.GET_HEAD_ADAPTER] = StepType.GET_HEAD_ADAPTER
    labware_name: str = Field(..., description="Labware containing the head adapter")
    device_alias: Optional[str] = None  # Uses default if not specified
    available_id: Optional[str] = None
    blowout_airgap: int = Field(default=0, ge=0, description="Air gap during blowout")


class DropHeadAdapterStep(BaseStep):
    """Step to drop the head adapter."""
    step_type: Literal[StepType.DROP_HEAD_ADAPTER] = StepType.DROP_HEAD_ADAPTER
    labware_name: Optional[str] = Field(default=None, description="Adapter to drop (defaults to mounted adapter)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    blowout_airgap: int = Field(default=0, ge=0)
    back_position: str = Field(default="BackToSource", description="Back position mode")
    adapter_after_drop: bool = Field(default=False)


class PickUpTipsStep(BaseStep):
    """Step to pick up tips."""
    step_type: Literal[StepType.PICK_UP_TIPS] = StepType.PICK_UP_TIPS
    labware_name: str = Field(..., description="Labware containing tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    blowout_airgap: int = Field(default=0, ge=0)
    partial_columns: int = Field(default=24, ge=1, le=24, description="Number of columns")
    partial_rows: int = Field(default=16, ge=1, le=16, description="Number of rows")
    head_position: str = Field(default="Left", description="Head position (Left/Right)")


class SetTipsBackStep(BaseStep):
    """Step to return tips."""
    step_type: Literal[StepType.SET_TIPS_BACK] = StepType.SET_TIPS_BACK
    labware_name: Optional[str] = Field(default=None, description="Labware to return tips to (defaults to pickup source)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    back_position: str = Field(default="BackToPosition")
    partial_columns: int = Field(default=24, ge=1, le=24, description="Number of columns")
    partial_rows: int = Field(default=16, ge=1, le=16, description="Number of rows")
    head_position: str = Field(default="Left", description="Head position (Left/Right)")


class AspirateStep(BaseStep):
    """Step to aspirate liquid."""
    step_type: Literal[StepType.ASPIRATE] = StepType.ASPIRATE
    labware_name: str = Field(..., description="Source labware")
    volume: Union[float, str] = Field(..., description="Volume in microliters (value or variable name)")
    liquid_class: Optional[str] = None  # Uses default if not specified
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    columns: Optional[list[int]] = Field(
        default=None,
        description=(
            "1-based MCA plate columns to address for partial-column pipetting "
            "(e.g. [1, 2, 3] or [1, 3, 5]). None addresses the full plate."
        ),
    )


class DispenseStep(BaseStep):
    """Step to dispense liquid."""
    step_type: Literal[StepType.DISPENSE] = StepType.DISPENSE
    labware_name: str = Field(..., description="Destination labware")
    volume: Union[float, str] = Field(..., description="Volume in microliters (value or variable name)")
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    columns: Optional[list[int]] = Field(
        default=None,
        description=(
            "1-based MCA plate columns to address for partial-column pipetting "
            "(e.g. [1, 2, 3] or [1, 3, 5]). None addresses the full plate."
        ),
    )


class RgaTransferLabwareStep(BaseStep):
    """Step to transfer labware using the RGA (Robotic Gripper Arm)."""
    step_type: Literal[StepType.RGA_TRANSFER_LABWARE] = StepType.RGA_TRANSFER_LABWARE
    labware_name: str = Field(..., description="Labware to transfer")
    destination_location: str = Field(..., description="Destination location (e.g., 'Nest61mm_Pos')")
    destination_site: int = Field(..., ge=1, description="Destination site/position number")
    fixed_site: bool = Field(default=True, description="Use fixed site positioning")
    move_to_base: bool = Field(default=False, description="Move to base position after transfer")
    module_name: str = Field(default="RGA 1", description="RGA module name")
    available_id: Optional[str] = None


class CgaGetFingersStep(BaseStep):
    """Step to get gripper fingers from a labware position."""
    step_type: Literal[StepType.CGA_GET_FINGERS] = StepType.CGA_GET_FINGERS
    labware_name: Optional[str] = Field(default=None, description="Labware/adapter to grip fingers from")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class CgaDropFingersStep(BaseStep):
    """Step to drop gripper fingers/release labware."""
    step_type: Literal[StepType.CGA_DROP_FINGERS] = StepType.CGA_DROP_FINGERS
    labware_name: Optional[str] = Field(default=None, description="Labware/adapter to drop fingers to")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    use_source_as_back_position: str = Field(default="BackToPosition", description="Back position mode")


class Mca384MixStep(BaseStep):
    """Step to mix liquid using MCA384 pipetting head."""
    step_type: Literal[StepType.MCA384_MIX] = StepType.MCA384_MIX
    labware_name: str = Field(..., description="Labware containing liquid to mix")
    volume: Union[float, str] = Field(..., description="Mix volume in microliters (value or variable name)")
    cycles: Union[int, str] = Field(default=10, description="Number of mix cycles (value or variable name)")
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384EmptyTipsStep(BaseStep):
    """Step to empty tips using MCA384 pipetting head."""
    step_type: Literal[StepType.MCA384_EMPTY_TIPS] = StepType.MCA384_EMPTY_TIPS
    labware_name: str = Field(..., description="Destination labware (usually Waste)")
    volume: Union[float, str] = Field(..., description="Volume to empty in microliters")
    liquid_class: Optional[str] = Field(default="Empty Tip", description="Liquid class for emptying")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class LihaAspirateStep(BaseStep):
    """Step to aspirate liquid using LiHa."""
    step_type: Literal[StepType.LIHA_ASPIRATE] = StepType.LIHA_ASPIRATE
    labware_name: str = Field(..., description="Source labware")
    volume: Union[float, str] = Field(..., description="Volume in microliters (value or variable name)")
    well_offset: Optional[Union[int, str]] = Field(
        default=None,
        description="Optional well offset expression for LiHa selection"
    )
    selection: Optional[str] = Field(
        default=None,
        description="Deprecated; selection is auto-derived. Use well_offset inside loops."
    )
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class LihaDispenseStep(BaseStep):
    """Step to dispense liquid using LiHa."""
    step_type: Literal[StepType.LIHA_DISPENSE] = StepType.LIHA_DISPENSE
    labware_name: str = Field(..., description="Destination labware")
    volume: Union[float, str] = Field(..., description="Volume in microliters (value or variable name)")
    well_offset: Optional[Union[int, str]] = Field(
        default=None,
        description="Optional well offset expression for LiHa selection"
    )
    selection: Optional[str] = Field(
        default=None,
        description="Deprecated; selection is auto-derived. Use well_offset inside loops."
    )
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class LihaMixStep(BaseStep):
    """Step to mix liquid using LiHa."""
    step_type: Literal[StepType.LIHA_MIX] = StepType.LIHA_MIX
    labware_name: str = Field(..., description="Labware containing liquid to mix")
    volume: Union[float, str] = Field(..., description="Mix volume in microliters (value or variable name)")
    cycles: Union[int, str] = Field(default=10, description="Number of mix cycles (value or variable name)")
    well_offset: Optional[Union[int, str]] = Field(
        default=None,
        description="Optional well offset expression for LiHa selection"
    )
    selection: Optional[str] = Field(
        default=None,
        description="Deprecated; selection is auto-derived. Use well_offset inside loops."
    )
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class LihaGetTipsStep(BaseStep):
    """Step to get tips using LiHa."""
    step_type: Literal[StepType.LIHA_GET_TIPS] = StepType.LIHA_GET_TIPS
    labware_name: Optional[str] = Field(default=None, description="Labware containing tips")
    tip_index: Optional[int] = Field(default=None, description="Tip channel index (0-7)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class LihaDropTipsStep(BaseStep):
    """Step to drop tips using LiHa."""
    step_type: Literal[StepType.LIHA_DROP_TIPS] = StepType.LIHA_DROP_TIPS
    labware_name: Optional[str] = Field(default=None, description="Location to drop tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384GetTipsStep(BaseStep):
    """Step to get mounted MCA384 tips."""
    step_type: Literal[StepType.MCA384_GET_TIPS] = StepType.MCA384_GET_TIPS
    labware_name: Optional[str] = Field(default=None, description="Labware containing tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384DropTipsStep(BaseStep):
    """Step to drop mounted MCA384 tips."""
    step_type: Literal[StepType.MCA384_DROP_TIPS] = StepType.MCA384_DROP_TIPS
    labware_name: Optional[str] = Field(default=None, description="Labware to receive tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384MoveArmStep(BaseStep):
    """Step to move the MCA384 arm."""
    step_type: Literal[StepType.MCA384_MOVE_ARM] = StepType.MCA384_MOVE_ARM
    movement_type: str = Field(default="GlobalZTravel", description="MCA384 arm movement type")
    labware_name: Optional[str] = Field(default=None, description="Reference labware")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class WaitStep(BaseStep):
    """Step to wait/incubate for a specified duration."""
    step_type: Literal[StepType.WAIT] = StepType.WAIT
    duration_seconds: Union[int, float, str] = Field(..., description="Duration to wait in seconds (value or variable name)")
    comment: Optional[str] = Field(default=None, description="Optional comment describing the wait")


class CommentStep(BaseStep):
    """Step to add a comment to the protocol."""
    step_type: Literal[StepType.COMMENT] = StepType.COMMENT
    comment: str = Field(..., description="Comment text")


class UserPromptStep(BaseStep):
    """Step to prompt the operator during execution."""
    step_type: Literal[StepType.USER_PROMPT] = StepType.USER_PROMPT
    prompt: str = Field(..., description="Prompt message shown to operator")
    timeout: int = Field(default=0, ge=0, description="Auto-close timeout in seconds (0 = wait for user)")


class StartTimerStep(BaseStep):
    """Step to start an asynchronous timer."""
    step_type: Literal[StepType.START_TIMER] = StepType.START_TIMER
    timer: int = Field(default=1, ge=1, description="Timer ID")


class WaitForTimerStep(BaseStep):
    """Step to wait until a timer reaches a specified duration."""
    step_type: Literal[StepType.WAIT_FOR_TIMER] = StepType.WAIT_FOR_TIMER
    timer: int = Field(default=1, ge=1, description="Timer ID to wait for")
    duration_seconds: Union[int, float, str] = Field(..., description="Target duration in seconds")


class LihaEmptyTipsStep(BaseStep):
    """Step to empty LiHa tips into a labware (usually waste)."""
    step_type: Literal[StepType.LIHA_EMPTY_TIPS] = StepType.LIHA_EMPTY_TIPS
    labware_name: str = Field(..., description="Destination labware (usually waste)")
    volume: Union[float, str] = Field(default=0, description="Volume to empty")
    liquid_class: Optional[str] = Field(default="Empty Tip", description="Liquid class for emptying")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class ExportVariableStep(BaseStep):
    """Export one or more variables to a file."""
    step_type: Literal[StepType.EXPORT_VARIABLE] = StepType.EXPORT_VARIABLE
    variables: list[str] = Field(default_factory=list, description="Variables to export")
    export_file: str = Field(..., description="Destination file path")
    write_header: bool = Field(default=False, description="Whether to write a header row")
    replace_existing_file: bool = Field(default=False, description="Whether to overwrite an existing file")
    export_strings_with_quotes: bool = Field(default=False, description="Whether string values should be quoted")
    delimiter_code: int = Field(default=59, description="ASCII delimiter code; 59 is semicolon")


class ImportVariableStep(BaseStep):
    """Import one or more variables from a file."""
    step_type: Literal[StepType.IMPORT_VARIABLE] = StepType.IMPORT_VARIABLE
    variables: list[str] = Field(default_factory=list, description="Variables to import")
    import_file: str = Field(..., description="Source file path")
    read_line: bool = Field(default=False, description="Whether to read a specific line")
    line: int = Field(default=1, ge=1, description="1-based line to read when read_line is enabled")
    start_in_column: bool = Field(default=False, description="Whether to start import in a specific column")
    column: int = Field(default=1, ge=1, description="1-based column to start from")
    has_header: bool = Field(default=False, description="Whether the source file has a header row")
    delimiter_code: int = Field(default=59, description="ASCII delimiter code; 59 is semicolon")


class QueryVariableStep(BaseStep):
    """Prompt the operator to provide a variable value."""
    step_type: Literal[StepType.QUERY_VARIABLE] = StepType.QUERY_VARIABLE
    variable_name: str = Field(..., description="Variable to query")
    query_prompt: str = Field(..., description="Prompt text shown to the operator")
    limit_range: bool = Field(default=False, description="Whether to enforce min/max limits")


class ExecuteApplicationStep(BaseStep):
    """Run an external application."""
    step_type: Literal[StepType.EXECUTE_APPLICATION] = StepType.EXECUTE_APPLICATION
    application: str = Field(..., description="Application path or command")
    arguments: str = Field(default="", description="Application arguments")
    wait: bool = Field(default=True, description="Whether to wait for the process to finish")
    store_return: bool = Field(default=False, description="Whether to store the return code")
    variable: str = Field(default="", description="Variable to receive the return code")


class DelayStep(BaseStep):
    """Delay execution for a number of milliseconds."""
    step_type: Literal[StepType.DELAY] = StepType.DELAY
    delay: int = Field(..., ge=0, description="Delay duration in milliseconds")


class SetLocationStep(BaseStep):
    """Move an existing labware item to a specific worktable location."""
    step_type: Literal[StepType.SET_LOCATION] = StepType.SET_LOCATION
    labware: str = Field(..., description="Labware label")
    location: str = Field(..., description="Destination location")
    site: int = Field(..., ge=1, description="Destination site")
    rotation: int = Field(default=0, ge=0, lt=360, description="Rotation angle in degrees")


class VariableMapping(BaseModel):
    """Subroutine variable mapping."""
    target: str = Field(..., description="Target variable in the subroutine")
    source: str = Field(..., description="Source expression or variable in the caller")


class SubRoutineStep(BaseStep):
    """Call a FluentControl subroutine."""
    step_type: Literal[StepType.SUBROUTINE] = StepType.SUBROUTINE
    subroutine: str = Field(..., description="Subroutine path")
    execution_mode: str = Field(default="Synchronous", description="Execution mode")
    variable_mappings_start: list[VariableMapping] = Field(default_factory=list, description="Start mappings")
    variable_mappings_end: list[VariableMapping] = Field(default_factory=list, description="End mappings")


class WorklistColumnMapping(BaseModel):
    """CSV column assignment for FluentControl Convert CSV to GWL."""
    column_name: str = Field(..., description="CSV column letter, e.g. A")
    column_index: int = Field(..., ge=0, description="Zero-based column index")
    gwl_index: str = Field(..., description="FluentControl worklist field name")


class WorklistImportStep(BaseStep):
    """Convert a CSV file into a GWL worklist inside FluentControl."""
    step_type: Literal[StepType.WORKLIST_IMPORT] = StepType.WORKLIST_IMPORT
    csv_path: str
    gwl_path: str
    start_line: int = Field(default=1, ge=1)
    stop_with_last_line: bool = True
    stop_with_line: int = Field(default=1, ge=1)
    separator: str = ","
    columns: list[WorklistColumnMapping] = Field(default_factory=list)


class LoadWorklistStep(BaseStep):
    """Load a GWL file for later execution by FluentControl."""
    step_type: Literal[StepType.LOAD_WORKLIST] = StepType.LOAD_WORKLIST
    gwl_path: str
    liquid_class: Optional[str] = None
    diti_type: str = "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 50ul SBS"
    selected_tips: list[int] = Field(default_factory=lambda: list(range(8)))
    handle_missing_labware: str = "SkipWithoutWarning"
    skip_initial_wash: bool = False
    waste_labware: str = "FCA Thru Deck Waste Chute_1"
    empty_tips_liquid_class: str = "Empty Tip"
    use_legacy_gwl_file_format: bool = False
    ignore_filename_until_run: bool = True
    device_alias: Optional[str] = None
    well_positions: str = Field(default="numeric", description="numeric or alphanumeric")
    dynamic_diti_table: str = ""
    dynamic_diti_handling: bool = False
    airgap_speed: int = 70
    airgap_volume: int = 10


class ExecuteWorklistStep(BaseStep):
    """Execute all loaded worklists since the previous Execute Worklist command."""
    step_type: Literal[StepType.EXECUTE_WORKLIST] = StepType.EXECUTE_WORKLIST
    delete_gwl_scripts: bool = False


class LegacyDriverMacroStep(BaseStep):
    """A FluentControl ``LegacyDriverMacro`` device command.

    This is how external driver modules are invoked from a script — e.g. the
    Inheco underdeck ODTC (module ``SiLA-ODTC``: open/close door, set/execute
    method) or the Inheco MTC (module ``inhecoMTC``). The macro is bound to a
    driver by ``module_name`` and identified by ``name``; ``execution_settings``
    carries the macro payload (e.g. a method name, or
    ``Parameter:MethodsXML:String:File:Annealing.xml``). ``None``/empty renders
    as a self-closing ``<ExecutionSettings />``.

    NOTE: emitting this command does not require the driver to be installed in
    FluentControl, but *running* it on hardware does (the driver software is an
    instrument-side dependency).
    """
    step_type: Literal[StepType.LEGACY_DRIVER_MACRO] = StepType.LEGACY_DRIVER_MACRO
    name: str = Field(..., description="Macro name, e.g. 'SiLA-ODTC_ExecuteMethod'")
    module_name: str = Field(..., description="Driver module/CallName, e.g. 'SiLA-ODTC'")
    execution_settings: Optional[str] = Field(
        default=None, description="Macro payload string; None/empty -> <ExecutionSettings />"
    )


class GenericStep(BaseModel):
    """
    Generic step that accepts any step type from the reference.

    Used for commands extracted from protocols that don't have
    dedicated Pydantic classes yet.
    """
    step_type: str = Field(..., description="Step type from reference")
    line_number: Optional[int] = None
    disabled: bool = False
    breakpoint: bool = False
    # All other parameters stored as dict
    parameters: dict = Field(default_factory=dict, description="Step parameters")

    @property
    def name(self) -> str:
        return self.step_type

    def __init__(self, **data):
        # Extract known fields, put rest in parameters
        known_fields = {'step_type', 'line_number', 'disabled', 'breakpoint', 'parameters'}
        params = {k: v for k, v in data.items() if k not in known_fields}
        if 'parameters' not in data:
            data['parameters'] = params
        else:
            data['parameters'].update(params)
        # Remove extra keys
        data = {k: v for k, v in data.items() if k in known_fields}
        super().__init__(**data)


class ScriptGroupStep(BaseStep):
    """A nested FluentControl script group inside another group."""
    step_type: Literal[StepType.SCRIPT_GROUP] = StepType.SCRIPT_GROUP
    name: str = Field(default="Steps", description="Nested script group name")
    steps: list["Step"] = Field(default_factory=list, description="Steps in this nested group")


class LoopStep(BaseStep):
    """A loop that repeats a sequence of steps."""
    step_type: Literal[StepType.LOOP] = StepType.LOOP
    name: str = Field(default="Loop", description="Name of the loop")
    iterations: int = Field(default=2, ge=1, description="Number of times to repeat")
    loop_variable: Optional[str] = Field(default=None, description="Optional loop variable name")
    number_of_loops: Optional[Union[int, str]] = Field(
        default=None,
        description="Loop count (int or variable name). Defaults to iterations when omitted."
    )
    steps: list["Step"] = Field(default_factory=list, description="Steps to repeat")


class ConditionalStep(BaseStep):
    """A typed if/else conditional block."""
    step_type: Literal[StepType.CONDITIONAL] = StepType.CONDITIONAL
    name: str = Field(default="If", description="Name of the conditional branch")
    left_variable: str = Field(..., description="Declared variable name on the left-hand side")
    operator: str = Field(..., description="Comparator operator")
    right_value: Union[str, int, float, bool] = Field(..., description="Right-hand operand value or variable name")
    right_is_variable: bool = Field(default=False, description="Whether right_value refers to another variable")
    then_steps: list["Step"] = Field(default_factory=list, description="Steps executed when the condition is true")
    else_steps: list["Step"] = Field(default_factory=list, description="Steps executed when the condition is false")


# Union type for all step variants - includes GenericStep for dynamic types
Step = Union[
    AddLabwareStep,
    RemoveLabwareStep,
    GetHeadAdapterStep,
    DropHeadAdapterStep,
    PickUpTipsStep,
    SetTipsBackStep,
    AspirateStep,
    DispenseStep,
    RgaTransferLabwareStep,
    CgaGetFingersStep,
    CgaDropFingersStep,
    Mca384MixStep,
    Mca384EmptyTipsStep,
    Mca384GetTipsStep,
    Mca384DropTipsStep,
    Mca384MoveArmStep,
    LihaAspirateStep,
    LihaDispenseStep,
    LihaMixStep,
    LihaGetTipsStep,
    LihaDropTipsStep,
    LihaEmptyTipsStep,
    WaitStep,
    SetVariableStep,
    CalculateVariableStep,
    CommentStep,
    UserPromptStep,
    StartTimerStep,
    WaitForTimerStep,
    ExportVariableStep,
    ImportVariableStep,
    QueryVariableStep,
    ExecuteApplicationStep,
    DelayStep,
    SetLocationStep,
    SubRoutineStep,
    WorklistImportStep,
    LoadWorklistStep,
    ExecuteWorklistStep,
    LegacyDriverMacroStep,
    ScriptGroupStep,
    LoopStep,
    ConditionalStep,
    GenericStep
]


class Group(BaseModel):
    """A group of related steps in the protocol."""
    name: str = Field(..., description="Group name (e.g., 'Worktable Setup', 'Pipetting')")
    steps: list[Step] = Field(default_factory=list, description="Steps in this group")
    line_number: Optional[int] = None  # Auto-assigned


class Protocol(BaseModel):
    """
    Complete protocol representation.

    This is the top-level IR that gets rendered to XML.
    """
    name: str = Field(..., description="Protocol name")
    comment: str = Field(default="", description="Optional protocol comment")
    variables: list[str] = Field(default_factory=list, description="List of variable names used in protocol")
    variable_defaults: dict[str, Union[float, int, str]] = Field(
        default_factory=dict,
        description="Declared variable defaults emitted into VariableDeclarations",
    )
    groups: list[Group] = Field(default_factory=list, description="Protocol groups")

    # Optional overrides for config defaults
    worktable_guid: Optional[str] = None
    worktable_name: Optional[str] = None
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    file_references: list[str] = Field(default_factory=list, description="External files referenced by the script")

    def total_steps(self) -> int:
        """Count total steps across all groups."""
        return sum(self._count_steps(g.steps) for g in self.groups)

    def _count_steps(self, steps: list[Step]) -> int:
        count = 0
        for step in steps:
            count += 1
            if isinstance(step, LoopStep):
                count += self._count_steps(step.steps)
            elif isinstance(step, ScriptGroupStep):
                count += self._count_steps(step.steps)
            elif isinstance(step, ConditionalStep):
                count += self._count_steps(step.then_steps)
                count += self._count_steps(step.else_steps)
        return count

    def assign_line_numbers(self) -> None:
        """Auto-assign line numbers to all groups and steps."""
        self._current_line = 1
        for group in self.groups:
            group.line_number = self._current_line
            self._current_line += 1
            self._assign_steps_line_numbers(group.steps)

    def _assign_steps_line_numbers(self, steps: list[Step]) -> None:
        for step in steps:
            step.line_number = self._current_line
            self._current_line += 1
            if isinstance(step, LoopStep):
                self._assign_steps_line_numbers(step.steps)
            elif isinstance(step, ScriptGroupStep):
                self._assign_steps_line_numbers(step.steps)
            elif isinstance(step, ConditionalStep):
                self._assign_steps_line_numbers(step.then_steps)
                self._assign_steps_line_numbers(step.else_steps)


# Type mapping from IR step types to command IDs
# Note: MCA384 commands use device-prefixed IDs in the reference
STEP_TO_COMMAND_ID = {
    StepType.ADD_LABWARE: "AddLabware",
    StepType.REMOVE_LABWARE: "RemoveLabware",
    StepType.GET_HEAD_ADAPTER: "Mca384GetHeadAdapter",
    StepType.DROP_HEAD_ADAPTER: "Mca384DropHeadAdapter",
    StepType.PICK_UP_TIPS: "Mca384PickUpTips",
    StepType.SET_TIPS_BACK: "Mca384SetTipsBack",
    StepType.ASPIRATE: "Mca384Aspirate",
    StepType.DISPENSE: "Mca384Dispense",
    # LiHa commands
    StepType.LIHA_ASPIRATE: "LihaAspirate",
    StepType.LIHA_DISPENSE: "LihaDispense",
    StepType.LIHA_MIX: "LihaMix",
    StepType.LIHA_GET_TIPS: "LihaGetTips",
    StepType.LIHA_DROP_TIPS: "LihaDropTips",
    StepType.MCA384_GET_TIPS: "Mca384GetTips",
    StepType.MCA384_DROP_TIPS: "Mca384DropTips",
    StepType.MCA384_MOVE_ARM: "Mca384MoveArm",
    # RGA (Robotic Gripper Arm) commands
    StepType.RGA_TRANSFER_LABWARE: "ApplicationDriverMacro",  # Uses RGA1_TransferLabware macro
    StepType.CGA_GET_FINGERS: "CgaGetFingers",
    StepType.CGA_DROP_FINGERS: "CgaDropFingers",
    # Mixing command
    StepType.MCA384_MIX: "Mca384Mix",
    StepType.MCA384_EMPTY_TIPS: "Mca384EmptyTips",
    # Loop command
    StepType.LOOP: "LoopGroup",
    # Variable commands
    StepType.SET_VARIABLE: "SetVariable",
    # CalculateVariable uses SetVariable with Expression in Tecan, but let's assume we map to something
    # For now map to SetVariable, Renderer will handle the specifics
    StepType.CALCULATE_VARIABLE: "SetVariable", 
    # Wait/Timer command
    StepType.WAIT: "Wait",  # Uses Timer command
    # Annotation and control
    StepType.COMMENT: "Comment",
    StepType.USER_PROMPT: "UserPrompt",
    StepType.START_TIMER: "StartTimer",
    StepType.WAIT_FOR_TIMER: "WaitForTimer",
    # LiHa empty tips
    StepType.LIHA_EMPTY_TIPS: "LihaEmptyTips",
    # Variable import/export/query
    StepType.EXPORT_VARIABLE: "ExportVariable",
    StepType.IMPORT_VARIABLE: "ImportVariable",
    StepType.QUERY_VARIABLE: "QueryVariable",
    # External/runtime commands
    StepType.EXECUTE_APPLICATION: "ExecuteApplication",
    StepType.DELAY: "Delay",
    # Worktable/routines
    StepType.SET_LOCATION: "SetLocation",
    StepType.SUBROUTINE: "SubRoutine",
    StepType.WORKLIST_IMPORT: "WorklistImport",
    StepType.LOAD_WORKLIST: "LoadWorklist",
    StepType.EXECUTE_WORKLIST: "ExecuteWorklist",
    # Device / legacy driver macros
    StepType.LEGACY_DRIVER_MACRO: "LegacyDriverMacro",
}
