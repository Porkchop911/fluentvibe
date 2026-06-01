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
| Liquid-waste sink | `Trough` | `300ml SBS` | High-capacity waste. Never use a shallow 96-well plate as waste |
| Standard reagent reservoir | `Trough` | `25ml_short` | Small aqueous reagents (beads, elution buffer/water) when total < ~20 mL |
| Ethanol / high-volume reservoir | `Trough` | `100ml` | Ethanol and any reagent whose total fill exceeds ~20 mL (e.g. 96-well ethanol washes). A trough is a trough — `100ml` handles ethanol the same as the old `25ml_short_EtOH`, just with the capacity 96×2 washes actually need |
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

**Deck placement — trough rules (avoid FC "out of range" / "cannot reach
Z-Max" / "No connector for this rotation" errors):**
- Place **all troughs** on the trough site `WS_100ml_1` (positions 1–4) — that
  is the only reachable trough slot on the SAT_Fluent_780 deck. Plates, magnet
  racks, and tip boxes go on `Nest61mm_Pos` (sites 1–6 per the layout: plates
  on 1–3, MCA tips site 4, FCA tips site 6).
- For the trough catalog, **prefer `25ml_short`** over `100ml` whenever possible
  — the `100ml` trough is taller than standard tips can reach (FC throws
  `Tip N cannot reach Z-Max of labware …`). Use `25ml_short` for anything under
  ~20 mL fill; only use `100ml` when ethanol washes need the capacity (96 ×
  200 µL × 2 ≈ 42 mL).
- Use `300ml SBS` exclusively as the waste sink (never for liquid reagents).
- Different trough catalogs on the same trough site need different rotation
  connectors. If the LM picks a slot/catalog combo that fails with
  `No connector for this rotation at this site available`, swap to `25ml_short`
  at `WS_100ml_1` — the proven-reachable combo.

## Liquid classes (approved)

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
