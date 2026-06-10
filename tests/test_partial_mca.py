"""Partial-column MCA pipetting: authoring → IR → compile + simulate.

Covers the partial-plate addressing the FluentControl ``PartialMCAexamples``
script demonstrates: an aspirate/dispense that touches only a subset of plate
columns. The well-selection is carried on ``AspirateStep.columns`` /
``DispenseStep.columns`` and lands in the
``Mca384ScriptCommandUsingWellSelectionBaseDataV6`` block on compile.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import (  # noqa: E402
    FCA1000Box,
    MCA100Box,
    Plate96,
    Reagent,
    Worktable,
)
from fluentvibe.compiler.renderer import Renderer  # noqa: E402
from fluentvibe.decompiler.xscr_parser import parse_xscr  # noqa: E402
from fluentvibe.ir.schema import AspirateStep  # noqa: E402


def _mca_worktable() -> tuple[Worktable, object, object, object]:
    wt = Worktable(name="partial mca")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    src.fill_all(Reagent("Buffer"), 50.0)
    return wt, src, dst, tip_box


def _vol(report, labware: str, address: str) -> float:
    return report.final_labware[labware]["wells"].get(address, {"volume_ul": 0.0})["volume_ul"]


# ── Simulator semantics ─────────────────────────────────────────────

def test_partial_columns_touch_only_selected_columns() -> None:
    """A dispense over columns 1-3 fills only those columns; column 4+ stays dry."""
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 10.0, liquid_class="Water Free Single", columns=[1, 2, 3])
    head.dispense(dst, 10.0, liquid_class="Water Free Single", columns=[1, 2, 3])
    head.return_tips()

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Columns 1-3 (top and bottom row) received liquid.
    for addr in ("A1", "H1", "A2", "A3", "H3"):
        assert _vol(report, "Dest", addr) == pytest.approx(10.0)
    # Column 4 onward was never addressed.
    for addr in ("A4", "H4", "A12"):
        assert _vol(report, "Dest", addr) == pytest.approx(0.0)

    # Source only lost liquid from the addressed columns.
    assert _vol(report, "Source", "A1") == pytest.approx(40.0)
    assert _vol(report, "Source", "A3") == pytest.approx(40.0)
    assert _vol(report, "Source", "A4") == pytest.approx(50.0)


def test_full_plate_unchanged_when_columns_none() -> None:
    """Omitting columns addresses the whole plate (regression guard)."""
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    head.dispense(dst, 10.0, liquid_class="Water Free Single")
    head.return_tips()

    wt.simulate()
    report = wt.simulation_report
    for addr in ("A1", "A4", "A12", "H12"):
        assert _vol(report, "Dest", addr) == pytest.approx(10.0)


# ── Compile (forward XML) ───────────────────────────────────────────

def _compile_xml(wt: Worktable) -> str:
    """Render the worktable IR to XSCR XML with a placeholder workspace binding."""
    protocol = wt.to_protocol()
    protocol.worktable_guid = protocol.worktable_guid or "00000000-0000-0000-0000-000000000000"
    protocol.worktable_name = protocol.worktable_name or "TestWorkspace"
    return Renderer().render(protocol)


def test_contiguous_columns_emit_first_last_tip_positions() -> None:
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 15.0, liquid_class="Water Free Single", columns=[7, 8, 9, 10, 11, 12])
    head.return_tips()

    xml = _compile_xml(wt)
    assert "<FirstTipXPosition>7</FirstTipXPosition>" in xml
    assert "<LastTipXPosition>12</LastTipXPosition>" in xml
    # Contiguous selection is bounded by First/Last only — no explicit list.
    assert "<SelectedRowsOrColumns>7" not in xml


def test_sparse_columns_emit_selected_rows_or_columns() -> None:
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.dispense(dst, 15.0, liquid_class="Water Free Single", columns=[1, 3, 5, 7, 9, 11])
    head.return_tips()

    xml = _compile_xml(wt)
    assert "<FirstTipXPosition>1</FirstTipXPosition>" in xml
    assert "<LastTipXPosition>11</LastTipXPosition>" in xml
    assert "<SelectedRowsOrColumns>1,3,5,7,9,11</SelectedRowsOrColumns>" in xml


def test_full_plate_compile_has_default_well_selection() -> None:
    """columns=None must leave the well-selection block at its full-plate default."""
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 15.0, liquid_class="Water Free Single")
    head.return_tips()

    xml = _compile_xml(wt)
    assert "<FirstTipXPosition>1</FirstTipXPosition>" in xml
    # No explicit column list is injected for a full-plate aspirate.
    assert "<SelectedRowsOrColumns>" not in xml.replace("<SelectedRowsOrColumns />", "")


# ── Partial-tip pickup/setback offsets (tip sorting) ────────────────

def test_pickup_setback_default_offsets_are_zero() -> None:
    """Without offsets, the partial-tip block stays at the origin (regression guard)."""
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.return_tips()

    xml = _compile_xml(wt)
    assert "<PartialColumnOffset>0</PartialColumnOffset>" in xml
    assert "<PartialRowsOffset>0</PartialRowsOffset>" in xml
    # No stray placeholder ever leaks through.
    assert "{{PartialColumnOffset}}" not in xml
    assert "{{PartialRowsOffset}}" not in xml


def test_partial_tip_offset_is_derived_from_column() -> None:
    """PartialColumnOffset is derived as head_width - max(column), matching the
    PartialMCAexamples reference (box col 1 -> offset 11, col 4 -> offset 8)."""
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Sort")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box, columns=[1])      # 12 - 1 = 11
    head.return_tips(tip_box, columns=[4])   # 12 - 4 = 8

    xml = _compile_xml(wt)
    assert "<PartialColumns>1</PartialColumns>" in xml
    assert "<PartialColumnOffset>11</PartialColumnOffset>" in xml
    assert "<PartialColumnOffset>8</PartialColumnOffset>" in xml
    # The offset is never 12 - 1's mirror: a stuck/constant offset would contradict
    # the well-selection. Well-selection tracks the box columns.
    assert "<FirstTipXPosition>1</FirstTipXPosition>" in xml
    assert "<FirstTipXPosition>4</FirstTipXPosition>" in xml


# ── Tip sorting (per-column tip-box simulation) ─────────────────────

def test_sort_tips_into_columns_then_use() -> None:
    """Sorting columns into an empty box simulates; the box tracks occupancy."""
    wt, src, dst, full = _mca_worktable()
    empty = wt.place(MCA100Box("EmptyTips", catalog="MCA96, 100ul, Box"), "Nest", 5)
    empty.is_full = False
    full2 = wt.place(MCA100Box("FullTips2", catalog="MCA96, 100ul, Box"), "Nest", 6)

    wt.group("Sort")
    head = wt.mca96
    head.mount_adapter()
    # Peel source columns 1-4 off the moving left edge into 1/4/7/10.
    for src_col, tgt_col in ((1, 1), (2, 4), (3, 7), (4, 10)):
        head.pick_up(full2, columns=[src_col])
        head.return_tips(empty, columns=[tgt_col])
    wt.group("Use")
    # The sorted box is used by picking the whole thing at once.
    sorted_cols = [1, 4, 7, 10]
    head.pick_up(empty, columns=sorted_cols)
    head.aspirate(src, 20.0, liquid_class="Water Free Single", columns=sorted_cols)
    head.dispense(dst, 20.0, liquid_class="Water Free Single", columns=sorted_cols)
    head.return_tips(empty, columns=sorted_cols)
    head.drop_adapter()

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    for col in (1, 4, 7, 10):
        assert _vol(report, "Dest", f"A{col}") == pytest.approx(20.0)
    assert _vol(report, "Dest", "A2") == pytest.approx(0.0)
    # The empty box ends holding exactly the sorted columns; the full box is
    # drained of the four it gave up.
    assert empty.columns_present == {1, 4, 7, 10}
    assert full2.columns_present == {5, 6, 7, 8, 9, 10, 11, 12}


def test_partial_pickup_rejects_interior_column() -> None:
    """A single column can only be peeled from the current outer filled edge;
    an interior column of a full box is physically impossible and must raise."""
    from fluentvibe.simulator.invariants import MissingTipsError

    wt, src, dst, _ = _mca_worktable()
    full2 = wt.place(MCA100Box("FullTips2", catalog="MCA96, 100ul, Box"), "Nest", 6)
    wt.group("Bad peel")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(full2, columns=[5])  # interior of a full box — not an edge
    with pytest.raises(MissingTipsError):
        wt.simulate()


def test_double_full_pickup_still_reports_empty() -> None:
    """Two full pickups with no return still raise tip_box_empty (regression)."""
    from fluentvibe.simulator.invariants import MissingTipsError

    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.pick_up(tip_box)  # nothing returned — box is empty
    with pytest.raises(MissingTipsError):
        wt.simulate()


# ── Render → decompile round-trip (closes the loop) ─────────────────

def _roundtrip_aspirate_columns(columns: list[int], tmp_path: Path) -> list[AspirateStep]:
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 15.0, liquid_class="Water Free Single", columns=columns)
    head.return_tips()

    protocol = wt.to_protocol()
    protocol.worktable_guid = "00000000-0000-0000-0000-000000000000"
    protocol.worktable_name = "TestWorkspace"
    out = tmp_path / "rt.xscr"
    out.write_text(Renderer().render(protocol), encoding="utf-8")

    parsed = parse_xscr(out)
    return [
        step
        for group in parsed.groups
        for step in group.steps
        if isinstance(step, AspirateStep)
    ]


def test_roundtrip_recovers_contiguous_columns(tmp_path: Path) -> None:
    aspirates = _roundtrip_aspirate_columns([7, 8, 9, 10, 11, 12], tmp_path)
    assert any(step.columns == [7, 8, 9, 10, 11, 12] for step in aspirates)


def test_roundtrip_recovers_sparse_columns(tmp_path: Path) -> None:
    aspirates = _roundtrip_aspirate_columns([1, 3, 5, 7, 9, 11], tmp_path)
    assert any(step.columns == [1, 3, 5, 7, 9, 11] for step in aspirates)


def test_roundtrip_full_plate_stays_raw(tmp_path: Path) -> None:
    """A full-plate aspirate must not be promoted to a modeled columns= step."""
    wt, src, dst, tip_box = _mca_worktable()
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 15.0, liquid_class="Water Free Single")  # no columns
    head.return_tips()

    protocol = wt.to_protocol()
    protocol.worktable_guid = "00000000-0000-0000-0000-000000000000"
    protocol.worktable_name = "TestWorkspace"
    out = tmp_path / "rt_full.xscr"
    out.write_text(Renderer().render(protocol), encoding="utf-8")

    parsed = parse_xscr(out)
    modeled = [s for g in parsed.groups for s in g.steps if isinstance(s, AspirateStep)]
    assert modeled == []  # full-plate aspirate stays raw-preserved
