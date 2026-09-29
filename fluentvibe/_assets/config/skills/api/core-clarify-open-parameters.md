---
name: core-clarify-open-parameters
axis: api
description: What to do when the request or document leaves protocol-defining numbers open (per-well volumes, bead amounts, sample count/layout, times, temperatures) — ask the user one question with concrete, deck-feasible proposals via ask_user instead of inventing values. Always loaded.
always_on: true
---
## Open parameters: ask, with proposals — do not guess silently

Many documents are written for tubes and leave the plate-scale numbers to the
user ("transfer the desired volume", "an equal volume of buffer", "optimize by
titration"). Inventing those numbers wastes turns — the simulator then rejects
wells that overflow or tips that cannot hold the volume — and the protocol
encodes choices nobody approved.

**Before drafting**, list the numbers the protocol needs that neither the
request, an approved Bench Spec, nor the document states: per-well volumes of
every reagent and wash, bead amount per sample, sample count and plate layout,
incubation times and temperatures, number of washes. Ratios the document does
state ("equal volume", "1.8×") are not open — derive from them.

**If any are open, call `ask_user` once**, with every open number in one
question, each with a concrete proposal and the reason it fits:

```
ask_user(
  question=(
    "The document gives no plate-scale volumes. Proposed per well (96 wells), please confirm or correct:\n"
    "1. Reagent A: 50 µL (the document's 1:1 ratio to the 50 µL sample)\n"
    "2. Incubation: 30 min at room temperature on the deck (document: 30 min, RT)\n"
    "3. Washes: 3 × 150 µL wash buffer (document: 'wash three times', volume open)\n"
    "4. Final volume: 50 µL (document: 'resuspend in a suitable volume')"
  ),
  axes=["reagent_a_ul", "incubation_min", "wash_ul", "final_ul"],
)
```

Proposals must fit the deck, so the answer can be used as is:
- one tip trip ≤ 200 µL with `MCA96, 200ul` / `FCA, 200ul` tips (larger
  volumes need trips — blocks split them);
- a 96-well plate well holds at most ~330 µL (≤ 180 µL while beads are in it);
- a mix volume ≤ the liquid in the well and ≤ 90% of the tip capacity;
- reagent totals for 96 wells fit their trough (25 mL / 100 mL) with dead volume.

**Do not ask** for what the deck profile and skills already fix (labware,
positions, liquid classes, tips), for numbers the document states, or more
than once per run. One clear question with proposals, then wait.

**When nobody can answer** — the request says to choose values yourself, or
asks for an unattended/batch run — do not ask: use the proposals, declare
each as a FluentControl variable, mark it `# ASSUMED` in the source, and list
the assumed values in your final message.
