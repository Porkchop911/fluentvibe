---
name: family-pcr-setup
axis: family
description: PCR master mix preparation — dispense water, DNA template, and chilled master mix into a 96- or 384-well PCR plate; also library-quant qPCR setup with stepwise sample dilution, standard-curve dispensing, and replicate splitting. Select for PCR setup, master mix plating, qPCR prep, library quantification, multiplex PCR, or amplification reaction assembly workflows.
always_on: false
---
## Canonical workflow: PCR setup (liquid-handling skeleton)

Dispense water, DNA template, and master mix into a 96-well PCR plate in
preparation for thermocycling. The thermocycler program itself is **not**
modelled — it becomes an off-deck step with `wt.add_comment(...)`.

### Variables

```
WATER_VOLUME_UL        — water per well (e.g. 7)
DNA_VOLUME_UL          — DNA template per well (e.g. 5-10)
MASTERMIX_VOLUME_UL    — master mix per well (e.g. 38-40)
TOTAL_REACTION_VOL_UL  = WATER + DNA + MASTERMIX   (derived, typically 50)
NUM_SAMPLES            — number of wells to fill (max 96)
LIQUID_CLASS_WATER     — liquid class for water, default "Water Free Single"
LIQUID_CLASS_DNA       — liquid class for DNA template
LIQUID_CLASS_MASTERMIX — liquid class for master mix
```

### Labware Placement

- PCR plate (`Plate96` — use `96_ABgene_SuperPlate_Thermo_AB2800` or nearest
  approved catalog name; full-skirt PCR plates need whitelist addition)
- Water reservoir (`Trough`, `25ml_short`)
- DNA source plate (`Plate96`)
- Master mix tubes — modelled as a trough or deep-well plate on deck
- FCA tip box — must be **FCA**-class for the LiHa, not MCA. Use
  `FCA, 200ul SBS` for water/DNA, `FCA, 1000ul SBS` for larger master-mix
  volumes:
  ```python
  from fluentvibe import TipBox
  fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
  ```

### Step sequence (LiHa, column-wise with loop)

```python
head = wt.liha

# 1. Dispense water into all sample wells
head.get_tips(fca_tips)
with wt.loop(times=NUM_COLUMNS, name="Dispense water", loop_variable="col"):
    head.aspirate(water_trough, "WATER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_WATER")
    head.dispense(pcr_plate, "WATER_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_WATER", well_offset="(col-1)*8")
head.drop_tips()

# 2. Dispense DNA template from source plate
head.get_tips(fca_tips)
with wt.loop(times=NUM_COLUMNS, name="Dispense DNA", loop_variable="col"):
    head.aspirate(dna_source, "DNA_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_DNA", well_offset="(col-1)*8")
    head.dispense(pcr_plate, "DNA_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_DNA", well_offset="(col-1)*8")
head.drop_tips()

# 3. Dispense master mix (from cold tube or reservoir)
head.get_tips(fca_tips)
with wt.loop(times=NUM_COLUMNS, name="Dispense master mix", loop_variable="col"):
    head.aspirate(mastermix_trough, "MASTERMIX_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_MASTERMIX")
    head.dispense(pcr_plate, "MASTERMIX_VOLUME_UL",
                  liquid_class="LIQUID_CLASS_MASTERMIX", well_offset="(col-1)*8")
head.drop_tips()

# 4. Mix briefly (optional)
head.get_tips(fca_tips)
with wt.loop(times=NUM_COLUMNS, name="Mix reaction", loop_variable="col"):
    head.mix(pcr_plate, "TOTAL_REACTION_VOL_UL", cycles=3,
             liquid_class="LIQUID_CLASS_MASTERMIX", well_offset="(col-1)*8")
head.drop_tips()

# Thermocycling is off-deck
wt.add_comment("Remove PCR plate and run thermocycler program: "
               "95C 3min; then 30x [95C 15s, 60C 30s]; final 72C 5min")
```

### Variant: library-quant qPCR (kapa-library-quant)

Library quantification adds sample dilution, a standard curve, and 96/384
format switching:

- `PLATE_FORMAT` (`96`|`384`) — drive conditional well offsets and replicate
  counts. `Plate384` (`"384 Well LowVol LoBase"`) is already on the whitelist.
- **Stepwise dilution** (`DILUTION_VOL_1_UL`, `DILUTION_VOL_2_UL`, `BUFFER_VOL_UL`)
  for serial 1:50 → 1:20 sample prep before reaction assembly — see
  `family-serial-dilution` for the carry-down pattern.
- **Standard curve**: `STANDARD_VOLUME_UL` dispensed by a column-wise `wt.loop`
  into the leading wells from a standards source plate.
- **Multi-replicate dispensing**: `wt.loop(times=NUM_REPLICATES)` to split the
  reaction across replicate wells. If replicate/standard volumes vary per well,
  use a worklist (`api-worklists`).

### Limitation: no thermocycler or temperature module (needs-extension P4/P5)

The Opentrons protocols use a **thermocycler module** for amplification and a
**temperature module** to keep master mix chilled. fluentvibe has neither
abstraction yet:

- **Thermocycling**: represented as `wt.add_comment(...)` with the program
  described; plate is removed from deck manually.
- **Reagent cooling (4°C)**: not modelled — note in comments that master mix
  should be kept cold until use.

See [capability-roadmap](../../docs/skill-authoring/capability-roadmap.md) P4
(thermocycler) and P5 (temperature module) for planned extensions.

### Whitelist additions needed

- **PCR full-skirt 96 plate**: resolve exact catalog name via `fluentvibe.catalog`
  (`find_components`) and add to `generation.yaml` `lab_scope.labware`.
- **Cold tube rack** (e.g. NEST 2 mL snapcap on aluminum block): same process.
