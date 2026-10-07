---
name: task-opentrons-conversion
axis: api
description: Converting an Opentrons protocol (OT-2 or Flex) to this deck - run the deterministic converter as a tool, read its report, finish what it could not convert (modules, magnet, gripper moves, deck space), compress the well-by-well code into loops and blocks, and prove every well still matches the Opentrons run. Select whenever the request names an Opentrons protocol, an OT-2/Flex script or a protocol id from the Opentrons library.
always_on: false
select_when:
  - opentrons
  - ot-2
  - ot2
  - flex
---
## The converter is your first tool, not your answer

1. No id or path given: `opentrons_search` with the assay or kit words; pick by title and steps, say which one you
   took. Protocols split in parts (`-part-2`, `_pt3`) are listed under `related`: convert the part asked for.
2. `opentrons_convert(protocol)`. It runs the Opentrons simulator, maps every Opentrons well to a well on this deck
   and writes the protocol well by well. Its report is the work list:
   - `fidelity`: wells whose net volume matches the Opentrons run. Everything you change must keep this.
   - `unconverted`, `open_lines`: steps it could not do (`TODO` comments, `wt.user_prompt` operator steps).
   - `stage` other than `done`: see "When the converter stops" below.
3. `read_draft` around the open lines, fix them (`edit_draft` for small changes), then
   `opentrons_check_fidelity` after **every** change. Finish with `compile_and_simulate`.

Never re-type the volumes yourself: the converter's numbers come from the Opentrons simulation. Keep its container
labels and well addresses - the fidelity check maps Opentrons wells by them.

## Finishing unconverted steps

| Opentrons step | On this deck |
|---|---|
| Magnetic module engage / disengage, magnetic block | `wt.gripper.move(plate, onto=magnet)` / `to=(nest, pos)` (head-gripper, api-magnetization-model); wait the engage time with `wt.wait` |
| Flex gripper `move_labware` | `wt.gripper.move(plate, to=(location, position))` to the matching site |
| Temperature module, thermocycler program | `thermal_step` (ODTC if the deck has one, else operator) |
| Heater-shaker shake / heat | `offdeck_step` with the speed, temperature and time in the text |
| Plate reader | `offdeck_step` unless the deck lists a reader site |
| `pause` / `delay` | kept by the converter; leave them |

A step that only re-labels or loads labware needs nothing.

## When the converter stops

- `trace`: the Opentrons run moved no liquid with its defaults - it needs an input file or parameters (sample
  count, CSV). `ask_user` for them; do not invent a sample sheet.
- `deck`: more labware than plate sites. This deck has plate hotels (`HotelMP_Pos` microplates, `HotelDWP_Pos`
  deep-well plates) reached by the gripper. Plates that are only needed in some stages live in a hotel and are
  moved onto a free nest for those stages and back afterwards. No pipetting in a hotel. Report which plates you
  staged this way.
- `gate`: the draft breaks a simulator rule. If the message says the Opentrons protocol itself does it (a tip
  filled beyond its volume, a well drawn below empty), say so as a problem of the source; fix the draft only where
  the converter mis-translated.

## Compressing well-by-well code

The converter writes one call per Opentrons move. Make it read like a protocol, one stage at a time:
- a column-by-column run with the same volume -> a native `wt.loop` with `well_offset` (api-loops-and-conditionals);
- a whole-plate add, stamp, pool or distribution -> the matching block (api-blocks);
- keep tip changes where the Opentrons protocol changes tips; a tip that touched a sample is not reused for the next.
Check fidelity after each stage you rewrite; if wells stop matching, undo that stage rather than chase volumes.

## Say what the protocol does

Before the code, name the workflow in one or two lines (e.g. "Bradford assay: BSA standards serially diluted,
samples in triplicate, dye added to all wells") and its family skill if one fits. Flag source problems you saw
(volumes beyond the tip, missing reagents, steps the Opentrons script leaves to the operator).
