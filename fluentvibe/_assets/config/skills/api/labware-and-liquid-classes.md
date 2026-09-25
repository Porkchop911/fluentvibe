---
name: labware-and-liquid-classes
axis: api
description: The approved labware catalog names, the approved liquid classes, fill_all semantics, and the labware/volume-as-variable rules. Always loaded — every protocol places labware.
always_on: true
---
## Approved labware (use these exact `catalog=` names)

| Role | Python class | `catalog=` name | Notes |
|---|---|---|---|
| 96-well sample/elution plate | `Plate96` | `96_ABgene_SuperPlate_Thermo_AB2800` | Default 96-well plate for all sample/elution work |
| 384-well plate | `Plate96` (384 layout) | `384 Well LowVol LoBase` | Only when a 384 request is explicit |
| 96-well magnet rack | `MagnetRack` | `LV_Alpaqua_A000350` | Bead separation. Magnetization is implied by gripper-moving a plate **onto** this — never an explicit step |
| Liquid-waste sink | `Trough25mL` | `300ml SBS` | High-capacity waste on a 7 mm nest (`Nest7mm_Pos`). Never use a shallow 96-well plate as waste |
| Reagent trough (FCA) | `Trough25mL` | `25ml_short` | **Default for reagents** (beads, elution buffer, master mix, buffers) dispensed by the FCA; total < ~20 mL |
| Large reagent trough (FCA) | `Trough100mL` | `100ml` | FCA reagents whose total exceeds ~20 mL |
| Bulk reservoir for the MCA96 | `Trough100mL` | `60ml SBS MCA96` | Cheap bulk liquids the MCA96 adds to a whole plate (ethanol, water, wash buffer), on a 61 mm nest; up to ~55 mL |
| Large bulk reservoir for the MCA96 | `Trough25mL` | `300ml SBS` | Ethanol/wash beyond ~55 mL, on a `Nest7mm_Pos` the MCA reaches (the deck's reach data; not positions 1-3 on the 1080 deck) |
| MCA96 tips, small | `MCA100Box` | `MCA96, 100ul, Box` | Only when every MCA aspirate is ≤100 µL |
| MCA96 tips, medium | `MCA200Box` | `MCA96, 200ul, Box` | **Default** — ≤200 µL MCA work, covers ethanol-wash aspirates |
| MCA96 tips, large | `MCA500Box` | `MCA96, 500ul, Box` | Large-volume MCA |
| FCA tips, small | (FCA/LiHa) | `FCA, 200ul SBS` | FCA/LiHa fixed-channel ≤200 µL |
| FCA tips, large | (FCA/LiHa) | `FCA, 1000ul SBS` | FCA/LiHa fixed-channel large volume |

**Naming rules:**
- Copy labware type names EXACTLY from this table. Never add parenthetical
  suffixes like `(96-well)` or `(SBS)` (use `LV_Alpaqua_A000350`, not
  `LV_Alpaqua_A000350 (96-well)`).
- Labware types may be declared as String variables and referenced by name,
  the same way volumes are — this keeps protocols easy to re-target.

**Which head adds reagents (lab practice):**
- **Reagents come from the FCA (LiHa)**: kit reagents, beads, buffers, master
  mixes, enzymes — anything costly — are dispensed column by column with FCA
  tips from tubes or a slim trough (`25ml_short`, or `100ml` for larger
  totals). Slim troughs and tubes have little dead volume; use
  `distribute_reagent` (or `spri_cleanup(..., fca_tips=...)` for beads and
  elution buffer).
- **The MCA96 takes only cheap bulk liquids** (80% ethanol, water, wash buffer)
  from an SBS reservoir (`60ml SBS MCA96` on a 61 mm nest, `300ml SBS` on a
  reachable 7 mm nest), plus plate-to-plate work (`stamp`, supernatant removal,
  eluate transfer). The MCA96 can never pipette in a slim trough (FC: "out of
  range"); compile refuses it.
- Choose by volume and number of wells: a few wells or small volumes → FCA
  from tubes; a full plate of an expensive reagent → FCA from a slim trough; a
  full plate of ethanol/water → MCA96 from an SBS reservoir.
- Do not invent a pre-filled 96-well "reagent plate" to stamp a common reagent
  from: someone would have to aliquot 96 wells by hand first. Put the reagent
  in a trough or tubes and let the FCA distribute it. Stamp from a plate only
  when the reagent really differs per well (barcodes, indexes, samples) or the
  kit ships it plated.

**Deck placement — trough rules (avoid FC "out of range" / "cannot reach
Z-Max" / "No connector for this rotation" errors):**
- Place labware on the locations/sites the **deck skill** lists (its "Valid
  deck positions" table and role→slot layout are authoritative for this deck).
  Troughs go on the deck's trough site (a `WS_*ml_*` location); plates, magnet
  racks, and tip boxes go on the deck's plate nest (e.g. `Nest61mm_Pos`).
- For the trough catalog, **prefer `25ml_short`** over `100ml` whenever possible
  — the `100ml` trough is taller than standard tips can reach (FC throws
  `Tip N cannot reach Z-Max of labware …`). Use `25ml_short` for anything under
  ~20 mL fill; only use `100ml` when ethanol washes need the capacity (96 ×
  200 µL × 2 ≈ 42 mL).
- `300ml SBS` is the waste sink, or a bulk ethanol/wash reservoir for the MCA96
  — never for costly reagents. It has a plate (SBS) footprint and sits on a
  7 mm nest (`Nest7mm_Pos`), not on the trough carrier sites that slim troughs
  need. `MCA96 200ml` does not fit a 61 mm nest ("No connector").
- Different trough catalogs on the same trough site need different rotation
  connectors. If a slot/catalog combo fails with `No connector for this
  rotation at this site available`, swap to `25ml_short` on the deck's trough
  site — the most broadly reachable combo.

## Liquid classes (approved)

The list is intentionally short: one transfer class for every liquid is the
lab's convention, not a mistake to fix. The fixed exceptions are mixing
(`Water Mix`) and emptying tips (`Empty Tip`).

- `Water Free Single` — general default for aspirate/dispense (aqueous). When
  the user asks for liquid classes as variables, declare one variable per role
  with default **and** sim value `"Water Free Single"`.
- `Water Mix` — **use this for `head.mix(...)` calls**, not `Water Free Single`.
  `Water Free Single` has no Mix subclass section for MCA tip combinations, so FC
  rejects the protocol with `Liquid subclass section "Mix" is missing in
  "MCA384 1" with "MCA96 DiTi 200µl"`. Declare a `LIQUID_CLASS_MIX` variable
  defaulted to `"Water Mix"` and pass it to every `head.mix(...)`.
- `Empty Tip` — use as the `empty_tips_liquid_class` for `head.empty_tips(...)`
  and worklist `EmptyTips` parameters (not `Water Free Single`).
- For ethanol/volatile dispensing, keep the liquid handled by the FCA/LiHa
  from the `100ml` reservoir; do not invent new liquid-class names —
  resolve with `lookup_liquid_class` only if a non-water class is explicitly
  requested.

## `Labware` API

```python
plate.well('A1').add_layer(sample, 20.0)
for well in plate.all_wells(): ...
plate.column(1)
plate.row('A')
source.fill_all(water, 80.0)
```
**Attributes:** label, wells, catalog_name, slot, is_magnetized
**Never call:** `fill`, `plate['A1']`

**`labware.fill_all(reagent, volume_ul)` *replaces* the well's layer list —
it is not additive.** A second `fill_all(...)` on the same labware clobbers
the first; `fill_all(buffer, 20)` then `fill_all(dna, 2)` leaves every well
holding only `[dna 2]`, not `[buffer 20, dna 2]`. To seed a well with
multiple reagents, call `fill_all` **once** for the bulk and append further
layers per well:
```python
plate.fill_all(sample_buffer, 20.0)
for w in plate.wells.values():
    w.layers.append(Layer(reagent=sample_dna, volume_ul=2.0))
```
Bead suspension is normally *dispensed* (a `bead_carrier` reagent from a
source plate/trough) rather than seeded — that route is additive and also
establishes the well's bead phase.

## Volume / type variable rules

- All pipetting volumes MUST be declared as Floating Point variables. Declare
  the name in the top-level variables list, `set_variable` an initial float
  value (`90.0`, not `90`), and reference the name as a string in volume
  fields.
- Fill troughs for the WHOLE run, with dead volume: required fill ≥
  `wells × per_well_µL × repeats × 1.1` (the ×1.1 covers tip/dead volume).
  Under-filling is the #1 simulator failure (`Aspirate: ... short by N uL`).
