---
name: family-normalize-to-target
axis: family
description: Normalise samples to a target concentration or amount - per-well diluent and sample volumes computed from measured concentrations, then the diluted samples in a new plate (DNA/RNA/library/protein normalisation). Select for normalisation, normalize to X ng/µl, equal input, dilution to target concentration.
always_on: false
---
## What the product is

A new plate where every sample is at the target concentration (or holds the target amount) in the final volume.

## The arithmetic (per well)

```text
sample volume  = target conc × final volume / measured conc
diluent volume = final volume - sample volume
```
- measured conc below target: the sample cannot reach it; use all of it (sample = final volume, no diluent) and
  flag the well.
- sample volume below the pipetting minimum (about 1 µl on the FCA): raise the final volume or pre-dilute; ask.
Compute the volumes in Python from the concentrations the user gives (or a file they name) and pass the dicts.

## Steps and what must be kept

1. Diluent first: `distribute_volumes(source=diluent, plate=norm_plate, volumes={...})`.
2. Samples: `transfer_volumes(source=samples, dest=norm_plate, volumes={...})` (fresh tips per sample).
3. Mix, if the document asks for it.

Diluent first, sample second: the sample then lands in liquid and is mixed by the dispense.

## What to ask

The concentrations (or the file), the target, the final volume, the diluent. Never invent concentrations: without
them the protocol cannot be computed; ask.
