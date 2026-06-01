# Lab Scope — grounded API reference (enforce mode)

This file carries the information the authoring tools used to gather at runtime
(`lookup_api`, `lookup_workspace`, `list_valid_positions`, `lookup_rules`).
In enforce mode those tools are not available — **everything you need to write the
protocol is here and in `lab_scope.md`**. Write the complete `build_worktable()`
in one pass and call `simulate_python_draft`, then `compile_and_simulate`.

> **Variables are passed BY NAME (a string).** `aspirate(src, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS')`.
> Passing the Python value (e.g. `BEAD_VOLUME_UL` holding `36.0`, or `'Water Free Single'`) bakes a literal
> into the rendered protocol and leaves the declared FluentControl variable dead. The renderer emits a variable
> reference **only** when the string equals a declared variable name.

> **Loops are native FluentControl loops, never Python `for` unrolls.**
> `with wt.loop(times=12, loop_variable='col'):` then address columns with `well_offset='(col-1)*8'`.

## Object / head API (from `lookup_api`)

### `Worktable`
```python
wt = Worktable.from_workspace('SAT_Fluent_780_Rev3', workspace_guid='291ba293-6361-4f8f-aa8d-7c2643d3f096', auto_place=False)
plate = wt.place(Plate96('DestPlate', catalog='96_ABgene_SuperPlate_Thermo_AB2800'), 'Nest61mm_Pos', 2)
wt.group('Transfer')
# native FluentControl loop — do NOT unroll with a Python for-loop
with wt.loop(times=12, name='Dispense columns', loop_variable='col'):
    head.aspirate(trough, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS')
    head.dispense(plate, 'BEAD_VOLUME_UL', liquid_class='LIQUID_CLASS_BEADS', well_offset='(col-1)*8')
wt.declare_variable('RunId', 'demo')
wt.set_sim_value('RunId', 'demo')
wt.set_variable('RunId', 'demo')
wt.wait(30)
wt.add_comment('Incubate at room temperature')
```
**Attributes:** liha, mca96, gripper
**Never call:** `wt.pick_up(...)`, `wt.aspirate(...)`, `wt.dispense(...)`

### `wt.liha`

wt.liha is the only fixed-channel pipetting head exposed by fluentvibe. Use it for FCA-style operations (trough-to-plate dispenses, single-channel or per-column transfers, individual well aspirate/dispense). Use wt.mca96 only for true 96-channel plate-to-plate moves. PASS DECLARED VARIABLES BY NAME (a string) for volume and liquid_class — `'BEAD_VOLUME_UL'`, not the Python value — or the rendered protocol bakes in a literal and the FC variable is dead. To cover all 12 columns, wrap a single aspirate/dispense in `with wt.loop(times=12, loop_variable='col')` and address columns with `well_offset='(col-1)*8'`; NEVER unroll with a Python `for` loop.

```python
head = wt.liha
head.get_tips(tips)
head.aspirate(source, 'TARGET_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER')
with wt.loop(times=12, loop_variable='col'):
    head.dispense(dest, 'TARGET_VOLUME_UL', liquid_class='LIQUID_CLASS_TRANSFER', well_offset='(col-1)*8')
head.mix(plate, 'MIX_VOLUME_UL', cycles=10, liquid_class='LIQUID_CLASS_MIX')
head.empty_tips(waste, 'SUPERNATANT_VOLUME_UL')
head.drop_tips()
head.drop_tips(tips)
```
**Never call:** `pick_up`, `return_tips`, `mount_adapter`, `drop_adapter`

### `wt.mca96`

True 96-channel head: one aspirate/dispense/mix touches all 96 wells at once — no per-column loop. PASS DECLARED VARIABLES BY NAME (a string) for the volume and liquid_class (e.g. 'SUPERNATANT_ASPIRATE_UL', 'LIQUID_CLASS_SUPERNATANT'); passing the Python value bakes a literal into the protocol and leaves the FC variable unused.

```python
head = wt.mca96
head.mount_adapter()
head.pick_up(tips)
head.aspirate(source, 'SUPERNATANT_ASPIRATE_UL', liquid_class='LIQUID_CLASS_SUPERNATANT')
head.dispense(dest, 'TRANSFER_VOLUME_UL', liquid_class='LIQUID_CLASS_ELUATE')
head.mix(plate, 'MIX_VOLUME_UL', cycles=10, liquid_class='LIQUID_CLASS_BEADS')
head.empty_tips(waste, 'SUPERNATANT_ASPIRATE_UL')
head.return_tips(tips)
head.drop_adapter()
```
**Never call:** `get_tips`, `drop_tips`

