---
name: family-bead-affinity-capture
axis: family
description: Magnetic-bead affinity capture of a target species — antibody immunoprecipitation (Dynabeads IP) and His-tag IMAC (Ni-NTA magnetic agarose) — plus on-bead protein sample prep (SP3). Bind a target to a ligand-coated bead, incubate, separate on a magnet, wash, then elute. Select for immunoprecipitation, IP, pull-down, Ni-NTA / IMAC / His-tag purification, affinity capture, antibody bead capture, or SP3 proteomic bead cleanup. Distinct from SPRI nucleic-acid cleanup.
always_on: false
---
## Canonical workflow: affinity capture on magnetic beads

A *ligand-coated* magnetic bead (antibody for IP, Ni-NTA for His-tagged protein)
binds a specific target from the sample; everything else is washed away on the
magnet, then the target is eluted. The bead/magnet mechanics are identical to
SPRI — magnetization is implied by stacking onto the `MagnetRack`, beads are a
solid-phase well attribute via reagent roles, binding happens on an off-magnet
mix. **Reuse the mechanism from `api-magnetization-model`** and the bind/wash/
elute structure from `family-bead-cleanup-spri`; this family adds the
*affinity ligand* and a *binding incubation*.

### Variables

```
BEAD_SLURRY_VOL_UL        — ligand bead suspension volume per well (Ni-NTA / Dynabeads)
ANTIBODY_VOLUME_UL        — antibody / affinity ligand added per well (IP)
BINDING_INCUBATION_SECONDS— off-magnet incubation while the target binds the beads
MAGNET_WAIT_SEC           — pelleting time on the magnet before aspirating
WASH_VOLUME_UL            — wash buffer per well
NUM_WASHES                — number of wash cycles
ELUTE_VOLUME_UL           — elution / denaturation buffer per well
LIQUID_CLASS_ANTIBODY     — liquid class for the antibody/ligand
LIQUID_CLASS_WASH, LIQUID_CLASS_ELUTE
```

### Reagent roles (see api-magnetization-model)

- `Reagent("Affinity beads", role="bead_carrier")` — the ligand-coated bead
  suspension; its µL is normal liquid, dispensing it establishes the bead phase.
- `Reagent("Target", role="analyte")` — the captured species (protein/complex).
- `Reagent("Elution buffer", role="eluent")` — releases the target on an
  off-magnet mix.

### Step sequence

1. **Variables + Labware Placement**: sample `Plate96` (or `Plate96Deep` for
   large volumes — add to whitelist if needed), `MagnetRack`, antibody/bead and
   wash/elution reservoirs (`Trough`; a 15 mL tube maps to `25ml_short`/`100ml`),
   MCA + FCA tip boxes, `300ml SBS` waste.
2. **Add beads (+ antibody for IP)** off the magnet; dispense `BEAD_SLURRY_VOL_UL`
   and, for IP, `ANTIBODY_VOLUME_UL` from `LIQUID_CLASS_ANTIBODY`.
3. **Bind**: mix off the magnet, then incubate —
   `head.mix(plate, "MIX_VOLUME_UL", cycles=...)` then
   `wt.wait(duration_seconds="BINDING_INCUBATION_SECONDS")`. The off-magnet mix
   binds the target to the suspended beads.
4. **Separate**: `wt.gripper.move(plate, onto=magnet)` (this *is* the
   magnetization), then `wt.wait(duration_seconds="MAGNET_WAIT_SEC")`.
5. **Wash loop** (plate on magnet): a native loop of `NUM_WASHES` —
   aspirate supernatant to waste, move off magnet, dispense `WASH_VOLUME_UL`,
   mix, move back `onto=magnet`, aspirate to waste. See api-loops-and-conditionals.
6. **Elute**: move off the magnet, dispense `ELUTE_VOLUME_UL` eluent, mix
   off-magnet to release; for heat-denaturation elution (IP → SDS-PAGE) model
   the heat step as `wt.wait(...)` + `wt.add_comment("denature 95C 5 min")`.
7. **Recover**: move back `onto=magnet`, transfer the eluate to a clean plate.

### Elution-path branch (IP)

Some IP protocols choose between SDS-PAGE prep (heat-denature in place) and cold
storage. Drive it with a conditional (see api-loops-and-conditionals):

```python
wt.declare_variable("ELUTION_PATH", 0); wt.set_sim_value("ELUTION_PATH", 0)
with wt.conditional(left="ELUTION_PATH", op="==", right=0, name="SDS-PAGE elute"):
    wt.wait(duration_seconds="DENATURE_SECONDS")
    wt.add_comment("Heat-denature eluate for SDS-PAGE")
# else branch: transfer eluate to a cold plate (cond.else_steps, gap G4)
```

## Variant: SP3 on-bead protein sample prep

SP3 (Single-Pot Solid-phase-enhanced Sample Prep) binds protein to a **dual**
bead type, then runs reduction → alkylation → digestion → labeling *on the
beads* without intermediate transfers:

- `BEAD_TYPE_A_RATIO`, `BEAD_TYPE_B_RATIO` → derived
  `BEAD_VOLUME_UL = round((BEAD_TYPE_A_RATIO + BEAD_TYPE_B_RATIO) * SAMPLE_VOLUME_UL, 1)`.
- A native `wt.loop` over the sequential reagent steps, each: dispense reagent
  from a trough, mix off-magnet, `wt.wait` incubation; no transfers between.
- `LIQUID_CLASS_ENZYME` (protease) and `LIQUID_CLASS_TMT` (labeling) roles; note
  in prose that viscous TMT buffer flow-rate/geometry tuning is not exposed.

## Notes

- The magnet never withholds liquid from a tip — on-magnet aspirate draws all
  free liquid and leaves the bead-bound target; see api-magnetization-model.
- Fill reservoirs for the whole run incl. dead volume:
  `wells × per_well × reps × 1.1`.
