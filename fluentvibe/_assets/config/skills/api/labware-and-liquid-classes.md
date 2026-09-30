---
name: labware-and-liquid-classes
axis: api
description: Which labware to use for what (catalog names and Python classes), which head takes liquid from where, capacities and fill limits, liquid classes, and how to fill sources and samples. Always loaded.
always_on: true
---
## Labware (the deck section's class contract is binding)

| Role | Class | `catalog=` | Head / notes |
|---|---|---|---|
| 96-well plate (samples, products, reactions) | `Plate96` | `96_ABgene_SuperPlate_Thermo_AB2800` | the default plate |
| 384-well plate | `Plate384` | `384 Well LowVol LoBase` | only when 384 is asked for; 29 µl per well |
| Magnet | `MagnetRack` | `LV_Alpaqua_A000350` | on a `Nest61mm_Pos`; the gripper moves the plate onto it |
| Reagent trough, small | `Trough25mL` | `25ml_short` | FCA only; on `WS_100ml_1`; fill ≤ 22 ml |
| Reagent trough, large | `Trough100mL` | `100ml` | FCA only; on `WS_100ml_1`; for totals over 22 ml |
| Bulk reservoir | `Trough100mL` | `60ml SBS MCA96` | MCA (or FCA); on a `Nest61mm_Pos`; fill ≤ 55 ml |
| Large bulk reservoir / waste | `Trough25mL` | `300ml SBS` | MCA (or FCA); on a `Nest7mm_Pos` the MCA reaches (not 1-3 on the 1080 deck); fill ≤ 250 ml. Also the waste |
| MCA tips | `MCA100Box` / `MCA200Box` / `MCA500Box` | `MCA96, 100ul, Box` / `MCA96, 200ul, Box` / `MCA96, 500ul, Box` | 200 µl is the default |
| FCA tips | `FCA200Box` / `FCA1000Box` | `FCA, 200ul SBS` / `FCA, 1000ul SBS` | never an MCA box with the FCA |

The class name is the contract, not the volume: `300ml SBS` is a `Trough25mL`. Copy catalog names exactly.

## Which head takes what

- **Reagents** (kit reagents, beads, buffers, master mixes, enzymes): the **FCA** from a slim trough or tubes
  (`distribute_reagent`). Little dead volume, and reagents are costly.
- **Cheap bulk liquids** (water, ethanol, wash buffer): the **MCA** from an SBS reservoir (`add_reagent`).
- **The MCA never pipettes in a slim trough** (`25ml_short`, `100ml`): FluentControl reports "out of range".
- Plate-to-plate work (samples, supernatant removal, products): the MCA (`stamp`, `remove_liquid`, `mix_wells`).
- A reagent that differs per well (barcodes, indexes, samples) comes from a plate and is stamped; a common reagent
  is never put into a plate first ("someone would have to fill 96 wells by hand").

## Capacities: let the simulator count

| | Limit |
|---|---|
| 96-well well | 350 µl; keep ≤ 330, and ≤ 180 while beads are in the well |
| 384-well well | 29 µl |
| Tips | MCA 100 / 200 / 500 µl, FCA 200 / 1000 µl per trip; blocks split larger volumes into trips |
| Mixing | a mix volume ≤ the liquid in the well and ≤ 90 % of the tip |
| Fills | the fill limits in the labware table |

Fill sources generously and run the simulator: it reports a short source, an overflowing well or a tip that is too
small, exactly, with the line. Do not add these up by hand.

## Liquid classes

- `Water Free Single`: every aspirate and dispense (the lab's convention: one transfer class for all liquids).
- `Water Mix`: every mix. FluentControl rejects `Water Free Single` for MCA mixing ("Liquid subclass section 'Mix'
  is missing"). The blocks use it by default.
- `Empty Tip`: emptying tips. The blocks use it by default.

Do not invent other class names. If the request asks for liquid classes as variables, declare one string variable
per role (`wt.declare_variable("LC_BEADS", "Water Free Single")` + `wt.set_sim_value(...)`) and pass its name.

## Filling sources and samples

```python
beads_trough.fill_all(Reagent("Beads", role="bead_carrier"), 6000)          # a source
samples.fill_all(Reagent("Sample matrix"), 18)                               # sample liquid ...
samples.layer_all(Reagent("Sample DNA", role="analyte"), 2)                  # ... with a small analyte marker on top
part.fill_wells(part.first_wells(24), Reagent("Sample"), 20)                 # only the first 24 wells
```

`fill_all` replaces what is in the wells; `layer_all` / `layer_wells` add on top. Roles: `analyte` (the thing you
carry through the protocol), `bead_carrier` (bead suspension), `eluent` (a buffer that releases the analyte from
beads), `plain` (everything else, the default).
