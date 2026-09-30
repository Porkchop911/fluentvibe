---
name: family-bead-immobilization
axis: family
description: Immobilising a biotinylated molecule (DNA, RNA, oligo, antibody, protein) on streptavidin magnetic beads (Dynabeads M-280 / MyOne, streptavidin beads) where the product is the beads with the bound material - bind, magnet, wash, resuspend. No elution. Select for streptavidin, biotin, immobilise/immobilize, capture onto beads, bead-bound product.
always_on: false
select_when:
  - streptavidin beads
  - streptavidin magnetic
  - streptavidin-coated
  - dynabeads m-280
  - myone streptavidin
  - immobilize
  - immobilise
  - immobilization
  - immobilisation
---
## What the product is

The **beads with the bound molecule**. Streptavidin–biotin binding is practically irreversible; the protocol ends
with the beads resuspended in a low-salt buffer for downstream use. There is **no elution and no eluate transfer**
unless the document asks for a separate step (e.g. generating single-stranded DNA with NaOH, where the product is
then the released strand: only when the document says so).

## Steps and what must be kept

1. **Bead preparation**: resuspend the stock (settles within minutes), wash once in buffer on the magnet, then
   **replace** the liquid with 2X binding buffer (e.g. 2X B&W, 2 M NaCl) at twice the original bead volume: on
   the magnet, remove the bead liquid, add the 2X buffer, resuspend off the magnet. "Beads come pre-washed" skips
   the wash, not the exchange: adding 2X buffer on top of the bead liquid dilutes it, and the binding step then
   runs at about half the salt.
2. **Bind**: add an **equal volume** of the biotinylated material (in water or low-salt buffer) to the beads in 2X
   buffer, which brings the salt to 1X (1 M NaCl) for binding. The equal volume is the point: keep it.
3. **Incubate** 15 min at room temperature (nucleic acids ≤ 1 kb; ≤ 10 min for short oligos) **with the beads kept in
   suspension**: gentle rotation (a rotator: operator step) or, on the deck, a gentle mix every few minutes.
4. **Separate** on the magnet 2–3 min; discard the supernatant (keep a few µl so the pellet is not disturbed).
5. **Wash** 2–3 × with 1X buffer: off the magnet, add, resuspend, magnet, discard.
6. **Resuspend** in the final low-salt buffer (e.g. 10 mM Tris or TE, or what the document names) at the wanted
   concentration. The plate with the beads is the result.

## Typical values and what to ask

Per well of a 96-well plate: 5–20 µl bead stock (10 mg/ml; capacity roughly 10–20 µg dsDNA per mg for short
fragments, less for long ones), equal volumes for binding (so 20–40 µl in the binding step), 100–200 µl per
wash, 20–50 µl final. Ask when open: amount of beads per sample, wash volume and count, final buffer and volume,
whether the beads arrive washed.

## On this deck

- Beads and 2X buffer: FCA from slim troughs (`distribute_reagent`); mix the bead trough before dispensing.
- Samples: `stamp` from the sample plate (one channel per sample).
- Keep beads suspended: `mix_wells`; a rotator is an `offdeck_step`.
- Magnet: `separate` / `remove_liquid` / `release`; washes: `add_reagent` (1X buffer as bulk from an SBS reservoir,
  MCA) or `distribute_reagent`, then `mix_wells`. Repeat with a native loop.
- Model the final buffer with the default role (`plain`), not `eluent`: the simulator then keeps the molecule on
  the beads.

## Do not

Elute, transfer the "supernatant" or "eluate" as product, neutralise, or add ethanol/SPRI steps: that is SPRI
chemistry, a different product.
