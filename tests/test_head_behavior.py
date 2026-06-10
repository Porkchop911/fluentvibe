"""Chunk 4: Direct LiHa and MCA head behavior tests.

Validates simulator semantics for both heads independently of raw XML adapters
or decompiled flows. Each test asserts semantic state changes and report effects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import (  # noqa: E402
    FCA1000Box,
    Plate96,
    Reagent,
    SimulationError,
    Trough100mL,
    Worktable,
)
from fluentvibe.catalog import index_exists  # noqa: E402

# ── LiHa direct tests ───────────────────────────────────────────────

def test_liha_get_tips_aspirate_dispense_cycle() -> None:
    """LiHa get tips → aspirate → dispense → drop tips full cycle."""
    wt = Worktable(name="liha cycle")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 50.0)

    head = wt.liha
    head.get_tips()
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    head.dispense(dst, 10.0, liquid_class="Water Free Single")
    head.drop_tips("Trash")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Source lost 10 µL (channel 0 → well A1)
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    # Dest gained 10 µL
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)
    # After drop_tips, all LiHa tips should be None
    for tip in wt.snapshots[-1].liha_tips:
        assert tip is None

    # Report: get_tips=TIP_STATE_CHANGE, aspirate=LIQUID_TRANSFER,
    # dispense=LIQUID_TRANSFER, drop_tips=TIP_STATE_CHANGE → all fully simulated
    assert report.fully_simulated_steps >= 4
    assert report.opaque_noop_steps == 0


def test_liha_aspirate_from_single_trough_repeats_for_mounted_channels() -> None:
    wt = Worktable(name="liha trough source")
    wt.group("Setup")
    src = wt.place(Trough100mL("SourceTrough", catalog="100ml Trough 156mm"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    src.fill_all(Reagent("Water"), 1000.0)

    head = wt.liha
    head.get_tips(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    head.dispense(dst, 20.0, liquid_class="Water Free Single")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.final_labware["SourceTrough"]["wells"]["A1"]["volume_ul"] == pytest.approx(840.0)
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(20.0)
    assert report.final_labware["Dest"]["wells"]["H1"]["volume_ul"] == pytest.approx(20.0)
    a2 = report.final_labware["Dest"]["wells"].get("A2", {"volume_ul": 0.0})
    assert a2["volume_ul"] == pytest.approx(0.0)


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_liha_get_tips_prefers_sbs_fca_tip_variant(tmp_path: Path) -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="liha tip normalization",
    )
    wt.group("Setup")
    tips = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest61mm_Pos", 6)

    wt.liha.get_tips(tips)

    out = tmp_path / "liha_tip_normalization.xscr"
    wt.compile(out)
    xml = out.read_text(encoding="utf-8")
    assert "<LabwareType>FCA, 1000ul SBS</LabwareType>" in xml
    assert "TOOLNAME:FCA, 1000ul SBS" in xml


def test_liha_mix_validation_and_empty_tips() -> None:
    """LiHa mix validates volume availability; empty tips deposits into wells."""
    wt = Worktable(name="liha mix+empty")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 50.0)

    head = wt.liha
    head.get_tips()
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    # Mix validates that the well has enough volume (but doesn't change state)
    head.mix(src, 5.0, cycles=3, liquid_class="Water Mix")
    # Empty tips deposits remaining tip contents into destination wells
    head.empty_tips(dst, 10.0)

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Source: aspirated 10 µL (mix doesn't change volumes)
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    # Dest: received 10 µL from empty_tips
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)

    # Mix is validation_only; aspirate/dispense/empty are fully simulated
    assert report.validation_only_steps >= 1  # mix step
    assert report.fully_simulated_steps >= 3  # get_tips, aspirate, empty_tips


def test_liha_errors_missing_tip_and_invalid_well_index() -> None:
    """LiHa aspirate fails when no tip mounted; well_offset out of range fails."""
    # Test 1: missing tip failure
    wt1 = Worktable(name="liha no tip")
    wt1.group("Setup")
    src = wt1.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt1.liha.aspirate(src, 10.0, liquid_class="Water Free Single")
    with pytest.raises(SimulationError):
        wt1.simulate()

    # Test 2: invalid well index (well_offset beyond plate range)
    wt2 = Worktable(name="liha bad offset")
    wt2.group("Setup")
    src2 = wt2.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src2.fill_all(Reagent("Buffer"), 50.0)

    wt2.liha.get_tips()
    # Plate96 has 96 wells (indices 0-95); offset=96 is out of range for channel 0
    wt2.liha.aspirate(src2, 10.0, liquid_class="Water Free Single", well_offset=96)
    with pytest.raises(SimulationError):
        wt2.simulate()


def test_liha_well_offset_selects_correct_wells() -> None:
    """LiHa aspirate with well_offset targets the expected wells."""
    wt = Worktable(name="liha offset")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    head = wt.liha
    # Get tips on a single channel (tip_index=3 → only channel 3)
    head.get_tips()  # gets all 8 channels by default
    # well_offset=10 means: ch0→idx10, ch1→idx11, ..., ch7→idx17
    # Index 10 = A2 row 2 (B2), index 17 = H2. All in row 2.
    head.aspirate(src, 5.0, liquid_class="Water Free Single", well_offset=10)

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # A1 (index 0) should be untouched — offset starts at 10
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(50.0)
    # C2 (index 10, ch0 target with well_offset=10) should have lost 5 µL
    assert report.final_labware["Source"]["wells"]["C2"]["volume_ul"] == pytest.approx(45.0)


# ── MCA direct tests ────────────────────────────────────────────────

def test_mca_adapter_mount_pickup_aspirate_dispense_cycle() -> None:
    """MCA mount adapter → pick up tips → aspirate → dispense → return tips."""
    wt = Worktable(name="mca cycle")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    src.fill_all(Reagent("Buffer"), 50.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    head.dispense(dst, 10.0, liquid_class="Water Free Single")
    head.return_tips()

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Source lost 10 µL from A1 (tip[0] pairs with well[0])
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    # Dest gained 10 µL in A1
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)
    # After return_tips, MCA tips should be empty list
    assert wt.snapshots[-1].mca_tips == []

    # All steps fully simulated: mount=TIP_STATE_CHANGE, pickup=TIP_STATE_CHANGE,
    # aspirate=LIQUID_TRANSFER, dispense=LIQUID_TRANSFER, return=TIP_STATE_CHANGE
    assert report.fully_simulated_steps >= 5
    assert report.opaque_noop_steps == 0


def test_mca_aspirate_from_single_trough_fills_all_tips() -> None:
    """A single trough pool can feed every mounted MCA tip."""
    wt = Worktable(name="mca trough source")
    wt.group("Setup")
    src = wt.place(Trough100mL("SourceTrough", catalog="100ml Trough 156mm"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    src.fill_all(Reagent("Water"), 5000.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    head.dispense(dst, 20.0, liquid_class="Water Free Single")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.final_labware["SourceTrough"]["wells"]["A1"]["volume_ul"] == pytest.approx(3080.0)
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(20.0)
    assert report.final_labware["Dest"]["wells"]["H12"]["volume_ul"] == pytest.approx(20.0)


def test_mca_insufficient_volume_failure() -> None:
    """MCA aspirate fails when well doesn't have enough volume."""
    wt = Worktable(name="mca insuf")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 2)
    src.well("A1").add_layer(Reagent("Buffer"), 5.0)  # only 5 µL

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")  # want 20 from 5
    with pytest.raises(SimulationError):
        wt.simulate()


