from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import FCA1000Box, Plate96, Reagent, SimulationError, Worktable  # noqa: E402


def test_authored_protocol_gets_coverage_report() -> None:
    wt = Worktable(name="coverage")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Water"), 20.0)
    wt.wait(1)

    wt.simulate()

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == len(wt.snapshots)
    assert report.fully_simulated_steps == 1
    assert report.validation_only_steps == 1
    assert report.opaque_noop_steps == 0
    assert report.modeled_coverage == pytest.approx(1.0)
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(20.0)
    assert wt.snapshots[-1].warnings == []


def test_unknown_generic_step_is_reported_and_can_fail() -> None:
    wt = Worktable(name="opaque")
    wt.group("Steps")
    wt.generic_step("UnknownProductionCommand")

    with pytest.raises(SimulationError):
        wt.simulate(fail_on_opaque=True)

    report = wt.simulation_report
    assert report is not None
    assert report.opaque_noop_steps == 1
    assert report.unsupported_command_ids == {"UnknownProductionCommand": 1}
    assert report.opaque_events[0]["command_id"] == "UnknownProductionCommand"


def test_min_coverage_can_fail() -> None:
    wt = Worktable(name="low coverage")
    wt.group("Steps")
    wt.wait(1)
    wt.generic_step("UnknownProductionCommand")

    with pytest.raises(SimulationError):
        wt.simulate(min_coverage=0.75)

    assert wt.simulation_report is not None
    assert wt.simulation_report.modeled_coverage == pytest.approx(0.5)


def test_raw_known_liha_command_is_modeled_once_and_changes_liquid_state() -> None:
    wt = Worktable(name="raw liha")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        """
        <Object>
          <LabwareName>Source</LabwareName>
          <Volumes><Object><string>10</string></Object></Volumes>
          <LiquidClassName>Water Free Single</LiquidClassName>
        </Object>
        """,
    )

    wt.simulate()

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 3
    assert report.raw_xml_generic_steps == 2
    assert report.unsupported_command_ids == {}
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    assert report.final_liha_tips[0]["volume_ul"] == pytest.approx(10.0)
    assert wt.snapshots[-1].liha_tips[0].volume_ul == pytest.approx(10.0)


# ── Chunk 3: Report arithmetic and semantics ───────────────────────

def test_report_arithmetic_fully_modeled() -> None:
    """Fully modeled protocol: all steps are fully simulated or validation-only."""
    wt = Worktable(name="fully modeled")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Water"), 50.0)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 2)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    head.return_tips()

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Arithmetic invariants
    assert report.total_executed_steps == len(report.steps)
    modeled = report.fully_simulated_steps + report.validation_only_steps
    assert report.modeled_steps == modeled
    assert report.modeled_coverage == pytest.approx(modeled / report.total_executed_steps)
    # No opaque steps in a fully modeled protocol
    assert report.opaque_noop_steps == 0
    assert len(report.opaque_events) == 0
    assert report.unsupported_command_ids == {}


def test_report_arithmetic_validation_only() -> None:
    """Protocol with only validation-only steps (waits, comments)."""
    wt = Worktable(name="validation only")
    wt.group("Steps")
    wt.wait(1)
    wt.add_comment("hello")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    assert report.total_executed_steps == 2
    assert report.fully_simulated_steps == 0
    assert report.validation_only_steps == 2
    assert report.modeled_coverage == pytest.approx(1.0)  # validation-only counts as modeled
    assert report.opaque_noop_steps == 0


def test_report_arithmetic_opaque() -> None:
    """Protocol with only opaque steps."""
    wt = Worktable(name="opaque")
    wt.group("Steps")
    wt.generic_step("UnknownCmd1")
    wt.generic_step("UnknownCmd2")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    assert report.total_executed_steps == 2
    assert report.fully_simulated_steps == 0
    assert report.validation_only_steps == 0
    assert report.opaque_noop_steps == 2
    assert len(report.opaque_events) == 2
    assert report.modeled_coverage == pytest.approx(0.0)
    assert set(report.unsupported_command_ids.keys()) == {"UnknownCmd1", "UnknownCmd2"}


