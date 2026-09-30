---
name: family-serial-dilution
axis: family
description: Serial (stepwise) dilution along the columns or rows of a plate - pre-fill diluent, put the stock in the first column, carry a transfer from column to column with a mix each time; standard curves, titrations, dose-response series. Select for serial dilution, dilution series, titration, standard curve, N-fold dilution.
always_on: false
---
## What the product is

A plate where each column (or row) is the previous one diluted by a fixed factor. Column 1 = most concentrated.

## Volumes: derive, do not guess

```text
mix volume M (the volume in a well while it is mixed), factor F
transfer t = M / F          diluent pre-fill D = M - t
stock column: M of stock
```
Each column is mixed at M, then gives t away, so it ends at D; the last column ends at M unless t is taken out of it
to waste (do that only if the document asks for equal volumes). For 1:2 with M = 100 µl: t = 50, D = 50. For 1:10
with M = 100 µl: t = 10, D = 90. Say in the final message which volume each column ends with.

## Steps and what must be kept

1. Diluent into columns 2..N (`distribute_reagent`, restricted to those columns).
2. Stock into column 1 (from a trough: `distribute_reagent`; from samples: `transfer_volumes`).
3. Transfer column k → k+1 and mix in k+1, k = 1..N-1, **in order** (each step needs the previous one finished).
   By hand with the FCA: a native `wt.loop` over the steps, `well_offset="(col-1)*8"` for the source and `"col*8"`
   for the destination, `fca.mix(...)` after each dispense.
4. Blank column (diluent only), if the document asks for one.

## Tips

The usual technique keeps one set of tips down the series (carry-over goes toward lower concentration). If the
document asks for fresh tips per step, or the range is very wide, change tips each step. Different samples never
share tips.

## What to ask

Factor, number of steps, volume per well, the stock and diluent, whether a blank and replicates are wanted.
