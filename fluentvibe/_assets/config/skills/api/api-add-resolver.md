---
name: api-add-resolver
axis: api
description: wt.add(reagent, to=plate, volume_ul=...) — add a reagent to every well with the source trough, head, tips and fill resolved from the deck; explicit head= / liquid_class_var= are honoured or refused.
always_on: true
requires_env: FLUENTVIBE_RESOLVER
---
## `wt.add` — reagent additions without deck bookkeeping

For adding a reagent to the wells of a plate, call `wt.add` instead of placing
troughs and tip boxes, filling them and calling `distribute_reagent` /
`add_reagent` yourself:

```python
beads = Reagent("AMPure XP beads", role="bead_carrier")
wt.add(beads, to=samples, volume_ul=36, name="Add beads")                 # FCA from a slim trough
wt.add(ethanol, to=samples, volume_ul=200, name="Ethanol wash 1")         # bulk liquid: MCA96
wt.add(ethanol, to=samples, volume_ul=200, head="fca",                    # the request says FCA
       liquid_class_var="LC_ETHANOL", name="Ethanol wash 1")
```

The deck resolves: the source trough and a free position for it, the head
(reagents: FCA from a slim trough; water/ethanol/wash: MCA96 from an SBS
reservoir), the tip box, and the fill volume (the demand of every `wt.add`
for that reagent plus dead volume). Do **not** place or fill those sources
yourself, and do not place tip boxes for `wt.add`.

Requirements from the request go in as arguments and are enforced:

- `head="fca"` / `head="mca"` — which head dispenses (an impossible
  combination raises `ResolutionConflict`; fix the request, do not work around it);
- `liquid_class_var="LC_BEADS"` — use a string variable for the liquid class
  (declared for you, default `liquid_class=` or the deck's default);
- `columns=[1, 2, 3]` — a partial plate;
- `source=my_trough` — a source you placed yourself.

Everything else (mixing, removing supernatant, magnet moves, transfers to a new
plate, waits, loops, worklists) stays ordinary DSL and blocks:
`mix_wells`, `remove_liquid`, `separate` / `release`, `stamp`, `wt.wait`.
Sample-touching steps (mix, remove, transfer) need their own MCA96 tip boxes,
which you place.
