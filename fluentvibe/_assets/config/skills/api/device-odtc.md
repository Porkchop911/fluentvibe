---
name: device-odtc
axis: api
description: Driving external device-driver macros (LegacyDriverMacro) — especially the Inheco underdeck ODTC thermal cycler via wt.odtc_*. Select for PCR/annealing/thermocycling protocols that open/close the ODTC door, load a method file, and run a thermal program.
always_on: false
---
## `wt.odtc_*` — Inheco underdeck ODTC (driver module `SiLA-ODTC`)

```python
wt.odtc_open_door()
wt.gripper.move(plate, to=('Inheco_Pos', 4))   # load plate into the ODTC
wt.odtc_close_door()
wt.odtc_set_parameters("Annealing.xml")          # load the thermal method file
wt.odtc_execute_method("Annealing")              # run the named program (blocks)
wt.odtc_open_door()
wt.gripper.move(plate, to=('Nest61mm_Pos', 1))   # remove plate
```

Helpers: `odtc_open_door()`, `odtc_close_door()`, `odtc_get_parameters()`,
`odtc_set_parameters(methods_xml_file)`, `odtc_execute_method(method_name)`.
Inheco MTC pre-heat: `wt.inheco_set_temperature(...)` (module `inhecoMTC`).

## Generic escape hatch

```python
wt.legacy_driver_macro("SiLA-ODTC_OpenDoor", "SiLA-ODTC")
wt.legacy_driver_macro("Magellan_Close", "Magellan")
```

## Rules

- **The thermal profile is NOT authored here.** `set_parameters` only points the
  ODTC at a method file (e.g. `Annealing.xml`) that lives on the driver side;
  `execute_method` runs a program by name. Author the program in the Inheco
  Script Editor, not in the protocol.
- **Plate movement in/out of the ODTC uses the gripper** to/from the deck
  position the ODTC occupies (commonly `Inheco_Pos`). Open the door before
  moving a plate in or out; close it before executing a method.
- **These commands compile and simulate without the driver installed**, but
  *running* them on hardware requires the Tecan `SiLA-ODTC` / `inhecoMTC`
  drivers configured in FluentControl — an instrument-side dependency. The
  simulator treats the macros as validation-only (no twin/liquid effect).
