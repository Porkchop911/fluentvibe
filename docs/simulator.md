# Simulator

`fluentvibe/simulator/`

The simulator is the introspection engine. Author method calls produce IR;
the simulator reads the IR and rebuilds world state, freezing a `Snapshot`
after each step. The simulator does not run during authoring — it runs when
you call `wt.simulate()`.

```
[ wt.protocol_ir (list[Step]) ]
       │  Simulator(wt).run()
       ▼
+----------------------+
| step 0: AddLabware   |  → snapshot 0
| step 1: AddLabware   |  → snapshot 1
| step 2: PickUpTips   |  → snapshot 2
| step 3: Aspirate     |  → snapshot 3
| step 4: Dispense     |  → snapshot 4
+----------------------+
       │
       ▼
wt.snapshots: list[Snapshot]
```

Files:

| File | What lives here |
|---|---|
| `walk.py` | The `Simulator` class. Step dispatch, layered aspirate/dispense math, gripper move logic. |
| `snapshots.py` | The `Snapshot` dataclass and `take_snapshot` helper. |
| `invariants.py` | Exception hierarchy (`SimulationError` and its subclasses). |
| `report.py` | Coverage accounting plus structured `status` / `failure` reporting. |

## `Simulator.run()`

`fluentvibe/simulator/walk.py:53`

1. Clear `wt.snapshots`.
2. Build a fresh `Protocol` IR via `wt.to_protocol()`.
3. For every step in every group, `_dispatch(step)`.
4. After each `_dispatch`, take a Snapshot and append it.

Twin state held during the walk:

| Field | Meaning |
|---|---|
| `_slot_map` | Fresh slot map keyed on `(loc, pos)`, bottom→top stacks. |
| `_twin` | Maps catalog label → twin Labware instance. |
| `_mca_adapter_label` | The currently-mounted MCA adapter, or `None`. |
| `_mca_tips` | List of 96 `Tip` instances when tips are picked up; empty otherwise. |
| `_mca_tip_box_label` | Tip box currently sourced from. |

Note: the twin Labware is the same object as the author-side Labware
(`_on_add_labware` looks it up by label). Snapshots `deepcopy` the twin so
historical state stays frozen — but the author-side state is mutated
in-place during the walk. That means **authoring side state after
`simulate()` reflects the *last* simulated state**, not the original
author-time state. Don't rely on author-side `wt.slot_map` after
`simulate()`; use snapshots instead.

## Step dispatch

`fluentvibe/simulator/walk.py:62`

