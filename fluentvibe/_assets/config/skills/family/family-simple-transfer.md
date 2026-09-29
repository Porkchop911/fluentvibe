---
name: family-simple-transfer
axis: family
description: Plain liquid transfer - move a volume from a source (plate or trough) into a destination plate, whole plate or selected wells. The default family when the request has no assay-specific shape (no beads, no magnet, no dilution series).
always_on: false
---
## What the product is

The destination plate with the requested volume in the requested wells. Nothing else is added.

## Choose by the shape of the move

| Move | Use |
|---|---|
| Plate → plate, every well to the same address | `stamp` (MCA, one aspirate/dispense for all 96) |
| Trough → every well of a plate | `distribute_reagent` (FCA; reagent) or `add_reagent` (MCA; cheap bulk liquid from an SBS reservoir) |
| Some wells, or a different volume per well | `transfer_volumes` / `distribute_volumes` (FCA, fresh tips per sample) |

## What to ask

Volume per well, which wells (all 96, the first N samples, named wells), and whether a mix after the dispense is
wanted. Everything else has a default.

## Pitfalls

- A tip that touched a sample never goes into another sample (the blocks already change tips).
- Do not add a mix, incubation or reagent the request does not describe.
