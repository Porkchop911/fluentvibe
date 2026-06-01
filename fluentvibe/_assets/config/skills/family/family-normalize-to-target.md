---
name: family-normalize-to-target
axis: family
description: Normalize sample wells toward a target concentration by adding diluent/buffer, then transferring a fixed aliquot to a destination plate. Uniform/column-wise via scalar loops; true per-well volumes from a CSV via a worklist. Select for dilution-to-target, buffer top-up, concentration normalization, BCA normalization from CSV, DNA/cDNA-library normalization with dual pipettes, or equalization workflows.
always_on: false
---
## Canonical workflow: normalize to target (uniform)

Add a buffer/diluent to a plate and consolidate a fixed aliquot into a
destination plate. Two common variants exist — single-pipette (BCA-style) and
dual-pipette (DNA-normalization style). Adapt volumes; keep the structure.

### Volume model

```
TARGET_CONC              — desired final concentration (informational, not enforced by fluentvibe)
TRANSFER_VOLUME_UL       — fixed sample aliquot moved from source to dest per well
BUFFER_VOLUME_UL         — diluent added per well after transfer
TOTAL_FINAL_VOLUME_UL    = TRANSFER_VOLUME + BUFFER_VOLUME               (derived)
```

In the true per-well case, `BUFFER_VOLUME` differs per well based on starting
concentration. That is the **worklist** path (Variant C below); the uniform /
column-wise variants (A, B) cover the cases where the volume is the same
everywhere or groups by column.

### Variant A: single-pipette (BCA-style)

One LiHa/MCA96 head does both the sample transfer and buffer add with a tip
change in between.

1. **Variables**: `TRANSFER_VOLUME_UL`, `BUFFER_VOLUME_UL`,
   `LIQUID_CLASS_SAMPLE`, `LIQUID_CLASS_BUFFER` (default + sim value
   `"Water Free Single"`). Compute trough fill:
   `96 × BUFFER_VOLUME_UL × 1.1`.
2. **Labware Placement**: source `Plate96`, destination `Plate96`, buffer
   `Trough` (`25ml_short`), one tip box per head used.
3. **Transfer sample** then **add buffer + mix**:

```python
head = wt.mca96
head.mount_adapter()
# transfer sample aliquot
head.pick_up(tips_sample)
head.aspirate(source, "TRANSFER_VOLUME_UL", liquid_class="LIQUID_CLASS_SAMPLE")
head.dispense(dest, "TRANSFER_VOLUME_UL", liquid_class="LIQUID_CLASS_SAMPLE")
head.return_tips(tips_sample)
# add buffer and mix
head.pick_up(tips_buffer)
head.aspirate(buffer_src, "BUFFER_VOLUME_UL", liquid_class="LIQUID_CLASS_BUFFER")
head.dispense(dest, "BUFFER_VOLUME_UL", liquid_class="LIQUID_CLASS_BUFFER")
head.mix(dest, "TRANSFER_VOLUME_UL", cycles=10,
         liquid_class="LIQUID_CLASS_BUFFER")
head.return_tips(tips_buffer)
head.drop_adapter()
```

### Variant B: dual-pipette (DNA-normalization style)

Two pipettes work in sequence — a small-volume pipette transfers the DNA/sample
aliquot, then a large-volume pipette adds diluent. This matches protocols where
sample and buffer volumes differ significantly.

1. **Variables**: same as above plus `MIX_CYCLES` (default 3).
2. **Labware Placement**: source plate, destination plate, diluent trough or
tube rack, separate tip boxes for small and large pipettes.
3. **Step sequence** (LiHa, column-wise with loop):

```python
# small-pipette: transfer sample aliquot per column
head = wt.liha
head.get_tips(tips_small)
with wt.loop(times=12, name="Transfer sample", loop_variable="col"):
    head.aspirate(source, "TRANSFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
    head.dispense(dest, "TRANSFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
head.drop_tips()

# large-pipette: add diluent per column
head.get_tips(tips_large)
with wt.loop(times=12, name="Add diluent", loop_variable="col"):
    head.aspirate(diluent_trough, "BUFFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_BUFFER")
    head.dispense(dest, "BUFFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_BUFFER", well_offset="(col-1)*8")
    head.mix(dest, "MIX_VOLUME_UL", cycles=MIX_CYCLES,
             liquid_class="LIQUID_CLASS_BUFFER", well_offset="(col-1)*8")
head.drop_tips()
```

### Variant C: true per-well normalization via worklist (CSV-driven)

When each well needs a **different** buffer/sample volume (the usual case for
real concentration normalization), drive it with a **worklist** — one record per
well, each carrying its own volume. This is the right path for
`bca_normalization_from_csv`, `ml-normalization-cdna-library`, and any
"normalize from CSV" request. See `api-worklists` for the full DSL.

1. **Inputs**: a CSV pick list with, per row, the destination well plus its
   buffer and sample volumes (standard columns `SourceLabel, SourcePosition,
   DestLabel, DestPosition, Volume`; use a custom `columns=` mapping for a
   buffer-then-sample layout).
2. **Sequence per well** (BCA-style): pre-dispense buffer → aspirate sample →
   dispense sample → mix in destination. A buffer-first worklist plus a sample
   worklist (or one merged worklist) expresses this; `head.mix(...)` after.
3. **Dual-head routing** (cDNA-library style): when sample volumes straddle a
   pipette's range, branch on a volume threshold with `wt.conditional`
   (`if vol > 20`) to a small/large LiHa configuration, or unify under one head
   with the right liquid class.

```python
wt.group("Per-well normalization from CSV")
# buffer first (distinct per-well buffer volumes), then sample, both per-record
wt.worklist(r"C:\ProgramData\Tecan\VisionX\Worklists\norm_buffer.csv",
            liquid_class="LIQUID_CLASS_BUFFER")
wt.worklist(r"C:\ProgramData\Tecan\VisionX\Worklists\norm_sample.csv",
            liquid_class="LIQUID_CLASS_SAMPLE")
```

Declare `"BUFFER_VOLUME_UL"` / `"SAMPLE_VOLUME_UL"` as the per-row worklist
volumes when building the CSV programmatically with `Gwl`.

**Simulator caveat:** worklist steps are VALIDATION_ONLY — the protocol renders
and validates, but `wt.snapshots` will not reflect the per-well volume changes
(Variants A/B, being scalar, *are* walked). State this when shipping Variant C.
