---
name: head-liha
axis: api
description: The wt.liha fixed-channel pipetting head — trough-to-plate dispenses, per-column and single-channel work, and the FCA-to-LiHa mapping. Select for any protocol that dispenses reagents from a trough or does column-wise/single-channel pipetting.
always_on: false
---
## `wt.liha`

`wt.liha` is the only fixed-channel pipetting head exposed by fluentvibe. Use it
for FCA-style operations (trough-to-plate dispenses, single-channel or
per-column transfers, individual well aspirate/dispense). Use `wt.mca96` only
for true 96-channel plate-to-plate moves. PASS DECLARED VARIABLES BY NAME (a
string) for volume and liquid_class — `'BEAD_VOLUME_UL'`, not the Python value
— or the rendered protocol bakes in a literal and the FC variable is dead. To
cover all 12 columns, wrap a single aspirate/dispense in
`with wt.loop(times=12, loop_variable='col')` and address columns with
`well_offset='(col-1)*8'`; NEVER unroll with a Python `for` loop.

```python
head = wt.liha
head.get_tips(tips)
head.aspirate(source, 'TARGET_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER')
with wt.loop(times=12, loop_variable='col'):
    head.dispense(dest, 'TARGET_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER', well_offset='(col-1)*8')
head.mix(plate, 'MIX_VOLUME_UL', cycles=10, liquid_class='LIQUID_CLASS_MIX')
head.empty_tips(waste, 'SUPERNATANT_VOLUME_UL')
head.drop_tips()
head.drop_tips(tips)
```
**Never call:** `pick_up`, `return_tips`, `mount_adapter`, `drop_adapter`

## LiHa tip box — must be an FCA DiTi (not MCA)

`get_tips(tips)` on `wt.liha` looks for an **FCA**-class DiTi box on the worktable.
Placing an `MCA96, *, Box` and binding it to `tips` makes FC report `No DiTi-Labware
"MCA96, …" found` and `Tip(s) are not mounted on channels 1–8`. The deck-valid FCA
tip boxes (per the whitelist) are `FCA, 200ul SBS` and `FCA, 1000ul SBS`.

Place it like this and bind it to `fca_tips`:

```python
from fluentvibe import TipBox
fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
# (use "FCA, 200ul SBS" for low-volume work)
```

Then use it as the `get_tips(fca_tips)` / `drop_tips(fca_tips)` argument throughout
the protocol. Never use an MCA96 tip box with the LiHa.

## `wt.fca` does not exist

fluentvibe does NOT expose `wt.fca` as a runtime head. FCA-style fixed-channel
pipetting (single-channel, per-column, trough-to-plate dispenses) is authored
through `wt.liha`. Use `wt.mca96` only for true 96-channel plate-to-plate
operations.

**Never call:** `wt.fca.aspirate`, `wt.fca.dispense`, `wt.fca.pick_up`

## Rule

- `liha_mix` requires `labware_name` (string) and `volume` (float or variable
  name). Optional `cycles` (int, default 10).
