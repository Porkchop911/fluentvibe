import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import FCA1000Box, Plate96, Reagent, Worktable
from fluentvibe.catalog.catalog import index_exists, list_by_category
from fluentvibe.decompiler import emit_python, parse_xscr
from fluentvibe.ir.schema import (
    ExecuteApplicationStep,
    ExportVariableStep,
    GenericStep,
    ImportVariableStep,
    QueryVariableStep,
    SetLocationStep,
    SetVariableStep,
    StartTimerStep,
    UserPromptStep,
    WaitForTimerStep,
    WaitStep,
)
from tests._module_loader import load_module


def test_statement_aliases_decode_to_structured_steps(tmp_path: Path) -> None:
    src = tmp_path / "statements.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Statements</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.SetVariableStatement">
          <SetVariableStatement><Name>cycles</Name><Value>3</Value></SetVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.WaitStatement">
          <WaitStatement><Duration>60</Duration></WaitStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.UserPromptStatement">
          <UserPromptStatement><Prompt>Load plate</Prompt><Timeout>5</Timeout></UserPromptStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.StartTimerStatement">
          <StartTimerStatement><Timer>2</Timer></StartTimerStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.WaitForTimerStatement">
          <WaitForTimerStatement><Timer>2</Timer><Duration>120</Duration></WaitForTimerStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.VariableExportImport.ExportVariableStatement">
          <ExportVariableStatement>
            <Variables><Object Type="System.String"><string>cycles</string></Object></Variables>
            <ExportFile>"C:\\tmp\\vars.txt"</ExportFile>
            <WriteHeader>True</WriteHeader>
            <ReplaceExistingFile>False</ReplaceExistingFile>
            <ExportStringsWithQuotes>False</ExportStringsWithQuotes>
            <DelimiterCode>59</DelimiterCode>
          </ExportVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.VariableExportImport.ImportVariableStatement">
          <ImportVariableStatement>
            <Variables><Object Type="System.String"><string>cycles</string></Object></Variables>
            <ImportFile>"C:\\tmp\\vars.txt"</ImportFile>
            <ReadLine>True</ReadLine><Line>4</Line>
            <StartInColumn>True</StartInColumn><Column>2</Column>
            <HasHeader>True</HasHeader><DelimiterCode>59</DelimiterCode>
          </ImportVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.QueryVariableStatement">
          <QueryVariableStatement><Name>cycles</Name><QueryPrompt>Cycles?</QueryPrompt><LimitRange>True</LimitRange></QueryVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
          <ExecuteApplicationStatement><Application>tool.exe</Application><Arguments>--x</Arguments><Wait>False</Wait><StoreReturn>True</StoreReturn><Variable>rc</Variable></ExecuteApplicationStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.Worktable.SetLocationStatement">
          <SetLocationStatement><Labware>Lid</Labware><Location>Nest</Location><Site>4</Site><Rotation>90</Rotation></SetLocationStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert isinstance(steps[0], SetVariableStep)
    assert steps[0].variable_name == "cycles"
    assert steps[0].value == 3
    assert isinstance(steps[1], WaitStep)
    assert steps[1].duration_seconds == 60.0
    assert isinstance(steps[2], UserPromptStep)
    assert isinstance(steps[3], StartTimerStep)
    assert isinstance(steps[4], WaitForTimerStep)
    assert isinstance(steps[5], ExportVariableStep)
    assert steps[5].export_file == r"C:\tmp\vars.txt"
    assert isinstance(steps[6], ImportVariableStep)
    assert steps[6].line == 4
    assert isinstance(steps[7], QueryVariableStep)
    assert isinstance(steps[8], ExecuteApplicationStep)
    assert isinstance(steps[9], SetLocationStep)
    assert steps[9].location == "Nest"