def test_mca_dispense_overflow_failure() -> None:
    """MCA dispense fails when well would overflow its max volume."""
    wt = Worktable(name="mca overflow")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    src.fill_all(Reagent("Buffer"), 50.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    # Aspirate a large volume that would overflow the destination well
    head.aspirate(src, 400.0, liquid_class="Water Free Single")
    head.dispense(dst, 400.0, liquid_class="Water Free Single")
    with pytest.raises(SimulationError):
        wt.simulate()


# ── Mixed-head test ────────────────────────────────────────────────

def test_mixed_head_liha_and_mca_independent_states() -> None:
    """Same protocol uses both LiHa and MCA; final states remain independent."""
    wt = Worktable(name="mixed heads")
    wt.group("Setup")
    src1 = wt.place(Plate96("SrcMCA", catalog="96 Well Flat"), "Nest", 1)
    src2 = wt.place(Plate96("SrcLiHa", catalog="96 Well Flat"), "Nest", 2)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 3)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 4)
    src1.fill_all(Reagent("BufferA"), 50.0)
    src2.fill_all(Reagent("BufferB"), 50.0)

    # MCA operations
    mca = wt.mca96
    mca.mount_adapter()
    mca.pick_up(tip_box)
    mca.aspirate(src1, 10.0, liquid_class="Water Free Single")
    mca.dispense(dst, 10.0, liquid_class="Water Free Single")
    mca.return_tips()

    # LiHa operations (independent of MCA state)
    liha = wt.liha
    liha.get_tips()
    liha.aspirate(src2, 5.0, liquid_class="Water Free Single")
    liha.drop_tips("Trash")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # MCA source lost 10 µL from A1
    assert report.final_labware["SrcMCA"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    # LiHa source lost 5 µL from A1 (channel 0 → well index 0)
    assert report.final_labware["SrcLiHa"]["wells"]["A1"]["volume_ul"] == pytest.approx(45.0)
    # Dest gained 10 µL from MCA dispense
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)

    # Both final tip summaries populated correctly
    assert len(report.final_mca_tips) == 0  # return_tips emptied them
    assert all(t is None for t in wt.snapshots[-1].liha_tips)  # drop_tips cleared them

    # Report: no opaque steps, everything modeled
    assert report.opaque_noop_steps == 0
