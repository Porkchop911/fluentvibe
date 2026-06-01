from pathlib import Path

import pytest

from fluentvibe.compiler import render_protocol
from fluentvibe.decompiler.xscr_parser import parse_xscr
from fluentvibe.ir.schema import (
    ExecuteWorklistStep,
    LoadWorklistStep,
    Protocol,
    WorklistColumnMapping,
    WorklistImportStep,
)
from fluentvibe.worklists import Gwl, WorklistFormatError, infer_csv_well_positions, infer_gwl_well_positions
from fluentvibe.worktable import Worktable


def _protocol(*steps):
    return Protocol(
        name="worklist-test",
        worktable_guid="2baf8c89-406a-455a-9a91-6378fc41a0a5",
        worktable_name="SAT_Fluent_780_Rev4",
        groups=[{"name": "Steps", "steps": list(steps)}],
    )


def test_gwl_builder_writes_documented_records(tmp_path):
    path = tmp_path / "all.gwl"
    gwl = Gwl()
    gwl.aspirate("S1", position="9", volume=10.1, rack_type="Microplate")
    gwl.dispense("D1", position="1", volume=6, rack_type="Microplate")
    gwl.wash()
    gwl.decontamination_wash()
    gwl.flush()
    gwl.break_records()
    gwl.set_diti_type(2)
    gwl.comment("hello")
    gwl.start_timer(1)
    gwl.wait_for_timer(1, 5)
    gwl.reagent_distribution(
        source=["S1", "", "", "1", "8"],
        destination=["D1", "", "", "1", "8"],
        volume=10,
    )
    gwl.sample_transfer(
        source=["S1", "", "", "1", "8"],
        destination=["D1", "", "", "1", "8"],
        volume=10,
        sample_count=8,
        replication_count=1,
    )

    gwl.write(path)
    lines = path.read_text().splitlines()

    assert lines[0] == "A;S1;;Microplate;9;;10.1;;;;"
    assert lines[2] == "W;"
    assert lines[3] == "WD;"
    assert lines[8] == "TS;1"
    assert lines[9] == "TW;1;5"
    assert lines[10].startswith("R;")
    assert lines[11].startswith("T;")


def test_infer_gwl_alphanumeric_and_numeric(tmp_path):
    alpha = tmp_path / "alpha.gwl"
    alpha.write_text("A;S1;;;A1;;10;;;;\nD;D1;;;B1;;10;;;;\nW;\n")
    numeric = tmp_path / "numeric.gwl"
    numeric.write_text("A;S1;;;1;;10;;;;\nD;D1;;;9;;10;;;;\nW;\n")
    mixed = tmp_path / "mixed.gwl"
    mixed.write_text("A;S1;;;1;;10;;;;\nD;D1;;;A1;;10;;;;\n")

    assert infer_gwl_well_positions(alpha) == "alphanumeric"
    assert infer_gwl_well_positions(numeric) == "numeric"
    with pytest.raises(WorklistFormatError):
        infer_gwl_well_positions(mixed)


def test_infer_csv_uses_mapped_position_columns(tmp_path):
    csv_path = tmp_path / "transfers.csv"
    csv_path.write_text(
        "Source Labware Label,Source Position,Destination Labware Label,Destination Position,Volume\n"
        "Smalltrough,A1,96wellplate,B1,5\n"
    )

    assert infer_csv_well_positions(csv_path, start_line=2) == "alphanumeric"


