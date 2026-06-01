---
name: api-loops-and-conditionals
axis: api
description: Runtime variables and control flow — wt.declare_variable/set_sim_value, native wt.loop (column iteration with well_offset), and wt.conditional (sim-time-gated branches). Select for repeated cycles (washes, mixes), column-wise iteration, or any step that runs a variable number of times or only under a condition.
always_on: false
---
## Runtime variables

Declare every count, volume, and liquid class as a variable; seed its
simulation value so the simulator can walk the protocol.

```python
wt.declare_variable("cycles", 3)        # FluentControl variable, default 3
wt.set_sim_value("cycles", 3)           # value the simulator iterates with
wt.set_variable("cycles", 3)            # optional runtime re-assignment
```

`declare_variable` + `set_sim_value` is the pair you need for anything a loop
count or conditional predicate reads. Pass the **name string** wherever the
value is used (see core-worktable-api), so the renderer emits a variable
reference rather than baking in a literal.

## Native loops

Use a FluentControl loop, never a Python `for` (a `for` unrolls into N
hardcoded steps; the lab wants ONE `LoopGroup`).

```python
with wt.loop(times="cycles", name="Wash cycles"):
    head.aspirate(src, "WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_WASH")
    head.dispense(waste, "WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_WASH")
```

- `times` is an integer literal **or** a declared numeric variable name
  (`"cycles"`). Nothing else.
- For column-wise iteration over a 96-well plate, loop 12 times with a
  `loop_variable` and address each column by a 1-based `well_offset`
  expression string. **You MUST `declare_variable` the loop variable before
  using it** — otherwise FC throws `'Name of Loop Variable': Invalid
  expression: 'col'` / `Enter a valid well offset` when it tries to resolve
  the expression:
  ```python
  wt.declare_variable("col", 1)        # required for FC to resolve `col` in well_offset
  wt.set_sim_value("col", 1)
  with wt.loop(times=12, name="Dispense columns", loop_variable="col"):
      head.dispense(plate, "VOL_UL", liquid_class="LIQUID_CLASS_X",
                    well_offset="(col-1)*8")
  ```
  This renders `<WellOffset>(col-1)*8</WellOffset>` and simulates all 96 wells.
  (The MCA96 head touches all 96 wells per call, so it needs no column loop —
  only LiHa column-wise work does.)

## Conditionals

`wt.conditional` gates its body on a sim-time predicate. The body emitted
inside the `with` becomes the **then** branch.

```python
with wt.conditional(left="ph", op=">=", right=7, name="Extra rinse if pH high"):
    head.aspirate(src, "RINSE_UL", liquid_class="LIQUID_CLASS_WASH")
    head.dispense(waste, "RINSE_UL", liquid_class="LIQUID_CLASS_WASH")
```

- `op` is one of `==`, `!=`, `<`, `<=`, `>`, `>=`.
- `left` is a declared variable name; `right` is a literal or variable.
- The simulator follows the then-branch when the predicate is true (it uses
  the `set_sim_value`s).

**`else_steps` caveat (known gap G4):** the `with wt.conditional(...)` context
manager only populates the **then** branch. There is no `with ... else:`. To
author an else branch, populate it directly on the returned object:
```python
with wt.conditional(left="ph", op=">=", right=7) as cond:
    head.aspirate(src, "RINSE_UL", liquid_class="LIQUID_CLASS_WASH")
cond.else_steps.append(...)   # build the else branch steps explicitly
```

## Combined shape (loop + nested conditional)

```python
wt.declare_variable("cycles", 3); wt.set_sim_value("cycles", 3)
wt.declare_variable("ph", 7);     wt.set_sim_value("ph", 7)

head.get_tips(tips)
with wt.loop(times="cycles", name="Wash cycles"):
    head.aspirate(src, "WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_WASH")
    head.dispense(waste, "WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_WASH")
    with wt.conditional(left="ph", op=">=", right=7, name="Extra rinse"):
        head.aspirate(src, "RINSE_UL", liquid_class="LIQUID_CLASS_WASH")
        head.dispense(waste, "RINSE_UL", liquid_class="LIQUID_CLASS_WASH")
head.drop_tips()
```

The renderer emits a `<LoopGroup>` / `<ConditionalGroup>`; the simulator runs
the loop body N times and follows the conditional when its predicate holds.
