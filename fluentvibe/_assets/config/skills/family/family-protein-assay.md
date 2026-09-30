---
name: family-protein-assay
axis: family
description: Colorimetric total-protein quantitation (Pierce BCA, Bradford, Lowry) - standards and samples into a plate, working reagent into every well, incubate, read off the deck. Select for BCA, Bradford, protein concentration or total-protein assay.
always_on: false
---
## What the product is

A plate with samples, standards and blanks mixed with working reagent, incubated and ready for the reader.

## Steps and what must be kept

1. **Standards** (BSA dilution series) and **samples** into the plate, usually in duplicate or triplicate:
   `stamp` from a prepared plate, `transfer_volumes` for a partial layout. If the robot must make the standards,
   do the series first (serial dilution) in the first columns.
2. **Working reagent** into every used well: `distribute_reagent` (FCA; one set of tips, dispensed from above).
3. **Mix** (`mix_wells`, or a plate shake as an operator step).
4. **Incubate**: BCA 30 min at 37 °C (operator: incubator, `offdeck_step`) or 2 h at room temperature (`wt.wait`);
   Bradford 5–10 min at room temperature.
5. **Read** off the deck: BCA 562 nm, Bradford 595 nm (`offdeck_step`).

## Typical values

BCA microplate: 25 µl sample + 200 µl working reagent (or 10 µl + 200 µl for low volume). Bradford: 5–10 µl
sample + 250 µl reagent. Working reagent is prepared by the operator (BCA A:B 50:1) unless the document asks for
it on the deck.

## What to ask

Sample count and replicates, standards made by hand or by the robot, incubation (37 °C or room temperature).