def test_alternate_group_attaches_to_conditional_else(tmp_path: Path) -> None:
    src = tmp_path / "alternate.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Alternate</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ConditionalGroup">
          <ConditionalGroup>
            <Name>If pH</Name><Condition>ph=7</Condition>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>1</Duration></WaitStatement></Object>
            </Objects>
          </ConditionalGroup>
        </Object>
        <Object Type="Tecan.Core.Scripting.AlternateGroup">
          <AlternateGroup>
            <Name>Else pH</Name>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>2</Duration></WaitStatement></Object>
            </Objects>
          </AlternateGroup>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert isinstance(steps[0], GenericStep)
    assert steps[0].step_type == "ConditionalGroup"
    assert "ph=7" in steps[0].parameters["raw_xml"]
    assert isinstance(steps[1], GenericStep)
    assert steps[1].step_type == "AlternateGroup"
    assert "Else pH" in steps[1].parameters["raw_xml"]


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_mca384_mix_and_empty_tips_decode_and_codegen_executes(tmp_path: Path) -> None:
    src = tmp_path / "mca.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>MCA</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>96 Well Flat</LabwareType><LabwareLable>Plate1</LabwareLable><Location>Site</Location><Position>1</Position></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384MixScriptCommandDataV2">
          <Mca384MixScriptCommandDataV2><Cycles>4</Cycles><Data><Mca384PipettingWithVolumesScriptCommandDataV2><LiquidClassName>Water Mix</LiquidClassName><Volume>5</Volume><Data><Mca384ScriptCommandUsingWellSelectionBaseDataV6><Data><ScriptCommandCommonDataV2><LabwareName>Plate1</LabwareName></ScriptCommandCommonDataV2></Data></Mca384ScriptCommandUsingWellSelectionBaseDataV6></Data></Mca384PipettingWithVolumesScriptCommandDataV2></Data></Mca384MixScriptCommandDataV2>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.Mca.Mca384.Scripting.Mca384EmptyTipsScriptCommandDataV2">
          <Mca384EmptyTipsScriptCommandDataV2><Data><Mca384PipettingWithVolumesScriptCommandDataV2><LiquidClassName>Empty Tip</LiquidClassName><Volume>5</Volume><Data><Mca384ScriptCommandUsingWellSelectionBaseDataV6><Data><ScriptCommandCommonDataV2><LabwareName>Plate1</LabwareName></ScriptCommandCommonDataV2></Data></Mca384ScriptCommandUsingWellSelectionBaseDataV6></Data></Mca384PipettingWithVolumesScriptCommandDataV2></Data></Mca384EmptyTipsScriptCommandDataV2>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    proto = parse_xscr(src)

    assert isinstance(proto.groups[0].steps[1], GenericStep)
    assert proto.groups[0].steps[1].step_type == "Mca384Mix"
    assert isinstance(proto.groups[0].steps[2], GenericStep)
    assert proto.groups[0].steps[2].step_type == "Mca384EmptyTips"

    py = tmp_path / "decompiled.py"
    py.write_text(emit_python(proto, source_xscr=str(src)), encoding="utf-8")
    text = py.read_text(encoding="utf-8")
    assert "wt.raw_xml_step('Mca384Mix'" in text
    assert "wt.raw_xml_step('Mca384EmptyTips'" in text
    load_module(py).build_worktable()


def test_complex_liha_commands_are_raw_preserved(tmp_path: Path) -> None:
    src = tmp_path / "complex_liha.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Complex LiHa</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaPickUpScriptCommandDataV1">
          <LihaPickUpScriptCommandDataV1><LabwareName>Tips50</LabwareName></LihaPickUpScriptCommandDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data>
              <LihaPipettingWithVolumesScriptCommandDataV7>
                <Volumes>
                  <Object Type="System.String"><string>1.5*var_aspirate_volume</string></Object>
                </Volumes>
                <LiquidClassName>Water Free Single</LiquidClassName>
                <LiquidClassNameByExpression>var_liquidclass1</LiquidClassNameByExpression>
                <IsLiquidClassNameByExpressionEnabled>True</IsLiquidClassNameByExpressionEnabled>
                <LabwareName>Source</LabwareName>
                <SelectedWellsString>A2 - H2</SelectedWellsString>
                <SerializedWellIndexes>8&gt;1&gt;15;</SerializedWellIndexes>
                <TipSpacing>18</TipSpacing>
              </LihaPipettingWithVolumesScriptCommandDataV7>
            </Data>
          </LihaAspirateScriptCommandDataV5>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert isinstance(steps[0], GenericStep)
    assert steps[0].parameters["raw_xml"].find("LihaPickUpScriptCommandDataV1") >= 0
    assert isinstance(steps[1], GenericStep)
    assert "1.5*var_aspirate_volume" in steps[1].parameters["raw_xml"]
    assert "A2 - H2" in steps[1].parameters["raw_xml"]


def test_simulator_set_variable_mix_and_empty_tips() -> None:
    wt = Worktable(name="sim")
    wt.group("Steps")
    if index_exists():
        plate_catalog = list_by_category("plate")[0].name
        tip_catalog = list_by_category("tip_box")[0].name
        src_lw = Plate96("Source", catalog=plate_catalog)
        tips_lw = FCA1000Box("Tips", catalog=tip_catalog)
    else:
        src_lw = Plate96("Source")
        tips_lw = FCA1000Box("Tips")
    src = wt.place(src_lw, "Site", 1)
    tip_box = wt.place(tips_lw, "Site", 2)
    src.fill_all(Reagent("water"), 20)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 5, liquid_class="Water Free Single")
    wt.set_variable("cycles", 2)
    head.mix(src, "cycles", cycles="cycles", liquid_class="Water Mix")
    head.empty_tips(src, 5)

    wt.simulate()

    assert wt.sim_values["cycles"] == 2
    assert sum(t.volume_ul for t in wt.snapshots[-1].mca_tips) == 0
