---
name: family-ngs-library-prep
axis: family
description: Next-generation sequencing library preparation — tagmentation, amplification, and bead-based size selection/cleanup of DNA libraries. Select for NGS library prep, Illumina Nextera XT, Illumina DNA Prep, tagmentation, amplicon library construction, or bead cleanup of sequencing libraries.
always_on: false
---
## Canonical workflow: NGS library prep (liquid-handling skeleton)

Multi-step workflow covering tagmentation (fragmentation + adapter tagging),
library amplification by PCR, and bead-based size selection/cleanup. Reuses
patterns from `family-bead-cleanup-ampure` for the cleanup phase and
`family-pcr-setup` for amplification setup.

### Overview of steps

1. **Tagmentation**: mix DNA with tagment mix (transposase + adapters), incubate
2. **Stop tagmentation**: add stop buffer, brief incubation/mix
3. **Amplification setup**: add PCR master mix to tagmented DNA
4. **PCR amplification** — off-deck thermocycler
5. **Bead cleanup / size selection**: AMPure XP or equivalent beads, magnet,
   washes, elution

### Variables (tagmentation + amplification phase)

```
DNA_INPUT_VOLUME_UL    — input DNA volume per sample (e.g. 30)
TAGMENT_MIX_VOL_UL     — tagment mix per well (e.g. 22-44 depending on format)
STOP_BUFFER_VOL_UL     — tagmentation stop buffer per well (e.g. 10)
MASTERMIX_VOL_UL       — PCR master mix per well (e.g. 5 for Nextera XT)
NUM_SAMPLES            — number of samples (columns in the plate)
```

### Variables (bead cleanup phase — reuses bead-cleanup skill)

See `family-bead-cleanup-ampure` for bead ratio, wash volumes, and elution.
Typical NGS cleanup uses 0.8x-1.0x beads for clean-up or 0.5x/0.9x for
double-size-selection.

### Labware Placement

- Sample/library plate (`Plate96`)
- Reagent plate (`Plate96` — tagment mix, stop buffer, master mix in columns)
- Reservoirs/troughs for beads, wash buffers (ethanol), elution buffer
- Magnet rack (`MagnetRack`)
- FCA tip box — must be **FCA**-class for the LiHa, not MCA:
  ```python
  from fluentvibe import TipBox
  fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
  ```

### Step sequence outline

```python
# === PHASE 1: Tagmentation ===
head = wt.liha
head.get_tips(fca_tips)
with wt.loop(times=NUM_SAMPLES, name="Add tagment mix", loop_variable="col"):
    head.aspirate(reagent_plate, "TAGMENT_MIX_VOL_UL",
                  liquid_class="LIQUID_CLASS_TAGMENT", well_offset="(col-1)*8")
    head.dispense(sample_plate, "TAGMENT_MIX_VOL_UL",
                  liquid_class="LIQUID_CLASS_TAGMENT", well_offset="(col-1)*8")
    head.mix(sample_plate, "TAGMENT_MIX_VOL_UL", cycles=10,
             liquid_class="LIQUID_CLASS_TAGMENT", well_offset="(col-1)*8")
head.drop_tips()

# Incubate 5 min at 55C (off-deck or wt.wait approximation)
wt.add_comment("Incubate 5 min at 55C (heater-shaker or off-deck)")
wt.wait(duration_seconds=300)

# === PHASE 2: Stop tagmentation ===
head.get_tips(fca_tips)
with wt.loop(times=NUM_SAMPLES, name="Add stop buffer", loop_variable="col"):
    head.aspirate(reagent_plate, "STOP_BUFFER_VOL_UL",
                  liquid_class="LIQUID_CLASS_STOP", well_offset="(col-1)*8")
    head.dispense(sample_plate, "STOP_BUFFER_VOL_UL",
                  liquid_class="LIQUID_CLASS_STOP", well_offset="(col-1)*8")
    head.mix(sample_plate, "STOP_BUFFER_VOL_UL", cycles=5,
             liquid_class="LIQUID_CLASS_STOP", well_offset="(col-1)*8")
head.drop_tips()

wt.add_comment("Incubate 5 min at room temperature")
wt.wait(duration_seconds=300)

# === PHASE 3: Amplification setup ===
head.get_tips(fca_tips)
with wt.loop(times=NUM_SAMPLES, name="Add PCR master mix", loop_variable="col"):
    head.aspirate(reagent_plate, "MASTERMIX_VOL_UL",
                  liquid_class="LIQUID_CLASS_MASTERMIX", well_offset="(col-1)*8")
    head.dispense(sample_plate, "MASTERMIX_VOL_UL",
                  liquid_class="LIQUID_CLASS_MASTERMIX", well_offset="(col-1)*8")
head.drop_tips()

# PCR is off-deck
wt.add_comment("Thermocycle: 72C 3min; 10-14x [95C 30s, 55C 30s, 72C 60s]; "
               "final 72C 1min, hold at 4C")

# === PHASE 4: Bead cleanup (reuses family-bead-cleanup-ampure) ===
# See api-magnetization-model for magnet engage/disengage via gripper
# Typical: add beads, mix, incubate, move to magnet, aspirate supernatant,
# wash x2 with ethanol, air-dry, elute in buffer
```

### Variant: mechanical shearing / SRE (pacbio-sre-shearing)

Long-read prep (PacBio) shears DNA by **high-cycle pipette mixing** rather than
enzymatic fragmentation, in deep-well plates:

- `SHEARING_MIX_CYCLES` (int) → `head.mix(plate, "VOL_UL", cycles="SHEARING_MIX_CYCLES", ...)`;
  the high cycle count *is* the fragmentation.
- Deep-well plate role (`Plate96Deep`) for the large SRE volumes.
- Optional SRE-buffer-addition step gated by a runtime flag — branch with
  `wt.conditional` (see api-loops-and-conditionals) between "add SRE buffer then
  shear" and "shear directly".

### Limitations (needs-extension P3/P4)

- **Heater-shaker incubation** (tagmentation at 55C): modelled as `wt.wait()` +
  comment. See [capability-roadmap](../../docs/skill-authoring/capability-roadmap.md) P3.
- **Thermocycler** (amplification cycles): off-deck, noted in comments. See roadmap P4.
- **Bead cleanup**: fully authorable with current magnet + gripper model — see
  `family-bead-cleanup-ampure` and `api-magnetization-model`.

### Whitelist additions needed

- **PCR full-skirt 96 plate**: resolve catalog name via `fluentvibe.catalog` and
  add to `generation.yaml` `lab_scope.labware`.
