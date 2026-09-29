---
name: family-pooling
axis: family
description: Pool samples from many wells into fewer wells or one tube - equal-volume pooling of a plate or of named wells, equimolar pooling with a different volume per sample, pooling of whole columns. Select for pooling, sample combining, library pool construction, multiplex pooling or consolidation.
always_on: false
select_when:
  - pool
  - pooled
  - pooling
---
## What the product is

Fewer wells than went in: the pool (one well or a few). A step that leaves every sample in its own well is not
pooling, whatever it is called.

## Choose by what is pooled

| Pooling | Use |
|---|---|
| Named wells (or the first N samples) into one well, equal volume | `pool_wells(..., source_wells=samples.first_wells(N), dest_well="A1")` |
| Whole plate, 8 row pools in one column (then the operator combines them) | `pool_columns`, then `offdeck_step` for the final combine if one tube is wanted |
| Different volume per sample (equimolar) | compute the volumes from the concentrations; FCA by hand, per source column (below), or a worklist the user supplies |

Equimolar, one source column at a time (up to 8 wells, one volume per well, fresh tips per pass):

```python
vols = {"A1": 4.0, "B1": 6.5, "C1": 3.2}                  # from the concentrations
fca.get_tips(fca_tips)
fca.aspirate(samples, max(vols.values()), liquid_class="Water Free Single", wells=list(vols), volumes=list(vols.values()))
fca.dispense(pool_plate, max(vols.values()), liquid_class="Water Free Single", wells=["A1"] * len(vols), volumes=list(vols.values()))
fca.drop_tips()
```

Check the pool volume: 96 × 10 µl is 960 µl and does not fit a 96-well well (350 µl). Pool into several wells or
ask for a smaller volume per sample; the simulator reports an overflow.

## What to ask

Volume per sample (or the concentrations and the target for equimolar), which samples, where the pool goes.

## Pitfalls

Fresh tips per sample (the blocks do it). Keep the document's order: if it pools before a clean-up, pool first.
