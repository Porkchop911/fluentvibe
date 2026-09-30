---
name: family-cell-seeding
axis: family
description: Cell culture liquid handling - seed a cell suspension into culture plates, exchange medium, wash with PBS, add compounds or treatments. Select for cell seeding, plating cells, media exchange, compound treatment, transfection set-up.
always_on: false
---
## What the product is

Culture plates with cells (and treatment) in the named wells. The plates go back to the incubator: an operator step.

## Steps and what must be kept

- **Seeding**: keep the cells in suspension. Mix the suspension (trough or tube) right before each dispense round;
  dispense gently. `distribute_reagent` from a trough (FCA, dispensed from above, one set of tips), with a mix of the
  source between rounds if the document asks for it.
- **Medium exchange**: remove spent medium without touching the cell layer (adherent cells; leave a little
  liquid), add fresh medium along the wall. Removal: `remove_liquid` (MCA) or FCA per column; fresh medium:
  `distribute_reagent` or `add_reagent` (bulk medium from an SBS reservoir).
- **PBS wash**: add, remove; do not let the cells dry between steps.
- **Treatment / compound**: from a compound plate by `stamp` or `transfer_volumes` (different compounds per well);
  one compound for all wells by `distribute_reagent`.
- **Incubation** (37 °C, CO₂): `offdeck_step`.

## Labware

Only the plates in the lab scope. A 6/12/24-well culture plate that is not listed must be asked for, not replaced by
a 96-well plate.

## What to ask

Cell volume per well (and cell density, for the final message), plate format, which wells, medium exchange
volumes, treatment layout.