### `wt.gripper`
```python
wt.gripper.move(plate, to=('Nest61mm_Pos', 2))
wt.gripper.move(plate, onto=magnet)
```
**Never call:** `pick_up`, `drop`, `place`, `aspirate`, `dispense`

### `wt.fca`

fluentvibe does NOT expose wt.fca as a runtime head. FCA-style fixed-channel pipetting (single-channel, per-column, trough-to-plate dispenses) is authored through wt.liha. Call lookup_api('wt.liha') for the full method surface. Use wt.mca96 only for true 96-channel plate-to-plate operations.

**Never call:** `wt.fca.aspirate`, `wt.fca.dispense`, `wt.fca.pick_up`

### `Labware`
```python
plate.well('A1').add_layer(sample, 20.0)
for well in plate.all_wells(): ...
plate.column(1)
plate.row('A')
source.fill_all(water, 80.0)
```
**Attributes:** label, wells, catalog_name, slot, is_magnetized
**Never call:** `fill`, `plate['A1']`

## Valid deck positions (from `lookup_workspace` / `list_valid_positions`)

Workspace: `SAT_Fluent_780_Rev3`  GUID: `291ba293-6361-4f8f-aa8d-7c2643d3f096`

| Location | Valid positions |
|---|---|
| `InfiniteM200_Pos` | 1 |
| `MCA384_Diti_ActiveNest` | 1, 2 |
| `Nest61mm_Pos` | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| `Nest7mm_Pos` | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16 |
| `Regrip_Pos` | 1 |
| `ThruDeckWaste_Pos` | 1 |
| `WS_100ml_1` | 1, 2, 3, 4, 5, 6, 7, 8 |

**Default layout (role → slot):**

| Role | Location | Site |
|---|---|---|
| sample_plate | `Nest61mm_Pos` | 1 |
| final_plate | `Nest61mm_Pos` | 2 |
| magnet_plate | `Nest61mm_Pos` | 3 |
| mca_tips | `Nest61mm_Pos` | 4 |
| waste | `Nest61mm_Pos` | 5 |
| fca_tips | `Nest61mm_Pos` | 6 |
| reservoir_start | `WS_100ml_1` | 1 |

## Authoring rules (from `lookup_rules`)

### ampure_derived_volumes_in_python  *(hard, bead_cleanup)*
Dependent volumes must be derived from primary variables in python expressions BEFORE the wt.declare_variable call -- never hardcoded. Example: PCR_SAMPLE_UL = 20.0; BEAD_VOLUME_UL = 36.0; RESIDUAL_SUPERNATANT_UL = 5.0; SUPERNATANT_ASPIRATE_UL = PCR_SAMPLE_UL + BEAD_VOLUME_UL - RESIDUAL_SUPERNATANT_UL. Then call wt.declare_variable for each name with the computed numeric value. This keeps the protocol parameterizable: changing PCR_SAMPLE_UL alone re-derives every dependent volume.

### ampure_per_role_liquid_class_variables  *(hard, bead_cleanup)*
Declare one string variable per reagent role -- LIQUID_CLASS_BEADS, LIQUID_CLASS_ETHANOL, LIQUID_CLASS_SUPERNATANT, LIQUID_CLASS_ELUATE, LIQUID_CLASS_ELUTION_BUFFER -- even when all initial values are the same. Each pipetting call passes the role-specific variable. This lets the lab tech tune one role's class on the fly without rewriting the protocol.

### adapter_persists_until_dropped  *(soft, None)*
Once get_head_adapter is called, the adapter stays mounted for ALL subsequent MCA operations until drop_head_adapter. Do NOT drop and re-pick between every operation

### ampure_separate_tip_box_for_eluate  *(hard, bead_cleanup)*
Place TWO MCA200Box labware objects: one for waste and wash operations, one used exclusively for the final eluate transfer. Re-using the same tip box across waste and eluate causes bead carryover. Name the second box something like 'MCATipsEluate' and pick up only from it inside the Transfer Eluate group.

### only_include_requested_operation_groups  *(soft, None)*
Generated protocol should include only operations requested by user; avoid unrelated template filler groups such as unnecessary plate-transfer or RGA move sections.

