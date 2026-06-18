---
name: family-pooling
axis: family
description: Pool samples from multiple wells or plates into a single destination well or tube — column-wise consolidation and fixed-volume pooling via scalar loops; volume-equalized / arbitrary source-to-destination pooling from a CSV pick list via a worklist. Select for pooling, sample combining, library pool construction, multiplex pooling, normalase/normalization pooling, or consolidation transfers.
always_on: false
select_when:
  - pool
  - pooled
  - pooling
---
## Canonical workflow: pooling (fixed-volume skeleton)

Combine liquid from multiple source wells into a single destination well or tube.
The common pattern is column-wise pooling (all 8 wells of a column → one dest
well) repeated across columns, then final consolidation into tubes.

### Variables

```
POOL_VOLUME_UL         — volume aspirated per source well (uniform approximation)
WELLS_PER_POOL         — number of source wells contributing to each pool (e.g. 8 for a column)
NUM_POOLS              — number of destination pools
NUM_SOURCE_PLATES      — number of input plates being pooled (1-4)
LIQUID_CLASS_SAMPLE    — liquid class for sample, default "Water Free Single"
```

### Labware Placement

- Source plate(s) (`Plate96` × `NUM_SOURCE_PLATES`)
- Pooling plate or collection plate (`Plate96`)
- Final pool tubes (modelled as trough or tube rack)
- FCA tip box — must be **FCA**-class for the LiHa, not MCA:
  ```python
  from fluentvibe import TipBox
  fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
  ```

### Step sequence: column-wise pooling from one plate

```python
head = wt.liha
head.get_tips(fca_tips)

# Pool each column of the source plate into a single well of the pooling plate
with wt.loop(times=NUM_POOLS, name="Pool columns", loop_variable="col"):
    # aspirate from source column and dispense into corresponding pool well
    head.aspirate(source_plate, "POOL_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
    head.dispense(pooling_plate, "POOL_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
head.drop_tips()
```

### Multi-plate pooling (e.g. 4 plates → 4 pools)

When pooling from multiple source plates into separate pools:

```python
# For each source plate, pool its columns into a designated column of the pooling plate
with wt.loop(times=NUM_SOURCE_PLATES, name="Pool from plates", loop_variable="p"):
    with wt.loop(times=12, name="Column-wise pool", loop_variable="col"):
        head.aspirate(source_plates[p], "POOL_VOLUME_UL",
                      liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
        # dest column offset depends on plate index (e.g. columns 1,3,5,7)
        dest_col = p * 2 + 1
        head.dispense(pooling_plate, "POOL_VOLUME_UL",
                      liquid_class="LIQUID_CLASS_SAMPLE",
                      well_offset=f"{dest_col}*8")
```

### Final consolidation (pooling plate → tubes)

After column-wise pooling into the intermediate plate, consolidate each pool
column into a final tube:

```python
head.get_tips(fca_tips)
with wt.loop(times=NUM_POOLS, name="Consolidate to tubes", loop_variable="col"):
    head.aspirate(pooling_plate, "POOL_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
    head.dispense(final_tubes, "POOL_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE")
head.drop_tips()

wt.add_comment("Pools ready for downstream processing (e.g. bead cleanup)")
```

### Homogenization step

Some protocols (e.g. seqWell ExpressPlex) seal the pooling plate and homogenize
on a heater-shaker before final consolidation:

```python
wt.add_comment("Seal pooling plate and homogenize on heater-shaker at 800 RPM, 5 min")
wt.wait(duration_seconds=300)
wt.add_comment("Unseal pooling plate; quick spin to collect droplets")
```

### Volume-equalized / arbitrary pooling via worklist (CSV-driven)

When each source well contributes a **different** volume (normalization-aware
pooling, so every sample is equimolar in the final pool) or the source→dest
mapping is **arbitrary/scattered** (not a clean column→pool), drive it with a
**worklist** — one record per transfer, each with its own source well, dest
well, and volume. This is the path for `sci-idt-normalase` and the pooling half
of `019d4d_2`. See `api-worklists`.

```python
wt.group("Volume-equalized pooling from pick list")
# CSV columns: SourceLabel, SourcePosition, DestLabel, DestPosition, Volume
wt.worklist(r"C:\ProgramData\Tecan\VisionX\Worklists\pool_picklist.csv",
            liquid_class="LIQUID_CLASS_SAMPLE")
```

- Each record carries the per-well volume, so equalized pooling is native — no
  scalar approximation.
- Tip strategy follows pick-list density: a worklist `wash` record between
  transfers gives fresh-tip behavior; omit it for deliberate reuse.
- **Simulator caveat:** worklist steps are VALIDATION_ONLY — the protocol
  renders and validates, but `wt.snapshots` will not reflect the per-well pooled
  volumes (the fixed-volume scalar variants above *are* walked). State this when
  shipping the worklist variant.

### Tip reuse strategy

- **One tip per column** (LiHa): aspirate a full column, dispense into pool well,
  repeat with same tip for next column if sources are from the same plate.
- **Fresh tips between plates**: avoid cross-contamination when pooling from
  different source plates.
