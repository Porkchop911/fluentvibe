---
name: fluentvibe
description: Write, simulate and check Tecan Fluent (FluentControl) protocols in the fluentvibe Python DSL. Use when asked to automate a lab protocol on the Fluent, to write or fix a fluentvibe protocol, or to check one in FluentControl.
---

# fluentvibe: Fluent protocols in Python

A protocol is a Python file with `build_worktable() -> Worktable`. The CLI
simulates it (volumes, tips, deck), compiles it to a FluentControl `.xscr` and
opens it in FluentControl. Run everything from the repo root.

## Before you start

- The deck profile is the folder in `$FLUENTVIBE_PROFILE_DIR` (`wt.add` and
  the deck rules need it). If it is not set, ask the user which deck; do not
  pick one yourself.
- Read the workspace context in that folder before writing: the `deck-*.md`
  file (workspace name and GUID, valid positions, which Python class each
  catalog needs), `site_rules.json` (what fits where) and `reach.json` (which
  head reaches which site). Catalog names:
  `python -m fluentvibe.cli catalog find <text>`.
- The API is `python -m fluentvibe.cli api` (objects) and
  `python -m fluentvibe.cli api <object>` (e.g. `Worktable`, `wt.liha`,
  `wt.mca96`, `wt.gripper`, `blocks`, `Plate96`). It is generated from the
  code: if it is not listed there it does not exist, and if it is, it does.
  Do not read the `fluentvibe/` source to find out how things work.
- Examples of the API shape: `examples/ampure_resolver.py`,
  `examples/ont_rbk114_blocks.py`. Take deck positions from the deck file, not
  from examples.

## Words

| Lab says | fluentvibe | What it is |
|---|---|---|
| FCA | `wt.liha` | the 8-channel arm (Flexible Channel Arm), FCA tip boxes |
| MCA | `wt.mca96` | the 96-channel head: one move touches all 96 wells |
| RGA | `wt.gripper` | the gripper: moves plates (onto the magnet, off it) |

## The document decides

- Every deck step and every reagent must come from the protocol document or
  the user's request. Do not add steps "for completeness" (no elution,
  neutralisation or clean-up the document does not have), and do not swap a
  reagent for another. If the document leaves something open, choose, and
  list it under "Assumed" in your answer.
- Keep the document's ratios: "an equal volume" means equal to what is in the
  wells at that moment.
- Steps the robot cannot do (vortexing a vial, a rotator, a heated
  incubation, a centrifuge) are operator steps: `offdeck_step(...)`.

## Heads, tips, liquids

- Reagents (beads, buffers, enzymes, kit reagents) go by the **FCA** from slim
  troughs (`25ml_short`, `100ml` on `WS_100ml_1`) or tubes. Beads are viscous
  and costly: FCA.
- The **MCA** takes only cheap bulk (water, ethanol, wash buffer) from SBS
  reservoirs (`60ml SBS MCA96` on `Nest61mm_Pos`, `300ml SBS` on
  `Nest7mm_Pos`), and does plate-to-plate work (stamping samples, removing
  supernatant, mixing). The MCA cannot use slim troughs and cannot reach
  `Nest7mm_Pos` 1-3.
- Tips: a fresh MCA tip box for each liquid that touches the samples; reusing
  tips for the same reagent into the same wells is fine. Never carry one
  sample into another well.
- Liquid classes: `Water Free Single` for transfers, `Water Mix` for mixing
  (FluentControl rejects Free Single for mixing), `Empty Tip` for emptying
  tips.
- Waste: a `300ml SBS` reservoir on a nest.
- Hotels (`HotelMP_Pos` …) are storage: the FCA and MCA pipette only on
  nests and troughs.
- `Reagent(name, role=...)`: role is `plain` (default), `bead_carrier`,
  `analyte` or `eluent`.

## Specific wells (cherry-picks, patterns, per-well volumes)

The FCA has 8 channels: one trip serves up to 8 wells, any plate (also
`Plate384`). Pass `wells=`, `channels=` (0-7) and, if they differ,
`volumes=`; set `channels` yourself when wells lie beyond row H. There is no
`wt.mca384`.

```python
head.aspirate(trough, ul, liquid_class=LC, wells=["A1"] * n, volumes=[ul] * n, channels=list(range(n)))
head.dispense(plate, ul, liquid_class=LC, wells=group, volumes=[ul] * n, channels=list(range(n)))
```

## Volumes: let the simulator count

- 96-well plate wells hold about 330 µl; keep bead clean-ups under about
  180 µl. 200 µl tips: larger volumes go in several trips.
- Slim 25 ml trough: fill at most 22 ml (else the 100 ml one). `60ml SBS`:
  at most 55 ml. `300ml SBS`: at most 250 ml.
- Do not add up supplies by hand: fill sources generously and run the
  simulator, which reports shortages, overflows and empty wells exactly.

## Building blocks (`from fluentvibe.blocks import ...`)

Use them rather than raw head calls: `distribute_reagent` (FCA, trough to
plate), `add_reagent` (MCA, reservoir to plate), `stamp` (plate to plate, MCA),
`remove_liquid` (to waste), `mix_wells`, `separate` / `release` (onto / off the
magnet, by the gripper), `pool_columns`, `pool_wells`, `transfer_volumes` /
`distribute_volumes` (different volume per well), `offdeck_step` (operator).
`python -m fluentvibe.cli api blocks` has the exact signatures.

## Workflow

1. Read the document (PDF: `python -c "from fluentvibe.authoring.attachments import extract_file_text; print(extract_file_text('<file>')[0])"`).
   List its deck steps in order, with the sentence each comes from.
2. Write the protocol to `build/eval/pi-<short-name>.py` (an existing folder;
   do not create folders).
3. `python -m fluentvibe.cli simulate build/eval/pi-<name>.py --strict` and
   fix every error it reports. Repeat until it passes. To see what ended up
   in a plate: `wt.simulate()`, then `wt.snapshots[-1].labware("<label>")`
   and its wells' `volume_ul`.
4. `python -m fluentvibe.cli check build/eval/pi-<name>.py` (deck and
   protocol rules).
5. `python -m fluentvibe.cli compile build/eval/pi-<name>.py -o build/eval/pi-<name>.xscr`.
6. If FluentControl is running and logged in:
   `python -m fluentvibe.cli fc-open build/eval/pi-<name>.py --profile "$FLUENTVIBE_PROFILE_DIR" --json`.
   Its InfoPad is the final word; fix what it reports.

Say "simulates" or "FluentControl clean" only when that command passed, and
end with the document steps you followed and what you assumed.
