---
name: head-mca96
axis: api
description: The wt.mca96 true 96-channel head — one aspirate/dispense/mix touches all 96 wells at once, with adapter mount/pick-up lifecycle. Supports partial-column pipetting via columns=[...]. Select for plate-to-plate 96-channel transfers, supernatant/eluate moves, and partial-plate (fewer-than-full) runs.
always_on: false
---
## `wt.mca96`

True 96-channel head: one aspirate/dispense/mix touches all 96 wells at once —
no per-column loop. PASS DECLARED VARIABLES BY NAME (a string) for the volume
and liquid_class (e.g. `'SUPERNATANT_ASPIRATE_UL'`, `'LIQUID_CLASS_SUPERNATANT'`);
passing the Python value bakes a literal into the protocol and leaves the FC
variable unused.

```python
head = wt.mca96
head.mount_adapter()
head.pick_up(tips)
head.aspirate(source, 'SUPERNATANT_ASPIRATE_UL', liquid_class='LIQUID_CLASS_SUPERNATANT')
head.dispense(dest, 'TRANSFER_VOLUME_UL', liquid_class='LIQUID_CLASS_ELUATE')
head.mix(plate, 'MIX_VOLUME_UL', cycles=10, liquid_class='LIQUID_CLASS_BEADS')
head.empty_tips(waste, 'SUPERNATANT_ASPIRATE_UL')
head.return_tips(tips)
head.drop_adapter()
```
**Never call:** `get_tips`, `drop_tips`

## Adapter lifecycle rules

- Use the EVA[001] adapter for 96-well plate operations.
- Once `get_head_adapter` / `mount_adapter` is called, the adapter stays
  mounted for ALL subsequent MCA operations until `drop_head_adapter`. Do NOT
  drop and re-pick the adapter between every operation.

## Partial-column pipetting (fewer than the full plate)

When the user wants only some columns of the plate, pass `columns=` (1-based
plate columns) to `aspirate`/`dispense`. The head still fires in parallel — it
just addresses the selected columns. This is one native command, NOT a loop.

```python
# First 24 samples = columns 1-3
head.aspirate(reservoir, 'REAGENT_UL', liquid_class='LIQUID_CLASS_REAGENT', columns=[1, 2, 3])
head.dispense(plate,     'REAGENT_UL', liquid_class='LIQUID_CLASS_REAGENT', columns=[1, 2, 3])
```

- **Contiguous or sparse both work:** `columns=[7, 8, 9, 10, 11, 12]` (right
  half) or `columns=[1, 3, 5, 7, 9, 11]` (odd columns). The compiler emits the
  matching FluentControl well-selection.
- **Whole columns only.** `columns` selects entire columns (all 8 rows). A
  ragged final column (e.g. 23 samples = columns 1-2 plus 7 wells of column 3)
  or an arbitrary cherry-pick of wells (`A1, C3, H12`) is NOT a `columns`
  selection — use a worklist (see `api-worklists`) or ask the user to clarify.
- **Do NOT** unroll columns with a Python `for` loop, and do NOT use a LiHa
  column loop when the MCA can address the columns in one call.
- Omit `columns` entirely for a full-plate (all 12 columns) operation.
