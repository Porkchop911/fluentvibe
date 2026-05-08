# Manual test — tecanlab v1.1

This walks the four Phase A example protocols plus a REPL session that
exercises catalog lookup, physical invariants, and workspace loading.
Use it to convince yourself the system holds together end-to-end before
trusting it on a lab machine.

All commands assume the repo root as cwd. If `tecanlab` isn't installed
(`pip install -e .`), prefix with `PYTHONPATH=. python -m tecanlab.cli`
in place of `tecanlab`.

## 0. Catalog sanity

```
tecanlab catalog info
tecanlab catalog find magnet
tecanlab catalog find "96 Well" --category plate
```

Expect ~629 components and 14 workspaces against
`C:\ProgramData\Tecan\VisionX\Database`. If the index is empty, run
`tecanlab catalog refresh`.

## 1. Run the four example protocols

Each compiles to a `.xscr` you can open in FluentControl on the lab
machine.

```
PYTHONPATH=. python examples/simple_transfer.py
PYTHONPATH=. python examples/round_trip_780_empty.py
PYTHONPATH=. python examples/ampure_cleanup.py
PYTHONPATH=. python examples/loop_conditional.py
PYTHONPATH=. python examples/normalize_to_target.py
```

All five should print a confirmation and write a `.xscr` to the cwd.
Move them to the lab machine and open in FluentControl. None should
fail to load.

| Example | Exercises | Expected console output |
|---|---|---|
| `simple_transfer` | base parity (forward direction) | `DestPlate A1 layers: [Layer(reagent=Reagent('Input gDNA'), volume_ul=20.0)]` |
| `round_trip_780_empty` | `from_workspace` + valid_slots | `Valid slots: 56` |
| `ampure_cleanup` | gripper-derived magnetization, pinned-when-magnetized aspirate | `is_magnetized = True` then `Sample DNA` (no beads in tip) then `is_magnetized = False` |
| `loop_conditional` | `wt.loop` + `wt.conditional` IR + simulator dispatch | `Waste A1 final volume: 45.0` |
| `normalize_to_target` | per-well state divergence under uniform pipetting (limit: see gap log) | every dest well = 60 µL |

## 2. Open .xscr in FluentControl (lab machine only)

