---
name: family-cherrypicking
axis: family
description: Cherry-pick samples between plates or tubes from a pick list — per-well variable volumes and arbitrary source-to-destination moves via a worklist (the canonical path), with in-tip dilution, buffer pre-fill, and volume-threshold pipette selection. Select for cherry-picking, hit-picking, sample selection, targeted transfers, CSV-driven well-to-well moves, or subset plating workflows.
always_on: false
---
## Canonical workflow: cherrypicking via worklist (CSV pick list)

Cherrypicking IS per-well variable volumes from a pick list — so the canonical
path is a **worklist**: a CSV with one record per pick (source well, dest well,
volume), executed with `wt.worklist(...)`. Each record carries its own volume
and arbitrary source→dest mapping, so the defining feature is native — no
"uniform approximation." See `api-worklists`.

### Labware Placement

- Source plate(s) — `Plate96` or troughs (labware names must match the CSV's
  `SourceLabel` column)
- Destination plate(s) — `Plate96` (matches `DestLabel`)
- FCA tip box

```python
wt.group("Cherrypick from pick list")
# CSV columns: SourceLabel, SourcePosition, DestLabel, DestPosition, Volume
wt.worklist(r"C:\ProgramData\Tecan\VisionX\Worklists\picklist.csv",
            liquid_class="LIQUID_CLASS_SAMPLE")
```

A worklist `wash` record between transfers gives one-tip-per-pick behavior;
omit it to reuse a tip across consecutive same-source picks.

**Simulator caveat:** worklist steps are VALIDATION_ONLY — the protocol renders
and validates, but `wt.snapshots` will not reflect the per-well transfers. Say
so when shipping a worklist-based cherrypick.

### Fallback: uniform-volume loop (no CSV)

If every pick is the same volume and the picks are regular (e.g. column-wise),
a scalar `wt.loop` is simpler and *is* simulator-walked:

```python
head = wt.liha
with wt.loop(times=NUM_PICKS, name="Cherrypick transfers", loop_variable="i"):
    head.get_tips(fca_tips)
    head.aspirate(source, "PICK_VOLUME_UL", liquid_class="LIQUID_CLASS_SAMPLE")
    head.dispense(dest, "PICK_VOLUME_UL", liquid_class="LIQUID_CLASS_SAMPLE")
    head.drop_tips()
```

### Technique variants

- **In-tip dilution** (`cherrypicking-sick-kids`): declare `DILUENT_VOLUME_UL`
  and, in one tip, aspirate diluent then aspirate sample before dispensing the
  combined volume; follow with `head.mix(...)` in the destination well. Build
  this as a `Gwl` with paired aspirate records per pick, or two worklists.
- **Buffer pre-fill** (`2bba96`): distribute a fixed buffer volume to all
  destination wells first (a scalar `wt.loop` or `reagent_distribution`), then
  run the sample pick worklist.
- **Volume-threshold pipette selection** (`2bba96`): split the pick list into
  ≤20 µL and >20 µL groups and route each to the appropriate LiHa
  configuration (or branch with `wt.conditional` on the row volume).

### Tip reuse strategy

- **One tip per pick** (default): safest, avoids cross-contamination between
  unrelated samples. Consumes one tip well per pick from the FCA box.
- **Reuse within groups**: if multiple picks share the same source well, reuse
  the tip across that group and change only between groups.
