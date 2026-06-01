---
name: family-purification
axis: family
description: Solid-phase extraction and resin-based purification — load sample over a C18 or filter plate, wash with sequential buffers, elute purified fraction. Select for peptide purification, C18 SPE, desalting, filter-plate cleanup, MS sample prep, or resin/column chromatography workflows.
always_on: false
---
## Canonical workflow: solid-phase extraction (skeleton)

Load a sample onto a binding matrix (C18 tips, filter plate, or resin column),
wash with sequential buffers to remove contaminants, then elute the purified
fraction. Modelled here as transfers through a 96-well filter/resin plate.

### Variables

```
CONDITION_VOL_UL       — conditioning buffer volume per well (e.g. 100)
EQUILIBRATE_VOL_UL     — equilibration buffer volume per well (e.g. 100)
SAMPLE_LOAD_VOL_UL     — sample volume loaded per well (e.g. 50-100)
WASH_VOL_UL            — wash buffer volume per well (e.g. 100)
NUM_WASH_STEPS         — number of distinct wash buffers/steps
ELUTE_VOL_UL           — elution buffer volume per well (e.g. 50)
NUM_ELUTIONS           — number of elution repetitions (typically 2-3)
LIQUID_CLASS_CONDITION — liquid class for conditioning buffer
LIQUID_CLASS_SAMPLE    — liquid class for sample
LIQUID_CLASS_WASH      — liquid class for wash buffers
LIQUID_CLASS_ELUTE     — liquid class for elution buffer
```

### Labware Placement

- Filter/resin plate (`Plate96` — needs whitelist addition for C18/filter plates)
- Collection plate underneath (`Plate96`)
- Reservoirs/troughs for each buffer (condition, equilibrate, wash, elute)
- Waste reservoir (`Trough`, `300ml SBS`)
- FCA tip box — must be **FCA**-class for the LiHa, not MCA:
  ```python
  from fluentvibe import TipBox
  fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
  ```

### Step sequence (LiHa, column-wise with loop)

```python
head = wt.liha

# 1. Condition the resin/tips (e.g. 50% ACN x2)
head.get_tips(fca_tips)
with wt.loop(times=12, name="Condition", loop_variable="col"):
    head.aspirate(condition_trough, "CONDITION_VOL_UL",
                  liquid_class="LIQUID_CLASS_CONDITION")
    # dispense through the filter/resin into waste or collection plate below
    head.dispense(filter_plate, "CONDITION_VOL_UL",
                  liquid_class="LIQUID_CLASS_CONDITION", well_offset="(col-1)*8")
head.drop_tips()

# 2. Equilibrate (e.g. 0.1% TFA x2)
head.get_tips(fca_tips)
with wt.loop(times=12, name="Equilibrate", loop_variable="col"):
    head.aspirate(equil_trough, "EQUILIBRATE_VOL_UL",
                  liquid_class="LIQUID_CLASS_CONDITION")
    head.dispense(filter_plate, "EQUILIBRATE_VOL_UL",
                  liquid_class="LIQUID_CLASS_CONDITION", well_offset="(col-1)*8")
head.drop_tips()

# 3. Load sample (dispense through resin; bind peptides)
head.get_tips(fca_tips)
with wt.loop(times=NUM_LOAD_COLS, name="Load sample", loop_variable="col"):
    head.aspirate(sample_plate, "SAMPLE_LOAD_VOL_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
    head.dispense(filter_plate, "SAMPLE_LOAD_VOL_UL",
                  liquid_class="LIQUID_CLASS_SAMPLE", well_offset="(col-1)*8")
head.drop_tips()

# 4. Wash (sequential buffers, e.g. 0.1% TFA/5% ACN x2)
for wash_step in range(NUM_WASH_STEPS):
    head.get_tips(fca_tips)
    with wt.loop(times=12, name=f"Wash step {wash_step+1}", loop_variable="col"):
        head.aspirate(wash_troughs[wash_step], "WASH_VOL_UL",
                      liquid_class="LIQUID_CLASS_WASH")
        head.dispense(filter_plate, "WASH_VOL_UL",
                      liquid_class="LIQUID_CLASS_WASH", well_offset="(col-1)*8")
    head.drop_tips()

# 5. Elute (e.g. 0.1% FA / 70% ACN x2 into collection plate).
# Identical elutions repeat -> native outer loop, not a Python for.
head.get_tips(fca_tips)
with wt.loop(times=NUM_ELUTIONS, name="Elute", loop_variable="elution"):
    with wt.loop(times=12, name="Elute columns", loop_variable="col"):
        head.aspirate(elute_trough, "ELUTE_VOL_UL",
                      liquid_class="LIQUID_CLASS_ELUTE")
        head.dispense(collection_plate, "ELUTE_VOL_UL",
                      liquid_class="LIQUID_CLASS_ELUTE", well_offset="(col-1)*8")
head.drop_tips()

wt.add_comment("Collection plate contains purified peptides; proceed to MS injection")
```

### Limitations (needs-extension)

- **Filter/resin plate labware**: not on the approved whitelist. Resolve exact
  catalog name via `fluentvibe.catalog` (`find_components`) and add to
  `generation.yaml` `lab_scope.labware`.
- **Gravity flow / vacuum manifold**: C18 SPE relies on liquid passing through
  the matrix by gravity or vacuum. fluentvibe models this as dispense into the
  filter plate — the collection plate underneath receives the filtrate. The
  actual flow-through mechanics are not modelled explicitly.

### Tip reuse strategy

Use fresh tips between buffer changes to avoid cross-contamination of buffers.
Within a single wash step, one tip per column is sufficient with LiHa.
