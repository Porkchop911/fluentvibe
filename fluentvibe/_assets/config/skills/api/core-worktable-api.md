---
name: core-worktable-api
axis: api
description: The Worktable, the three heads (FCA = wt.liha, MCA = wt.mca96, RGA = wt.gripper), variables with wt.volume, native loops and waits. Always loaded.
always_on: true
---
## Words

| The lab says | In fluentvibe | What it is |
|---|---|---|
| FCA | `wt.liha` | the 8-channel arm; FCA tip boxes (`FCA200Box`, `FCA1000Box`) |
| MCA | `wt.mca96` | the 96-channel head: one move touches all 96 wells (or the columns you name) |
| RGA | `wt.gripper` | moves plates, e.g. onto and off the magnet |

There is no `wt.fca`. Most work goes through the blocks (see api-blocks); write head calls by hand only
for what no block covers.

## Shape of a protocol

```python
from fluentvibe import Worktable, Plate96, MCA200Box, Reagent
from fluentvibe.blocks import stamp

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(WORKSPACE_NAME, workspace_guid=WORKSPACE_GUID, auto_place=False,
                                  protocol_name="Copy samples")
    wt.group("Variables")
    SAMPLE_UL = wt.volume("SAMPLE_UL", 20)

    wt.group("Labware Placement")
    samples = wt.place(Plate96("Samples", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    copy = wt.place(Plate96("Copy", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA200Box("CopyTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 3)
    samples.fill_all(Reagent("Sample", role="analyte"), 50)

    stamp(wt, source=samples, dest=copy, volume_ul=SAMPLE_UL, tips=tips,
          liquid_class="Water Free Single", name="Copy samples")
    return wt
```

Take the workspace name, GUID, positions and classes from the deck section; every labware label is unique in the
whole protocol.

## Volumes: `wt.volume`, passed as values

Every per-well volume is a FluentControl variable in a `Variables` group at the top. A volume computed from
others keeps its formula, so an edit in FluentControl carries through:

```python
wt.group("Variables")
SAMPLE_UL = wt.volume("SAMPLE_UL", 20)
BEADS_UL = wt.volume("BEADS_UL", 36)
SUPERNATANT_UL = wt.volume("SUPERNATANT_UL", SAMPLE_UL + BEADS_UL - 5)
```

Pass these values themselves to blocks and head calls (`volume_ul=SUPERNATANT_UL`), never a string. They also work
in Python arithmetic and in fills. Liquid classes are strings: the class name (`"Water Free Single"`), or the name
of a string variable you declared with `wt.declare_variable("LC_BEADS", "Water Free Single")` and
`wt.set_sim_value("LC_BEADS", "Water Free Single")`.

## Repeats: a native loop, not a Python `for`

A Python `for` writes N copies into the protocol; FluentControl should get one loop:

```python
wt.declare_variable("WASHES", 3)
wt.set_sim_value("WASHES", 3)
with wt.loop(times="WASHES", name="Washes"):
    ...   # the steps of one wash, written once
```

`times` is a number or a declared numeric variable name. The FCA addresses columns inside a loop with
`well_offset="(col-1)*8"` and `loop_variable="col"` (declare `col` first); the MCA needs no column loop.

## Waiting and operator steps

- `wt.wait(duration_seconds=300)`: only a wait at room temperature on the deck.
- Anything that needs a device the deck does not have (heating, cooling, shaking, a rotator, a centrifuge, a
  thermal cycler, a plate reader) is an operator step: `offdeck_step(...)` from the blocks. Never a `wt.wait` with a
  comment.
- `wt.add_comment("...")` only annotates; it does not stop the run.

## Never

`wt.aspirate`, `wt.dispense`, `wt.pick_up` (use a head or a block), `wt.comment(...)` (`comment` is a keyword of
`from_workspace`), raw XML or generic steps.
