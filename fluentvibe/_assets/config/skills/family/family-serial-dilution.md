---
name: family-serial-dilution
axis: family
description: Serial (stepwise) dilution across columns of a 96-well plate — total-mixing-volume and dilution-factor drive derived transfer/diluent volumes; pre-fill diluent, then transfer-and-mix column-to-column. Select for serial dilution, titration curves, dose-response/standard-curve setups, concentration-limiting dilutions, and N-fold dilution series.
always_on: false
---
## Canonical workflow: serial dilution across columns

A standard N-fold serial dilution along the 12 columns of a 96-well plate
(column 1 = most concentrated). The two primary knobs are **dilution factor**
and **total mixing volume**; transfer and diluent volumes are derived.

### Volume model (total-volume conservation)

```
TOTAL_MIXING_VOLUME_UL   — per-well volume after each step (e.g. 150)
DILUTION_FACTOR          — fold factor, e.g. 2 for 1:2, 3 for 1:3
TRANSFER_VOLUME_UL       = TOTAL_MIXING_VOLUME / DILUTION_FACTOR    (derived)
DILUENT_VOLUME_UL        = TOTAL_MIXING_VOLUME - TRANSFER_VOLUME     (derived)
NUM_DILUTIONS            — number of transfer steps (max 11; max 10 if blank column used)
BLANK_COLUMN             — bool, add diluent-only blank after last dilution
```

For a 3-fold series at 150 µL total: `TRANSFER = 50`, `DILUENT = 100`.
For a 2-fold (equal-split) series: `TRANSFER == DILUENT`.

### Variables, labware, and step sequence

1. **Variables** (top-level, before any `wt.group`):
   - Primary: `TOTAL_MIXING_VOLUME_UL`, `DILUTION_FACTOR`, `NUM_DILUTIONS`
     (`wt.declare_variable(...)` + `wt.set_sim_value(...)`).
   - Derived: compute `TRANSFER_VOLUME_UL = round(TOTAL / FACTOR, 1)` and
     `DILUENT_VOLUME_UL = TOTAL - TRANSFER` in Python, then declare them too.
   - Mixing: `MIX_VOLUME_UL` (typically `TOTAL_MIXING_VOLUME / 2`).
   - Liquid classes: `LIQUID_CLASS_DILUENT`, `LIQUID_CLASS_SAMPLE`, default
     + sim value `"Water Free Single"`.
   - Compute diluent trough fill for the whole run:
     `(NUM_DILUTIONS + 1) × 8 × DILUENT_VOLUME_UL × 1.1`
     (see labware-and-liquid-classes).

2. **Labware Placement** group: dilution `Plate96`, a diluent `Trough`
   (`25ml_short`), and an **FCA** (not MCA) tip box for the LiHa — place it
   explicitly. The sample/stock is assumed pre-loaded in column 1 (or dispensed
   first from its own source):
   ```python
   from fluentvibe import TipBox
   fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
   ```

3. **Pre-fill diluent** (LiHa, column-wise): dispense `DILUENT_VOLUME_UL`
   into columns 2..(NUM_DILUTIONS+1). If a blank column is requested,
   include column NUM_DILUTIONS+2 as well.

4. **Dilute down the series** (LiHa): from each column, transfer
   `TRANSFER_VOLUME_UL` into the next column and mix. Use a native loop over
   `NUM_DILUTIONS` steps with a `well_offset` expression; do **not** unroll
   with a Python `for`.

5. **Optional blank column**: if `BLANK_COLUMN`, dispense `DILUENT_VOLUME_UL`
   into the first unused column after the dilution series (fresh tips).

```python
head = wt.liha
head.get_tips(fca_tips)

# pre-fill diluent into columns 2..NUM_DILUTIONS+1 (+ blank if requested)
prefill_cols = NUM_DILUTIONS + (1 if BLANK_COLUMN else 0)
with wt.loop(times=prefill_cols, name="Pre-fill diluent", loop_variable="col"):
    head.aspirate(diluent, "DILUENT_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_DILUENT")
    head.dispense(plate, "DILUENT_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_DILUENT", well_offset="col*8")

# carry the dilution down the series, column n -> n+1
with wt.loop(times=NUM_DILUTIONS, name="Serial transfer", loop_variable="col"):
    head.aspirate(plate, "TRANSFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
    head.dispense(plate, "TRANSFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="col*8")
    head.mix(plate, "MIX_VOLUME_UL", cycles=5,
             liquid_class="LIQUID_CLASS_SAMPLE", well_offset="col*8")

# optional blank column (diluent only, no sample carry).
# The series fills columns 2..(NUM_DILUTIONS+1); the first unused column is
# NUM_DILUTIONS+1 in 0-based offset terms (well_offset = (NUM_DILUTIONS+1)*8).
if BLANK_COLUMN:
    head.drop_tips()
    head.get_tips(fca_tips)
    blank_offset = (NUM_DILUTIONS + 1) * 8     # first unused column
    head.aspirate(diluent, "DILUENT_VOLUME_UL", liquid_class="LIQUID_CLASS_DILUENT")
    head.dispense(plate, "DILUENT_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_DILUENT", well_offset=str(blank_offset))

head.drop_tips()
```

### Tip reuse strategy

The default is **one tip for the entire series** (`get_tips` once, `drop_tips`
at end) — this matches the standard serial-dilution technique where carryover
is intentional. If cross-contamination must be avoided (e.g. very high
concentration ratios), drop and pick up fresh tips per column by moving
`head.drop_tips()` / `head.get_tips(fca_tips)` inside the loop.

### Notes

- `well_offset` is a 1-based column expression in wells (8 wells per column),
  so `(col-1)*8` is the source column and `col*8` the destination.
- Use the LiHa (column-wise) for this; the MCA96 cannot address one column
  at a time.
- Air gaps, touch-tip, and blow-out are Opentrons handling details not exposed
  in fluentvibe — drop them silently.
