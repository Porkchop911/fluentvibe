---
name: family-bead-cleanup-spri
axis: family
description: SPRI magnetic-bead PCR cleanup (e.g. AMPure XP and equivalents) — add beads at a configurable ratio, bind on magnet, configurable ethanol washes, air-dry, elute, transfer eluate. Select for any bead-based cleanup, SPRI, size-selection, or magnetic-separation request. Covers bead ratio, reagent roles, resuspension mixing, and derived supernatant/eluate volumes.
always_on: false
select_when:
  - bead
  - beads
  - spri
  - magnetic
  - magnet
---
## REQUIRED INVARIANTS — a bead cleanup is INVALID otherwise

A cleanup that adds beads but never gets the product back off them is wrong even
if every other step looks plausible. Before anything else, guarantee all five:

1. **Tag the product** `Reagent("<name> DNA", role="analyte")` — without the
   `analyte` role the model of what you are purifying does not exist and the
   recovery cannot happen or be verified. The beads are `role="bead_carrier"`,
   the elution buffer `role="eluent"`.
2. **Elute OFF the magnet**: `wt.gripper.move(plate, to=("Nest61mm_Pos", 1))`
   before dispensing elution buffer, then mix to release the analyte.
3. **RECOVER the eluate**: after eluting, move the plate **back**
   `onto=magnet`, then transfer the cleared eluate (now carrying the analyte)
   to a SEPARATE clean plate. NEVER add downstream reagents (barcodes,
   adapters, master mix) into the bead-containing well — barcoding/ligating on
   the bead slurry is a failed protocol.
4. **Analyte/eluate NEVER goes to waste** — only the supernatant and ethanol
   washes are emptied to waste; the eluate transfer target is the clean plate.
5. **Separate eluate tip box**: a second MCA tip box used only for the final
   eluate transfer, so waste/wash beads do not carry over into the product.

## Canonical workflow: AMPure XP PCR cleanup (96-well)

Annotated reference for the lab's most common bead-cleanup shape. Adapt
volumes to the user's request; keep the structure. Steps 7–8 below are the
required eluate recovery from invariants 2–5 — they are not optional.

1. **Variables** (top-level, before any `wt.group`): a liquid-class variable
   per role (default `"Water Free Single"`), `TARGET_VOLUME_UL`, bead/wash/
   elution volumes.
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

## Bead modelling

This protocol relies on the magnetization model (magnetization implied by
stacking onto the magnet; beads as a solid-phase well attribute via reagent
roles; mix off-magnet to bind, mix off-magnet with eluent to release) — see the
**api-magnetization-model** skill for the full mechanism. For AMPure:
`Reagent("AMPure beads", role="bead_carrier")`,
`Reagent("Sample DNA", role="analyte")` (distinct from the bulk `plain`
buffer), `Reagent("Elution buffer", role="eluent")`. Always mix after adding
beads and after adding elution buffer.

## Derived volumes — compute, never guess

Supernatant/eluate volumes are the *whole free liquid* minus the small
`RETAIN_VOLUME_UL` you leave for tip clearance — never reduced by the magnet.
Compute these from the primitive volume variables in Python expressions
BEFORE the `wt.declare_variable` call:

- `SUPERNATANT_ASPIRATE_UL` = `SAMPLE_VOLUME_UL` + `BEAD_VOLUME_UL`
  − `RETAIN_VOLUME_UL` (remove everything added that is not the deliberately
  retained bead pellet volume).
- `TRANSFER_VOLUME_UL` / `ELUATE_*_UL` = `ELUTION_VOLUME_UL`
  − `RETAIN_VOLUME_UL` (recover the cleared eluate, leaving the retained
  dead volume behind).

A supernatant aspirate smaller than sample+beads strands liquid on the
magnet; an eluate transfer smaller than the elution volume loses product.
**Always declare all four primitives explicitly** — `SAMPLE_VOLUME_UL` (or
`TARGET_VOLUME_UL`, treated as the same input quantity), `BEAD_VOLUME_UL`,
`RETAIN_VOLUME_UL`, `ELUTION_VOLUME_UL` — then let `SUPERNATANT_ASPIRATE_UL`
and `TRANSFER_VOLUME_UL` follow. If any primitive is missing the two
dependents cannot be derived and the model's guessed value ships uncorrected.

## Bead ratio (parameterize the bead volume)

The bead volume is a *ratio* of the sample volume — the size-selection knob
(default 1.8× for general cleanup, lower for large-fragment selection, range
~0.5–2.0×). Declare the ratio and derive the volume:
```python
BEAD_RATIO = 1.8                       # size-selection knob
BEAD_VOLUME_UL = round(BEAD_RATIO * SAMPLE_VOLUME_UL, 1)
```
Then `SUPERNATANT_ASPIRATE_UL` follows from the derived `BEAD_VOLUME_UL` as
above. Expose `BEAD_RATIO` as its own variable so the lab tech can retune size
selection without touching the volume math.

