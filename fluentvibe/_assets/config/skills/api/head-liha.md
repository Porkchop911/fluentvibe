---
name: head-liha
axis: api
description: The FCA (wt.liha, 8 channels) by hand - column-wise work with a native loop and well_offset, FCA tip boxes. Select when writing FCA pipetting that no block covers.
always_on: false
---
## `wt.liha` — the FCA

The 8-channel arm. Prefer the blocks (`distribute_reagent`, `pool_columns`, `pool_wells`, `transfer_volumes`,
`distribute_volumes`); write `wt.liha` calls only for what no block does. Its tip box is an FCA box
(`FCA200Box` / `FCA1000Box`), never an MCA box ("No DiTi-Labware ... found").

A reagent from a trough into every column: aspirate **inside** the loop, once per column, and dispense from above
(the tips never touch the wells, so one set of tips serves all columns):

```python
fca = wt.liha
fca_tips = wt.place(FCA200Box("FcaTips", catalog="FCA, 200ul SBS"), "Nest61mm_Pos", 4)
wt.declare_variable("col", 1)
wt.set_sim_value("col", 1)
fca.get_tips(fca_tips)
with wt.loop(times=12, name="Buffer to every column", loop_variable="col"):
    fca.aspirate(buffer_trough, BUFFER_UL, liquid_class="Water Free Single")
    fca.dispense(plate, BUFFER_UL, liquid_class="Water Free Single", well_offset="(col-1)*8")
fca.drop_tips()
```

Anything that touches samples (aspirating from a sample plate, mixing in sample wells) takes **fresh tips for every
column**: put `get_tips` / `drop_tips` inside the loop, or use `transfer_volumes` / `pool_columns`, which do.

`well_offset` counts wells from A1 (8 per column), so `(col-1)*8` is the column the loop is at; declare the loop
variable before the loop. `fca.mix(plate, VOL, cycles=10, liquid_class="Water Mix", well_offset=...)` mixes;
`fca.empty_tips(waste, VOL)` empties (class `Empty Tip` by default).

**Never call on `wt.liha`:** `pick_up`, `return_tips`, `mount_adapter`, `drop_adapter` (those are MCA calls).