| IR step type | Twin effect |
|---|---|
| `AddLabwareStep` | Lookup the labware by label, push to slot. Raises `OccupiedSlotError` if slot is already occupied. |
| `RemoveLabwareStep` | Remove labware from slot. |
| `GetHeadAdapterStep` | Set `_mca_adapter_label`. |
| `DropHeadAdapterStep` | Clear `_mca_adapter_label`. |
| `PickUpTipsStep` | Validate adapter mounted; validate tip box is full; instantiate 96 fresh `Tip(capacity_ul=...)`. Raises `MissingAdapterError` / `MissingTipsError` on failure. |
| `SetTipsBackStep` | Clear `_mca_tips`. Mark source tip box as full. |
| `AspirateStep` | Pre-checks (adapter + tips); auto-parallel over all wells of the target labware; subtract from each well, push onto matching tip. |
| `DispenseStep` | Pre-check (tips); auto-parallel over wells; pop FIFO from each tip, add layer to each well. |
| `RgaTransferLabwareStep` | Pop labware from current slot, push onto destination slot. Recompute `is_magnetized` automatically (derived from `stack_below`). |
| `CgaGetFingersStep` / `CgaDropFingersStep` | No-ops in the twin (gripper finger pickup/drop don't change worktable state). |
| `LoopStep` | Resolve iteration count (literal int OR sim-time variable); `_dispatch` each child step that-many times. Raises `MissingSimValueError` if the variable has no sim-time value. |
| `ConditionalStep` | Resolve `left_variable` and `right_value` to sim-time values; evaluate `==`/`!=`/`<`/`<=`/`>`/`>=`; dispatch `then_steps` or `else_steps`. |
| `SubRoutineStep` | Opaque pass-through (v1.1: subroutine bodies are external `.smt` files, not parsed). |
| Anything else | Snapshot only — no twin change. (Wait, Comment, UserPrompt, Timers, ExportVariable, ImportVariable, QueryVariable, ExecuteApplication, Delay, SetLocation, …) |

## Layered aspirate semantics

`fluentvibe/simulator/walk.py:232` — `_aspirate_one(labware, well, volume_ul, tip)`

Walking layers **top-down** (bottom→top is the storage order; iteration
runs from index `len-1` to `0`):

```
remaining = volume_ul
i = len(well.layers) - 1
while remaining > 0 and i >= 0:
    layer = well.layers[i]
    if labware.is_magnetized and layer.reagent.pinned_when_magnetized:
        i -= 1                       # skip pinned layers; beads stay
        continue
    take = min(layer.volume_ul, remaining)
    layer.volume_ul -= take
    remaining -= take
    tip.layers.append(Layer(reagent=layer.reagent, volume_ul=take))
    if layer.volume_ul ≈ 0:
        del well.layers[i]
    i -= 1

if remaining > 0:           raise InsufficientVolumeError(...)
if tip.volume_ul > capacity: raise OverdrawError(...)
```

The order of pushes onto `tip.layers` means tip layers are in *aspirate
order* — the first thing aspirated is at index 0. This is FIFO when
dispensing (see below).

## Layered dispense semantics

`fluentvibe/simulator/walk.py:268` — `_dispense_one(well, volume_ul, tip)`

```
if tip.volume_ul < volume_ul:               raise OverdrawError(...)
if well.volume_ul + volume_ul > max:        raise OverdrawError(overflow)

remaining = volume_ul
while remaining > 0 and tip.layers:
    layer = tip.layers[0]                   # FIFO: oldest aspirate dispenses first
    take = min(layer.volume_ul, remaining)
    layer.volume_ul -= take
    remaining -= take
    well.add_layer(layer.reagent, take)     # merge-or-push (see below)
    if layer.volume_ul ≈ 0:
        del tip.layers[0]
```

`Well.add_layer(reagent, volume_ul)` decides whether the dispensed volume
**merges** with the existing top layer or **pushes** a new layer:

```
if well.layers and well.layers[-1].reagent is reagent:
    well.layers[-1].volume_ul += volume_ul        # merge
else:
    well.layers.append(Layer(reagent, volume_ul)) # push new layer
```

Identity (`is`) — not name equality — is what controls merge. Two
`Reagent("Buffer")` calls produce two different reagents and would push
two layers.

## Magnet-aware aspirate

`is_magnetized` is a **derived property** (not stored state):

```python
@property
def is_magnetized(self) -> bool:        # fluentvibe/labware/base.py:240
    return any(isinstance(x, MagnetRack) for x in self.stack_below)
```

The simulator updates `stack_below` whenever the gripper moves the labware
(`_on_gripper_move` in `walk.py:140` captures `list(dest_stack)` *before*
appending). So the moment a plate is gripper-moved onto a `MagnetRack`,
its `is_magnetized` flips True; gripper-moving it off flips it False.
Aspirate semantics use this directly — pinned layers are skipped only
when `is_magnetized` is True.

## Snapshots

`fluentvibe/simulator/snapshots.py`

```python
@dataclass
class Snapshot:
    step_index: int
    step: Step                                       # the IR step that produced this snapshot
    slot_map: dict[(loc,pos)] -> list[Labware]       # deep-copied
    mca_adapter_label: str | None
    mca_tips: list[Tip]                              # deep-copied
    mca_tip_box_label: str | None

    def labware(self, label: str) -> Labware: ...    # find by label across stacks
```

Snapshots are written by `take_snapshot` (`snapshots.py:39`), which
`copy.deepcopy`s the slot map and the tip list. This is the layer that
isolates history from later mutations. The slot snapshot is independent
even when later steps reuse the same author-side Labware object.

## Report Contract

Every `wt.simulate()` call populates `wt.simulation_report`.

- `status`: `passed`, `passed_with_opaque`, or `failed`
- `failure`: `None` on success, otherwise a structured object with
  `category`, `exception_type`, `message`, and optional step metadata
- `unsupported_command_ids`: opaque command counts keyed by command id

Current failure categories are machine-oriented: `workspace_binding`,
`workspace_slot`, `catalog`, `opaque_policy`, `coverage_policy`,
`liquid_state`, `tip_state`, `adapter_state`, `runtime_variable`, and
`simulation_state`.

For decompiled validation, prefer `wt.simulate(strict=True,
fail_on_opaque=True)` or the equivalent CLI flags so unsupported
runtime steps fail as a gate instead of surfacing only as
`passed_with_opaque`.

Cost: each snapshot deep-copies the full slot map + tips. For typical
protocols (<200 steps) this is fine. Long protocols with many large tubeRacks
may want a v1.2 optimisation (reference counting / structural sharing).

## Sim-time variables

When a `LoopStep`, `ConditionalStep`, or `ImportVariableStep` references a
runtime variable, the simulator must know the value to walk the right path.
Authors provide values via:

```python
wt.set_sim_value("cycles", 3)          # for a LoopStep with iterations='cycles'
wt.set_sim_value("ph", 7.4)            # for a Conditional with left_variable='ph'
wt.set_sim_value("imported_count", 12) # for an ImportVariableStep target
```

Resolution order in `_resolve_sim_value`:

1. `wt.sim_values[name]` — author-provided.
2. `wt.protocol_variables[name]` — declared via `wt.declare_variable(...)`.
3. Otherwise → `MissingSimValueError`.

This ensures the simulator never silently assumes a value.

## Physical invariants

`fluentvibe/simulator/invariants.py`

All raise during `simulate()`. All inherit from `SimulationError`.

| Exception | When |
|---|---|
| `MissingTipsError` | Aspirate / dispense / mix attempted with no tips on the head, or pickup from a non-tip-box, or pickup from an empty tip box. |
| `MissingAdapterError` | MCA pickup or aspirate without a head adapter mounted. |
| `InsufficientVolumeError` | Aspirate request exceeds available volume in the well (after skipping pinned layers if magnetized). |
| `OverdrawError` | Tip cannot dispense more than it holds; or dispense would overflow `well.max_volume_ul`. |
| `OccupiedSlotError` | `AddLabwareStep` lands on an occupied slot. |
| `CannotAspirateError` | Reserved for explicit "you can't aspirate this layer" errors (currently delivered as `InsufficientVolumeError` for pinned-only wells; see [docs/development.md](development.md) for the v1.2 split). |
| `MissingSimValueError` | A runtime variable's sim-time value is required and not provided. |
| `InvalidSlotError` | `place()` targets a `(location, position)` outside the workspace's `valid_slots` whitelist. |

These are physics, not domain rules. The simulator never knows what
"AMPure cleanup should look like" — it only enforces what's physically
possible.

## What the simulator does **not** do

- **Liquid-class–driven volume corrections.** The IR carries `liquid_class`
  on aspirate/dispense, but the simulator treats volumes literally —
  no liquid-class-induced over/under-aspirate.
- **Tip wash modelling.** A `WashStation` is a labware destination but no
  reagent state is tracked there.
- **Subroutine descent.** External `.smt` subroutines are not parsed.
- **Time / kinetics.** Wait/Delay/Timer steps are snapshot-only; the
  simulator has no clock.
- **Collision detection.** mm geometry is exposed but unused.

## Common patterns

### Verify the final state

```python
wt.simulate()
final = wt.snapshots[-1].labware("DestPlate")
assert final.well("A1").volume_ul == 20.0
```

### Walk to a specific step

```python
wt.simulate()
asp_snap = next(
    s for s in wt.snapshots
    if type(s.step).__name__ == "AspirateStep"
)
print(asp_snap.mca_tips[0].layers)
```

### Check magnet state changes

```python
transfer_snaps = [
    s for s in wt.snapshots
    if type(s.step).__name__ == "RgaTransferLabwareStep"
]
plate_after_first = transfer_snaps[0].labware("Plate")
assert plate_after_first.is_magnetized is True
```
