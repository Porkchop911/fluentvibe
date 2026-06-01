# Lab Scope — curated FluentControl subset for this lab

This is the finite set of deck, labware, liquid classes, and workflows this
lab actually runs. The FluentControl install contains hundreds more
components; **prefer the names below**. Only fall back to open-ended
`search_labware` when a request genuinely needs something not listed here —
and say so explicitly when you do.

## Deck / workspace

- Workspace: `SAT_Fluent_780_Rev3`
- Workspace GUID: `291ba293-6361-4f8f-aa8d-7c2643d3f096`
- Always: `Worktable.from_workspace("SAT_Fluent_780_Rev3", workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096", auto_place=False, ...)`

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

## Liquid classes (approved)

- `Water Free Single` — general default for aqueous transfers. When the user
  asks for liquid classes as variables, declare one variable per role with
  default **and** sim value `"Water Free Single"`.
- For ethanol/volatile dispensing, keep the liquid handled by the FCA/LiHa
  from the `100ml` reservoir; do not invent new liquid-class names —
  resolve with `lookup_liquid_class` only if a non-water class is explicitly
  requested.

## Pipetting strategy

- **MCA96** (`wt.mca96`): 96-channel plate-to-plate only. Mount adapter →
  pick up MCA96 tips → aspirate → dispense → return tips → drop adapter.
- **FCA / LiHa** (`wt.liha`): fixed-channel, trough-to-plate dispenses and
  single-channel work. fluentvibe has no `wt.fca`; map any "FCA" request to
  `wt.liha`.
- MCA-96 **cannot** fan one trough well across 96 destination wells in a
  single aspirate — dispense reagents/ethanol/elution from troughs with the
  FCA/LiHa; use the MCA only for the 96-channel plate-to-plate steps.

## Deck placement (typical SAT layout)

Place on `Nest61mm_Pos`: sample plate → site 1, final/elution plate →
site 2, magnet rack → site 3, MCA tips → site 4, waste → site 5, FCA tips →
site 6. Reservoirs go on the `WS_100ml_1` location.

## Canonical workflow: AMPure XP PCR cleanup (96-well)

Annotated reference for the lab's most common bead-cleanup shape. Adapt
volumes to the user's request; keep the structure.

1. **Variables** (top-level, before any `wt.group`): a liquid-class variable
   per role (default `"Water Free Single"`), `TARGET_VOLUME_UL`, bead/wash/
   elution volumes from `plan_protocol_resources`.
2. **Labware Placement** group: sample `Plate96`, elution `Plate96`,
   `MagnetRack`, MCA tip box, FCA tip box, bead/elution reservoir
   (`25ml_short`), ethanol reservoir (`100ml`), `300ml SBS` waste.
3. **Add beads** (FCA from `25ml_short`): ~1.8× sample volume; pipette-mix;
   incubate.
4. **Bind**: `wt.gripper.move(plate, onto=magnet)` — this *is* the
   magnetization; no separate engage step. Wait for clear.
5. **Remove supernatant** (MCA, plate still on magnet): aspirate to `300ml
   SBS` waste, leave a few µL behind.
6. **Ethanol washes ×2** (FCA from `100ml`): dispense ~200 µL,
   short incubate, aspirate to waste. The magnet retains the beads (and
   bound DNA) in the well — all wash liquid still aspirates normally.
7. **Elute**: `wt.gripper.move(plate, to=("Nest61mm_Pos", 1))` off the
   magnet; FCA dispense elution buffer from `25ml_short`; pipette-mix
   (this releases the bound DNA back into the liquid); incubate.
8. **Separate & transfer eluate**: move back `onto=magnet`; MCA transfer
   the cleared eluate (now carrying the DNA) to the elution plate.

Bead modelling (reagent **roles**, not pinned liquid layers — a magnet
never withholds liquid, it only holds the beads):
- AMPure beads: `Reagent("AMPure beads", role="bead_carrier")`. Dispensing
  it establishes the well's bead phase; its µL is normal aspirable liquid.
- The captured DNA: a small `Reagent("Sample DNA", role="analyte")` marker
  layer, **distinct from the bulk sample buffer** (a `plain` reagent).
- Elution buffer: `Reagent("Elution buffer", role="eluent")`.
- A **mix off the magnet** binds free analyte to suspended beads; a mix
  off the magnet with an eluent present releases bound analyte back to the
  liquid. Always mix after adding beads and after adding elution buffer.
- Supernatant/eluate volumes are the *whole free liquid* minus the small
  `RETAIN_VOLUME_UL` you leave for tip clearance — never reduced by the
  magnet. Hence `SUPERNATANT_ASPIRATE_UL = SAMPLE + BEADS − RETAIN` and
  `TRANSFER_VOLUME_UL = ELUTION − RETAIN` (see authoring rule 6).

## Authoring rules — get these right the FIRST time

These are the exact mistakes that otherwise cost dozens of failed
`simulate_python_draft` repair rounds. Follow them up front.

1. **No `wt.comment(...)`.** `comment` is only the
   `Worktable.from_workspace(..., comment="...")` keyword argument — it is a
   string attribute, not a method. Calling `wt.comment("...")` raises
   `'str' object is not callable`. For step notes use plain `#` Python
   comments.

