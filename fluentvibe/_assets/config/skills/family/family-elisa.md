---
name: family-elisa
axis: family
description: Enzyme-linked immunosorbent assay (ELISA) — coat plate with capture antibody, block, add samples/standards, wash cycles, add detection reagents, develop signal. Select for ELISA, sandwich ELISA, cytokine quantification, target capture assay, or immunoassay workflows.
always_on: false
---
## Canonical workflow: ELISA (liquid-handling skeleton)

Multi-step plate-based immunoassay with repeated wash cycles and long incubation
periods. The full protocol spans three phases: coating/capture, target capture,
and signal development. This skill covers the liquid handling common to all
phases.

### Variables

```
COAT_VOL_UL            — capture antibody volume per well (e.g. 50-100)
BLOCK_VOL_UL           — blocking buffer per well (e.g. 200-300)
WASH_VOL_UL            — wash buffer per well per cycle (e.g. 200-300)
NUM_WASH_CYCLES        — repetitions of aspirate-wash/dispense-wash/drain
SAMPLE_VOL_UL          — sample or standard volume per well (e.g. 50-100)
DETECT_AB_VOL_UL       — detection antibody per well (e.g. 100)
HRP_VOL_UL             — HRP conjugate per well (e.g. 100)
TMB_VOL_UL             — TMB substrate per well (e.g. 100)
STOP_VOL_UL            — stop solution per well (e.g. 50-100)
NUM_TARGETS            — number of different targets/antibodies (2, 3, 4, or 6)
NUM_SAMPLES            — samples assayed in duplicate
```

### Labware Placement

- ELISA plate (`Plate96` — high-affinity protein binding plate)
- Wash buffer reservoir (`Trough`, `100ml`)
- Waste reservoir (`Trough`, `300ml SBS`)
- Reagent troughs for blocking buffer, detection reagents, TMB, stop solution
- Sample tubes (modelled as deep-well or tube rack — needs whitelist if not on
  approved list)
- FCA tip box — must be **FCA**-class for the LiHa, not MCA:
  ```python
  from fluentvibe import TipBox
  fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
  ```

### Step sequence outline

```python
head = wt.liha

# === PHASE 1: Coating (done in prior run or pre-coated plate) ===
# Capture antibody dispensed column-wise, one antibody per column
# Incubate overnight at 4C (off-deck or temperature module)
wt.add_comment("Plate pre-coated with capture antibodies; incubate overnight at 4C")

# === PHASE 2: Blocking ===
head.get_tips(fca_tips)
with wt.loop(times=12, name="Dispense blocking buffer", loop_variable="col"):
    head.aspirate(block_trough, "BLOCK_VOL_UL",
                  liquid_class="LIQUID_CLASS_BLOCK")
    head.dispense(elisa_plate, "BLOCK_VOL_UL",
                  liquid_class="LIQUID_CLASS_BLOCK", well_offset="(col-1)*8")
head.drop_tips()

wt.add_comment("Incubate 1h at room temperature (or 37C on heater-shaker)")
wt.wait(duration_seconds=3600)

# === Wash cycles (repeated NUM_WASH_CYCLES times) ===
with wt.loop(times=NUM_WASH_CYCLES, name="Wash plate", loop_variable="wash"):
    head.get_tips(fca_tips)
    # aspirate and discard wash buffer from all wells
    with wt.loop(times=12, name="Aspirate wash", loop_variable="col"):
        head.aspirate(elisa_plate, "WASH_VOL_UL",
                      liquid_class="LIQUID_CLASS_WASH", well_offset="(col-1)*8")
        head.dispense(waste, "WASH_VOL_UL",
                      liquid_class="LIQUID_CLASS_WASH")
    head.drop_tips()

# === PHASE 3: Sample incubation ===
head.get_tips(fca_tips)
with wt.loop(times=NUM_TARGETS * SAMPLE_COLS_PER_TARGET, name="Add samples",
             loop_variable="col"):
    head.aspirate(sample_trough, "SAMPLE_VOL_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE")
    head.dispense(elisa_plate, "SAMPLE_VOL_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
head.drop_tips()

wt.add_comment("Incubate 2h at room temperature (or 37C on heater-shaker)")
wt.wait(duration_seconds=7200)

# Repeat wash cycles after sample incubation
with wt.loop(times=NUM_WASH_CYCLES, name="Wash after samples", loop_variable="wash"):
    head.get_tips(fca_tips)
    with wt.loop(times=12, name="Aspirate wash", loop_variable="col"):
        head.aspirate(elisa_plate, "WASH_VOL_UL",
                      liquid_class="LIQUID_CLASS_WASH", well_offset="(col-1)*8")
        head.dispense(waste, "WASH_VOL_UL",
                      liquid_class="LIQUID_CLASS_WASH")
    head.drop_tips()

# === PHASE 4: Signal development ===
head.get_tips(fca_tips)
with wt.loop(times=12, name="Add detection antibody", loop_variable="col"):
    head.aspirate(detect_trough, "DETECT_AB_VOL_UL",
                  liquid_class="LIQUID_CLASS_DETECT")
    head.dispense(elisa_plate, "DETECT_AB_VOL_UL",
                  liquid_class="LIQUID_CLASS_DETECT", well_offset="(col-1)*8")
head.drop_tips()

wt.add_comment("Incubate 1h at room temperature")
wt.wait(duration_seconds=3600)

# Wash again, then add HRP conjugate, wash, add TMB, develop, stop
# (same pattern — aspirate waste / dispense reagent / wait / repeat)

wt.add_comment("Read plate at 450 nm on microplate reader (off-deck)")
```

### Limitations (needs-extension P2/P3)

- **Incubation temperatures**: ELISA uses 4°C overnight, room temperature, and
  optionally 37°C incubations. fluentvibe models these as `wt.wait()` + comments.
  See [capability-roadmap](../../docs/skill-authoring/capability-roadmap.md) P2
  (annotated waits) and P3 (heater-shaker).
- **Plate reader**: final absorbance read at 450 nm is off-deck — noted in
  comments.

### Wash cycle pattern

The wash step is the most repeated operation. Use `wt.loop` for both the number
of cycles and the column-wise aspiration/dispense within each cycle. The MCA96
can aspirate all 96 wells at once if using whole-plate wash:

```python
# Alternative: MCA96 whole-plate wash (faster)
head = wt.mca96
head.mount_adapter()
head.pick_up(tips_wash)
head.aspirate(elisa_plate, "WASH_VOL_UL", liquid_class="LIQUID_CLASS_WASH")
head.dispense(waste, "WASH_VOL_UL", liquid_class="LIQUID_CLASS_WASH")
head.return_tips(tips_wash)
head.drop_adapter()
```
