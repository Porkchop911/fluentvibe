---
name: family-reagent-distribution
axis: family
description: Bulk-distribute reagent from a reservoir into the wells of a plate (broth/LB/media dispensing, plate filling, buffer aliquoting); also partitioned multi-mastermix distribution and tube-rack destinations. Select for "dispense X into all wells", plate filling, media distribution, mastermix layout, or reagent aliquoting requests.
always_on: false
---
## Canonical workflow: reagent distribution / plate filling

Fill a plate from a single reservoir with a fixed per-well volume. Pick the head
by throughput; adapt the volume and well count to the request.

1. **Variables** (top-level): `DISPENSE_VOLUME_UL` (per well) and one
   liquid-class variable (`LIQUID_CLASS_REAGENT`), default + sim value
   `"Water Free Single"`. Compute the reservoir fill
   (`wells × DISPENSE_VOLUME_UL × 1.1` for tip/dead volume) — use the `100ml`
   trough for high totals, `25ml_short` for small ones.
2. **Labware Placement** group: source `Trough` (the reagent reservoir),
   destination `Plate96`, and a tip box for the chosen head. For the LiHa path
   place an **FCA** (not MCA) DiTi box; for the MCA96 path place an MCA96 tip
   box:
   ```python
   from fluentvibe import TipBox, MCA100Box
   # LiHa (column-wise distribute):
   fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
   # OR MCA96 (whole-plate):
   tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
   ```
3. **Distribute**:

Whole-plate at once (MCA96 — fastest, all 96 wells in one dispense):
```python
head = wt.mca96
head.mount_adapter(); head.pick_up(tips)
head.aspirate(reservoir, "DISPENSE_VOLUME_UL", liquid_class="LIQUID_CLASS_REAGENT")
head.dispense(plate, "DISPENSE_VOLUME_UL", liquid_class="LIQUID_CLASS_REAGENT")
head.return_tips(tips); head.drop_adapter()
```

Column-wise with the LiHa (when you want tip reuse across columns, or fewer
channels):
```python
head = wt.liha
head.get_tips(fca_tips)
with wt.loop(times=12, name="Distribute reagent", loop_variable="col"):
    head.aspirate(reservoir, "DISPENSE_VOLUME_UL", liquid_class="LIQUID_CLASS_REAGENT")
    head.dispense(plate, "DISPENSE_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_REAGENT", well_offset="(col-1)*8")
head.drop_tips()
```

## Variants

- **Multiple plates:** repeat the distribute group per destination plate (place
  each as its own `Plate96`); keep one reservoir fill that covers the total.
- **Control column with fresh tips:** the Opentrons broth protocols reuse tips
  for columns 1–11 and use fresh tips for column 12. To mirror that, run the
  column loop `times=11`, then `drop_tips()` / `get_tips()` and dispense the
  last column separately.
- **Manual inoculation step** (e.g. LB distribution then add cells): the Tecan
  only does the distribution; mark the manual step with
  `wt.add_comment("Manually inoculate wells, then move plate off-deck to incubate")`.
- **Partitioned multi-mastermix** (`sci-lucif-assay2`): when several distinct
  mixes each fill a contiguous block of columns, declare `NUM_MASTERMIX`
  (e.g. choice 1/2/3/4/6/12) and derive `COLS_PER_MIX = 12 // NUM_MASTERMIX`.
  Loop the source wells of a multi-well rack/`Plate96` (outer
  `wt.loop(times=NUM_MASTERMIX)`), inner column dispense into the contiguous
  destination block with a `well_offset` expression; drop/renew tips between
  mixes to prevent cross-contamination.
- **Tube-rack destination** (`4a0be6`): when filling tubes rather than a plate,
  map the rack to an approved trough, or add a `TubeRack` labware to the
  whitelist (resolve the real catalog name via `fluentvibe.catalog`
  `find_components`). Distribute single-channel with a native
  `wt.loop(times=N, loop_variable="well_idx")` and explicit well addressing.
- **Per-well variable fills:** if each well needs a *different* volume, that is a
  worklist, not this scalar loop — see `api-worklists`.
