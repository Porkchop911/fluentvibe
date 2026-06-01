---
name: api-worklists
axis: api
description: Per-well / variable-volume liquid handling via FluentControl worklists — wt.worklist(csv|gwl) loads a pick list and executes it, the Gwl builder emits per-record volumes, and CSV column-mapping drives it. Select whenever each well needs a DIFFERENT volume or an arbitrary source-to-dest mapping that scalar aspirate/dispense plus a native loop cannot express — CSV-driven cherrypicking, hit-picking, pick lists, per-well normalization, variable-volume distribution, large transfer lists.
always_on: false
---
## When to use a worklist

Scalar `aspirate`/`dispense` + a native `wt.loop` express *uniform* or
*column-wise* volumes. When each well needs a **different** volume, or the
source→destination mapping is arbitrary (a CSV pick list), use a **worklist** —
FluentControl's native per-record transfer file. This is the right tool for
CSV-driven cherrypicking, per-well normalization, and variable-volume
distribution. Do **not** fake per-well volumes with a Python `for` over scalar
steps and do **not** declare the volumes "unsupported" — the worklist carries
one volume per record.

## Required deck setup — the worklist runs on the LiHa (FCA)

A worklist is executed by the **LiHa/FCA**, and its Load Worklist step does its
own tip pickup using `diti_type` (default
`"TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS"`). FluentControl rejects the
script at open with *"Get DiTis: '…' not found on the workspace"* unless a DiTi
box **of that exact type** is placed on the deck. So you **must place an FCA DiTi
box matching `diti_type`** (you do not call `get_tips`/`pick_up` yourself — the
worklist handles tips). Keep the box catalog name and `diti_type` tool name in
sync; use a deck-approved FCA tip (`FCA, 1000ul SBS` or `FCA, 200ul SBS`).

## One-call form (CSV or GWL)

```python
wt.group("Cherrypick from pick list")
source = wt.place(Plate96("Source", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
dest   = wt.place(Plate96("Dest",   catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
# REQUIRED: an FCA DiTi box matching the worklist's diti_type (default FCA, 1000ul SBS)
fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)

wt.worklist(r"C:\ProgramData\Tecan\VisionX\Worklists\picklist.csv",
            liquid_class="Water Free Single")
```

`wt.worklist(source_path, *, liquid_class=None, gwl_path=None, diti_type=…, execute=True, ...)`
loads a `.csv` or `.gwl` and executes it. A `.csv` source emits FluentControl's
*Convert CSV to GWL* command first, then loads and runs the result; a `.gwl`
source is loaded directly. Pass `execute=False` to stage several worklists and
fire them together with `wt.execute_worklist()`. The labware names in the CSV
columns A/C must match labware you placed (the `Source`/`Dest` above).

`liquid_class` accepts a literal class name (`"Water Free Single"`) **or** a
declared `LIQUID_CLASS_*` variable — the latter is resolved to its value at author
time (the worklist renders a literal class name, not an FC variable reference).
If you set a non-default `diti_type`, place a DiTi box whose catalog name matches.

### CSV column format
The default `columns="standard"` mapping is, by column letter:

| col | GWL field |
|---|---|
| A | SourceLabel (source labware name) |
| B | SourcePosition (well) |
| C | DestLabel (dest labware name) |
| D | DestPosition (well) |
| E | Volume (µL — **per row**) |

Override with `columns={...}` and adjust `start_line` / `separator` for other
layouts. The labware names in columns A/C must match labware you placed on the
worktable.

## Granular form

```python
wt.convert_csv_to_gwl("picklist.csv", "picklist.gwl")   # emits Convert CSV to GWL
wt.load_worklist("picklist.gwl", liquid_class="Water Free Single")
wt.execute_worklist()
```

## Building a worklist in Python (Gwl)

When the pick list is computed, not a file on disk, build it with `Gwl` and
write a `.gwl`, then load it. Each record carries its own volume:

```python
from fluentvibe import Gwl

gwl = Gwl()
for src_well, dst_well, vol in rows:          # vol differs per row — that's the point
    gwl.aspirate("Source", position=src_well, volume=vol, liquid_class="Water Free Single")
    gwl.dispense("Dest",   position=dst_well, volume=vol, liquid_class="Water Free Single")
    gwl.wash()
gwl.write(r"C:\ProgramData\Tecan\VisionX\Worklists\computed.gwl")
wt.worklist(r"C:\ProgramData\Tecan\VisionX\Worklists\computed.gwl")
```

`Gwl` also has `reagent_distribution(...)` (one source → many dests),
`sample_transfer(...)` (replicated transfers), and `flush` / `comment` /
`start_timer` / `wait_for_timer` records.

## Simulator caveat (important)

Worklist steps (`convert_csv_to_gwl` / `load_worklist` / `execute_worklist`) are
**VALIDATION_ONLY** in the simulator: the protocol renders and validates, but the
simulator does **not** walk the per-well volume changes the worklist performs, so
`wt.snapshots` will not reflect them. State this in any skill that relies on a
worklist — the recipe is correct and compiles, but post-worklist well volumes are
not modelled. (Contrast with scalar `aspirate`/`dispense`, which the simulator
does walk well-by-well.)

## Choosing scalar vs worklist

- Uniform / column-wise volume, repeated → scalar `aspirate`/`dispense` +
  `wt.loop` (simulator walks it). See api-loops-and-conditionals.
- Per-well distinct volume or arbitrary source→dest mapping → **worklist**.
- Families that lean on this: cherrypicking (pick lists), normalize-to-target
  (per-well buffer), reagent-distribution (variable per-well fills).