def test_worktable_worklist_csv_emits_import_load_execute(tmp_path):
    csv_path = tmp_path / "transfers.csv"
    csv_path.write_text(
        "Source Labware Label,Source Position,Destination Labware Label,Destination Position,Volume\n"
        "Smalltrough,A1,96wellplate,B1,5\n"
    )
    wt = Worktable(name="wl")
    wt.workspace_name = "SAT_Fluent_780_Rev4"
    wt.workspace_guid = "2baf8c89-406a-455a-9a91-6378fc41a0a5"

    wt.worklist(csv_path, liquid_class="Water Free Single")
    protocol = wt.to_protocol()
    steps = protocol.groups[0].steps

    assert isinstance(steps[0], WorklistImportStep)
    assert isinstance(steps[1], LoadWorklistStep)
    assert isinstance(steps[2], ExecuteWorklistStep)
    assert steps[0].gwl_path.endswith("transfers.gwl")
    assert steps[1].well_positions == "alphanumeric"
    assert str(csv_path) in protocol.file_references
    assert str(csv_path.with_suffix(".gwl")) in protocol.file_references


def test_render_worklist_commands_include_paths_and_file_references(tmp_path):
    csv_path = tmp_path / "transfers.csv"
    gwl_path = tmp_path / "transfers.gwl"
    protocol = _protocol(
        WorklistImportStep(
            csv_path=str(csv_path),
            gwl_path=str(gwl_path),
            start_line=2,
            columns=[
                WorklistColumnMapping(column_name="A", column_index=0, gwl_index="SourceLabel"),
                WorklistColumnMapping(column_name="B", column_index=1, gwl_index="SourcePosition"),
            ],
        ),
        LoadWorklistStep(
            gwl_path=str(gwl_path),
            liquid_class="Water Free Single",
            well_positions="alphanumeric",
        ),
        ExecuteWorklistStep(),
    )
    protocol.file_references = [str(csv_path), str(gwl_path)]

    xml = render_protocol(protocol)

    assert f"<File>{csv_path}</File>" in xml
    assert f'<CsvExpressionOrFilename>"{csv_path}"</CsvExpressionOrFilename>' in xml
    assert f'<WorklistPath>"{gwl_path}"</WorklistPath>' in xml
    # LoadWorklistStatementDataV4 in FC build 3.5.7 has no UseWellIndexNumbers
    # element; emitting it makes FC reject the command on load ("load operation
    # failed during processing the script commands"). The ground-truth example
    # scripts (e.g. 1954eff2…) omit it, so we must too.
    assert "<UseWellIndexNumbers>" not in xml
    assert "<ExecuteWorklistStatementDataV1>" in xml


def test_worklist_resolves_liquid_class_variable_and_deck_diti():
    """worklist liquid_class accepts a declared variable (resolved to its value),
    and the default diti_type is a tip the 780 deck provides (FCA, 1000ul SBS).
    Authoring-level fix: the LIQUID_CLASS_* idiom must not leak the placeholder
    into FC's <LiquidClassName>, and FCA, 50ul SBS is not on the deck."""
    wt = Worktable(name="wl")
    wt.workspace_name = "SAT_Fluent_780_Rev4"
    wt.workspace_guid = "2baf8c89-406a-455a-9a91-6378fc41a0a5"
    wt.declare_variable("LIQUID_CLASS_TRANSFER", "Water Free Single")

    wt.load_worklist("picklist.gwl", liquid_class="LIQUID_CLASS_TRANSFER", well_positions="alphanumeric")
    protocol = wt.to_protocol()
    load = next(s for s in protocol.groups[0].steps if isinstance(s, LoadWorklistStep))

    assert load.liquid_class == "Water Free Single"          # variable resolved, not the name
    assert "FCA, 1000ul SBS" in load.diti_type               # deck-valid default
    assert "50ul" not in load.diti_type


def test_decompile_user_worklist_script_when_available():
    path = Path(r"C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\1954eff2-5889-4600-8f29-379d67e900ce.xscr")
    if not path.exists():
        pytest.skip("local FluentControl example is not installed")

    protocol = parse_xscr(path)
    typed = [
        step
        for group in protocol.groups
        for step in group.steps
        if isinstance(step, (WorklistImportStep, LoadWorklistStep, ExecuteWorklistStep))
    ]

    assert [type(step) for step in typed] == [WorklistImportStep, LoadWorklistStep, ExecuteWorklistStep]
    assert typed[0].start_line == 2
    assert typed[1].liquid_class == "Water Free Single"
