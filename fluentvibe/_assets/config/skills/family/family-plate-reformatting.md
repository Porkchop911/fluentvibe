---
name: family-plate-reformatting
axis: family
description: Copy or rearrange wells between plates - whole-plate 1:1 copy, column rearrangement, consolidation of scattered wells into a compact layout, 96 → 384 quadrants. Select for plate copying, replication, reformatting, consolidation or rearranging samples between plates.
always_on: false
---
## What the product is

A destination plate whose layout is a function of the source layout. Write the mapping down (source well →
destination well) before choosing a block; the mapping is the protocol.

## Choose by the mapping

| Mapping | Use |
|---|---|
| Same address, all 96 wells | `stamp` (MCA) |
| Whole columns to other columns (e.g. left half 1-6 centred on 4-9) | MCA by hand with `columns=` (see head-mca96) |
| Several copies of one plate (replicates) | one `stamp` per copy, a tip box per stamp unless the document allows reuse |
| Same address, a different volume per well | `transfer_volumes` (FCA) |
| Arbitrary well → well | FCA by hand, one pick per sample: `fca.aspirate(src, v, wells=["C5"], channels=[0])`, `fca.dispense(dst, v, wells=["A1"], channels=[0])`, fresh tips each |
| Many wells into one (consolidation of volume) | that is pooling: `pool_wells` / `pool_columns` |
| A pick list the user supplies as a file | a worklist |

96 → 384: each 96-well plate goes to one quadrant (A1, A2, B1, B2 offsets). The 384 wells hold 29 µl: check the
volume first and ask if it does not fit.

## What to ask

The mapping if the document does not fix it; volume; whether the source is kept (transfer) or emptied.

## Pitfalls

Fresh tips per sample; never let the FCA reuse a tip across different source wells.
