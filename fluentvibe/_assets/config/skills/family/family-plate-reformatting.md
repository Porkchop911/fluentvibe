---
name: family-plate-reformatting
axis: family
description: Move or rearrange wells between 96-well plates — whole-plate 1:1 copy (MCA96) or selective per-column cherry-pick/consolidation (LiHa). Select for plate copying, reformatting, pooling, consolidation, or rearranging samples between plates.
always_on: false
---
## Canonical workflow: plate reformatting

Choose the head by the shape of the move:

- **Whole-plate 1:1 copy (MCA96):** every well of the source goes to the same
  address on the destination. One aspirate + one dispense touches all 96 wells
  — no loop.
- **Selective / per-column (LiHa):** copy, pool, or rearrange specific columns.
  Iterate columns with a native loop and `well_offset`; do not unroll with a
  Python `for`.

1. **Variables** (top-level): `TRANSFER_VOLUME_UL` and one liquid-class
   variable (`LIQUID_CLASS_TRANSFER`), default + sim value `"Water Free Single"`.
2. **Labware Placement** group: source `Plate96`, destination `Plate96`, and a
   tip box matching the chosen head — **MCA** box for `wt.mca96`, **FCA** box
   for `wt.liha` (never cross them; the LiHa rejects MCA boxes):
   ```python
   from fluentvibe import TipBox, MCA100Box
   # MCA96 path:
   tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
   # LiHa path:
   fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
   ```
3. **Reformat** group:

Whole-plate copy (MCA96):
```python
head = wt.mca96
head.mount_adapter()
head.pick_up(tips)
head.aspirate(source, "TRANSFER_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER")
head.dispense(dest, "TRANSFER_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER")
head.return_tips(tips)
head.drop_adapter()
```

Per-column cherry-pick / consolidation (LiHa):
```python
head = wt.liha
head.get_tips(fca_tips)
with wt.loop(times=12, name="Reformat columns", loop_variable="col"):
    head.aspirate(source, "TRANSFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_TRANSFER", well_offset="(col-1)*8")
    head.dispense(dest, "TRANSFER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_TRANSFER", well_offset="(col-1)*8")
head.drop_tips()
```

For pooling/consolidation, dispense several source columns into one destination
column by giving the dispense a fixed `well_offset` while the aspirate offset
advances — adapt the offsets to the requested mapping. Use a fresh tip set per
distinct sample group to avoid carryover.
