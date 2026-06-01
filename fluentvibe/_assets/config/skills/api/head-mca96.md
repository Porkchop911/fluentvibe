---
name: head-mca96
axis: api
description: The wt.mca96 true 96-channel head — one aspirate/dispense/mix touches all 96 wells at once, with adapter mount/pick-up lifecycle. Select for plate-to-plate 96-channel transfers and supernatant/eluate moves.
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
