---
name: family-cherrypicking
axis: family
description: Cherry-pick selected samples (hits, passing samples) from source plates into a destination plate, each pick with its own source well, destination well and volume, optionally with diluent first. Select for cherry-picking, hit-picking, sample selection, targeted transfers or pick-list-driven well-to-well moves.
always_on: false
---
## What the product is

A destination plate holding the picked samples in the listed positions.

## Where the pick list comes from

| Source of the list | Use |
|---|---|
| In the request or document, each sample stays at its address | `transfer_volumes` (FCA; `volumes={"A1": 5, ...}`; well X goes to well X) |
| In the request or document, samples move to new addresses | FCA by hand, one pick at a time (below) |
| A CSV/GWL file the lab produces at run time | a worklist (`wt.worklist`); tell the user it is checked only if the file exists at simulation time |
| Neither | ask for it; never invent picks |

A pick to a new address (one channel, fresh tips per pick; a Python list of picks is fine here):

```python
for src_well, dst_well, vol in PICKS:          # e.g. [("C5", "A1", 10), ("H12", "B1", 7)]
    fca.get_tips(fca_tips)
    fca.aspirate(source, vol, liquid_class="Water Free Single", wells=[src_well], channels=[0])
    fca.dispense(dest, vol, liquid_class="Water Free Single", wells=[dst_well], channels=[0])
    fca.drop_tips()
```

With **diluent first** (pick into a pre-filled well): `distribute_volumes` from the diluent trough, then the picks,
then a mix if the document asks for one.

## What to ask

The list (source well, destination well, volume), the destination layout if not given, and whether a diluent goes in
first.

## Pitfalls

One fresh tip per pick (samples never share a tip). Keep the list's order unless the document says otherwise.
