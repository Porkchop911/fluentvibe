---
name: head-mca96
axis: api
description: The wt.mca96 true 96-channel head — one aspirate/dispense/mix touches all 96 wells at once, with adapter mount/pick-up lifecycle. Supports partial-column pipetting and partial-tip pickup / tip sorting via pick_up(columns=[...]) (the PartialColumnOffset is derived, never passed). Select for plate-to-plate 96-channel transfers, supernatant/eluate moves, partial-plate (fewer-than-full) runs, and tip-sorting / reformatting protocols.
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

## Partial-tip pickup & tip sorting

`pick_up` / `return_tips` take **only** `columns` — the 1-based **box columns**
to address, e.g. `[1]` for one column, `[7,8,9,10,11,12]` for the right half, or
`[1,4,7,10]` to grab a whole sorted box. **Never pass an offset.** The physical
`PartialColumnOffset` is *derived* (`head_width - max(columns)`, so box col 1 →
11, col 12 → 0). Passing it independently is what makes the partial block and
the well-selection disagree — don't.

**The physical peel rule (this dictates what's legal):** a single column can
only be peeled from the box's **current left-most or right-most filled column**,
so the head's idle channels overhang empty space. A full box: column 1 or 12.
After column 1 is gone the left edge is column 2, then 3 … So peel
left-to-right (1, 2, 3, 4) or right-to-left (12, 11, 10, 9) — never an interior
column (the simulator rejects it). Setting tips **into an empty box** has no such
constraint: any target column is fine, nothing is there to collide with.

```python
# Place a FULL tip box AND an empty tip box; mark the target empty in the twin.
full_tips   = wt.place(MCA100Box("FullTips",   catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 3)
sorted_tips = wt.place(MCA100Box("SortedTips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
sorted_tips.is_full = False

# Sort: peel source columns 1-4 off the moving left edge into 1,4,7,10.
head.mount_adapter()
for src_col, tgt_col in ((1, 1), (2, 4), (3, 7), (4, 10)):
    head.pick_up(full_tips, columns=[src_col])       # offset derived (11,10,9,8)
    head.return_tips(sorted_tips, columns=[tgt_col])  # offset derived (11,8,5,2)
```

- **Using sorted tips:** pick up the **whole sorted box at once** — pass all the
  filled columns in one `pick_up`, pipette over them, then return them together.
  Do NOT peel the sorted columns one at a time: an interior sorted column (e.g.
  4, with 1 and 7 still present) has no clear edge and cannot be peeled alone.

```python
sorted_cols = [1, 4, 7, 10]
head.pick_up(sorted_tips, columns=sorted_cols)
head.aspirate(source, 'TRANSFER_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER', columns=sorted_cols)
head.dispense(dest,   'TRANSFER_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER', columns=sorted_cols)
head.return_tips(sorted_tips, columns=sorted_cols)
head.drop_adapter()
```