## Per-role liquid-class variables

Declare one string variable per reagent role — `LIQUID_CLASS_BEADS`,
`LIQUID_CLASS_ETHANOL`, `LIQUID_CLASS_SUPERNATANT`, `LIQUID_CLASS_ELUATE`,
`LIQUID_CLASS_ELUTION_BUFFER` (default `"Water Free Single"`), plus
`LIQUID_CLASS_MIX` defaulted to **`"Water Mix"`** for every `head.mix(...)`
call. `"Water Free Single"` lacks a Mix subclass for MCA tip combinations and
FC will reject mix steps that use it. Each pipetting call passes the
role-specific variable, letting the lab tech tune one role's class without
rewriting the protocol.

## Trough fill arithmetic

A trough is a finite pool. Required fill ≥ `wells × per_well_µL × repeats
× 1.1` (the ×1.1 covers tip/dead volume). For a 96-well, 2× 200 µL ethanol
wash that is `96 × 200 × 2 × 1.1 ≈ 42 mL` → use the `100ml` trough and set
`ETHANOL_TROUGH_FILL_UL ≈ 42000`. Do the same arithmetic for bead and
elution troughs (`96 × per_well × 1.1`).

## Ethanol washes (parameterize volume + count) and air-dry

Make the wash volume and the number of washes variables, and run the washes in
a native loop so the count is one knob:
```python
ETHANOL_WASH_VOLUME_UL = 200.0
ETHANOL_WASH_COUNT = 2                 # typically 2
AIR_DRY_SECONDS = 300                  # dry the bead pellet after the last wash
```
With the plate on the magnet, each wash dispenses `ETHANOL_WASH_VOLUME_UL` from
the `100ml` ethanol trough and aspirates it (plus its volume) to `300ml SBS`
waste; the magnet keeps the beads. After the final wash, **air-dry** before
eluting:
```python
with wt.loop(times="ETHANOL_WASH_COUNT", name="Ethanol washes", loop_variable="wash"):
    head.dispense(plate, "ETHANOL_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_ETHANOL")
    head.aspirate(plate, "ETHANOL_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_ETHANOL")
    head.empty_tips(waste, "ETHANOL_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_ETHANOL")
wt.wait(duration_seconds="AIR_DRY_SECONDS")   # residual ethanol evaporates off the pellet
```
Size the ethanol trough fill for `96 × ETHANOL_WASH_VOLUME_UL × ETHANOL_WASH_COUNT × 1.1`.

## Resuspension mixing

To get DNA on/off the beads you must actually resuspend the pellet, not just
overlay liquid. Mix **off the magnet** with enough cycles and a volume near the
well contents (e.g. `cycles=10`, volume ≈ sample+beads for binding, ≈ elution
volume for release). On Opentrons these protocols resuspend by pipetting at the
pellet from several positions; fluentvibe has no per-point geometry, so model it
as a sufficiently vigorous `head.mix(plate, "<vol>_UL", cycles=10, ...)` off the
magnet after adding beads and again after adding elution buffer (this is what
binds, then releases, the analyte — see **api-magnetization-model**).

## Variant: on-bead enzymatic treatment (DNase, lysis/neutralization)

Nucleic-acid extraction kits (`zymo_quick-rna`, `magnesil_rna`) interleave
enzymatic / chemical steps with the bead workflow:

- **Pre-bead lysis** (cell input): a lysis/neutralization group *before* bead
  binding — dispense `"LYSIS_VOLUME_UL"`, mix, `wt.wait`, then dispense
  `"NEUTRALIZATION_VOLUME_UL"` and mix — modelled as sequential dispense/mix/
  wait steps before adding beads.
- **On-bead DNase** between washes: off-magnet, dispense `"DNASE_VOLUME_UL"`
  (`role="enzyme"`), mix, `wt.wait(duration_seconds="DNASE_INCUBATION_SECONDS")`,
  then dispense `"NEUTRALIZATION_VOLUME_UL"` (`role="stop_buffer"`) and mix.
- **Distinct wash buffers**: replace the single ethanol role with per-wash
  liquid classes (`LIQUID_CLASS_WASH1`, `LIQUID_CLASS_WASH2`, …) when the kit
  uses different buffers per wash.

Declare each enzyme/neutralizer volume as a scalar variable, and **update the
supernatant aspirate volume** to include these added reagents before the next
wash (otherwise you under-aspirate). Beads stay retained throughout per the
magnetization model.

## Separate tip box for the eluate

Place TWO MCA200Box labware objects: one for waste/wash operations, one used
exclusively for the final eluate transfer. Re-using the same tip box across
waste and eluate causes bead carryover. Name the second box e.g.
`MCATipsEluate` and pick up only from it inside the Transfer Eluate group.

## Scope note

Generated protocols should include only operations the user requested — avoid
unrelated template filler groups.