def test_report_arithmetic_mixed_protocol() -> None:
    """Mixed protocol: modeled + validation-only + opaque steps."""
    wt = Worktable(name="mixed")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)

    # fully simulated (AddLabwareStep → LABWARE_MOVEMENT)
    # validation-only (WaitStep)
    wt.wait(1)
    # opaque (GenericStep with unknown command)
    wt.generic_step("UnknownCmd")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    assert report.total_executed_steps == 3
    assert report.fully_simulated_steps == 1  # AddLabwareStep
    assert report.validation_only_steps == 1  # WaitStep
    assert report.opaque_noop_steps == 1      # GenericStep(unknown)
    modeled = report.fully_simulated_steps + report.validation_only_steps
    assert report.modeled_coverage == pytest.approx(modeled / 3.0)
    assert len(report.opaque_events) == report.opaque_noop_steps
    assert "UnknownCmd" in report.unsupported_command_ids


def test_raw_xml_adapted_still_counts_as_raw() -> None:
    """Raw XML steps that are adapted should still count as raw_xml_generic_steps."""
    wt = Worktable(name="raw adapted")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    # These raw XML steps are adapted to structured LiHa steps internally,
    # but they should still be counted as raw_xml_generic_steps.
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        """
        <Object>
          <LabwareName>Source</LabwareName>
          <Volumes><Object><string>5</string></Object></Volumes>
          <LiquidClassName>Water Free Single</LiquidClassName>
        </Object>
        """,
    )

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Both raw XML steps are adapted (not opaque), but still counted as raw_xml
    assert report.raw_xml_generic_steps == 2
    # They should be fully simulated (TIP_STATE_CHANGE + LIQUID_TRANSFER)
    assert report.fully_simulated_steps >= 2
    # No unsupported commands since they were adapted successfully
    assert report.unsupported_command_ids == {}


def test_unsupported_command_ids_only_opaque() -> None:
    """unsupported_command_ids should only contain truly opaque commands."""
    wt = Worktable(name="mixed supported")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Steps")
    # Known command adapted from raw XML → not unsupported
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        """
        <Object>
          <LabwareName>Source</LabwareName>
          <Volumes><Object><string>5</string></Object></Volumes>
          <LiquidClassName>Water Free Single</LiquidClassName>
        </Object>
        """,
    )
    # Unknown command → opaque
    wt.generic_step("TotallyUnknownCmd")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Only the unknown command should appear in unsupported_command_ids
    assert "LihaAspirate" not in report.unsupported_command_ids
    assert "TotallyUnknownCmd" in report.unsupported_command_ids


def test_strict_simulation_requires_bound_workspace_and_preserves_report() -> None:
    wt = Worktable(name="strict workspace binding")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)

    with pytest.raises(SimulationError, match="not bound to a specific FluentControl workspace"):
        wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None
    assert report.status == "failed"
    assert report.total_executed_steps == 0
    assert report.failure is not None
    assert report.failure.category == "workspace_binding"
    assert report.failure.step_index is None


def test_strict_simulation_rejects_invalid_workspace_slot_with_partial_state() -> None:
    wt = Worktable(name="strict bad slot")
    wt.workspace_name = "FakeWorkspace"
    wt.workspace_guid = "11111111-1111-1111-1111-111111111111"
    wt.valid_slots = {("Site", 1)}

    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Site", 1)
    wt.group("Move")
    wt.set_location(src, "Site", 2)

    with pytest.raises(SimulationError):
        wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None
    assert report.status == "failed"
    assert report.total_executed_steps == 1
    assert report.failure is not None
    assert report.failure.category == "workspace_slot"
    assert report.failure.command_id == "set_location"
    assert report.final_labware["Source"]["slot"] == ["Site", 1]


def test_failed_report_to_dict_includes_failure_and_effect_counts() -> None:
    wt = Worktable(name="opaque failure report")
    wt.group("Steps")
    wt.generic_step("UnknownProductionCommand")

    with pytest.raises(SimulationError):
        wt.simulate(fail_on_opaque=True)

    report = wt.simulation_report
    assert report is not None
    payload = report.to_dict()
    assert payload["status"] == "failed"
    assert payload["failure"]["category"] == "opaque_policy"
    assert payload["failure"]["exception_type"] == "SimulationError"
    assert payload["effect_counts"]["opaque"] == 1
