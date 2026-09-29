---
name: family-pcr-setup
axis: family
description: PCR / qPCR reaction assembly - master mix, primers, water and template into a 96- or 384-well PCR plate, then the thermal program (operator or an on-deck thermocycler). Includes qPCR library quantification with standards and replicates. Select for PCR setup, master mix plating, qPCR, library quantification, amplification.
always_on: false
---
## What the product is

A sealed-ready PCR plate: every reaction well holds master mix + primers + template (+ water) at the reaction
volume, in the layout the document gives. The thermal program is the last step, off the deck unless the deck section
lists a thermocycler.

## Steps and what must be kept

1. **Master mix** (and water, if separate) into every reaction well: `distribute_reagent` from a slim trough.
   Master mix is a reagent: FCA, never the MCA.
2. **Primers / indexes** that differ per well: from a plate, `stamp` (MCA) or `transfer_volumes` (FCA).
3. **Template** last: `stamp` from the sample plate, or `transfer_volumes` for a partial plate; fresh tips per sample.
4. **Mix** if the document says so (`mix_wells`, gently; bubbles matter for qPCR).
5. **Thermal program**: `thermal_step` (an on-deck ODTC only if the deck section lists one; otherwise it becomes an
   operator step with the program text). Seal and spin are operator steps.

Keep master mix first and template last (least carry-over). Keep the enzyme mix cold: an operator note before the
run, not a wait.

## Typical values and what to ask

Reactions 10–50 µl (96) or 5–10 µl (384; 29 µl max). Template 1–5 µl. qPCR: standards in the first columns,
replicates (2–3) in adjacent wells; no-template controls get water instead of template. Ask when open: reaction
volume, template volume, the layout of standards/controls/replicates, the program.

## Pitfalls

- Never pipette below ~1 µl on the FCA: pre-dilute or raise the volume.
- Do not add a clean-up or a second PCR the document does not describe.
