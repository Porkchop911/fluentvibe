---
name: api-blocks
axis: api
description: Verified building blocks (fluentvibe.blocks) — one call emits a whole, tested stage (bead cleanup, plate stamp, reagent addition, column pooling, operator hand-off) with correct tip use and derived volumes. Prefer them over writing those stages step by step.
always_on: true
---
## `fluentvibe.blocks` — call a block instead of writing the stage

Each block emits a complete, simulator-tested stage: its own `wt.group(...)`s,
adapter mount/drop, tip handling that never touches two samples with one tip,
and derived volumes. Hand-writing these stages is where drafts go wrong (tips
reused across samples, eluate never recovered, thermal-cycler steps written as
waits). Import at the top of the file:

```python
from fluentvibe.blocks import spri_cleanup, stamp, add_reagent, pool_columns, offdeck_step, thermal_step
```

Blocks take plain numbers for volumes (not variable names) and declare them as
FluentControl variables themselves, named after the call's `name`
(`name="PCR clean-up"` → `PCR_CLEAN_UP_BEAD_VOLUME_UL` …), so the protocol stays
editable in FluentControl. Give every block call its own `name`; do not declare
variables for block values yourself. `liquid_class` is
used for transfers; mixing uses `mix_liquid_class` (default `"Water Mix"`,
which has the Mix section FluentControl requires). They raise
`BlockError` with a fix-oriented message if a precondition is missing — read it
and fix the setup, do not work around it.

### `spri_cleanup` — magnetic bead cleanup (MCA96, full plate)

```python
vols = spri_cleanup(
    wt,
    sample_plate=samples, magnet=magnet, bead_source=beads, wash_source=ethanol,
    elution_source=eb, waste=waste, eluate_plate=clean_plate,
    reagent_tips=mca_reagent_tips, sample_tips=mca_sample_tips, eluate_tips=mca_eluate_tips,
    sample_volume_ul=20.0, bead_ratio=1.8, elution_volume_ul=15.0,
    wash_volume_ul=150.0, wash_count=2, liquid_class="Water Free Single",
    name="PCR clean-up",
)
```

Does bind → magnet → remove supernatant → ethanol washes → dry → elute **off**
the magnet → back **onto** the magnet → move the eluate to `eluate_plate`.
Needs: samples tagged `Reagent(..., role="analyte")`, beads
`role="bead_carrier"`, elution buffer `role="eluent"`; **three different MCA96
tip boxes** (reagent, sample, eluate); the MCA adapter not mounted. The product
continues from `eluate_plate`, never from `sample_plate`.

### `stamp` — well-to-well plate copy (MCA96)

`stamp(wt, source=barcode_plate, dest=samples, volume_ul=1.0, tips=box, liquid_class=LC, mix_cycles=5)`
— A1→A1 … H12→H12, optional mix. Use a tip box per stamp (or reuse it only for
the same samples).

### `add_reagent` — trough to every well (MCA96)

`add_reagent(wt, reagent_source=trough, plate=samples, volume_ul=5.0, reagent_tips=box, liquid_class=LC, mix_tips=other_box, mix_cycles=5)`
— reagent tips dispense from above and stay clean; mixing needs a separate
`mix_tips` box.

### `pool_columns` — pool plate columns (LiHa, 8 channels)

`pool_columns(wt, source=samples, dest=pool_plate, volume_ul=10.0, tips=fca_box, liquid_class=LC, dest_column=1)`
— fresh tips per column; `dest` column 1 ends with 8 row pools (row A = all
row-A samples). A single tube pool needs a final operator step
(`offdeck_step`).

### `offdeck_step` — operator hand-off

`offdeck_step(wt, "Run 30 C 2 min, 80 C 2 min on the thermal cycler, then return the plate.", labware=samples, handoff=("Nest61mm_Pos", 10), name="Tagmentation")`
— gripper moves the plate to a reachable slot, the run pauses with the
instruction, then the plate goes home. Use for every step that happens away from
the deck (external thermal cycler, centrifuge, Qubit, ice, flow cell). Never
model such a step as `wt.wait(...)`. If the deck has an integrated ODTC, drive it
with `wt.odtc_*` instead.

### `thermal_step` — thermal program (ODTC or operator)

`thermal_step(wt, plate, "30 C 2 min, 80 C 2 min", odtc_position=("Inheco_Pos", 4), method_name="TAG", name="Tagmentation")`
— on a deck with an Inheco ODTC: door, gripper in, run the stored method, gripper
out. Without `odtc_position` it becomes an operator hand-off (`offdeck_step`).
Use it for every thermal-cycler step; never `wt.wait`.
