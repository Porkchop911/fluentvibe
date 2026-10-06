"""Reservoir -> 96-well fill whose destination wells spell "FV".

Workspace: 1080_DEV_TABLE.  Source: a 100 ml trough at WS_100ml_1.
Destination: a 96-well plate labelled "FV" at Nest61mm_Pos.

A LiHa head cannot address a single well: every aspirate/dispense covers eight
contiguous wells of the plate's flat well index (well_offset).  Drawing letter
strokes therefore needs per-well addressing, which this protocol gets from a
GWL worklist: one CSV row per destination well.  The worklist picks up its own
FCA DiTi, so no wt.liha.get_tips() call is needed.

Run:  python examples/reservoir_to_plate_fv.py
"""

from __future__ import annotations

from pathlib import Path

from fluentvibe import Plate96, Reagent, TipBox, Trough, Worktable

WORKSPACE_NAME = "1080_DEV_TABLE"
WORKSPACE_GUID = "e57462be-de02-4810-b4f7-868add6977c2"
LIQUID_CLASS = "Water Free Single"

SOURCE_LABEL = "Reservoir"
DEST_LABEL = "FV"
VOLUME_UL = 50.0

CSV_PATH = Path(__file__).with_suffix(".csv")
XSCR_PATH = Path(__file__).with_suffix(".xscr")

ROWS = "ABCDEFGH"

# '#' = dispense into that well. Columns 1-4 draw the F, columns 6-12 the V.
PATTERN = [
    "####.#.....#",
    "####.#.....#",
    "#.....#...#.",
    "###...#...#.",
    "###....#.#..",
    "#......#.#..",
    "#.......#...",
    "#.......#...",
]


def target_wells(pattern: list[str] = PATTERN) -> list[str]:
    """Turn the glyph grid into a list of well addresses."""
    wells: list[str] = []
    for row_index, row in enumerate(pattern):
        for col_index, cell in enumerate(row):
            if cell == "#":
                wells.append(f"{ROWS[row_index]}{col_index + 1}")
    return wells


def render_pattern(filled: set[str]) -> str:
    """Render a set of well addresses as an 8 x 12 plate grid."""
    lines = ["    " + "".join(str(col) for col in range(1, 13))]
    for row_letter in ROWS:
        cells = "".join(
            "#" if f"{row_letter}{col}" in filled else "." for col in range(1, 13)
        )
        lines.append(f"{row_letter}   {cells}")
    return "\n".join(lines)


def read_worklist(path: Path) -> list[tuple[str, str, float]]:
    """Read back (dest_well, source_well, volume_ul) from the generated CSV."""
    rows: list[tuple[str, str, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        source_label, source_pos, dest_label, dest_pos, volume = line.split(",")
        rows.append((dest_pos, source_pos, float(volume)))
    return rows


def write_worklist(path: Path) -> int:
    """Write the CSV pick list: one row per destination well."""
    wells = target_wells()
    rows = [f"{SOURCE_LABEL},A1,{DEST_LABEL},{well},{VOLUME_UL:g}" for well in wells]
    path.write_text(
        "SourceLabel,SourcePosition,DestLabel,DestPosition,Volume\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return len(rows)


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        WORKSPACE_NAME,
        workspace_guid=WORKSPACE_GUID,
        auto_place=False,
        protocol_name="Reservoir to plate - FV pattern",
        comment="LiHa worklist fill; destination wells spell FV.",
    )

    wt.group("Labware")
    reservoir = Trough(SOURCE_LABEL, catalog="100ml")
    wt.place(reservoir, "WS_100ml_1", 1)
    plate = Plate96(DEST_LABEL, catalog="96 Well Flat")
    wt.place(plate, "Nest61mm_Pos", 1)
    # REQUIRED: an FCA DiTi box matching the worklist diti_type. Without it
    # FluentControl rejects the script when it opens.
    wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)

    # Simulator-only: give the reservoir enough liquid for 32 x 50 uL.
    reservoir.fill_all(Reagent("Buffer"), 5_000.0)

    wt.group("Dispense the FV pattern")
    wt.worklist(CSV_PATH, liquid_class=LIQUID_CLASS, well_positions="alphanumeric")
    return wt


def apply_worklist_to_plate(wt: Worktable, rows: list[tuple[str, str, float]]) -> float:
    """Apply the rows the simulator skipped, so the pattern is visible.

    This is the example doing the arithmetic, not the Simulator: it also proves
    every DestPosition is a real well on the placed plate and fits its capacity.
    """
    state = wt.snapshots[-1]
    plate = state.labware(DEST_LABEL)
    buffer = Reagent("Buffer")
    dispensed = 0.0
    for dest, _source_pos, volume in rows:
        well = plate.well(dest)  # KeyError if the address is not on the plate
        if volume > well.max_volume_ul:
            raise ValueError(f"{dest}: {volume} uL exceeds {well.max_volume_ul} uL")
        well.add_layer(buffer, volume)
        dispensed += volume
    return dispensed


def main() -> None:
    wells = target_wells()
    print(f"Target wells ({len(wells)}): {' '.join(wells)}")
    rows_written = write_worklist(CSV_PATH)
    print(f"Worklist: {rows_written} rows -> {CSV_PATH}")

    # The worklist rows are what FluentControl executes, so verify the pattern
    # from the emitted file rather than from the PATTERN constant.
    rows = read_worklist(CSV_PATH)
    print(f"Pattern as the worklist defines it ({len(rows)} wells):")
    print(render_pattern({row[0] for row in rows}))

    wt = build_worktable()
    wt.simulate()
    report = wt.simulation_report
    print(
        f"\nSimulate: status={report.status} steps={report.total_executed_steps} "
        f"fully_simulated={report.fully_simulated_steps} "
        f"validation_only={report.validation_only_steps} opaque={report.opaque_noop_steps}"
    )
    for warning in report.warnings:
        print(f"  warning: {warning}")
    if report.failure is not None:
        print(f"  failure: {report.failure}")

    final_plate = wt.snapshots[-1].labware(DEST_LABEL)
    unmodeled = {a for a, w in final_plate.wells.items() if w.volume_ul > 0.0}
    print(
        f"\nSimulated plate after the script ({len(unmodeled)} wells filled):\n"
        f"{render_pattern(unmodeled)}\n"
        "The Simulator marks the worklist steps VALIDATION_ONLY "
        "(fluentvibe/simulator/walk.py): it validates them but does not move "
        "liquid for them."
    )

    dispensed = apply_worklist_to_plate(wt, rows)
    filled = {a for a, w in final_plate.wells.items() if w.volume_ul > 0.0}
    print(
        f"\nPlate with the {len(rows)} worklist rows applied by hand "
        f"({dispensed:g} uL dispensed, reservoir held 5000 uL):\n"
        f"{render_pattern(filled)}"
    )

    out = wt.compile(XSCR_PATH)
    print(f"Compiled: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
