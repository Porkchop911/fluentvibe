---
name: fluentvibe-incremental
description: Write, simulate and check Tecan Fluent (FluentControl) protocols in the fluentvibe Python DSL. Use when asked to automate a lab protocol on the Fluent, to write or fix a fluentvibe protocol, or to check one in FluentControl.
---

# fluentvibe: Fluent protocols in Python

A protocol is a Python file with `build_worktable() -> Worktable`. The CLI
simulates it (volumes, tips, deck), compiles it to a FluentControl `.xscr` and
opens it in FluentControl. Run everything from the repo root.

## Before you start

- `export FLUENTVIBE_PROFILE_DIR=build/workspaces/1080_Dev` (the deck profile;
  `wt.add` and the deck rules need it). Read
  `build/workspaces/1080_Dev/deck-1080_Dev.md` once: workspace name and GUID,
  valid positions, which Python class each catalog needs.
- The API is `python -m fluentvibe.cli api` (objects) and
  `python -m fluentvibe.cli api <object>` (e.g. `Worktable`, `wt.liha`,
  `wt.mca96`, `wt.gripper`, `blocks`, `Plate96`). It is generated from the
  code: if it is not listed there it does not exist, and if it is, it does.
- Do not look at other protocols (examples, earlier runs, tests): write this
  one from the document, this skill, the deck file and `fluentvibe api`. The
  core source under `fluentvibe/` may be read to understand the API.

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

## Workflow: build it like code, a phase at a time

Do not plan the whole protocol in your head and write it in one go. Write a
small working file first and grow it a phase at a time, the way code is
written bit by bit.

The simulator is for checking work you believe is done: a finished phase or
the finished protocol. Do not use it to explore or to try things out; work the
API out from `fluentvibe api` and the source. Only your own file can be
simulated.

1. Read the document (PDF: `python -c "from fluentvibe.authoring.attachments import extract_file_text; print(extract_file_text('<file>')[0])"`).
   List its deck steps in order, with the sentence each comes from, and
   group them into phases (e.g. setup, bead binding, washes, final
   resuspension). Keep this list short; do not work out volumes or tips yet.
2. **Phase 0, skeleton.** Write the file (the path you were given, or
   `build/eval/pi-<short-name>.py`; an existing folder, do not create folders)
   with only `build_worktable()`, the workspace, the Variables group and the
   labware placement and fills. When you believe it is right, run
   `python -m fluentvibe.cli simulate <file> --strict`. Fix what it reports.
3. **One phase at a time.** Add the next phase with the `edit` tool (append
   before `return wt`; do not rewrite the whole file). When you believe the
   phase is done, simulate once to check it. Fix only what that phase broke,
   then move on. A protocol that stops after a phase is a valid simulation:
   deck, tips and volumes so far are real.
4. When all phases are in: `python -m fluentvibe.cli check <file>`, then
   `python -m fluentvibe.cli compile <file> -o <file without .py>.xscr`.
5. If FluentControl is running and logged in:
   `python -m fluentvibe.cli fc-open <file> --profile build/workspaces/1080_Dev --json`.
   Its InfoPad is the final word; fix what it reports.

Never resend the whole protocol to fix one line: edit the line.

Say "simulates" or "FluentControl clean" only when that command passed, and
end with the document steps you followed and what you assumed.