2. **Object-draft `liquid_classes` shape is fixed.** Call
   `lookup_liquid_class("Water Free Single")` once, then make every entry
   exactly:
   `{"name": "Water Free Single", "default_value": "Water Free Single", "variable_name": "LIQUID_CLASS_<ROLE>"}`.
   `name` must be the resolved class string (not the variable). Declare the
   matching `wt.declare_variable("LIQUID_CLASS_<ROLE>", "Water Free Single")`
   + `wt.set_sim_value(...)`.

3. **Fill troughs for the WHOLE run, with dead volume.** A trough is a
   finite pool. Required fill ≥ `wells × per_well_µL × repeats × 1.1`
   (the ×1.1 covers tip/dead volume). For a 96-well, 2× 200 µL ethanol
   wash that is `96 × 200 × 2 × 1.1 ≈ 42 mL` → use the `100ml` trough and
   set `ETHANOL_TROUGH_FILL_UL ≈ 42000`. Do the same arithmetic for bead
   and elution troughs (`96 × per_well × 1.1`). Under-filling is the #1
   simulator failure (`Aspirate: ... short by N uL`).

4. **Pass declared variables BY NAME — as a string — for both `volume` and
   `liquid_class`.** The renderer emits a FluentControl variable reference
   *only* when the argument string equals a declared variable name. Passing
   the Python value bakes a literal into the protocol and leaves the FC
   variable dead, defeating the point of declaring it.
   - Right: `head.aspirate(trough, "BEAD_VOLUME_UL", liquid_class="LIQUID_CLASS_BEADS")`
   - Wrong: `head.aspirate(trough, BEAD_VOLUME_UL, liquid_class=LIQUID_CLASS_BEADS)`
     (here `BEAD_VOLUME_UL` is `36.0` and `LIQUID_CLASS_BEADS` is
     `"Water Free Single"`, so both render as literals).
   You still compute derived values as Python numbers and
   `wt.declare_variable("SUPERNATANT_ASPIRATE_UL", sample + beads - retain)` +
   `wt.set_sim_value(...)`; the simulator resolves the name-string back to the
   seeded value. Pass the **name string** in every `aspirate` / `dispense` /
   `mix` / `empty_tips` call.

5. **Never change approved labware across staged groups.** The `catalog=`,
   `python_class`, and label of every object are locked at object-draft
   approval — re-emit them identically in every later
   `simulate_python_draft`; do not add, drop, or rename labware mid-draft.

6. **Dependent bead-cleanup volumes are derived, not chosen.** Compute,
   never guess, these from the primitive volume variables:
   - `SUPERNATANT_ASPIRATE_UL` = `SAMPLE_VOLUME_UL` + `BEAD_VOLUME_UL`
     − `RETAIN_VOLUME_UL` (remove everything added that is not the
     deliberately retained bead pellet volume).
   - `TRANSFER_VOLUME_UL` / `ELUATE_*_UL` = `ELUTION_VOLUME_UL`
     − `RETAIN_VOLUME_UL` (recover the cleared eluate, leaving the
     retained dead volume behind).
   A supernatant aspirate smaller than sample+beads strands liquid on the
   magnet; an eluate transfer smaller than the elution volume loses
   product. **Always declare all four primitives explicitly** —
   `SAMPLE_VOLUME_UL` (or `TARGET_VOLUME_UL`, treated as the same input
   quantity), `BEAD_VOLUME_UL`, `RETAIN_VOLUME_UL`, `ELUTION_VOLUME_UL` —
   then let `SUPERNATANT_ASPIRATE_UL` and `TRANSFER_VOLUME_UL` follow. If
   any primitive is missing the two dependents cannot be derived and the
   model's guessed value ships uncorrected.

7. **`labware.fill_all(reagent, volume_ul)` *replaces* the well's layer
   list — it is not additive.** A second `fill_all(...)` on the same
   labware clobbers the first; calling `fill_all(buffer, 20)` then
   `fill_all(dna, 2)` leaves every well holding only `[dna 2]`, not
   `[buffer 20, dna 2]`. To seed a well with multiple reagents, call
   `fill_all` **once** for the bulk and append further layers per well:
   ```
   plate.fill_all(sample_buffer, 20.0)
   for w in plate.wells.values():
       w.layers.append(Layer(reagent=sample_dna, volume_ul=2.0))
   ```
   Bead suspension is normally *dispensed* (a `bead_carrier` reagent
   from a source plate/trough) rather than seeded — that route is
   additive and also establishes the well's bead phase.

8. **Iterate columns with a native `wt.loop`, never a Python `for`.** A
   Python `for col in range(12)` unrolls into 12 hardcoded steps in the
   rendered protocol; the lab wants ONE FluentControl loop. Use the
   loop counter to address each column with a `well_offset` **expression
   string** (the counter is 1-based):
   ```
   head.get_tips(fca_tips)
   with wt.loop(times=12, name="Add beads", loop_variable="col"):
       head.aspirate(bead_trough, "BEAD_VOLUME_UL", liquid_class="LIQUID_CLASS_BEADS")
       head.dispense(sample_plate, "BEAD_VOLUME_UL",
                     liquid_class="LIQUID_CLASS_BEADS", well_offset="(col-1)*8")
   head.drop_tips()
   ```
   This renders as a single `LoopGroup` with `<WellOffset>(col-1)*8</WellOffset>`
   and simulates across all 96 wells. The MCA96 head touches all 96 wells in
   one call, so it needs **no** column loop — only the LiHa column-wise
   trough dispenses do.
