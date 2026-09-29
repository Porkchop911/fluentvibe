---
name: api-blocks
axis: api
description: Verified building blocks (fluentvibe.blocks) — physical primitives (add, transfer, remove, mix, magnet on/off, operator hand-off) that compose any plate protocol, plus the spri_cleanup macro; correct tip use and derived volumes. Prefer them over writing stages step by step.
always_on: true
---
## `fluentvibe.blocks` — call a block instead of writing the stage

Each block emits a complete, simulator-tested stage: its own `wt.group(...)`s,
adapter mount/drop, tip handling that never touches two samples with one tip,
and derived volumes. Hand-writing these stages is where drafts go wrong (tips
reused across samples, eluate never recovered, thermal-cycler steps written as
waits). Import at the top of the file:

```python
from fluentvibe.blocks import spri_cleanup, stamp, distribute_reagent, add_reagent, pool_columns, offdeck_step, thermal_step
```

**Primitives first.** Any plate protocol is a sequence of physical primitives;
write the document's steps with them in its order. `spri_cleanup` is a macro —
use it only for a SPRI/AMPure clean-up that binds DNA, washes, **elutes** and
recovers the eluate into a new plate. Bead work that keeps the beads
(streptavidin capture, bead washes, probe immobilisation) is primitives:

| Step | Block |
|---|---|
| add a reagent to every well | `distribute_reagent` (FCA) / `add_reagent` (MCA96, bulk liquids) |
| wells → new plate, well to well | `stamp` |
| liquid out to waste (supernatant, used wash) | `remove_liquid` |
| mix in place (resuspend beads) | `mix_wells` |
| magnet on / off | `separate` / `release` |
| operator step (incubation off deck, spin) | `offdeck_step` |

```python
# Bead wash: magnet on, discard, magnet off, add buffer, resuspend.
separate(wt, plate=work, magnet=magnet, settle_seconds=120, name="Wash 1: magnet")
remove_liquid(wt, plate=work, waste=waste, volume_ul=18, tips=work_tips, liquid_class=LC, name="Wash 1: discard")
release(wt, plate=work, to=("Nest61mm_Pos", 1), name="Wash 1: off magnet")
add_reagent(wt, reagent_source=bw_trough, plate=work, volume_ul=20, reagent_tips=reagent_tips,
            liquid_class=LC, name="Wash 1: B&W buffer")
mix_wells(wt, plate=work, tips=work_tips, volume_ul=16, cycles=5, name="Wash 1: resuspend")
```

`work_tips` is the plate's own MCA96 box for everything that touches the wells
(`remove_liquid`, `mix_wells`): channel *i* only ever meets well *i*. `release`
moves the plate back to its home position (where it was placed).

**Reagents go through the FCA, bulk liquids through the MCA96.** Beads,
buffers, master mixes and kit reagents come from slim troughs (`25ml_short` /
`100ml`) or tubes via the FCA (`distribute_reagent`, `spri_cleanup(...,
fca_tips=...)`); the MCA96 adds only cheap bulk liquids (ethanol, water, wash)
from SBS reservoirs and does plate-to-plate work.

Blocks take plain numbers for volumes (not variable names) and declare them as
FluentControl variables themselves, named after the call's `name`
(`name="PCR clean-up"` → `PCR_CLEAN_UP_BEAD_VOLUME_UL` …), so the protocol stays
editable in FluentControl. Give every block call its own `name`; do not declare
variables for block values yourself. `liquid_class` is
used for transfers; mixing uses `mix_liquid_class` (default `"Water Mix"`,
which has the Mix section FluentControl requires). They raise
`BlockError` with a fix-oriented message if a precondition is missing — read it
and fix the setup, do not work around it.

### `spri_cleanup` — magnetic bead cleanup (full plate)

```python
beads = wt.place(Trough25mL("Beads", catalog="25ml_short"), "WS_100ml_1", 2)         # FCA
eb = wt.place(Trough25mL("EB", catalog="25ml_short"), "WS_100ml_1", 3)               # FCA
ethanol = wt.place(Trough100mL("Ethanol", catalog="60ml SBS MCA96"), "Nest61mm_Pos", 10)  # MCA96
vols = spri_cleanup(
    wt,
    sample_plate=samples, magnet=magnet, bead_source=beads, wash_source=ethanol,
    elution_source=eb, waste=waste, eluate_plate=clean_plate,
    reagent_tips=mca_reagent_tips, sample_tips=mca_sample_tips, eluate_tips=mca_eluate_tips,
    fca_tips=fca_reagent_tips,
    sample_volume_ul=20.0, bead_ratio=1.8, elution_volume_ul=15.0,
    wash_volume_ul=150.0, wash_count=2, liquid_class="Water Free Single",
    name="PCR clean-up",
)
```

