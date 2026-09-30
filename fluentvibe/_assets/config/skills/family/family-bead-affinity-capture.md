---
name: family-bead-affinity-capture
axis: family
description: Affinity capture of a target from a sample on ligand-coated magnetic beads - immunoprecipitation (antibody / Protein A/G beads), His-tag purification (Ni-NTA magnetic beads), SP3 protein clean-up - bind, wash, and elute or process on the beads as the document says. Select for IP, immunoprecipitation, pull-down, Ni-NTA, IMAC, His-tag, SP3. Not for immobilising a biotinylated molecule on streptavidin beads.
always_on: false
---
## What the product is

Depends on the document; decide before writing (core-lab-rules):
- **IP / pull-down**: usually the target eluted off the beads (low-pH buffer, or boiling in SDS sample buffer for a
  gel), sometimes the beads themselves (on-bead digestion, "keep beads").
- **His-tag (Ni-NTA)**: the protein eluted with imidazole buffer.
- **SP3**: the peptides after on-bead digestion, recovered from the supernatant.

## Steps and what must be kept

1. **Bind**: beads (often pre-coupled with antibody) + sample, off the magnet, kept in suspension during the
   incubation (30 min to overnight; cold incubations and rotation are operator steps).
2. **Separate** and discard the unbound fraction (unless the document keeps the flow-through).
3. **Wash** as the document says (number, buffer, stringency). Resuspend in each wash.
4. **Elute or process** exactly as the document describes. Heat (e.g. 95 °C in SDS buffer) is an operator step;
   then separate and move the eluate to a new plate with its own tips.

## Typical values and what to ask

Beads 10–50 µl slurry per sample; washes 200–500 µl (large volumes need a deep-well plate: ask); elution
20–50 µl. Ask when open: bead amount, wash volume and count, elution buffer and volume, whether the flow-through
is kept.

## On this deck

Beads and buffers by the FCA (`distribute_reagent`), samples with `stamp`, keep suspended with `mix_wells`,
`separate` / `remove_liquid` / `release` for the magnet, washes in a native loop, the eluate moved with `stamp` into
a clean plate. Heat and rotation: `offdeck_step`.
