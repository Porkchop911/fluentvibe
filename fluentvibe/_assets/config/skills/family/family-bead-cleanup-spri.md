---
name: family-bead-cleanup-spri
axis: family
description: SPRI / AMPure XP style clean-up and size selection of DNA - bind DNA to carboxyl beads with PEG/salt, magnet, ethanol washes, dry, elute in low-salt buffer, recover the eluate into a new plate. Select for AMPure, SPRI, SPRIselect, PCR clean-up, size selection, bead purification of DNA. Not for streptavidin/biotin immobilisation (the product stays on the beads there).
always_on: false
select_when:
  - spri
  - size selection
  - bead cleanup
  - bead clean-up
  - bead purification
  - pcr cleanup
  - pcr clean-up
  - ampure
  - spriselect
---
## What the product is

The purified DNA **in the eluate**, moved to a new plate. The beads are discarded with the plate.

## Steps and what must be kept

1. **Add beads** at the document's ratio to the sample volume (e.g. 1.8× for a general clean-up; 0.5–1.0× for
   size selection; double-sided selection uses two ratios and keeps a supernatant in between). Mix thoroughly;
   bind ~5 min at room temperature.
2. **Magnet** until clear (2–5 min); discard the supernatant (all free liquid, keep a few µl).
3. **Ethanol washes** (usually 2 × 80 % ethanol, freshly made): add on the magnet, ~30 s, remove. Remove the last
   ethanol completely.
4. **Dry** briefly (1–5 min; over-dried pellets crack and elute poorly).
5. **Elute off the magnet**: add the elution buffer (EB, TE or water), resuspend, ~2 min.
6. **Magnet** until clear; move the eluate to a new plate with its **own tip box**.

## Typical values and what to ask

Sample 20–50 µl; beads = ratio × sample; ethanol 150–200 µl per wash; elution 15–40 µl (recover slightly less
than added). Ask when open: ratio (if the document gives none), elution volume, sample volume.

## On this deck

- Whole plate: one `spri_cleanup(...)` call does all of it: beads and elution buffer by the FCA from
  slim troughs, ethanol by the MCA from an SBS reservoir (`60ml SBS MCA96` or `300ml SBS`, never a slim trough),
  three MCA tip boxes (reagent, sample, eluate). Fill the sample plate with the analyte reagent; the block handles
  the rest.
- Partial plate or an unusual order: the primitives (`distribute_reagent`, `mix_wells`, `separate`,
  `remove_liquid`, `add_reagent`, `release`, `stamp`), washes in a native loop.

## Do not

Put downstream reagents into the bead wells instead of recovering the eluate; send the eluate to waste; use this
chemistry for streptavidin–biotin capture.
