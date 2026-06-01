---
name: family-cell-seeding
axis: family
description: Seed cells from a stock suspension into tissue culture plates (6, 12, or 24-well format), and exchange media — remove supernatant to waste, PBS-wash, add compound/treatment. Select for cell seeding, cell plating, media exchange, compound treatment, transfection prep, culture preparation, or cell distribution workflows.
always_on: false
---
## Canonical workflow: cell seeding (skeleton)

Transfer cell suspension from stock tubes into the wells of tissue culture
plates. Supports up to 4 plates in parallel, each possibly a different format
(6-, 12-, or 24-well).

### Variables

```
CELL_VOLUME_UL         — volume of cell suspension per well (e.g. 2000 for 6-well,
                          1000 for 12-well, 500 for 24-well)
NUM_PLATES             — number of culture plates to seed (1-4)
WELLS_PER_PLATE        — wells per plate depending on format:
                          6-well → 6, 12-well → 12, 24-well → 24
LIQUID_CLASS_MEDIUM    — liquid class for cell medium, default "Water Free Single"
```

### Labware Placement

- Cell stock tubes (modelled as `Trough` or tube rack — needs whitelist addition
  for 15/50 mL conical tube racks)
- Culture plates (needs whitelist addition for 6/12/24-well formats)
- FCA tip box — must be **FCA**-class for the LiHa, not MCA:
  ```python
  from fluentvibe import TipBox
  fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
  ```

### Step sequence (LiHa, well-by-well with loop)

```python
head = wt.liha
head.get_tips(fca_tips)

# For each plate, seed its wells from the corresponding stock tube row
with wt.loop(times=NUM_PLATES, name="Seed plates", loop_variable="p"):
    # within each plate, iterate over wells
    with wt.loop(times=WELLS_PER_PLATE, name="Dispense cells", loop_variable="w"):
        head.aspirate(stock_tubes, "CELL_VOLUME_UL",
                      liquid_class="LIQUID_CLASS_MEDIUM")
        head.dispense(culture_plate, "CELL_VOLUME_UL",
                      liquid_class="LIQUID_CLASS_MEDIUM")
    # change stock tube row for next plate (handled by well offset)

head.drop_tips()
```

### Variant: media exchange / compound treatment (sci-lucif-assay3)

Assay protocols on seeded cells exchange the medium: remove spent supernatant to
waste, wash with PBS, then add a treatment/compound. Model column-wise with the
LiHa and a `WASTE_TROUGH` role (`Trough`, `100ml`/`300ml SBS`):

```
PBS_WASH_VOLUME_UL, TREATMENT_VOLUME_UL   # scalar per-well volumes
LIQUID_CLASS_MEDIUM, LIQUID_CLASS_PBS, LIQUID_CLASS_TREATMENT
```

```python
head = wt.liha
with wt.loop(times=TOTAL_COLS, name="Media exchange", loop_variable="col"):
    head.aspirate(plate, "MEDIUM_VOLUME_UL", liquid_class="LIQUID_CLASS_MEDIUM", well_offset="(col-1)*8")
    head.dispense(waste, "MEDIUM_VOLUME_UL", liquid_class="LIQUID_CLASS_MEDIUM")
    head.dispense(plate, "PBS_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_PBS", well_offset="(col-1)*8")
    head.aspirate(plate, "PBS_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_PBS", well_offset="(col-1)*8")
    head.dispense(waste, "PBS_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_PBS")
    head.dispense(plate, "TREATMENT_VOLUME_UL", liquid_class="LIQUID_CLASS_TREATMENT", well_offset="(col-1)*8")
```

Opentrons `air_gap`/custom flow rates are dropped (G2); standard liquid-class
defaults apply. If the treatment volume differs per well, use a worklist
(`api-worklists`).

### Limitations (needs-extension)

- **Air gap**: Opentrons protocols use air gaps before dispensing cells to avoid
  disturbing the meniscus. fluentvibe does not expose `air_gap` — drop silently.
- **Gentle handling**: cell work benefits from slow dispense rates and bottom
  approach. fluentvibe does not model flow rate or approach height — note in
  comments if critical.
- **Non-standard plate formats** (6/12/24-well): these are not on the approved
  labware whitelist yet. Resolve exact catalog names via `fluentvibe.catalog` and
  add to `generation.yaml` `lab_scope.labware`.

### Whitelist additions needed

- **6/12/24-well culture plate**: resolve catalog name (e.g. Corning Costar
  Multiple Well Cell Culture Plates)
- **15/50 mL tube rack** (NEST conical tubes): resolve catalog name
