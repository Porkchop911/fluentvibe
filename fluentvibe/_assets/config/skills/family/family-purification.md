---
name: family-purification
axis: family
description: Solid-phase extraction on filter or resin plates (C18 desalting, SPE, spin/vacuum plates) - condition, equilibrate, load, wash, elute into a collection plate, with the flow-through driven by vacuum or centrifuge. Select for SPE, C18, desalting, filter plate, resin clean-up, peptide clean-up for MS. Magnetic-bead purifications are other families.
always_on: false
---
## What the product is

The eluate in the collection plate under the filter plate.

## Steps and what must be kept

Condition → equilibrate → load sample → wash (×n) → elute (×n). After **every** addition the liquid has to pass
through the matrix: vacuum manifold or centrifuge. This deck has neither, so each pass is an operator step
(`offdeck_step`, e.g. "Spin the filter plate 1 min at 500 × g, discard the flow-through, return it on the waste
plate"). Before elution the operator swaps the waste plate for the collection plate: say so in that step.

- Solvents and buffers: `distribute_reagent` (FCA, slim trough). Organic solvents (acetonitrile, methanol) are
  reagents here too.
- Samples: `stamp` or `transfer_volumes`; fresh tips per sample.

## Typical values and what to ask

C18 plates: 100–200 µl per condition/wash step, 50–100 µl elution, 1–2 elutions. Ask for the plate type (whether it
is in the deck's labware; if not, ask which plate stands in for it), the spin or vacuum settings and the volumes.

## Pitfalls

Do not model the flow-through as a dispense "through" the plate or as a wait: without the operator step the liquid
never moves. If the filter plate is not in the lab scope, say so and ask instead of substituting silently.