Does bind → magnet → remove supernatant → ethanol washes → dry → elute **off**
the magnet → back **onto** the magnet → move the eluate to `eluate_plate`.
With `fca_tips` (an FCA tip box) the FCA adds beads and elution buffer from
their slim troughs; the MCA96 does the ethanol (`reagent_tips`) and all
sample work. Needs: samples tagged `Reagent(..., role="analyte")`, beads
`role="bead_carrier"`, elution buffer `role="eluent"`; **three different MCA96
tip boxes** (reagent, sample, eluate); the MCA adapter not mounted. The product
continues from `eluate_plate`, never from `sample_plate`.

### `distribute_reagent` — reagent into every well (FCA)

`distribute_reagent(wt, source=mastermix_trough, plate=samples, volume_ul=10.0, tips=fca_tips, liquid_class=LC, name="Add master mix")`
— the FCA dispenses column by column from a slim trough; one set of 8 tips
(reagent contact only, dispensed from above), trips above tip capacity. The
default way to add any reagent.

### `stamp` — well-to-well plate copy (MCA96)

`stamp(wt, source=barcode_plate, dest=samples, volume_ul=1.0, tips=box, liquid_class=LC, mix_cycles=5)`
— A1→A1 … H12→H12, optional mix. Use a tip box per stamp (or reuse it only for
the same samples).

### `add_reagent` — bulk liquid to every well (MCA96)

`add_reagent(wt, reagent_source=sbs_reservoir, plate=samples, volume_ul=50.0, reagent_tips=box, liquid_class=LC, mix_tips=other_box, mix_cycles=5)`
— only for cheap bulk liquids (water, ethanol, wash buffer) from an SBS
reservoir (`60ml SBS MCA96` / `300ml SBS`); reagents use `distribute_reagent`.
Reagent tips dispense from above and stay clean; mixing needs a separate
`mix_tips` box.

### `pool_columns` — pool plate columns (LiHa, 8 channels)

`pool_columns(wt, source=samples, dest=pool_plate, volume_ul=10.0, tips=fca_box, liquid_class=LC, dest_column=1)`
— fresh tips per column; `dest` column 1 ends with 8 row pools (row A = all
row-A samples). A single tube pool needs a final operator step
(`offdeck_step`).

### `pool_wells` — named wells into one well (FCA)

`pool_wells(wt, source=samples, dest=pool_plate, volume_ul=5.0, tips=fca_box, liquid_class=LC, source_wells=samples.first_wells(24), dest_well="A1")`
— fresh tips per source column; for pools that are not whole columns.

### `transfer_volumes` / `distribute_volumes` — a different volume per well (FCA)

`distribute_volumes(wt, source=water_trough, plate=norm_plate, volumes={"A1": 7.4, "B1": 5.7}, tips=fca_box, liquid_class=LC, name="Diluent")`
`transfer_volumes(wt, source=samples, dest=norm_plate, volumes={"A1": 1.6, "B1": 3.3}, tips=fca_box, liquid_class=LC, name="Samples")`
— normalisation and any per-well volumes; `distribute_volumes` is a reagent from one source, `transfer_volumes`
is well to same well with fresh tips per sample. No worklist file needed.

### `offdeck_step` — operator hand-off

`offdeck_step(wt, "Run 30 C 2 min, 80 C 2 min on the thermal cycler, then return the plate.", labware=samples, handoff=("Nest61mm_Pos", 10), name="Tagmentation")`
— gripper moves the plate to a reachable slot, the run pauses with the
instruction, then the plate goes home. Use for every step that happens away from
the deck (external thermal cycler, centrifuge, Qubit, ice, flow cell). Never
model such a step as `wt.wait(...)`. If the deck has an integrated ODTC, drive it
with `wt.odtc_*` instead.

`labware`/`handoff` are optional: `offdeck_step(wt, "Replace the tip racks.", name="Operator: s3")`
just pauses the run with the instruction (tip-rack swaps, reagent top-ups).
`wt.add_comment(...)` + `wt.wait(...)` does **not** stop for the operator.

### `thermal_step` — thermal program (ODTC or operator)

`thermal_step(wt, plate, "30 C 2 min, 80 C 2 min", odtc_position=("Inheco_Pos", 4), method_name="TAG", name="Tagmentation")`
— on a deck with an Inheco ODTC: door, gripper in, run the stored method, gripper
out. Without `odtc_position` it becomes an operator hand-off (`offdeck_step`).
Use it for every thermal-cycler step; never `wt.wait`.
