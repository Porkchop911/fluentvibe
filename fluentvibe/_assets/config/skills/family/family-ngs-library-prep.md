---
name: family-ngs-library-prep
axis: family
description: Sequencing library preparation (Illumina DNA Prep / Nextera XT tagmentation, ligation kits, ONT barcoding, amplicon libraries) - fragmentation or tagmentation, end repair, adapter or barcode addition, PCR, bead clean-ups, pooling. Select for NGS library prep, tagmentation, barcoding, adapter ligation, sequencing libraries.
always_on: false
---
## What the product is

Indexed libraries (per sample, or one pool) ready for quantification and loading. The kit document is the protocol:
follow its stages in its order, with its volumes.

## How to read the kit document

List the stages exactly as the document groups them (e.g. "Tagment", "Post-tagmentation clean-up", "Amplify",
"Clean up libraries", "Pool"). For every stage note: what goes in (reagent, volume, from where), mix, incubation
(where: bench, magnet, thermocycler), and what comes out (supernatant discarded, eluate kept, beads kept). Then write
one group per stage. Do not merge stages, drop one, or add one the document does not have.

## Map the stages onto the deck

| Stage element | Use |
|---|---|
| Kit reagent or master mix into every sample | `distribute_reagent` (FCA, slim trough or tube) |
| Per-sample reagent (barcodes, index adapters) from a plate | `stamp` (MCA, one tip box per stamp) |
| Samples into the reaction plate | `stamp` / `transfer_volumes` |
| Bead clean-up that ends in an eluate (AMPure, SPRI, "clean-up beads") | `spri_cleanup`, or the magnet primitives for a partial plate (see family-bead-cleanup-spri) |
| Bead wash where the product stays on the beads (e.g. tagmentation on beads, then wash) | magnet primitives: `separate` / `remove_liquid` / `add_reagent` / `release`, no elution |
| Thermal step (tagmentation 55 °C, end-prep 20/65 °C, PCR) | `thermal_step` (operator unless the deck lists an ODTC) |
| Pool | `pool_wells` / `pool_columns` |
| Quantification (Qubit, TapeStation), loading | `offdeck_step` |

## Typical values and what to ask

Take the volumes from the document. Ask for what the document leaves to the user: number of samples, input
amount/volume, the number of PCR cycles, and whether the run should stop after a stage (many kits have safe
stopping points).

## Pitfalls

- A clean-up returns the **eluate** in a new plate; the next reagent goes onto the eluate, not into the old bead wells.
- Barcodes differ per sample: never from a trough.
- Heat on the deck is not possible without a listed device; never model it as a wait.
