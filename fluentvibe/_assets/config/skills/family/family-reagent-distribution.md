---
name: family-reagent-distribution
axis: family
description: Distribute a reagent or medium from a trough into the wells of one or more plates (plate filling, buffer aliquoting, media dispensing, several master mixes each into its own block of columns). Select for "dispense X into all wells", plate filling, media distribution or reagent aliquoting.
always_on: false
---
## What the product is

Plates with the reagent in the named wells. Usually the first stage of something else (an assay, a PCR).

## Choose the head by what the liquid costs

- **Reagent** (kit reagent, enzyme, master mix, costly buffer): FCA from a slim trough or tubes -
  `distribute_reagent`. One set of tips serves all columns because the tips never touch the wells.
- **Cheap bulk liquid** (water, PBS, wash buffer, medium in bulk): MCA from an SBS reservoir (`60ml SBS MCA96` /
  `300ml SBS`) - `add_reagent`. Never an MCA dispense from a slim trough.
- **A different volume per well**: `distribute_volumes`.

## Variants

- **Several plates**: one call per plate; one trough fill for the total (the simulator checks it).
- **Several mixes, each into a block of columns**: one `distribute_reagent(..., columns=[...])` per mix, each from
  its own trough, with its own tips.
- **Some wells stay empty (controls, blanks)**: restrict the columns or wells; say which in the final message.

## What to ask

Volume per well, which plates and wells, which reagent in which block when there are several.

## Pitfalls

Do not put a common reagent into a plate first so the MCA can stamp it: someone would have to fill 96 wells by hand.
