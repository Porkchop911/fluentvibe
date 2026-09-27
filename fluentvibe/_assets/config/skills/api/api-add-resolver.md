---
name: api-add-resolver
axis: api
description: "wt.add(reagent, to=plate, volume_ul=...) is REQUIRED for reagent additions; the deck resolves the source trough, head, tips and fill; explicit head= / liquid_class_var= are honoured or refused."
always_on: true
requires_env: FLUENTVIBE_RESOLVER
---
## `wt.add` — the way to add reagents on this deck

**Rule: every addition of a reagent (beads, buffers, ethanol, water, master
mix, elution buffer) to the wells of a plate is one `wt.add(...)` call.** Do
not place reagent troughs or reservoirs, do not place FCA tip boxes, do not
`fill_all` reagent sources, and do not call `distribute_reagent` /
`add_reagent` for reagents: `wt.add` does all of that from the deck profile
(free positions, FCA vs MCA96, slim trough vs SBS reservoir, tip boxes, fill
volume = demand of all additions + dead volume). This overrides placement
advice for reagent sources in other skills.

Requirements from the request go in as arguments and are enforced:

- `head="fca"` / `head="mca"` — which head dispenses (impossible combinations
  raise `ResolutionConflict`: fix the call, do not work around it);
- `liquid_class_var="LC_BEADS"` — the liquid class as a string variable
  (declared for you, default `liquid_class=` or the deck default);
- `columns=[1, 2, 3]` — a partial plate.

You still place the plates, the magnet, the waste and the MCA96 tip boxes for
sample-touching steps, and write mixing, supernatant removal, magnet moves,
transfers and waits with the blocks.

### Complete example (AMPure XP clean-up, 20 ul samples, whole plate)

```python
from fluentvibe import MCA200Box, MagnetRack, Plate96, Reagent, Trough25mL, Worktable
from fluentvibe.blocks import mix_wells, release, remove_liquid, separate, stamp

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace("<workspace>", workspace_guid="<guid>", auto_place=False,
                                  protocol_name="AMPure XP clean-up")
    wt.group("Labware Placement")
    samples = wt.place(Plate96("Samples", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    eluate = wt.place(Plate96("Eluate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    sample_tips = wt.place(MCA200Box("SampleTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 3)
    eluate_tips = wt.place(MCA200Box("EluateTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 5)
    magnet = wt.place(MagnetRack("Magnet", catalog="LV_Alpaqua_A000350"), "Nest61mm_Pos", 13)
    waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)
    samples.fill_all(Reagent("PCR matrix"), 18)
    samples.layer_all(Reagent("PCR product", role="analyte"), 2)
    beads = Reagent("AMPure XP beads", role="bead_carrier")
    ethanol = Reagent("70% ethanol")
    eb = Reagent("Elution buffer", role="eluent")

    wt.add(beads, to=samples, volume_ul=36, name="Add beads")                 # FCA, slim trough: resolved
    mix_wells(wt, plate=samples, tips=sample_tips, volume_ul=45, cycles=10, name="Mix beads")
    wt.wait(duration_seconds=300)
    separate(wt, plate=samples, magnet=magnet, settle_seconds=120, name="Magnet")
    remove_liquid(wt, plate=samples, waste=waste, volume_ul=49, tips=sample_tips,
                  liquid_class="Water Free Single", name="Discard supernatant")
    for n in (1, 2):
        wt.add(ethanol, to=samples, volume_ul=200, name=f"Ethanol {n}")      # bulk liquid: MCA96, resolved
        wt.wait(duration_seconds=30)
        remove_liquid(wt, plate=samples, waste=waste, volume_ul=200, tips=sample_tips,
                      liquid_class="Water Free Single", name=f"Discard ethanol {n}")
    release(wt, plate=samples, to=("Nest61mm_Pos", 1), name="Off magnet")
    wt.add(eb, to=samples, volume_ul=40, name="Add elution buffer")
    mix_wells(wt, plate=samples, tips=sample_tips, volume_ul=32, cycles=10, name="Mix eluate")
    wt.wait(duration_seconds=120)
    separate(wt, plate=samples, magnet=magnet, settle_seconds=60, name="Magnet for eluate")
    stamp(wt, source=samples, dest=eluate, volume_ul=38, tips=eluate_tips,
          liquid_class="Water Free Single", name="Eluate to new plate")
    return wt
```

If the request says "dispense the ethanol with the FCA" and "liquid classes as
string variables": `wt.add(ethanol, to=samples, volume_ul=200, head="fca",
liquid_class_var="LC_ETHANOL", name=...)` — nothing else changes.
