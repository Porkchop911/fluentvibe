"""Tests for the gripper fingers-labware fix and the renderer's hard catalog validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from fluentvibe import FCA1000Box, Plate96, Reagent, Trough100mL, Worktable, MagnetRack
from fluentvibe.compiler.renderer import RenderError
from fluentvibe.ir.schema import StepType


def _build_with_gripper_move() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Gripper Fingers Fix",
        comment="",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    wt.group("Labware Placement")
    plate = wt.place(
        Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"),
        "Nest61mm_Pos",
        1,
    )
    magnet = wt.place(
        MagnetRack("MagnetRack", catalog="LV_Alpaqua_A000350"),
        "Nest61mm_Pos",
        3,
    )
    wt.group("Move")
    wt.gripper.move(plate, onto=magnet)
    return wt


def test_gripper_move_emits_fingers_steps_with_default_labware() -> None:
    wt = _build_with_gripper_move()
    protocol = wt.to_protocol()
    finger_steps: list = []
    for group in protocol.groups:
        for step in group.steps:
            stype = getattr(step, "step_type", None)
            if stype in (StepType.CGA_GET_FINGERS, StepType.CGA_DROP_FINGERS):
                finger_steps.append(step)
    assert len(finger_steps) >= 2, "gripper.move should emit Get + Drop Fingers steps"
    for step in finger_steps:
        assert step.labware_name is None, (
            f"{step.step_type.name} must leave labware_name=None so the renderer "
            "substitutes the configured default (Centric[001] / FES Centric Nest[001]). "
            f"Got {step.labware_name!r}."
        )


def test_renderer_known_labware_set_is_populated() -> None:
    """The renderer auto-fix and hard-validation depend on this set being non-empty.

    A regression where this returns 0 entries (e.g. wrong import path) silently
    disables every catalog-name correction in the renderer. The MCA tip-box
    coercion in particular has no string-fallback path for empty `known`, so
    when this set is empty the model's "MCA96, 200ul" survives all the way to
    FluentControl as a non-DiTi labware.
    """
    from fluentvibe.compiler.renderer import Renderer
    known = Renderer._get_known_labware_set()
    assert len(known) > 100, (
        f"renderer known-labware set has only {len(known)} entries; "
        "the auto-fix / validation will silently noop. Check the import path."
    )
    # Sanity: both the adapter ("MCA96, 200ul") and the tip-box
    # ("MCA96, 200ul, Box") catalog entries should be present so the auto-fix
    # can coerce one to the other.
    assert "MCA96, 200ul, Box" in known


def test_renderer_auto_fix_coerces_mca_adapter_to_box(tmp_path: Path, capsys) -> None:
    """The auto-fix rewrites `MCA96, 200ul` (adapter) -> `MCA96, 200ul, Box` (tip box).

    Repro of the AMPure failure: the model writes the adapter catalog name,
    which is technically valid (passes constructor validation) but FC rejects
    as not-a-DiTi at load time. The renderer's auto-fix must coerce it.
    """
    from fluentvibe import MCA200Box
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="MCA Auto-Fix",
        comment="",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    wt.group("Labware Placement")
    plate = wt.place(
        Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"),
        "Nest61mm_Pos",
        1,
    )
    tips = wt.place(
        MCA200Box("MCATips", catalog="MCA96, 200ul"),
        "Nest61mm_Pos",
        4,
    )
    sample = Reagent("Sample")
    plate.fill_all(sample, 80.0)
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(plate, 20.0, liquid_class="Water Free Single")
    head.dispense(plate, 20.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    out = tmp_path / "mca_autofix.xscr"
    wt.compile(out)
    xml_text = out.read_text(encoding="utf-8")
    assert "MCA96, 200ul, Box" in xml_text, (
        "Rendered .xscr must contain the corrected tip-box catalog name"
    )
    # The bare adapter name must NOT appear standalone in the rendered XML.
    # We check that any occurrence is followed by ', Box' suffix.
    import re
    standalone = re.findall(r"MCA96, 200ul(?!,\s*Box)", xml_text)
    assert not standalone, (
        f"Rendered .xscr still contains uncorrected adapter name: {standalone[:3]}"
    )


def test_seeded_ampure_rules_present_in_catalog() -> None:
    from fluentvibe.catalog import get_database

    db = get_database()
    required = [
        "ampure_keep_plate_on_magnet_during_washes",
        "ampure_mix_after_bead_addition_uses_mca",
        "ampure_separate_tip_box_for_eluate",
        "ampure_derived_volumes_in_python",
        "ampure_per_role_liquid_class_variables",
    ]
    for name in required:
        rule = db.get_rule(name)
        assert rule is not None, f"AMPure rule {name!r} is not seeded in the catalog"
        assert rule.get("active") in (1, True), f"AMPure rule {name!r} is inactive"
        assert rule.get("protocol_type") == "bead_cleanup", (
            f"AMPure rule {name!r} has unexpected protocol_type {rule.get('protocol_type')!r}"
        )


def test_liha_liquid_class_variable_renders_as_expression(tmp_path: Path) -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="LC variable expression",
        comment="",
    )
    wt.declare_variable("LIQUID_CLASS_BEADS", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_BEADS", "Water Free Single")
    lc_beads = "LIQUID_CLASS_BEADS"
    wt.declare_variable("VOLUME_BEADS_UL", 36.0)
    wt.set_sim_value("VOLUME_BEADS_UL", 36.0)
    volume = "VOLUME_BEADS_UL"
    wt.group("Labware Placement")
    source = wt.place(
        Trough100mL("TroughBeads", catalog="100ml Trough 156mm"),
        "WS_100ml_1",
        1,
    )
    dest = wt.place(
        Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"),
        "Nest61mm_Pos",
        1,
    )
    tips = wt.place(
        FCA1000Box("FCATips", catalog="FCA, 1000ul SBS"),
        "Nest61mm_Pos",
        5,
    )
    source.fill_all(Reagent("beads"), 5000.0)
    wt.group("Dispense")
    head = wt.liha
    head.get_tips(tips)
    head.aspirate(source, volume, liquid_class=lc_beads)
    head.dispense(dest, volume, liquid_class=lc_beads, well_offset=0)
    head.drop_tips()

    out = tmp_path / "lc_variable.xscr"
    wt.compile(out)
    xml_text = out.read_text(encoding="utf-8")

    assert "<IsLiquidClassNameByExpressionEnabled>True</IsLiquidClassNameByExpressionEnabled>" in xml_text
    assert "<LiquidClassSelectionMode>SingleByExpression</LiquidClassSelectionMode>" in xml_text
    assert "<LiquidClassNameBySelection></LiquidClassNameBySelection>" in xml_text
    assert "<LiquidClassNameByExpression>LIQUID_CLASS_BEADS</LiquidClassNameByExpression>" in xml_text


def test_worktable_loop_renders_as_fluentcontrol_loop_group(tmp_path: Path) -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Loop rendering",
        comment="",
    )
    wt.group("Looped waits")
    with wt.loop(times=12, name="Dispense columns", loop_variable="col"):
        wt.wait(1.0)

    out = tmp_path / "loop_rendering.xscr"
    wt.compile(out)
    xml_text = out.read_text(encoding="utf-8")

    assert '<Object Type="Tecan.Core.Scripting.LoopGroup">' in xml_text
    assert "<Name>Dispense columns</Name>" in xml_text
    assert "<LoopVariable>col</LoopVariable>" in xml_text
    assert "<NumberOfLoops>12</NumberOfLoops>" in xml_text
    assert xml_text.count("<WaitStatement>") == 1


def test_liha_loop_keeps_offset_expression_and_simulates(tmp_path: Path) -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Looped LiHa dispense",
        comment="",
    )
    wt.group("Labware Placement")
    source = wt.place(
        Trough100mL("TroughBuffer", catalog="100ml Trough 156mm"),
        "WS_100ml_1",
        1,
    )
    dest = wt.place(
        Plate96("DestinationPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"),
        "Nest61mm_Pos",
        1,
    )
    tips = wt.place(
        FCA1000Box("FCATips", catalog="FCA, 1000ul SBS"),
        "Nest61mm_Pos",
        5,
    )
    source.fill_all(Reagent("buffer"), 5000.0)

    wt.group("Dispense")
    head = wt.liha
    head.get_tips(tips)
    with wt.loop(times=12, name="Dispense columns", loop_variable="col"):
        head.aspirate(source, 10.0, liquid_class="Water Free Single")
        head.dispense(
            dest,
            10.0,
            liquid_class="Water Free Single",
            well_offset="(col-1)*8",
        )
    head.drop_tips()

    out = tmp_path / "looped_liha.xscr"
    wt.compile(out)
    xml_text = out.read_text(encoding="utf-8")

    assert '<Object Type="Tecan.Core.Scripting.LoopGroup">' in xml_text
    assert "<LoopVariable>col</LoopVariable>" in xml_text
    assert "<WellOffset>(col-1)*8</WellOffset>" in xml_text
    assert xml_text.count("<WellOffset>(col-1)*8</WellOffset>") == 1

    wt.simulate()
    assert all(well.volume_ul == pytest.approx(10.0) for well in dest.wells.values())
