---
name: family-elisa
axis: family
description: ELISA and other plate immunoassays - coat, block, samples and standards, detection antibody, conjugate, substrate, stop, with wash cycles between; the read is off the deck. Select for ELISA, sandwich ELISA, cytokine quantification, immunoassay.
always_on: false
---
## What the product is

A developed, stopped plate ready for the reader. The wells themselves are the product; nothing is recovered.

## Steps and what must be kept

The document's sequence, typically: (coat → wash →) block → wash → samples + standards → incubate → wash →
detection antibody → incubate → wash → conjugate (e.g. streptavidin-HRP) → incubate → wash → substrate (TMB) →
develop in the dark → stop solution → read.

- **Wash**: remove the liquid from all wells, add wash buffer, remove again; repeat the document's number of
  times. Removal: `remove_liquid` (MCA) with the volume in the well, the plate's own tip box (channel *i* only meets well *i*). Wash buffer:
  `add_reagent` (MCA, bulk from an SBS reservoir). Use a native loop for the repeats.
- **Reagents** (antibodies, conjugate, substrate, stop): `distribute_reagent` (FCA, slim trough), same volume in
  every well.
- **Samples and standards**: `stamp` from a prepared plate, or `transfer_volumes` for a partial layout; standards
  as a serial dilution only if the document asks the robot to make them.
- **Incubations**: at room temperature on the deck `wt.wait(...)` is fine; shaking, 37 °C, 4 °C overnight or
  "in the dark" away from the deck are operator steps (`offdeck_step`).
- **Stop** in the same order and pace as the substrate (timing matters); the read is `offdeck_step`.

## Typical values and what to ask

Coat/samples/antibodies 50–100 µl, block and washes 200–300 µl, substrate 100 µl, stop 50–100 µl, 3–5 washes.
Ask when open: layout (standards, blanks, duplicates), whether the plate arrives coated/blocked, incubation
conditions.

## Pitfalls

Samples never share a tip. Do not skip washes between antibody steps: they define the assay.
