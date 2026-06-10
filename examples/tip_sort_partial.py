"""Partial-tip sorting + partial-column pipetting on the MCA-96 (EVA, 12x8).

The geometry, verified against the FluentControl ``PartialMCAexamples``
reference ("pickup by column, sorting"):

* ``pick_up``/``return_tips`` take ``columns`` = the 1-based **box columns** to
  address. The physical ``PartialColumnOffset`` is **derived**, never passed:
  FluentControl stores it as ``head_width - max(columns)`` (box col 1 -> 11,
  col 12 -> 0), and the well-selection ``FirstTip..LastTipXPosition`` carries
  the actual columns. Verified: col 1 -> offset 11, X=1; cols 7-12 -> offset 0,
  X=7..12.
* A single column can only be peeled from the box's **current left-most or
  right-most filled** column, so the head's idle channels overhang empty space.
  A full box: col 1 or col 12. After col 1 is gone the left edge is col 2, etc.
* Setting tips back into an *empty* box has no such constraint — any target
  column is fine (nothing to collide with).
* To *use* a sorted box you pick the **whole thing** at once (all filled columns
  in one pickup), pipette over those columns, then return them together.

So the sort peels source columns 1,2,3,4 off the moving left edge and drops them
into target columns 1,4,7,10; the use phase grabs all four sorted columns in one
go.
"""

from fluentvibe import MCA100Box, Plate96, Reagent, Worktable

SORT = ((1, 1), (2, 4), (3, 7), (4, 10))  # (source box col, target box col)
SORTED_COLS = [tgt for _, tgt in SORT]    # [1, 4, 7, 10]


def build_sort_and_pipette() -> Worktable:
    wt = Worktable(name="tip sort + partial pipetting")

    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    full = wt.place(MCA100Box("FullTips", catalog="MCA96, 100ul, Box"), "Nest", 3)
    sorted_box = wt.place(MCA100Box("SortedTips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    sorted_box.is_full = False  # the sort target starts empty
    src.fill_all(Reagent("Sample"), 50.0)

    head = wt.mca96
    head.mount_adapter()

    # ── Sort: peel one column off the moving left edge, drop into 1/4/7/10 ──
    wt.group("Sort tips into columns 1,4,7,10")
    for src_col, tgt_col in SORT:
        head.pick_up(full, columns=[src_col])          # offset derived (11,10,9,8)
        head.return_tips(sorted_box, columns=[tgt_col])  # offset derived (11,8,5,2)

    # ── Use the sorted tips: pick the whole sorted box at once ─────────────
    wt.group("Transfer using the sorted tips")
    head.pick_up(sorted_box, columns=SORTED_COLS)
    head.aspirate(src, 20.0, liquid_class="Water Free Single", columns=SORTED_COLS)
    head.dispense(dst, 20.0, liquid_class="Water Free Single", columns=SORTED_COLS)
    head.return_tips(sorted_box, columns=SORTED_COLS)
    head.drop_adapter()

    return wt


def _vol(report, labware: str, addr: str) -> float:
    return report.final_labware[labware]["wells"].get(addr, {"volume_ul": 0.0})["volume_ul"]


if __name__ == "__main__":
    wt = build_sort_and_pipette()
    wt.simulate()
    report = wt.simulation_report
    print("Dest after sort+transfer (20 uL only in cols 1,4,7,10):")
    for col in (1, 2, 4, 7, 10, 12):
        print(f"  A{col} = {_vol(report, 'Dest', f'A{col}'):.1f}")
    full = wt.snapshots[-1].labware("FullTips")
    srt = wt.snapshots[-1].labware("SortedTips")
    print("FullTips columns left :", sorted(full.columns_present))
    print("SortedTips columns    :", sorted(srt.columns_present))
