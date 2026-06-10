"""Tests for LegacyDriverMacro / ODTC device-command authoring.

Covers the round trip: wt.odtc_* helpers -> IR -> rendered XML -> re-parsed IR,
plus generic legacy_driver_macro, the empty-settings self-closing form, the
simulator no-op, and decompiler codegen back to wt.* calls.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.compiler import render_protocol  # noqa: E402
from fluentvibe.decompiler import emit_python  # noqa: E402
from fluentvibe.decompiler.xscr_parser import _parse_step_object  # noqa: E402
from fluentvibe.ir.schema import (  # noqa: E402
    Group,
    LegacyDriverMacroStep,
    Protocol,
)
from fluentvibe.worktable import Worktable  # noqa: E402


def _protocol(*steps) -> Protocol:
    proto = Protocol(
        name="ODTC test",
        groups=[Group(name="Steps", steps=list(steps))],
        worktable_guid="00000001-4321-aaaa-ffff-000000000000",
        worktable_name="SAT_Fluent_780_Rev3",
    )
    proto.assign_line_numbers()
    return proto


# --------------------------------------------------------------------------
# wt.odtc_* helpers emit the correct IR
# --------------------------------------------------------------------------

def test_odtc_helpers_emit_correct_steps() -> None:
    wt = Worktable(name="anneal")
    wt.odtc_open_door()
    wt.odtc_set_parameters("Annealing.xml")
    wt.odtc_execute_method("Annealing")
    wt.odtc_close_door()
    wt.inheco_set_temperature()

    steps = [s for g in wt.to_protocol().groups for s in g.steps]
    assert all(isinstance(s, LegacyDriverMacroStep) for s in steps)

    by_name = {s.name: s for s in steps}
    assert by_name["SiLA-ODTC_OpenDoor"].module_name == "SiLA-ODTC"
    assert by_name["SiLA-ODTC_OpenDoor"].execution_settings is None
    assert (
        by_name["SiLA-ODTC_SetParameters"].execution_settings
        == "Parameter:MethodsXML:String:File:Annealing.xml"
    )
    assert by_name["SiLA-ODTC_ExecuteMethod"].execution_settings == "Annealing"
    assert by_name["inhecoMTC_SetTemperature"].module_name == "inhecoMTC"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_render_legacy_driver_macro_with_settings() -> None:
    step = LegacyDriverMacroStep(
        name="SiLA-ODTC_ExecuteMethod",
        module_name="SiLA-ODTC",
        execution_settings="Annealing",
    )
    xml = render_protocol(_protocol(step))
    assert '<Object Type="Tecan.VisionX.ApplicationDriver.LegacyDriverMacro">' in xml
    assert 'Name="SiLA-ODTC_ExecuteMethod"' in xml
    assert 'ModuleName="SiLA-ODTC"' in xml
    assert "<ExecutionSettings>Annealing</ExecutionSettings>" in xml
    # driver macros use lowercase bool attributes
    assert 'IsBreakpoint="false"' in xml
    assert 'IsDisabledForExecution="false"' in xml


def test_render_empty_settings_is_self_closing() -> None:
    step = LegacyDriverMacroStep(name="SiLA-ODTC_OpenDoor", module_name="SiLA-ODTC")
    xml = render_protocol(_protocol(step))
    assert "<ExecutionSettings />" in xml
    assert "<ExecutionSettings>" not in xml


# --------------------------------------------------------------------------
# Simulator: device macros are validation-only, never opaque
# --------------------------------------------------------------------------

def test_simulate_does_not_treat_odtc_as_opaque() -> None:
    wt = Worktable(name="anneal")
    wt.odtc_open_door()
    wt.odtc_set_parameters("Annealing.xml")
    wt.odtc_execute_method("Annealing")
    wt.odtc_close_door()
    # fail_on_opaque would raise if the step fell through to GenericStep/OPAQUE.
    wt.simulate(fail_on_opaque=True)


# --------------------------------------------------------------------------
# Render -> parse round trip
# --------------------------------------------------------------------------

def _render_single_object(step: LegacyDriverMacroStep) -> ET.Element:
    xml = render_protocol(_protocol(step))
    # Find the LegacyDriverMacro <Object> element in the rendered document.
    root = ET.fromstring(xml)
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] == "Object":
            if "LegacyDriverMacro" in (el.attrib.get("Type") or ""):
                return el
    raise AssertionError("LegacyDriverMacro Object not found in rendered XML")


def test_render_then_parse_roundtrip() -> None:
    for step in (
        LegacyDriverMacroStep(
            name="SiLA-ODTC_SetParameters",
            module_name="SiLA-ODTC",
            execution_settings="Parameter:MethodsXML:String:File:Annealing.xml",
        ),
        LegacyDriverMacroStep(name="SiLA-ODTC_OpenDoor", module_name="SiLA-ODTC"),
    ):
        obj = _render_single_object(step)
        parsed = _parse_step_object(obj)
        assert isinstance(parsed, LegacyDriverMacroStep)
        assert parsed.name == step.name
        assert parsed.module_name == step.module_name
        assert parsed.execution_settings == step.execution_settings


# --------------------------------------------------------------------------
# Decompiler codegen: typed helpers preferred, generic fallback otherwise
# --------------------------------------------------------------------------

def test_codegen_emits_typed_and_generic_calls() -> None:
    proto = _protocol(
        LegacyDriverMacroStep(name="SiLA-ODTC_OpenDoor", module_name="SiLA-ODTC"),
        LegacyDriverMacroStep(
            name="SiLA-ODTC_SetParameters", module_name="SiLA-ODTC",
            execution_settings="Parameter:MethodsXML:String:File:Annealing.xml",
        ),
        LegacyDriverMacroStep(
            name="SiLA-ODTC_ExecuteMethod", module_name="SiLA-ODTC",
            execution_settings="Annealing",
        ),
        LegacyDriverMacroStep(name="Magellan_Close", module_name="Magellan"),
    )
    src = emit_python(proto)
    assert "wt.odtc_open_door()" in src
    assert "wt.odtc_set_parameters('Annealing.xml')" in src
    assert "wt.odtc_execute_method('Annealing')" in src
    assert "wt.legacy_driver_macro('Magellan_Close', 'Magellan')" in src