| File | FluentControl status | Notes |
|---|---|---|
| `simple_transfer.xscr` | [ ] opens cleanly | Parity baseline. Should match fluentdsl byte-for-byte minus the random WorkspaceDelta GUID. |
| `round_trip_780_empty.xscr` | [ ] opens cleanly | The .xscr references a worktable workspace name from `_assets/config/generation.yaml` (intentional — that's where the FC-recognised name lives). |
| `ampure_cleanup.xscr` | [ ] opens cleanly | Includes RGA gripper transfer onto MagnetRack. |
| `loop_conditional.xscr` | [ ] opens cleanly | Has `<LoopGroup>` and nested `<ConditionalGroup>`. Open the script tree to verify. |
| `normalize_to_target.xscr` | [ ] opens cleanly | Two tip pickups, two transfer phases. |

## 3. REPL exploration

The highest-leverage path. Open a Python REPL with `PYTHONPATH=.` and
run through these blocks. The session is where the API either matches
your mental model or doesn't — if anything feels awkward, that's a
finding to add to the gap log.

### 3a. Catalog-driven plate construction

```python
from tecanlab import Plate96
src = Plate96("Src", catalog="96 Well Flat")
src.dim_mm                      # parsed mm dimensions from the .xcmp
src.well("A1").position_mm      # well-relative geometry
src.well("A1").max_volume_ul    # ≈ 392 µL from cavity geometry, not hardcoded
```

### 3b. Trip physical invariants on purpose

```python
from tecanlab import (
    Worktable, Reagent, Plate96, MCA100Box,
    InsufficientVolumeError, MissingTipsError, OccupiedSlotError,
)

wt = Worktable("invariant probe")
src = wt.place(Plate96("S", catalog="96 Well Flat"), "Nest", 1)
dst = wt.place(Plate96("D", catalog="96 Well Flat"), "Nest", 2)
tips = wt.place(MCA100Box("T", catalog="MCA96, 100ul, Box"), "Nest", 3)

# Aspirate more than the well holds
src.fill_all(Reagent("buffer"), 5.0)
wt.mca96.mount_adapter()
wt.mca96.pick_up(tips)
wt.mca96.aspirate(src, 50.0, liquid_class="Water Free Single")

try:
    wt.simulate()
except InsufficientVolumeError as e:
    print("Caught:", e)
```

Should raise `InsufficientVolumeError`. Try variations:
- Dispense without picking up tips → `MissingTipsError`
- Place onto an occupied slot → `OccupiedSlotError`

### 3c. Magnet round-trip

```python
from tecanlab import (
    Worktable, Reagent, Layer, Plate96, MCA100Box, MagnetRack,
)

wt = Worktable("magnet")
mag = wt.place(MagnetRack("Mag", catalog="24 Magnet Plate"), "Nest", 7)
plate = wt.place(Plate96("Plt", catalog="96 Well Flat"), "Nest", 1)

beads = Reagent("beads", pinned_when_magnetized=True)
sample = Reagent("sample")
plate.fill_all(sample, 100.0)
for w in plate.wells.values():
    w.layers.append(Layer(reagent=beads, volume_ul=20.0))

print("before move:", plate.is_magnetized)        # False
wt.gripper.move(plate, onto=mag)
print("after move :", plate.is_magnetized)         # True (derived from stack_below)
wt.gripper.move(plate, to=("Nest", 1))
print("after off  :", plate.is_magnetized)         # False
```

Note: there is no `wt.engage_magnet()` and no `MagnetizeStep` — the only
operation is a gripper move. Magnetization is a `@property` derived from
`stack_below`.

### 3d. Workspace exploration

```python
from tecanlab import Worktable
wt = Worktable.from_workspace("780_Empty", auto_place=False)
print("workspace:", wt.workspace_name)
print("slots:", len(wt.valid_slots))      # ~56 after the xwsp parser fix
print("loc types:", sorted(set(s[0] for s in wt.valid_slots)))
```

## 4. Run the test suite

```
PYTHONPATH=. python -m pytest tests/ -q
```

Expect 50/50 green.

## 5. Gap log

Findings surfaced during Phase A. These drive Phase B/C priorities.

### G1. Simulator auto-parallel aspirate is well-keyed, not channel-keyed

`Simulator._iter_aspirate_wells(labware)` returns `list(labware.wells.
values())` and zips that with `self._mca_tips` (96 channels). For a
trough (1 well), only 1 of 96 channels exchanges liquid. For a 384-well
plate against 96 channels, only the first 96 wells get touched.

This breaks the model for two real-world cases:
- 96-channel-into-trough (washes, waste dispense, buffer reservoirs)
- 96-channel-into-384 (with a stamp-style plate replicator pattern)

Workaround in v1.1: use plate-shaped containers everywhere
(`examples/ampure_cleanup.py` uses Plate96 as buffer reservoir + waste).

Fix path: model the well-target-set explicitly per step (channel mask /
tip→well map), or special-case troughs as "all 96 channels share the
same pool".

### G2. Per-well varying aspirate / dispense volumes

`AspirateStep.volume` and `DispenseStep.volume` are scalars. There is
no way to express "channel 0 takes 23 µL, channel 1 takes 47 µL, …"
without one IR step per well — and the auto-parallel design (G2) means
even that doesn't work for MCA. Real dilution-to-target normalization
needs either:
- per-well-volume vectors on AspirateStep / DispenseStep, or
- an FCA single-channel head class (IR steps `LihaAspirateStep` /
  `LihaDispenseStep` exist; no head class wraps them and the simulator
  doesn't dispatch them).

`examples/normalize_to_target.py` documents this and uses uniform
dispense as a workaround.

### G3. Snapshot deepcopy cost on long protocols

Each step takes a `copy.deepcopy` of the slot map + tip list. For
typical protocols (<200 steps) this is fine. Protocols with many large
tube racks or thousands of steps will see noticeable simulate() cost.
Structural sharing or lazy-evaluation snapshots would close it.
Deferred per the plan.

### G4. else_steps on conditional context manager

`wt.conditional(...)` only authors the then-branch. Authoring the
else-branch requires populating `cond_step.else_steps` directly on the
yielded ConditionalStep object. A `cond.else_branch()` nested context
manager would be tidier — small follow-up.

### G5. CannotAspirateError vs InsufficientVolumeError split

`CannotAspirateError` exists in the invariants module but is currently
delivered as `InsufficientVolumeError` for pinned-only wells. Splitting
the error types makes "the well had 100 µL but it's all pinned beads"
distinct from "the well had only 5 µL". Quality, not blocking.
