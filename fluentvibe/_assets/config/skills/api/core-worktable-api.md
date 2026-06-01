---
name: core-worktable-api
axis: api
description: Worktable construction (from_workspace), the variable-passing-by-name rule, native FluentControl loops, liquid-class variable shape, and the universal authoring idioms every protocol needs. Always loaded.
always_on: true
---
## `Worktable`

```python
wt = Worktable.from_workspace('SAT_Fluent_780_Rev3', workspace_guid='291ba293-6361-4f8f-aa8d-7c2643d3f096', auto_place=False)
plate = wt.place(Plate96('DestPlate', catalog='96_ABgene_SuperPlate_Thermo_AB2800'), 'Nest61mm_Pos', 2)
wt.group('Transfer')
# native FluentControl loop — do NOT unroll with a Python for-loop
with wt.loop(times=12, name='Dispense columns', loop_variable='col'):
    head.aspirate(trough, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS')
    head.dispense(plate, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS', well_offset='(col-1)*8')
wt.declare_variable('RunId', 'demo')
wt.set_sim_value('RunId', 'demo')
wt.set_variable('RunId', 'demo')
wt.wait(30)
wt.add_comment('Incubate at room temperature')
```
**Attributes:** liha, mca96, gripper
**Never call:** `wt.pick_up(...)`, `wt.aspirate(...)`, `wt.dispense(...)`

## Authoring idioms — get these right the FIRST time

These are the exact mistakes that otherwise cost dozens of failed
`simulate_python_draft` repair rounds. Follow them up front.

**No `wt.comment(...)`.** `comment` is only the
`Worktable.from_workspace(..., comment="...")` keyword argument — it is a
string attribute, not a method. Calling `wt.comment("...")` raises
`'str' object is not callable`. For step notes use plain `#` Python
comments.

**Object-draft `liquid_classes` shape is fixed.** Resolve the class once,
then make every entry exactly:
`{"name": "Water Free Single", "default_value": "Water Free Single", "variable_name": "LIQUID_CLASS_<ROLE>"}`.
`name` must be the resolved class string (not the variable). Declare the
matching `wt.declare_variable("LIQUID_CLASS_<ROLE>", "Water Free Single")`
+ `wt.set_sim_value(...)`.

**Pass declared variables BY NAME — as a string — for both `volume` and
`liquid_class`.** The renderer emits a FluentControl variable reference
*only* when the argument string equals a declared variable name. Passing
the Python value bakes a literal into the protocol and leaves the FC
variable dead, defeating the point of declaring it.
- Right: `head.aspirate(trough, "BEAD_VOLUME_UL", liquid_class="LIQUID_CLASS_BEADS")`
- Wrong: `head.aspirate(trough, BEAD_VOLUME_UL, liquid_class=LIQUID_CLASS_BEADS)`
  (here `BEAD_VOLUME_UL` is `36.0` and `LIQUID_CLASS_BEADS` is
  `"Water Free Single"`, so both render as literals).

You still compute derived values as Python numbers and
`wt.declare_variable("SUPERNATANT_ASPIRATE_UL", sample + beads - retain)` +
`wt.set_sim_value(...)`; the simulator resolves the name-string back to the
seeded value. Pass the **name string** in every `aspirate` / `dispense` /
`mix` / `empty_tips` call.

**Never change approved labware across staged groups.** The `catalog=`,
`python_class`, and label of every object are locked at object-draft
approval — re-emit them identically; do not add, drop, or rename labware
mid-draft.

**Iterate columns with a native `wt.loop`, never a Python `for`.** A
Python `for col in range(12)` unrolls into 12 hardcoded steps in the
rendered protocol; the lab wants ONE FluentControl loop. Use the
loop counter to address each column with a `well_offset` **expression
string** (the counter is 1-based):
```python
head.get_tips(fca_tips)
with wt.loop(times=12, name="Add beads", loop_variable="col"):
    head.aspirate(bead_trough, "BEAD_VOLUME_UL", liquid_class="LIQUID_CLASS_BEADS")
    head.dispense(sample_plate, "BEAD_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_BEADS", well_offset="(col-1)*8")
head.drop_tips()
```
This renders as a single `LoopGroup` with `<WellOffset>(col-1)*8</WellOffset>`
and simulates across all 96 wells. The MCA96 head touches all 96 wells in
one call, so it needs **no** column loop — only the LiHa column-wise
trough dispenses do.

## Rendering rules (always apply)

- **Canonical step-type names only** (e.g. `aspirate`, `dispense`,
  `pick_up_tips`, `set_tips_back`, `get_head_adapter`, `drop_head_adapter`,
  `liha_*`). Avoid vendor-prefixed aliases like `mca384_aspirate`.
- **`wait` uses `duration_seconds`** (integer). For minutes multiply by 60
  (5 min → `duration_seconds: 300`). Never `duration_minutes`/`duration`.
- **Loop count must be numeric or a declared variable** with a numeric
  `set_variable`. A `LoopGroup` uses a variable named `LoopVariable` for the
  current counter.
- **`calculate_variable.operation`** must map to Add/Subtract/Multiply/Divide
  (or `+ - * /`) so the renderer can emit valid expressions.