### MCA  *(soft, None)*
Use EVA[001] adapter for 96-well plate operations.

### Worktable Setup  *(soft, None)*
...

### canonical_step_type_names_only  *(soft, None)*
Use canonical YAML step_type names only (e.g. aspirate, dispense, pick_up_tips, set_tips_back, get_head_adapter, drop_head_adapter, liha_*). Avoid vendor-prefixed aliases like mca384_aspirate/mca384_pick_up_tips in generated YAML.

### use_only_known_worktable_locations  *(hard, None)*
Use only exact location keys from get_worktable_positions(); do not invent location names like WS_100ml_2/3/4 unless explicitly listed.

### ws100ml_100ml_double_reachable_sites  *(hard, None)*
For labware type 100ml Trough Double at WS_100ml_1, avoid sites 1 and 5 due LiHa reachability limits; use sites 2/3/4/6/7/8 or use 100ml Trough 156mm.

### magnet_carrier_not_liquid_target  *(hard, None)*
Do not aspirate/dispense/mix directly on magnet carrier labware labels. Use the process plate label; magnet carrier only provides magnetic field.

### calculate_variable_supported_operations  *(hard, None)*
calculate_variable.operation must map to Add/Subtract/Multiply/Divide (or +,-,*,/) so renderer can emit valid expressions.

### liha_mix_requires_fields  *(soft, None)*
liha_mix step requires labware_name (string) and volume (float or variable name). Optional: cycles (int, default 10). Example: step_type: liha_mix, labware_name: "Plate", volume: 50.0, cycles: 10

### loop_count_must_be_numeric_or_declared_variable  *(hard, None)*
loop.number_of_loops must be an integer literal or a declared variable with a numeric set_variable assignment.

### loop_variable_set  *(soft, None)*
LoopGroup must use a variable named 'LoopVariable' that holds the current loop counter.

### rga_for_plate_moves  *(soft, None)*
Plate movements between locations MUST use rga_transfer_labware with cga_get_fingers/cga_drop_fingers. NEVER use user_prompt to ask the user to manually move plates.

### volumes_as_float_variables  *(soft, None)*
All pipetting volumes MUST be declared as variables with Floating Point type. Declare volume variable names in the top-level variables: list. Use set_variable to set initial values (e.g. value: 90.0 not 90). Reference variable names as strings in volume fields. Example: set_variable variable_name: "BeadVolume" value: 90.0, then aspirate volume: "BeadVolume".

### wait_uses_duration_seconds  *(soft, None)*
The wait step requires the field "duration_seconds" (integer, in seconds). For minutes multiply by 60. Example: 5 min = duration_seconds: 300. Do NOT use duration_minutes or duration.

### alpaqua_magnet_sbs_format  *(soft, None)*
Alpaqua magnet plates (LV_Alpaqua_A000350, LV_Alpaqua_384) have SBS format and go on normal Nest positions (Nest61mm_Pos), NOT on special magnet stations. There is no 'Magnet station' location on the Tecan Fluent worktable. For 96-well bead cleanup, use LV_Alpaqua_A000350 (96-well).

### exact_labware_names_only  *(soft, None)*
Labware type names must be copied EXACTLY from lookup_labware results. Never add parenthetical suffixes like "(96-well)" or "(SBS)". Example: use "LV_Alpaqua_A000350" NOT "LV_Alpaqua_A000350 (96-well)".

### fca_sbs_tip_location_nest61_preferred  *(hard, None)*
On this deck profile, FCA SBS tip boxes should be placed on Nest61mm_Pos at reachable lower sites (commonly 2/3/4/6-10). Avoid Nest7mm_Pos placements that can be out-of-range.

### fca_sbs_tip_location_nest61_sites_1_to_6  *(hard, None)*
On this deck, FCA SBS tip boxes on Nest61mm_Pos are reachable only at sites 1-6; avoid sites 7-10.

### labware_types_as_variables  *(soft, None)*
Labware types (plate types, reservoir types, tip box types) MUST be declared as String variables. Add them to the top-level variables: list. Use set_variable with the labware type name as a string value. Reference the variable name in labware_type fields. Example: variables: ["PlateType", "BeadVolume"] then set_variable variable_name: "PlateType" value: "96 Well Flat" then add_labware labware_type: "PlateType" label: "SamplePlate". This makes protocols easy to adapt to different labware without editing every step.
