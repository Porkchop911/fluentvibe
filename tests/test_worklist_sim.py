"""The simulator applies worklist transfers (CSV via Worklist Import, or GWL)."""

import pytest

from fluentvibe import Plate96, Reagent, Worktable
from fluentvibe.simulator.worklist_sim import well_address


def _deck(tmp_path):
    wt = Worktable(name="wl")
    wt.worklist_dir = str(tmp_path)
    wt.group("Labware Placement")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_all(Reagent("Sample"), 50)
    wt.group("Pick")
    return wt


def _volume(wt, label, address):
    return sum(layer.volume_ul for layer in wt.snapshots[-1].labware(label).well(address).layers)


def test_csv_worklist_moves_liquid(tmp_path):
    (tmp_path / "pick.csv").write_text(
        "SourceLabel,SourcePosition,DestLabel,DestPosition,Volume\n"
        "Source,A1,Dest,B2,12.5\nSource,A1,Dest,C3,7\nSource,2,Dest,D4,5\n", encoding="utf-8")
    wt = _deck(tmp_path)
    wt.worklist("pick.csv", well_positions="alphanumeric")
    wt.simulate()
    assert _volume(wt, "Dest", "B2") == pytest.approx(12.5)
    assert _volume(wt, "Dest", "C3") == pytest.approx(7)
    assert _volume(wt, "Source", "A1") == pytest.approx(30.5)
    assert _volume(wt, "Source", "B1") == pytest.approx(45)       # position 2 = B1 (column-major)


def test_gwl_worklist_overdraw_fails(tmp_path):
    (tmp_path / "pick.gwl").write_text("A;Source;;;A1;;60;;;;\nD;Dest;;;A1;;60;;;;\nW;\n", encoding="utf-8")
    wt = _deck(tmp_path)
    wt.worklist(str(tmp_path / "pick.gwl"))
    with pytest.raises(Exception, match="short by"):
        wt.simulate()


def test_missing_labware_is_skipped_like_fluentcontrol(tmp_path):
    (tmp_path / "pick.gwl").write_text("A;Elsewhere;;;A1;;5;;;;\nD;Dest;;;A1;;5;;;;\nW;\n", encoding="utf-8")
    wt = _deck(tmp_path)
    wt.worklist(str(tmp_path / "pick.gwl"))
    wt.simulate()
    assert any("Elsewhere" in w for w in wt.simulation_report.warnings)
    assert _volume(wt, "Dest", "A1") == 0


def test_worklist_absent_at_simulation_time_is_not_an_error(tmp_path):
    wt = _deck(tmp_path)
    wt.worklist("later.csv", well_positions="alphanumeric")
    wt.simulate()
    assert _volume(wt, "Dest", "A1") == 0


def test_numeric_positions_are_column_major():
    plate = Plate96("P", catalog="96 Well Flat")
    assert [well_address(plate, p) for p in ("1", "8", "9", "96", "A12")] == ["A1", "H1", "A2", "H12", "A12"]
