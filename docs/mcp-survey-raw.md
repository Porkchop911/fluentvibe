# MCP + Agent Skill pattern survey — raw extraction

Method: GitHub REST API (`api.github.com/repos/...`, `/git/trees?recursive=1`), `raw.githubusercontent.com/.../HEAD/...`, and ordinary web pages. No repo was cloned; nothing in this repository changed except this file. Line numbers refer to the file at the default branch (`HEAD`) as read on 2026-10-07. Unauthenticated GitHub code-search API returns HTTP 401, so no code-search evidence is used.

## 1. K-Dense-AI/lab-instrument-mcps

- Repo: https://github.com/K-Dense-AI/lab-instrument-mcps — **17 stars**, last commit/push **2026-09-26T22:47:14Z**, default branch `main`, language Python, not a fork. Homepage `k-dense.ai`.
- GitHub description (verbatim): "Open-source MCP servers that let AI agents operate real lab instruments, with safety limits, read-only modes, audit trails, and a simulator for every instrument."

### 1.1 Repo tree (top 2 levels)

From `GET /repos/K-Dense-AI/lab-instrument-mcps/git/trees/HEAD?recursive=1`:

| Path (level 1) | Contents (level 2) |
|---|---|
| `.github/` | `ISSUE_TEMPLATE/`, `pull_request_template.md`, `workflows/ci.yml`, `workflows/release.yml` |
| `docs/` | `architecture.md` (4475 B), `clients.md` (117 lines), `official-servers.md` (34 lines), `writing-a-server.md` (9307 B, 131 lines) |
| `examples/` | `README.md`, `virtual-lab-claude-code.sh`, `virtual-lab.claude_desktop_config.json` |
| `packages/labmcp/` | shared core package (see 1.2) |
| `scripts/` | `build_catalog.py` (12157 B), `new_server.py` (7673 B) |
| `servers/` | `biology/`, `chemistry/`, `engineering/`, `health/`, `physics/`, `protocols/` → 27 server dirs total |
| root files | `README.md` (33338 B, 367 lines), `SAFETY.md` (3467 B, 38 lines), `catalog.json` (164306 B) |

Every instrument server dir has the same shape: `servers/<domain>/<instrument>/README.md`, `pyproject.toml`, `server.json`, `src/labmcp_<pkg>/{__init__.py, driver.py, server.py, simulator.py}`, `tests/test_server.py`.

Server inventory (domain / dir / package / catalog tool count):

| Domain | Server dir | Package | Catalog tools |
|---|---|---|---|
| biology | `atlas-ezo-sensors` | `labmcp-atlas-ezo` | 13 |
| biology | `micro-manager` | `labmcp-micro-manager` | 19 |
| biology | `new-era-syringe-pump` | `labmcp-new-era-syringe-pump` | 11 |
| biology | `opentrons` | `labmcp-opentrons` | 18 |
| biology | `tecan-cavro-pump` | `labmcp-cavro` | 9 |
| chemistry | `ika-stirrer` | `labmcp-ika-stirrer` | 14 |
| chemistry | `julabo-circulator` | `labmcp-julabo-circulator` | 10 |
| chemistry | `mettler-toledo-balance` | `labmcp-mettler-toledo-balance` | 17 |
| chemistry | `ms-data` | `labmcp-ms-data` | 12 |
| chemistry | `ms-worklist` | `labmcp-ms-worklist` | 12 |
| chemistry | `ocean-spectrometer` | `labmcp-ocean-spectrometer` | 16 |
| chemistry | `palmsens-potentiostat` | `labmcp-palmsens-potentiostat` | 10 |
| chemistry | `sartorius-balance` | `labmcp-sartorius-balance` | 10 |
| chemistry | `thermo-iapi` | `labmcp-thermo-iapi` | 15 |
| engineering | `alicat-flow-controller` | `labmcp-alicat-flow-controller` | 14 |
| engineering | `bench-power-supply` | `labmcp-bench-power-supply` | 12 |
| engineering | `labjack` | `labmcp-labjack` | 12 |
| engineering | `ni-daqmx` | `labmcp-ni-daqmx` | 11 |
| engineering | `rigol-oscilloscope` | `labmcp-rigol-oscilloscope` | 16 |
| health | `astm-lis-analyzer` | `labmcp-astm-lis-analyzer` | 8 |
| health | `ble-health-sensors` | `labmcp-ble-health-sensors` | 11 |
| health | `brainflow-biosensors` | `labmcp-brainflow-biosensors` | 12 |
| physics | `keithley-smu` | `labmcp-keithley-smu` | 12 |
| physics | `lakeshore-temperature` | `labmcp-lakeshore-temperature` | 11 |
| physics | `pfeiffer-tpg` | `labmcp-pfeiffer-tpg` | 11 |
| physics | `srs-lockin` | `labmcp-srs-lockin` | 15 |
| physics | `srs-rga` | `labmcp-srs-rga` | 18 |
| physics | `thorlabs-power-meter` | `labmcp-thorlabs-power-meter` | 11 |
| protocols | `epics` | `labmcp-epics` | 10 |
| protocols | `modbus` | `labmcp-modbus` | 13 |
| protocols | `scpi-instrument` | `labmcp-scpi-instrument` | 15 |
| protocols | `sila2` | `labmcp-sila2` | 13 |

Total in `catalog.json`: **411 tools across 32 servers** (27 server directories + 5 servers whose entries exist in the catalog under other package names; all 32 catalog entries carry `status = "simulated"`).

### 1.2 MCP library + transport

- `packages/labmcp/src/labmcp/server.py:45` `from fastmcp import FastMCP` and `:46` `from mcp.types import ToolAnnotations` → the MCP library is **FastMCP** (with `mcp.types` annotations). Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/packages/labmcp/src/labmcp/server.py#L45-L46
- Server construction: `server.py:226` `self.mcp = FastMCP(name, instructions=self._build_instructions(), lifespan=self._lifespan)`. Link: …/server.py#L226
- Transport, `InstrumentServer.run()` docstring `server.py:366-398`: "Parse the command line and serve over stdio (default) or HTTP." Implementation `server.py:393-396`:
  > `if args.transport == "http":`
  > `    self.mcp.run(transport="http", host=args.host, port=args.port)`
  > `else:`
  > `    self.mcp.run()`
  → **stdio default, streamable HTTP optional; no SSE option.** Link: …/server.py#L366-L398
- `docs/architecture.md:41-56` CLI/env table includes `--transport http --host --port` → "Serve over streamable HTTP instead of stdio". Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/docs/architecture.md#L41-L56
- `SAFETY.md:25-33` item 6:
  > "**Keep MCP servers local.** The default stdio transport is only reachable by the client that launched it. If you use `--transport http`, bind to `127.0.0.1` or put it behind authentication. Never expose instrument control to the internet."
  Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/SAFETY.md#L25-L33

### 1.3 Full tool list per instrument server

Tool kind legend used by the tables below (from `packages/labmcp/src/labmcp/server.py:62-96`, verbatim annotation presets):
- `read` = `ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)` (`server.py:63`)
- `control` = tags `{"control"}` (`server.py:72`) — "Hidden in ``--read-only`` mode"
- `hazard` = `destructive_hint=True`, tags `{"control","hazard"}` (`server.py:82`) — comment "MCP clients should ask the user before running these"
- `safety` = `idempotent_hint=True` (`server.py:91`) — "Always available, even in read-only mode, and never gated behind confirmation."

Parameters are taken from the `@mcp.tool` function signatures in each `server.py` (the `catalog.json` tool entries carry only `{name, kind, description}`, no parameter schema). "builtin/core-provided" = the three tools the shared base class registers (`get_connection_info`, `get_command_log`, `reconnect`; `server.py:456-477`), which appear in each server's catalog entry but are not defined in that server's `server.py`.

### Atlas Scientific EZO Sensors (`labmcp-atlas-ezo`, servers/biology/atlas-ezo-sensors/src/labmcp_atlas_ezo/server.py, 13 catalog tools / 10 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_value` | read | (none) | Take one reading (about 1 s) and return every enabled output with its unit: pH; ORP in mV; DO in mg/L (and % saturation if enabled); EC in µS/cm plus TDS (ppm), salinity (PSU) and specific gravity if enabled; RTD temperature; humidity %RH (+ air temperature, dew point); CO2 ppm; or pressure. |
| `get_info` | read | (none) | Report the circuit type, firmware, device name, supply voltage and last restart reason, LED state, calibration state, temperature compensation and type-specific settings (enabled outputs, EC probe constant K, DO salinity/pressure compensation). |
| `get_calibration_status` | read | (none) | Report how many calibration points are stored and what that means for this circuit type; for pH also the probe slope (acid/base % of ideal and zero offset in mV). |
| `calibrate` | control | point: Annotated[
        str,
        Field(description="pH: mid|low|high; ORP: single; DO: atmospheric|zero; EC: dry|single|low|high; "
                          "RTD: single; CO2: zero|high; PRS: zero|high"),
    ]; value: Annotated[
        float | None,
        Field(description="Value of the standard the probe is in (pH units, mV, µS/cm, temperature in the "
                          "RTD's current scale, ppm CO2, pressure in current units). Omit for DO "
                          "atmospheric/zero, EC dry and CO2/PRS zero."),
    ] = None | Store one calibration point |
| `clear_calibration` | control | (none) | Delete all stored calibration data (Cal,clear) |
| `set_temperature_compensation` | control | temperature_c: Annotated[float, Field(ge=-10, le=130, description="Sample temperature in °C")] | Set the sample temperature used to compensate pH, EC or DO readings (T,n; always °C) |
| `set_probe_constant` | control | k: Annotated[float, Field(ge=0.01, le=100, description="Cell constant K of the EC probe (e.g. 0.1, 1.0, 10)")] | EC circuits only: set the conductivity probe's cell constant K to match the probe (printed on it) |
| `set_do_compensation` | control | salinity: Annotated[float | None, Field(ge=0, le=100000, description="Sample salinity (see salinity_unit)")] = None; salinity_unit: Annotated[str, Field(pattern="^(us_cm|ppt)$", description="'us_cm' (µS/cm) or 'ppt'")] = "us_cm"; pressure_kpa: Annotated[float | None, Field(ge=50, le=200, description="Atmospheric pressure in kPa")] = None | DO circuits only: set salinity compensation (irrelevant below ~2500 µS/cm) and/or atmospheric pressure compensation (default 101.3 kPa; lower at altitude) |
| `set_led` | control | on: Annotated[bool, Field(description="True = LED on (default), False = off (e.g. light-sensitive cultures)")] | Turn the circuit's status LED on or off |
| `log_series` | read | count: Annotated[int, Field(ge=2, le=1000, description="Number of readings")] = 10; interval_s: Annotated[float, Field(ge=1.0, le=600, description="Seconds between readings (>= 1)")] = 2.0; save_path: Annotated[
        str | None, Field(description="Optional new .csv file for the full series (never overwritten)")
    ] = None | Record a series of readings (e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Micro-Manager Microscope (`labmcp-micro-manager`, servers/biology/micro-manager/src/labmcp_micro_manager/server.py, 19 catalog tools / 15 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_system_info` | read | (none) | Describe the microscope: loaded devices (camera, stages, shutters, filter wheels...), which device has which role, camera size/bit depth/exposure, pixel size, config groups with their presets, the current position, and the soft limits |
| `list_config_groups` | read | (none) | List Micro-Manager config groups (e.g |
| `set_config` | control | group: Annotated[str, Field(description="Config group, e.g. 'Channel'")]; preset: Annotated[str, Field(description="Preset in that group, e.g. 'FITC'")] | Apply a config-group preset (e.g |
| `set_objective` | hazard | preset: Annotated[str, Field(description="Objective preset, e.g. '20x'")]; group: Annotated[str | None, Field(description="Objective config group; default: auto-detected")] = None | Switch the objective (rotates the nosepiece/turret) |
| `get_exposure` | read | (none) | Return the camera exposure time in milliseconds. |
| `set_exposure` | control | exposure_ms: Annotated[float, Field(gt=0, le=60000, description="Camera exposure time in ms")] | Set the camera exposure time (ms) |
| `close_shutter` | safety | (none) | Close the shutter (stop illuminating the sample) and abort any running acquisition. |
| `get_position` | read | (none) | Read the XY stage and focus (Z) positions in micrometres, and which way increasing Z moves the objective. |
| `move_stage_xy` | hazard | x_um: Annotated[float, Field(description="Target X (µm), or the X offset if relative=True")]; y_um: Annotated[float, Field(description="Target Y (µm), or the Y offset if relative=True")]; relative: Annotated[bool, Field(description="Interpret x_um/y_um as offsets from the current position")] = False | Move the XY stage |
| `move_z` | hazard | z_um: Annotated[float, Field(description="Target focus position (µm), or the offset if relative=True")]; relative: Annotated[bool, Field(description="Interpret z_um as an offset from the current position")] = False | Move the focus drive (Z) |
| `stop_stage` | safety | (none) | Immediately stop XY and Z stage motion and abort any z-stack or time-lapse in progress. |
| `autofocus` | hazard | (none) | Run the configured autofocus device (e.g |
| `snap_image` | control | save_path: Annotated[
        str | None, Field(description="TIFF path for the full image; default: a timestamped file in data_dir")
    ] = None; include_preview: Annotated[bool, Field(description="Return a small PNG preview image")] = True; preview_size_px: Annotated[int, Field(ge=64, le=1024, description="Longest side of the preview")] = 384 | Acquire one image with the current channel, exposure and position |
| `acquire_z_stack` | hazard | start_offset_um: Annotated[float, Field(description="First slice relative to the current Z (e.g. -10)")]; end_offset_um: Annotated[float, Field(description="Last slice relative to the current Z (e.g. +10)")]; step_um: Annotated[float, Field(gt=0, le=100, description="Spacing between slices (µm)")]; channels: Annotated[
        list[str] | None, Field(description="Channel presets to image at every slice; omit for current settings")
    ] = None; channel_group: Annotated[str | None, Field(description="Config group of the channels; default: channel group")] = None; save_path: Annotated[str | None, Field(description="Multi-page TIFF path (ImageJ hyperstack, ZCYX)")] = None; include_preview: Annotated[bool, Field(description="Return a max-intensity projection preview")] = True | Acquire a z-stack around the current focus: moves Z through the slices (optionally imaging several channels at each), saves a multi-page TIFF, reports per-slice statistics and the sharpest slice, and returns Z to where it started |
| `acquire_time_lapse` | hazard | timepoints: Annotated[int, Field(ge=1, le=100000, description="Number of timepoints")]; interval_s: Annotated[float, Field(ge=0, le=86400, description="Time between timepoint starts (s)")]; channels: Annotated[
        list[str] | None, Field(description="Channel presets to image at every timepoint; omit for current settings")
    ] = None; channel_group: Annotated[str | None, Field(description="Config group of the channels; default: channel group")] = None; save_path: Annotated[str | None, Field(description="Multi-page TIFF path (ImageJ hyperstack, TCYX)")] = None; include_preview: Annotated[bool, Field(description="Return a preview of the last frame")] = True | Acquire a time-lapse at the current position: images every `interval_s` (optionally several channels per timepoint), saves a multi-page TIFF and reports per-frame statistics and the intensity change (bleaching) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |
| `set_shutter` | hazard | (builtin/core-provided; not a def in server.py) | Open or close the current shutter, and optionally switch auto-shutter |

### New Era Syringe Pump (`labmcp-new-era`, servers/biology/new-era-syringe-pump/src/labmcp_new_era/server.py, 11 catalog tools / 8 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_status` | read | (none) | Report what the pump is doing (infusing/withdrawing/stopped/paused, alarms), the syringe diameter, rate, target volume, direction and the accumulated dispensed volumes. |
| `list_syringe_presets` | read | contains: Annotated[
        str, Field(description="Only list presets whose name contains this text, e.g. 'BD'")
    ] = "" | List the syringe inside diameters from the New Era manual's reference table (BD, Monoject, Terumo, HSW Norm-Ject, Poulten & Graf glass, stainless steel, SGE, Hamilton), with the NE-1000 rate range each allows |
| `set_syringe` | control | preset: Annotated[
        str | None, Field(description="Syringe preset name from `list_syringe_presets`, e.g. 'BD 10 mL'")
    ] = None; diameter_mm: Annotated[
        float | None, Field(ge=0.1, le=50.0, description="Syringe inside diameter in mm (overrides preset)")
    ] = None | Set the syringe inside diameter, either from a preset or a measured value |
| `infuse` | hazard | volume_ml: Annotated[float, Field(gt=0, le=1000, description="Volume to infuse, in mL")]; rate_ml_min: Annotated[float, Field(gt=0, le=1000, description="Infusion rate, in mL/min")] | Push `volume_ml` out of the syringe at `rate_ml_min`, then stop |
| `withdraw` | hazard | volume_ml: Annotated[float, Field(gt=0, le=1000, description="Volume to withdraw, in mL")]; rate_ml_min: Annotated[float, Field(gt=0, le=1000, description="Withdrawal rate, in mL/min")] | Pull `volume_ml` into the syringe at `rate_ml_min`, then stop |
| `get_dispensed_volume` | read | (none) | Return the accumulated infused and withdrawn volumes (pump command DIS). |
| `clear_dispensed_volume` | control | (none) | Reset the infused and withdrawn volume accumulators to zero |
| `stop_pump` | safety | (none) | Stop the pump immediately (STP) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Opentrons OT-2 / Flex (`labmcp-opentrons`, servers/biology/opentrons/src/labmcp_opentrons/server.py, 18 catalog tools / 15 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_robot_status` | read | (none) | Report the robot's name, model (Flex/OT-2), software and firmware versions, lights, door and E-stop state, and the current run |
| `list_instruments` | read | (none) | List attached pipettes (and the Flex gripper): mount, name, channels, volume range, and whether a tip is detected and calibration data exists. |
| `list_modules` | read | (none) | List attached modules (Temperature Module, Heater-Shaker, Thermocycler, Magnetic Module, Absorbance Plate Reader, ...) with live temperatures, targets, shake speed and status. |
| `set_lights` | control | on: Annotated[bool, Field(description="True to turn the deck lights on")] | Turn the robot's deck (rail) lights on or off. |
| `home_robot` | hazard | (none) | Home all axes of the robot (the gantry and pipettes move to their home positions) |
| `deactivate_modules` | safety | module_ids: Annotated[
        list[str] | None, Field(description="Module IDs from list_modules; omit to switch off all modules")
    ] = None | Switch attached modules off: stop heating/cooling (Temperature Module, Thermocycler block and lid, Heater-Shaker heater), stop shaking, and lower Magnetic Module magnets |
| `list_protocols` | read | (none) | List protocols stored on the robot (newest last) with their analysis status and result. |
| `get_protocol` | read | protocol_id: Annotated[str, Field(description="Protocol ID from list_protocols")] | Show a stored protocol's analysis: whether it is ready to run, analysis errors, the deck layout it expects (labware and modules per slot, pipettes per mount, liquids), the number of steps and the highest module temperature / shake speed it requests |
| `upload_protocol` | control | path: Annotated[str, Field(description="Local path of the protocol file (.py Python API or .json)")]; labware_paths: Annotated[
        list[str] | None, Field(description="Custom labware definition .json files used by a Python protocol")
    ] = None; wait_for_analysis_s: Annotated[
        float, Field(ge=0, le=300, description="How long to wait for the robot's analysis to finish")
    ] = 90 | Upload a protocol file (plus optional custom labware) to the robot |
| `list_runs` | read | limit: Annotated[int, Field(ge=1, le=50, description="Maximum runs to list")] = 10 | List recent protocol runs, newest first, with their status. |
| `get_run_status` | read | run_id: Annotated[str | None, Field(description="Run ID; omit for the current run")] = None; recent_commands: Annotated[int, Field(ge=1, le=50, description="How many recent steps to include")] = 5 | Report a run's status, the step it is on, progress, recent steps and any errors, plus advice on what to do next (e.g |
| `start_run` | hazard | protocol_id: Annotated[str, Field(description="Protocol ID from upload_protocol or list_protocols")]; deck_confirmed: Annotated[
        bool,
        Field(description="True only after the user confirmed the deck matches get_protocol's layout "
              "(labware, tips, liquids, modules) and nothing else is in the robot's path"),
    ] | Create a run of an analyzed protocol and start it: the robot begins moving and pipetting |
| `pause_run` | safety | run_id: Annotated[str | None, Field(description="Run ID; omit for the current run")] = None | Pause a running protocol |
| `stop_run` | safety | run_id: Annotated[str | None, Field(description="Run ID; omit for the current run")] = None | Stop (cancel) a run immediately |
| `resume_run` | hazard | run_id: Annotated[str | None, Field(description="Run ID; omit for the current run")] = None; error_recovery: Annotated[
        Literal["continue", "assume_false_positive"] | None,
        Field(description="Only for runs awaiting error recovery, after the user physically checked the robot: "
              "'continue' resumes from the robot's current state (the failed step is skipped); "
              "'assume_false_positive' treats the failure as a false alarm (e.g. the tip really was picked up)"),
    ] = None | Resume a paused run (the robot starts moving again), or leave error recovery |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Tecan Cavro Syringe Pump (`labmcp-cavro`, servers/biology/tecan-cavro-pump/src/labmcp_cavro/server.py, 9 catalog tools / 6 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_status` | read | (none) | Report whether the pump is ready or busy, any error (decoded), the plunger position as the volume in the syringe, the valve position, resolution mode and current top speed. |
| `initialize` | hazard | valve_direction: Annotated[
        Literal["cw", "ccw", "no_valve"],
        Field(
            description="Valve homing/port numbering: cw (Z command), ccw (Y), or no_valve (W, plunger only)"
        ),
    ] = "cw"; force: Annotated[
        Literal["auto", "full", "half", "third"],
        Field(description="Plunger force against the syringe top; auto picks it from the syringe size"),
    ] = "auto"; input_port: Annotated[
        int | None, Field(ge=1, le=12, description="Distribution valves: input port")
    ] = None; output_port: Annotated[
        int | None, Field(ge=1, le=12, description="Distribution valves: output port")
    ] = None | Initialize the pump: drive the plunger to the top of the syringe (expelling its contents through the valve), set that as position 0 and home the valve |
| `set_valve` | hazard | position: Annotated[
        ValvePosition | None, Field(description="Non-distribution valves: input, output, bypass or extra")
    ] = None; port: Annotated[int | None, Field(ge=1, le=12, description="Distribution valves: port number")] = None; direction: Annotated[
        Literal["cw", "ccw"], Field(description="Distribution valves: rotation direction to the port")
    ] = "cw" | Turn the valve to a named position (3/4-port valves) or to a numbered port (distribution valves) |
| `aspirate_ul` | hazard | volume_ul: Annotated[float, Field(gt=0, le=50_000, description="Volume to draw into the syringe, µL")]; flow_ul_s: Annotated[float, Field(gt=0, le=50_000, description="Plunger flow rate, µL/s")] = 100.0; valve: Annotated[
        ValvePosition | None, Field(description="Turn the valve here first (e.g. 'input')")
    ] = None; port: Annotated[
        int | None, Field(ge=1, le=12, description="Distribution valves: port to turn to first")
    ] = None | Draw `volume_ul` into the syringe at `flow_ul_s` through the current (or given) valve port, and wait until the move has finished |
| `dispense_ul` | hazard | volume_ul: Annotated[float, Field(gt=0, le=50_000, description="Volume to push out of the syringe, µL")]; flow_ul_s: Annotated[float, Field(gt=0, le=50_000, description="Plunger flow rate, µL/s")] = 100.0; valve: Annotated[
        ValvePosition | None, Field(description="Turn the valve here first (e.g. 'output')")
    ] = None; port: Annotated[
        int | None, Field(ge=1, le=12, description="Distribution valves: port to turn to first")
    ] = None | Push `volume_ul` out of the syringe at `flow_ul_s` through the current (or given) valve port, and wait until the move has finished |
| `terminate` | safety | (none) | Stop any plunger move, loop or delay immediately (DT command T) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### IKA Hotplate & Overhead Stirrer (`labmcp-ika`, servers/chemistry/ika-stirrer/src/labmcp_ika/server.py, 14 catalog tools / 11 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_status` | read | (none) | Read temperatures, stirring speed and setpoints |
| `set_temperature` | hazard | temperature_c: Annotated[float, Field(ge=0, le=500, description="Temperature setpoint in °C")] | Set the hotplate temperature setpoint (OUT_SP_1) |
| `start_heating` | hazard | (none) | Switch the hotplate heater on (START_1) |
| `stop_heating` | safety | (none) | Switch the hotplate heater off (STOP_1) |
| `set_speed` | hazard | speed_rpm: Annotated[float, Field(ge=0, le=2000, description="Stirring speed setpoint in rpm")] | Set the stirring speed setpoint (OUT_SP_4) |
| `start_stirring` | hazard | (none) | Start the stirring motor (START_4) at the current speed setpoint, which is checked against `max_speed_rpm` first |
| `stop_stirring` | safety | (none) | Stop the stirring motor (STOP_4) |
| `stop_all` | safety | (none) | Emergency stop: switch the heater off (hotplates) and stop the motor |
| `wait_for_temperature` | read | target_c: Annotated[
        float | None, Field(ge=-10, le=500, description="Temperature to wait for; default: the current setpoint")
    ] = None; tolerance_c: Annotated[float, Field(gt=0, le=50, description="Accepted deviation in °C")] = 1.0; sensor: Annotated[
        Literal["hotplate", "external"],
        Field(description="'external' = probe in the medium (IN_PV_1, or IN_PV_3 on overhead stirrers)"),
    ] = "hotplate"; stable_for_s: Annotated[float, Field(ge=0, le=600, description="Must stay within tolerance this long")] = 30; timeout_s: Annotated[float, Field(ge=1, le=3600, description="Give up after this many seconds")] = 600; poll_interval_s: Annotated[float, Field(ge=0.5, le=60, description="Seconds between readings")] = 3.0 | Poll a temperature until it is within `tolerance_c` of the target for `stable_for_s` seconds, or until `timeout_s` passes |
| `enable_watchdog` | control | timeout_s: Annotated[
        int,
        Field(ge=WATCHDOG_MIN_S, le=WATCHDOG_MAX_S, description="Watchdog time (IKA allows 20-1500 s)"),
    ] = 60; mode: Annotated[
        Literal[1, 2],
        Field(description="1 = heater and motor off on timeout; 2 = fall back to the safety values below"),
    ] = 1; safety_temperature_c: Annotated[
        float, Field(ge=0, le=500, description="Mode 2 only: setpoint to fall back to (OUT_SP_12@)")
    ] = 50; safety_speed_rpm: Annotated[
        float, Field(ge=0, le=2000, description="Mode 2 only: speed to fall back to (OUT_SP_42@)")
    ] = 100 | Arm the hotplate's communication watchdog (OUT_WD1@m / OUT_WD2@m) |
| `disable_watchdog` | control | (none) | Cancel watchdog mode 2 (OUT_WD2@0) and stop the background refresh |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### JULABO Circulator (`labmcp-julabo`, servers/chemistry/julabo-circulator/src/labmcp_julabo/server.py, 10 catalog tools / 7 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_temperatures` | read | include_external: Annotated[
        bool, Field(description="Also read the external Pt100 sensor (MAGIO/DYNEO; not CORIO)")
    ] = False | Read the bath temperature, the heating/cooling power in % and the safety-sensor temperature, and optionally the external Pt100 sensor. |
| `get_status` | read | (none) | Report the circulator's status message (decoded: operating state, rejected command or alarm), whether temperature control is running, the setpoint, the device's own excess-temperature protection setting and warning limits, and the firmware version. |
| `get_setpoint` | read | (none) | Read the current temperature setpoint and whether temperature control is running. |
| `set_setpoint` | hazard | temperature_c: Annotated[float, Field(ge=-95, le=400, description="New setpoint in °C")] | Set the circulator's temperature setpoint (out_sp_00) |
| `start_circulation` | hazard | (none) | Start temperature control (out_mode_05 1): the pump runs and the bath heats or cools to the setpoint, which is checked against the safety limits first (it may have been changed on the front panel) |
| `stop_circulation` | safety | (none) | Stop temperature control and the pump (out_mode_05 0), then confirm with in_mode_05 |
| `wait_for_temperature` | read | target_c: Annotated[
        float | None, Field(ge=-95, le=400, description="Temperature to wait for; default: the current setpoint")
    ] = None; tolerance_c: Annotated[float, Field(gt=0, le=20, description="Accepted deviation in °C")] = 0.5; sensor: Annotated[
        Literal["bath", "external"], Field(description="'external' = Pt100 sensor (MAGIO/DYNEO)")
    ] = "bath"; stable_for_s: Annotated[
        float, Field(ge=0, le=1800, description="Must stay within tolerance this long")
    ] = 60; timeout_s: Annotated[float, Field(ge=1, le=7200, description="Give up after this many seconds")] = 1800; poll_interval_s: Annotated[float, Field(ge=0.5, le=120, description="Seconds between readings")] = 5.0 | Poll the bath (or external) temperature until it has been within `tolerance_c` of the target for `stable_for_s` seconds, or `timeout_s` passes |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Mettler Toledo Balance (`labmcp-mettler-toledo`, servers/chemistry/mettler-toledo-balance/src/labmcp_mettler_toledo/server.py, 17 catalog tools / 14 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_weight` | read | stable: Annotated[bool, Field(description="Wait for a stable reading (recommended)")] = True | Read the current net weight from the balance. |
| `log_weight_series` | read | count: Annotated[int, Field(ge=2, le=1000, description="Number of readings")] = 10; interval_s: Annotated[float, Field(ge=0.1, le=600, description="Seconds between readings")] = 1.0 | Record a series of immediate (unfiltered) readings to monitor drift, evaporation, moisture uptake or stabilisation |
| `get_tare` | read | (none) | Return the weight currently stored in the tare memory. |
| `tare` | control | immediately: Annotated[
        bool, Field(description="Tare now even if unstable (less accurate). Default waits for stability.")
    ] = False | Tare the balance: store the current load (e.g |
| `set_tare_preset` | control | value: Annotated[float, Field(ge=0, description="Tare weight to preset")]; unit: Annotated[str, Field(description="Must be the balance's unit 1, usually 'g'")] = "g" | Preset a known tare weight (e.g |
| `clear_tare` | control | (none) | Clear the tare memory (tare = 0). |
| `zero` | control | immediately: Annotated[bool, Field(description="Zero now even if the reading is unstable")] = False | Zero the balance with the current load |
| `run_internal_adjustment` | control | (none) | Adjust (calibrate) the balance with its built-in reference weight (MT-SICS C3) |
| `show_message` | control | text: Annotated[str, Field(max_length=40, description="Text for the balance display; '' clears it")] | Show a short message on the balance display (e.g |
| `show_weight_display` | control | (none) | Switch the balance display back to showing the weight. |
| `get_draft_shield` | read | (none) | Report the position of the motorised draft-shield doors (Excellence/XPR balances only). |
| `set_draft_shield` | hazard | position: Literal["closed", "right_open", "left_open"] | Open or close the motorised draft-shield doors |
| `read_temperature` | read | (none) | Read the balance's internal temperature probe(s) in °C (MT-SICS M28, if supported). |
| `reset_balance` | safety | (none) | Abort whatever the balance is doing (an adjustment, a repeating weight stream, a pending command) and reset it to its power-on state without zeroing (MT-SICS @) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Mass Spectrometry Data (mzML, Bruker TDF, vendor conversion) (`labmcp-ms-data`, servers/chemistry/ms-data/src/labmcp_ms_data/server.py, 12 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `list_runs` | read | subfolder: Annotated[str, Field(description="Folder to search, relative to the data folder")] = "."; recursive: Annotated[bool, Field(description="Also search subfolders (up to 6 levels)")] = True; max_results: Annotated[int, Field(ge=1, le=2000)] = 200 | Find mass-spectrometry runs in the data folder and detect each one's vendor and format (mzML/mzML.gz/mzMLb, Bruker .d TDF or BAF, Agilent .d, Thermo .raw, Waters .raw folder, SCIEX .wiff/.wiff2, Shimadzu .lcd, mzXML) |
| `get_run_info` | read | path: PathArg = None | Describe one run: instrument vendor/model/serial (when the file records them), acquisition date, software, number of spectra per MS level, retention-time and m/z ranges, polarity, centroid/profile, and whether ion-mobility data is present |
| `get_tic` | read | path: PathArg = None; ms_level: MsLevel = 1; rt_start_min: RtStart = None; rt_end_min: RtEnd = None; max_points: MaxPoints = 500; save_path: SavePath = None; overwrite: Overwrite = False | Total ion chromatogram (sum of all intensities per spectrum vs retention time) for one MS level, downsampled to `max_points` (keeping the maximum in each bin so peaks survive) |
| `get_bpc` | read | path: PathArg = None; ms_level: MsLevel = 1; rt_start_min: RtStart = None; rt_end_min: RtEnd = None; max_points: MaxPoints = 500; save_path: SavePath = None; overwrite: Overwrite = False | Base peak chromatogram (intensity of the most intense peak per spectrum, with its m/z) vs retention time, downsampled to `max_points` |
| `extract_ion_chromatogram` | read | mz: Annotated[list[float], Field(min_length=1, description="Target m/z value(s)")]; path: PathArg = None; tolerance: Tolerance = 10.0; tolerance_unit: TolUnit = "ppm"; ms_level: MsLevel = 1; rt_start_min: RtStart = None; rt_end_min: RtEnd = None; max_points: MaxPoints = 300; save_path: SavePath = None; overwrite: Overwrite = False | Extracted ion chromatogram (XIC/EIC) for one or more m/z values: the summed intensity within ± tolerance (ppm or Da) in every MS1 spectrum (or another `ms_level`) |
| `get_spectrum` | read | path: PathArg = None; index: Annotated[int | None, Field(ge=0, description="0-based spectrum index in the file")] = None; scan_number: Annotated[
        int | None, Field(ge=0, description="Native scan number (e.g. Thermo scan=N)")
    ] = None; native_id: Annotated[str | None, Field(description="Exact native spectrum id")] = None; rt_min: Annotated[
        float | None, Field(ge=0, description="Pick the spectrum nearest this RT (min)")
    ] = None; ms_level: Annotated[
        int | None, Field(ge=1, le=10, description="With rt_min: MS level to pick (default 1)")
    ] = None; top_n: Annotated[int, Field(ge=0, le=2000, description="Number of most intense peaks to return")] = 50; mz_min: Annotated[float | None, Field(ge=0, description="Only consider peaks above this m/z")] = None; mz_max: Annotated[float | None, Field(ge=0, description="Only consider peaks below this m/z")] = None; save_path: SavePath = None; overwrite: Overwrite = False | Read one spectrum, chosen by `index`, `scan_number`, `native_id` or nearest `rt_min` (give exactly one) |
| `find_ms2_scans` | read | precursor_mz: Annotated[float, Field(gt=0, description="Precursor m/z to look for")]; path: PathArg = None; tolerance: Tolerance = 10.0; tolerance_unit: TolUnit = "ppm"; rt_start_min: RtStart = None; rt_end_min: RtEnd = None; charge: Annotated[int | None, Field(ge=1, le=100, description="Only this precursor charge")] = None; max_results: Annotated[int, Field(ge=1, le=5000)] = 100 | Find the MS2 (MSn) spectra whose precursor m/z is within ± tolerance of `precursor_mz`, optionally within an RT window and for one charge state |
| `summarise_run` | read | path: PathArg = None | Quick QC of a run: MS1/MS2 counts, TIC stability (CV, spray dropouts), where the signal elutes, cycle time, MS2 scans per cycle, median injection times and how often MS2 hit the maximum injection time, and precursor charge states |
| `convert_to_mzml` | control | path: Annotated[
        str, Field(description="Vendor file or folder (from list_runs), relative to the data folder")
    ]; output_folder: Annotated[
        str | None,
        Field(description="Folder for the .mzML, inside the data folder (default: next to the input)"),
    ] = None; peak_picking: Annotated[
        bool, Field(description="Centroid with the vendor algorithm during conversion")
    ] = True; gzip: Annotated[bool, Field(description="Write .mzML.gz")] = False; overwrite: Annotated[bool, Field(description="Replace an existing output file")] = False; dry_run: Annotated[bool, Field(description="Only show the command that would be run")] = False; timeout_s: Annotated[float, Field(gt=0, le=86400, description="Give up after this many seconds")] = 1800 | Convert a vendor file (Thermo .raw, Waters .raw, Agilent .d, SCIEX .wiff, Shimadzu .lcd, Bruker .d) to mzML with the converter the user installed (ThermoRawFileParser, ProteoWizard msconvert, or msconvert in Docker; chosen with --option converter=...) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### LC-MS Worklist Builder (`labmcp-ms-worklist`, servers/chemistry/ms-worklist/src/labmcp_ms_worklist/server.py, 12 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `list_formats` | read | (none) | List the supported import formats (Waters MassLynx, SCIEX OS, Agilent MassHunter, Thermo Xcalibur): columns, required fields, sample-type names, file extensions, how to import, what is verified against vendor documents and what is assumed, and the source URLs |
| `create_worklist` | control | name: Annotated[str, Field(description="Draft name (letters, digits, _ - .); used in file names")]; format: Annotated[FormatName, Field(description="Target vendor format (see list_formats)")]; samples: SamplesArg; ms_method: Annotated[str, Field(description="Default MS/acquisition/instrument method")] = ""; lc_method: Annotated[str, Field(description="Default LC/inlet method (Waters INLET_FILE, SCIEX LC Method)")] = ""; tune_file: Annotated[str, Field(description="Default tune file (Waters MS_TUNE_FILE)")] = ""; processing_method: Annotated[str, Field(description="Default processing method (SCIEX OS, Xcalibur)")] = ""; data_path: Annotated[str, Field(description="Data folder (Xcalibur Path column)")] = ""; injection_volume_ul: VolumeArg = None; data_file_pattern: Annotated[
        str, Field(description="Data file naming pattern; placeholders {index} {sample_name} {sample_id} {type} "
                   "{worklist} {date} {position}; illegal characters become '_'")
    ] = "{worklist}_{index:03d}_{sample_name}"; position_pattern: Annotated[PositionPattern, Field(description="Autosampler position format to enforce")] = "any"; plate_size: Annotated[Literal[24, 48, 54, 96, 384], Field(description="Positions per plate/tray")] = 96; max_vial: Annotated[int, Field(ge=1, le=10000, description="Highest vial number (vial_number pattern)")] = 120; first_position: Annotated[
        str, Field(description="If set, samples without a position get sequential positions from here, e.g. 'A1'")
    ] = ""; vendor_columns: Annotated[
        dict[str, str] | None,
        Field(description="Constant vendor columns for every row, e.g. {'Rack Type': '...', 'Plate Type': '...'}"),
    ] = None; bracket_type: Annotated[
        Literal[1, 2, 3, 4], Field(description="Xcalibur only: 1 Overlapped, 2 None, 3 Non-Overlapped, 4 Open")
    ] = 4; replace: Annotated[bool, Field(description="Replace an existing draft with the same name")] = False | Create an in-memory draft worklist from a sample list plus defaults (methods, injection volume, tray positions, data-file naming pattern) |
| `add_samples` | control | name: Annotated[str, Field(description="Draft worklist name")]; samples: SamplesArg | Append samples to a draft worklist (the worklist's defaults and automatic positions apply; any blank/QC/randomisation plan is re-applied to the longer list with the same seed). |
| `insert_qc_blanks` | control | name: Annotated[str, Field(description="Draft worklist name")]; blank_every_n: Annotated[int | None, Field(ge=1, le=1000, description="Insert a blank after every N samples")] = None; blank_at_start: bool = False; blank_at_end: bool = False; blank_position: Annotated[str, Field(description="Tray position of the blank vial")] = ""; blank_name: str = "Blank"; blank_ms_method: Annotated[str, Field(description="Method for blanks (default: the worklist's ms_method)")] = ""; blank_injection_volume_ul: VolumeArg = None; qc_at_start: Annotated[int, Field(ge=0, le=50, description="Number of QC injections at the start")] = 0; qc_at_end: Annotated[int, Field(ge=0, le=50, description="Number of QC injections at the end")] = 0; qc_every_n: Annotated[int | None, Field(ge=1, le=1000, description="Insert a QC after every N samples")] = None; qc_position: Annotated[str, Field(description="Tray position of the pooled QC vial")] = ""; qc_name: str = "QC"; qc_injection_volume_ul: VolumeArg = None; randomize: Annotated[bool, Field(description="Randomise the run order of the samples first")] = False; seed: Annotated[
        int | None, Field(ge=0, le=2**31 - 1, description="Random seed (a new one is drawn and recorded if omitted)")
    ] = None; randomize_types: Annotated[
        list[Literal["sample", "blank", "qc", "standard", "solvent", "double_blank"]],
        Field(description="Which sample types are shuffled among their own slots (standards stay put by default)"),
    ] = ["sample"]; # noqa: B006 - pydantic copies defaults | Insert blanks and QC injections and optionally randomise the run order (with a recorded seed) |
| `get_worklist` | read | name: Annotated[str, Field(description="Draft worklist name")]; max_rows: Annotated[int, Field(ge=1, le=2000)] = 200 | Show a draft worklist in run order, with its defaults, blank/QC plan, randomisation seed and the history of operations applied to it. |
| `list_worklists` | read | (none) | List the draft worklists in memory and the files in the output folder. |
| `validate_worklist` | read | name: Annotated[str, Field(description="Draft worklist name")]; format: Annotated[FormatName | None, Field(description="Validate for another vendor format (default: the draft's)")] = None | Check a draft against the target format: required fields, duplicate data-file names, characters Windows does not allow in file names, tray/vial position format and plate bounds, injection volume (> 0 and <= the max_injection_volume_ul limit), and method file extensions |
| `export_worklist` | control | name: Annotated[str, Field(description="Draft worklist name")]; format: Annotated[FormatName | None, Field(description="Vendor format (default: the draft's); use another to convert")] = None; filename: Annotated[
        str | None, Field(description="File name inside the output folder (default '<name>_<format>.csv')")
    ] = None; overwrite: Annotated[bool, Field(description="Replace an existing file of the same name")] = False; template_path: Annotated[
        str | None,
        Field(description="A blank batch/sequence/worklist exported from the vendor software, in the output folder; "
              "its header (and delimiter) is used"),
    ] = None; template_columns: Annotated[list[str] | None, Field(description="Header columns to use instead of a template file")] = None; delimiter: Annotated[
        Literal[",", ";", "\t"] | None, Field(description="Field separator (Xcalibur must match the Windows list separator)")
    ] = None; bracket_type: Annotated[Literal[1, 2, 3, 4] | None, Field(description="Xcalibur bracket type override")] = None; utf8_bom: Annotated[bool | None, Field(description="Force a UTF-8 BOM on/off (default per format)")] = None; write_provenance: Annotated[
        bool, Field(description="Also write <file>.provenance.json (history, seed, original sample order)")
    ] = True | Validate the draft and write the vendor import file into the output folder |
| `import_worklist` | read | path: Annotated[str, Field(description="File in the output folder (relative path)")]; format: Annotated[FormatName | None, Field(description="Format; detected from the header if omitted")] = None; name: Annotated[str | None, Field(description="Draft name (default: the file name)")] = None; replace: bool = False | Parse an existing MassLynx, SCIEX OS, MassHunter or Xcalibur import file from the output folder into a draft, so it can be validated, edited or exported in another vendor's format |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Ocean Insight Spectrometer (`labmcp-ocean-spectrometer`, servers/chemistry/ocean-spectrometer/src/labmcp_ocean_spectrometer/server.py, 16 catalog tools / 13 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `list_spectrometers` | read | (none) | List the Ocean spectrometers seabreeze can see (model, serial number, whether open) and which one this server is connected to |
| `get_device_info` | read | (none) | Model, serial, pixel count, wavelength range, integration-time limits, saturation level, supported corrections, TEC presence, and which dark/reference spectra are stored. |
| `set_integration_time` | control | integration_time_ms: Annotated[float, Field(gt=0, le=3_600_000, description="Integration time in ms")] | Set the detector integration time (ms), within the device limits from `get_device_info` |
| `acquire_spectrum` | read | scans_to_average: Scans = 1; boxcar_half_width: Boxcar = 0; correct_dark_counts: Annotated[
        bool, Field(description="Subtract the mean of the optically masked (electric dark) pixels")
    ] = False; correct_nonlinearity: Annotated[
        bool, Field(description="Apply the EEPROM nonlinearity correction")
    ] = False; subtract_stored_dark: Annotated[
        bool, Field(description="Subtract the stored dark spectrum (same settings required)")
    ] = False; wavelength_min_nm: WlMin = None; wavelength_max_nm: WlMax = None; max_points: MaxPoints = 500; num_peaks: NumPeaks = 5; save_path: SavePath = None | Acquire an intensity spectrum (raw counts) with optional averaging, boxcar smoothing and corrections |
| `store_dark_reference` | control | scans_to_average: Scans = 10; boxcar_half_width: Boxcar = 0; correct_dark_counts: Annotated[
        bool, Field(description="Electric-dark correction (use the same later)")
    ] = False; correct_nonlinearity: Annotated[
        bool, Field(description="Nonlinearity correction (use the same later)")
    ] = False | Record and store a DARK spectrum (kept in memory) for absorbance/transmittance |
| `store_reference` | control | scans_to_average: Scans = 10; boxcar_half_width: Boxcar = 0; correct_dark_counts: Annotated[bool, Field(description="Must match the stored dark")] = False; correct_nonlinearity: Annotated[bool, Field(description="Must match the stored dark")] = False | Record and store the REFERENCE (100 % transmission) spectrum in memory |
| `measure_absorbance` | read | wavelengths_nm: RatioWavelengths = None; scans_to_average: Annotated[
        int | None, Field(ge=1, le=1000, description="Spectra averaged (default: as the reference)")
    ] = None; wavelength_min_nm: WlMin = None; wavelength_max_nm: WlMax = None; max_points: MaxPoints = 500; num_peaks: NumPeaks = 5; save_path: SavePath = None | Measure the absorbance spectrum A = -log10((S - D) / (R - D)) of the sample now in the beam, using the stored dark D and reference R (same settings) |
| `measure_transmittance` | read | wavelengths_nm: RatioWavelengths = None; scans_to_average: Annotated[
        int | None, Field(ge=1, le=1000, description="Spectra averaged (default: as the reference)")
    ] = None; wavelength_min_nm: WlMin = None; wavelength_max_nm: WlMax = None; max_points: MaxPoints = 500; num_peaks: NumPeaks = 5; save_path: SavePath = None | Measure the transmittance spectrum %T = 100 (S - D) / (R - D) of the sample now in the beam, using the stored dark and reference |
| `find_peaks` | read | max_peaks: Annotated[int, Field(ge=1, le=100, description="Maximum number of peaks")] = 10; min_prominence_fraction: Annotated[
        float, Field(ge=0, le=1, description="Minimum prominence as a fraction of the data range")
    ] = 0.05; min_separation_nm: Annotated[
        float, Field(ge=0, le=500, description="Minimum distance between peaks, nm")
    ] = 1.0; mode: Annotated[
        Literal["maxima", "minima"], Field(description="Peaks (maxima) or dips (minima)")
    ] = "maxima"; wavelength_min_nm: WlMin = None; wavelength_max_nm: WlMax = None | Find peaks (or dips) with position, height, prominence and FWHM in the most recent spectrum (intensity, absorbance or transmittance) |
| `auto_integration_time` | control | target_min_percent: Annotated[
        float, Field(ge=10, le=95, description="Lower edge of the target band, % of saturation")
    ] = 70; target_max_percent: Annotated[
        float, Field(ge=15, le=97, description="Upper edge of the target band, % of saturation")
    ] = 85; max_iterations: Annotated[int, Field(ge=1, le=20, description="Maximum adjustment steps")] = 8; wavelength_min_nm: WlMin = None; wavelength_max_nm: WlMax = None | Adjust the integration time until the brightest raw pixel (optionally within a wavelength window) is within the target band of saturation (default 70-85 %) |
| `read_detector_temperature` | read | (none) | Read the detector temperature from the thermo-electric cooler (TE-cooled models such as the QE Pro, when seabreeze exposes the thermo_electric feature for them). |
| `set_detector_cooling` | hazard | setpoint_c: Annotated[float, Field(ge=-40, le=30, description="Detector TEC setpoint, °C")] | Enable the detector thermo-electric cooler at `setpoint_c` (TE-cooled models only) |
| `detector_cooling_off` | safety | (none) | Switch the detector thermo-electric cooler off (the detector warms to ambient) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### PalmSens Potentiostat (`labmcp-palmsens`, servers/chemistry/palmsens-potentiostat/src/labmcp_palmsens/server.py, 10 catalog tools / 7 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_device_info` | read | (none) | Identify the potentiostat (model, firmware, serial number) and list its potential window, PGStat modes and current ranges, so measurement parameters can be chosen within them. |
| `run_cyclic_voltammetry` | hazard | begin_potential_v: Annotated[
        float, Field(ge=-10, le=10, description="Start (and end) potential, V vs RE")
    ]; vertex1_potential_v: Annotated[float, Field(ge=-10, le=10, description="First vertex, V vs RE")]; vertex2_potential_v: Annotated[float, Field(ge=-10, le=10, description="Second vertex, V vs RE")]; step_potential_v: Annotated[float, Field(ge=1e-4, le=0.25, description="Potential step, V")] = 0.01; scan_rate_v_s: Annotated[float, Field(gt=0, le=5, description="Scan rate, V/s")] = 0.1; n_scans: Annotated[int, Field(ge=1, le=100, description="Number of cycles")] = 1; current_range_a: CurrentRange = 1e-4; autorange: Autorange = True; equilibration_s: Equilibration = 0.0; max_points: MaxPoints = 500; save_path: SavePath = None | Run a cyclic voltammogram: begin -> vertex 1 -> vertex 2 -> begin, `n_scans` times, and return potential/current/time data with the anodic and cathodic peaks of every scan |
| `run_linear_sweep_voltammetry` | hazard | begin_potential_v: Annotated[float, Field(ge=-10, le=10, description="Start potential, V vs RE")]; end_potential_v: Annotated[float, Field(ge=-10, le=10, description="End potential, V vs RE")]; step_potential_v: Annotated[float, Field(ge=1e-4, le=0.25, description="Potential step, V")] = 0.01; scan_rate_v_s: Annotated[float, Field(gt=0, le=5, description="Scan rate, V/s")] = 0.1; current_range_a: CurrentRange = 1e-4; autorange: Autorange = True; equilibration_s: Equilibration = 0.0; max_points: MaxPoints = 500; save_path: SavePath = None | Run a linear sweep voltammogram from `begin_potential_v` to `end_potential_v` and return the data with the largest/smallest current and where they occur |
| `run_differential_pulse_voltammetry` | hazard | begin_potential_v: Annotated[float, Field(ge=-10, le=10, description="Start potential, V vs RE")]; end_potential_v: Annotated[float, Field(ge=-10, le=10, description="End potential, V vs RE")]; step_potential_v: Annotated[float, Field(ge=1e-4, le=0.25, description="Potential step, V")] = 0.005; pulse_potential_v: Annotated[
        float, Field(gt=0, le=0.25, description="Pulse amplitude, V (absolute)")
    ] = 0.025; pulse_time_s: Annotated[float, Field(ge=0.001, le=1, description="Pulse duration, s")] = 0.05; scan_rate_v_s: Annotated[float, Field(gt=0, le=1, description="Scan rate, V/s")] = 0.025; current_range_a: CurrentRange = 1e-4; autorange: Autorange = True; equilibration_s: Equilibration = 0.0; max_points: MaxPoints = 500; save_path: SavePath = None | Run differential pulse voltammetry (current = forward - reverse, per step) from begin to end potential and return the data with the peak current and peak potential |
| `run_chronoamperometry` | hazard | potential_v: Annotated[float, Field(ge=-10, le=10, description="Applied DC potential, V vs RE")]; run_time_s: Annotated[
        float, Field(gt=0, le=HARD_MAX_DURATION_S, description="Total measurement time, s")
    ]; interval_s: Annotated[float, Field(ge=0.001, le=60, description="Time between points, s")] = 0.1; current_range_a: CurrentRange = 1e-4; autorange: Autorange = True; equilibration_s: Equilibration = 0.0; max_points: MaxPoints = 500; save_path: SavePath = None | Hold the cell at `potential_v` for `run_time_s` and record the current every `interval_s` |
| `run_methodscript` | hazard | script: Annotated[
        str, Field(description="MethodSCRIPT, one command per line (a leading 'e' line is ignored)")
    ]; timeout_s: Annotated[
        float, Field(gt=0, le=HARD_MAX_DURATION_S, description="Abort the script after this")
    ] = 60; max_lines: Annotated[
        int, Field(ge=1, le=10_000, description="Maximum output lines/packages to return")
    ] = 200 | Advanced: run a raw MethodSCRIPT and return its raw output and decoded data packages |
| `abort_measurement` | safety | (none) | Abort the running measurement or script immediately (communication command Z) and make sure the cell is switched off |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Sartorius Balance (`labmcp-sartorius`, servers/chemistry/sartorius-balance/src/labmcp_sartorius/server.py, 10 catalog tools / 7 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_weight` | read | stable: Annotated[bool, Field(description="Wait for a stable reading (recommended)")] = True; timeout_s: Annotated[float, Field(ge=1, le=60, description="Max. seconds to wait for stability")] = 20 | Read the current net weight from the balance (ESC P) |
| `log_weight_series` | read | count: Annotated[int, Field(ge=2, le=1000, description="Number of readings")] = 10; interval_s: Annotated[float, Field(ge=0.5, le=600, description="Seconds between readings")] = 1.0 | Record a series of immediate (unfiltered) readings to monitor drift, evaporation, moisture uptake or stabilisation |
| `tare` | control | timeout_s: Annotated[float, Field(ge=1, le=60, description="Max. seconds to wait for the tared reading")] = 10 | Tare the balance (ESC U, or ESC T on legacy balances): the current load (e.g |
| `zero` | control | timeout_s: Annotated[float, Field(ge=1, le=60, description="Max. seconds to wait for the zeroed reading")] = 10 | Zero the balance (ESC V) |
| `run_internal_adjustment` | control | timeout_s: Annotated[float, Field(ge=30, le=300, description="Max. seconds for the adjustment")] = 240 | Adjust (calibrate) the balance with its built-in weight (ESC Z; isoCAL models only) |
| `set_ambient_conditions` | control | conditions: Literal["very_stable", "stable", "unstable", "very_unstable"] | Adapt the balance's filter to the ambient conditions (ESC K/L/M/N) |
| `lock_keypad` | control | locked: Annotated[bool, Field(description="True blocks the balance keys (ESC O), False unblocks (ESC R)")] | Block or unblock the balance keys, e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Thermo Orbitrap (Instrument API) (`labmcp-thermo-iapi`, servers/chemistry/thermo-iapi/src/labmcp_thermo_iapi/server.py, 15 catalog tools / 12 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_instrument_status` | read | (none) | Report the instrument model, IAPI service/instrument connection, system mode and state (On/Standby/Off; Running/ReadyToDownload/...), whether an acquisition can be paused or resumed, the IAPI licence where the API exposes it, requested readbacks, the latest scan status log (vacuum, source) and how many scans have arrived. |
| `get_possible_scan_parameters` | read | name_contains: Annotated[
        str | None, Field(max_length=50, description="Case-insensitive name filter")
    ] = None; refresh: Annotated[bool, Field(description="Re-read from the instrument instead of the cache")] = False | List the scan parameters this instrument accepts for custom and repeating scans (IScans.PossibleParameters): name, allowed range or choices, default and help |
| `get_recent_scans` | read | count: Annotated[int, Field(ge=1, le=100, description="Number of most recent matching scans")] = 10; ms_order: MsOrder = None; access_id: AccessId = None; max_centroids: MaxCentroids = 20; include_header_trailer: Annotated[bool, Field(description="Include the raw header and trailer")] = False; save_path: Annotated[
        str | None,
        Field(
            description="Optional .csv path (must not exist yet): write all kept centroids of the returned scans"
        ),
    ] = None | Return the most recent scans received from the instrument (oldest first), optionally only one MS order or one custom scan's access id: scan number, MS order, precursor m/z, AGC target, injection time and the most intense centroids |
| `wait_for_scan` | read | timeout_s: Annotated[float, Field(ge=0.1, le=300, description="Longest time to wait")] = 10.0; ms_order: MsOrder = None; access_id: AccessId = None; max_centroids: MaxCentroids = 20; include_header_trailer: Annotated[bool, Field(description="Include the raw header and trailer")] = False | Wait for the next scan that arrives after this call (optionally of one MS order, or the result of a custom scan by its access id) and return it |
| `start_acquisition` | hazard | mode: Annotated[
        Literal["duration", "scan_count", "until_stopped"],
        Field(description="Stop after duration_s, after scan_count scans, or only when stopped"),
    ]; raw_file_path: Annotated[
        str | None,
        Field(max_length=260, description="Raw file to write on the instrument PC, e.g. D:\\Data\\run1.raw"),
    ] = None; duration_s: Annotated[float | None, Field(ge=1, le=86400, description="For mode=duration")] = None; scan_count: Annotated[int | None, Field(ge=1, le=10_000_000, description="For mode=scan_count")] = None; sample_name: Annotated[str | None, Field(max_length=100)] = None; comment: Annotated[str | None, Field(max_length=200)] = None | Start an acquisition with the instrument's current settings (IAPI StartAcquisition), recording to a raw file |
| `pause_acquisition` | safety | (none) | Pause the running acquisition (IAPI Pause) |
| `resume_acquisition` | hazard | (none) | Resume a paused acquisition (IAPI Resume) |
| `stop_acquisition` | safety | cancel_scans: Annotated[
        bool, Field(description="Also cancel pending custom scans and the repeating scan")
    ] = True; standby: Annotated[bool, Field(description="Then put the instrument in Standby")] = False | Stop the running acquisition (IAPI CancelAcquisition), by default also cancelling custom and repeating scans, and optionally switch the instrument to Standby (switch back to On in Tune) |
| `cancel_custom_scans` | safety | (none) | Cancel any pending custom scan and its processing delay (IAPI CancelCustomScan). |
| `cancel_repeating_scan` | safety | (none) | Cancel the repeating scan set with set_repeating_scan (IAPI CancelRepetition). |
| `submit_custom_scan` | hazard | scan_type: ScanType = None; analyzer: Analyzer = None; first_mass_mz: FirstMass = None; last_mass_mz: LastMass = None; orbitrap_resolution: Resolution = None; agc_target: AgcTarget = None; max_injection_time_ms: MaxIT = None; polarity: Polarity = None; microscans: Microscans = None; precursor_mz: PrecursorMz = None; isolation_width_mz: IsolationWidth = None; activation_type: Activation = None; collision_energy: CollisionEnergy = None; scan_description: Description = None; extra_parameters: Extra = None; running_number: RunningNumber = None; single_processing_delay_s: Annotated[
        float,
        Field(
            ge=0,
            le=600,
            description="Hold further custom scans this long (ICustomScan.SingleProcessingDelay)",
        ),
    ] = 0.0 | Place one custom scan to run next (IAPI CreateCustomScan/SetCustomScan); unset values fall back to the instrument's defaults |
| `set_repeating_scan` | hazard | scan_type: ScanType = None; analyzer: Analyzer = None; first_mass_mz: FirstMass = None; last_mass_mz: LastMass = None; orbitrap_resolution: Resolution = None; agc_target: AgcTarget = None; max_injection_time_ms: MaxIT = None; polarity: Polarity = None; microscans: Microscans = None; precursor_mz: PrecursorMz = None; isolation_width_mz: IsolationWidth = None; activation_type: Activation = None; collision_energy: CollisionEnergy = None; scan_description: Description = None; extra_parameters: Extra = None; running_number: RunningNumber = None | Define or replace the scan the instrument repeats when no method or custom scan is running (IAPI CreateRepeatingScan/SetRepetitionScan) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Alicat Mass Flow & Pressure Controller (`labmcp-alicat`, servers/engineering/alicat-flow-controller/src/labmcp_alicat/server.py, 14 catalog tools / 11 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_flow` | read | (none) | Read the live data frame: mass flow, volumetric flow, pressure, temperature, setpoint (controllers), active gas and any status codes, each with the device's engineering units. |
| `get_device_info` | read | (none) | Report model, serial number, firmware, calibration date, whether the device is a controller, the units of every data-frame field, the setpoint full scale and setpoint source. |
| `list_gases` | read | (none) | List the gases installed on this mass flow device (number and short name), as used by `set_gas` |
| `log_flow_series` | read | count: Annotated[int, Field(ge=2, le=1000, description="Number of readings")] = 20; interval_s: Annotated[float, Field(ge=0.05, le=600, description="Seconds between readings")] = 1.0; save_path: Annotated[
        str | None, Field(description="Optional new .csv file for the full series (an existing file is not overwritten)")
    ] = None | Record a time series of data frames (e.g |
| `set_flow_setpoint` | hazard | setpoint: Annotated[
        float,
        Field(ge=-1e6, le=1e6, description="New setpoint in the controller's setpoint units (see get_device_info)"),
    ] | Change the controller setpoint: starts, changes or stops gas flow (or sets the target pressure on a pressure controller) |
| `set_gas` | control | gas: Annotated[
        str,
        Field(description="Gas short name or number, e.g. 'N2' (8), 'Air' (0), 'O2' (11), 'CO2' (4), "
                          "'Ar' (1), 'He' (7), 'H2' (6), 'CH4' (2); COMPOSER mixes are 236-255"),
    ]; save_as_power_up: Annotated[bool, Field(description="Also make it the power-up gas (10v05+)")] = False | Select the gas calibration the mass flow device uses (Gas Select) |
| `tare_flow` | control | (none) | Tare (zero) the flow reading |
| `tare_pressure` | control | kind: Annotated[
        Literal["gauge", "absolute"],
        Field(description="'gauge' zeroes a gauge/differential sensor; 'absolute' aligns the absolute "
                          "sensor to the built-in barometer (barometer option required)"),
    ] = "gauge" | Tare a pressure reading |
| `hold_valve` | control | (none) | Freeze the controller's valve(s) at their current position (HLD): closed-loop control stops, so flow will drift if upstream pressure changes |
| `resume_control` | hazard | (none) | Cancel any valve hold and resume closed-loop control to the current setpoint |
| `close_valve` | safety | (none) | Stop the flow: set the flow setpoint to 0 and hold all valves closed (Alicat HC) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Bench DC Power Supply (`labmcp-bench-psu`, servers/engineering/bench-power-supply/src/labmcp_bench_psu/server.py, 12 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_outputs` | read | channel: Annotated[int | None, Field(ge=1, le=4, description="One channel, or omit for all")] = None | Report each channel: voltage and current-limit setpoints, measured voltage/current/power, output on/off, CV/CC mode, and OVP/OCP levels and trip state where the model supports them. |
| `set_voltage` | control | channel: Channel; voltage_v: Annotated[float, Field(ge=-150, le=150, description="Voltage setpoint in V (negative only on negative channels)")] | Set a channel's voltage setpoint |
| `set_current_limit` | control | channel: Channel; current_a: Annotated[float, Field(ge=0, le=50, description="Current-limit setpoint in A")] | Set a channel's current limit (the constant-current setpoint) |
| `output_on` | hazard | channel: Channel | Switch a channel's output ON, energising the connected load at the present setpoints |
| `output_off` | safety | channel: Channel | Switch a channel's output OFF |
| `all_outputs_off` | safety | (none) | Switch every output OFF (emergency stop for the whole supply) |
| `set_protection` | control | channel: Channel; ovp_v: Annotated[float | None, Field(ge=0, le=150, description="Over-voltage trip level in V")] = None; ocp_a: Annotated[float | None, Field(ge=0, le=50, description="Over-current trip level in A")] = None; enabled: Annotated[bool | None, Field(description="Switch OVP/OCP on or off (models that allow it)")] = True | Set over-voltage (OVP) and/or over-current (OCP) protection for a channel |
| `clear_protection_trip` | control | channel: Channel | Clear an OVP/OCP trip after fixing its cause |
| `get_errors` | read | (none) | Read (and clear) the instrument's error queue / error registers |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### LabJack T-series DAQ (`labmcp-labjack`, servers/engineering/labjack/src/labmcp_labjack/server.py, 12 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_device_info` | read | (none) | Identify the connected LabJack (model, serial, firmware, connection) and list what it offers: analog inputs, valid input ranges, resolution indices, DAC range, digital lines and stream rate |
| `read_analog_inputs` | read | channels: Annotated[list[int], Field(min_length=1, max_length=14, description="AIN numbers, e.g. [0, 1, 2]")]; range_v: Annotated[
        float | None,
        Field(gt=0, le=11, description="± input range in V for all listed channels (T7: 10/1/0.1/0.01; T8: 11 ... 0.018). Omit to keep the current range. Not settable on the T4."),
    ] = None; resolution_index: Annotated[
        int | None,
        Field(ge=0, le=16, description="Higher = less noise, slower (T4 0-5, T7 0-8, T7-Pro 0-12, T8 0-16; 0 = default). Omit to keep."),
    ] = None; differential: Annotated[
        bool | None,
        Field(description="T7 only: true = measure AINn - AIN(n+1) for even n (e.g. AIN0-AIN1), false = single-ended, omit = keep the current setting"),
    ] = None | Read one or more analog inputs once (command-response) and return volts. |
| `read_digital_inputs` | read | lines: Annotated[
        list[str] | None,
        Field(description="Lines to report, e.g. ['FIO0', 'EIO3'] or ['DIO4']. Omit for all lines."),
    ] = None | Report the logic level and direction (input/output/analog) of digital I/O lines. |
| `read_device_temperature` | read | (none) | Read the LabJack's internal temperature sensor (°C, about ±2 °C) and the estimated ambient air temperature |
| `read_thermocouple` | read | channel: Annotated[int, Field(ge=0, le=13, description="AIN the thermocouple + lead is on")]; thermocouple_type: Literal["B", "C", "E", "J", "K", "N", "R", "S", "T"] = "K"; cjc: Annotated[
        Literal["internal", "lm34"],
        Field(description="Cold-junction sensor: 'internal' (device sensor; best for T7 screw terminals / T8) or 'lm34' (LM34 on cjc_channel, e.g. on a CB37)"),
    ] = "internal"; cjc_channel: Annotated[int | None, Field(ge=0, le=13, description="AIN of the LM34 when cjc='lm34'")] = None; differential: Annotated[
        bool, Field(description="T7 only: thermocouple between AINn (+) and AIN(n+1) (-); recommended on the CB37")
    ] = False; resolution_index: Annotated[int | None, Field(ge=0, le=16, description="Omit for the device default")] = None | Read a thermocouple with the T7/T8 AIN thermocouple extended feature (types B, C, E, J, K, N, R, S, T) and return °C with the measured voltage and cold-junction temperature. |
| `stream_analog` | read | channels: Annotated[list[int], Field(min_length=1, max_length=14, description="AIN numbers, e.g. [0, 1]")]; scan_rate_hz: Annotated[
        float,
        Field(ge=1, le=100_000, description="Scans per second (one sample of every channel per scan). At least 1, so "
              "set_outputs_safe can stop the stream within a second; use read_analog_inputs for slower logging"),
    ] = 1000.0; duration_s: Annotated[float, Field(gt=0, le=600, description="Acquisition time in seconds")] = 1.0; range_v: Annotated[float | None, Field(gt=0, le=11, description="± range in V for all channels (T7/T8); omit to keep")] = None; resolution_index: Annotated[int | None, Field(ge=0, le=16, description="Stream resolution index; omit for default")] = None; max_points: Annotated[int, Field(ge=10, le=20_000, description="Max points per channel in the returned waveform")] = 500; save_path: Annotated[
        str | None, Field(description="Write the full-resolution data to this new .csv file (never overwritten)")
    ] = None | Acquire a hardware-timed waveform on one or more analog inputs (LJM stream mode) and return per-channel statistics plus a downsampled waveform; optionally save everything to CSV. |
| `write_dac` | hazard | dac: Annotated[int, Field(ge=0, le=1, description="0 for DAC0, 1 for DAC1")]; voltage_v: Annotated[float, Field(ge=0, le=10, description="Output voltage (T4/T7: 0-5 V, T8: 0-10 V)")] | Set an analog output (DAC0/DAC1) to a DC voltage |
| `set_digital_output` | hazard | line: Annotated[str, Field(description="Digital line, e.g. 'FIO0', 'EIO2', 'CIO1' or 'DIO5'")]; level: Literal["high", "low"] | Make a digital line an output and drive it high (3.3 V) or low (0 V). |
| `set_outputs_safe` | safety | dio_mode: Annotated[
        Literal["input", "low"],
        Field(description="'input' (default) returns lines to the power-up state (input with pull-up); 'low' drives them to 0 V instead"),
    ] = "input" | Put the outputs in a safe state: stop any stream, set DAC0 and DAC1 to 0 V, and release every digital line this server drove (plus the `safe_dio` option lines) to input - or drive them low with dio_mode='low' |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### NI-DAQmx DAQ Devices (`labmcp-ni-daqmx`, servers/engineering/ni-daqmx/src/labmcp_ni_daqmx/server.py, 11 catalog tools / 8 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `list_devices` | read | (none) | List every NI-DAQmx device the driver can see (product type, serial number, channels, whether it is an NI MAX simulated device) and which one this server is connected to. |
| `get_device_info` | read | (none) | Describe the connected device: analog input/output channels, digital lines, supported voltage ranges, maximum sample rates, and the outputs this server has set |
| `read_analog` | read | channels: Annotated[str, Field(description="Analog inputs, e.g. 'ai0', 'ai0:3' or 'Dev1/ai0, Dev1/ai4'")]; terminal_config: Annotated[
        Literal["default", "rse", "nrse", "diff", "pseudo_diff"],
        Field(description="Input wiring: rse = single-ended to AI GND, diff = differential, nrse = to AI SENSE"),
    ] = "default"; min_v: Annotated[float, Field(ge=-100, le=100, description="Lowest expected voltage (sets the input range)")] = -10.0; max_v: Annotated[float, Field(ge=-100, le=100, description="Highest expected voltage (sets the input range)")] = 10.0; samples: Annotated[int, Field(ge=1, le=10_000_000, description="Samples per channel; 1 = one on-demand reading")] = 1; rate_hz: Annotated[float | None, Field(gt=0, le=10_000_000, description="Sample clock rate per channel (required if samples > 1)")] = None; max_points: Annotated[int, Field(ge=10, le=20_000, description="Max points per channel in the returned waveform")] = 500; save_path: Annotated[str | None, Field(description="Write all samples to this new .csv file (never overwritten)")] = None | Measure voltage on one or more analog inputs: a single on-demand reading, or a finite hardware-timed acquisition of `samples` per channel at `rate_hz` |
| `read_thermocouple` | read | channels: Annotated[str, Field(description="Thermocouple inputs, e.g. 'ai0' or 'ai0:3' (on a thermocouple-capable device/module)")]; thermocouple_type: Literal["B", "E", "J", "K", "N", "R", "S", "T"] = "K"; cjc_source: Annotated[
        Literal["built_in", "constant"],
        Field(description="'built_in' = the module's cold-junction sensor (e.g. NI 9211/9213, USB-TC01); 'constant' = cjc_value_c"),
    ] = "built_in"; cjc_value_c: Annotated[float, Field(ge=-50, le=150, description="Cold-junction temperature in °C when cjc_source='constant'")] = 25.0; min_c: Annotated[float, Field(ge=-270, le=1820, description="Lowest expected temperature, °C")] = 0.0; max_c: Annotated[float, Field(ge=-270, le=1820, description="Highest expected temperature, °C")] = 100.0; samples: Annotated[int, Field(ge=1, le=1_000_000, description="Samples per channel; 1 = one reading")] = 1; rate_hz: Annotated[float | None, Field(gt=0, le=100_000, description="Sample rate if samples > 1")] = None; max_points: Annotated[int, Field(ge=10, le=20_000)] = 500; save_path: Annotated[str | None, Field(description="Write all samples to this new .csv file (never overwritten)")] = None | Measure temperature (°C) with thermocouples (types B, E, J, K, N, R, S, T) on a thermocouple-capable device, e.g |
| `read_digital_lines` | read | lines: Annotated[str, Field(description="Digital lines, e.g. 'port0/line0:3' or 'Dev1/port1/line0'")] | Read the logic level of digital lines. |
| `write_analog` | hazard | channel: Annotated[str, Field(description="One analog output, e.g. 'ao0' or 'Dev1/ao1'")]; voltage_v: Annotated[float, Field(ge=-10.5, le=10.5, description="DC output voltage in V")] | Set an analog output to a DC voltage (static, software-timed) |
| `write_digital_lines` | hazard | lines: Annotated[str, Field(description="Digital lines, e.g. 'port0/line0' or 'port0/line0:3'")]; levels: Annotated[
        list[Literal["high", "low"]],
        Field(min_length=1, description="One level for all lines, or one per line in order"),
    ] | Drive digital output lines high or low |
| `set_outputs_safe` | safety | digital_low: Annotated[
        bool, Field(description="Also drive low every DO line this server drove (and the safe_do_lines option)")
    ] = True | Put the device's outputs in a safe state: every analog output to 0 V (or the bottom of its range if 0 V is not in it) and, by default, every digital line this server drove - plus the `safe_do_lines` option lines - driven low |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Rigol Oscilloscope (`labmcp-rigol-scope`, servers/engineering/rigol-oscilloscope/src/labmcp_rigol_scope/server.py, 16 catalog tools / 13 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_device_info` | read | (none) | Identify the oscilloscope (model, serial, firmware), the command family used for it, the number of analog channels, and the current sample rate and memory depth. |
| `get_settings` | read | (none) | Read the current vertical settings of every channel (on/off, V/div, offset, coupling, probe ratio, bandwidth limit), the timebase, the trigger (type, sweep, status, edge source/level/slope) and the acquisition sample rate / memory depth. |
| `autoscale` | control | (none) | Run the scope's automatic setup (AUTO key): it picks vertical scales, timebase and trigger for the connected signals |
| `run` | control | (none) | Start continuous acquisition (RUN). |
| `stop` | control | (none) | Stop acquisition (STOP) and freeze the current waveforms, e.g |
| `single` | control | wait_s: Annotated[float, Field(ge=0, le=60, description="Seconds to wait for the trigger; 0 = just arm")] = 5.0 | Arm a single acquisition (SINGLE key): the scope triggers once, then stops |
| `force_trigger` | control | (none) | Force one trigger (FORCE key) |
| `set_channel` | control | channel: Annotated[int, Field(ge=1, le=4, description="Analog channel number")]; enabled: Annotated[bool | None, Field(description="Show (acquire) the channel")] = None; scale_v_per_div: Annotated[float | None, Field(gt=0, le=10_000, description="Vertical scale in V/div (1-2-5 steps)")] = None; offset_v: Annotated[float | None, Field(ge=-10_000, le=10_000, description="Vertical offset in V")] = None; coupling: Literal["AC", "DC", "GND"] | None = None; probe_ratio: Annotated[float | None, Field(gt=0, le=50_000, description="Probe attenuation, e.g. 1 or 10 - must match the probe")] = None; bandwidth_limit_20mhz: Annotated[bool | None, Field(description="20 MHz bandwidth limit on/off")] = None | Change a channel's vertical settings; unspecified settings are left alone |
| `set_timebase` | control | scale_s_per_div: Annotated[float | None, Field(gt=0, le=1000, description="Horizontal scale in s/div (1-2-5 steps)")] = None; offset_s: Annotated[float | None, Field(ge=-1000, le=1000, description="Horizontal (trigger) offset in s")] = None | Set the main timebase scale and/or offset |
| `set_trigger` | control | source_channel: Annotated[int | None, Field(ge=1, le=4, description="Analog channel to trigger on")] = None; level_v: Annotated[float | None, Field(ge=-10_000, le=10_000, description="Trigger level in V (displayed units)")] = None; slope: Literal["rising", "falling", "either"] | None = None; sweep: Annotated[
        Literal["auto", "normal", "single"] | None,
        Field(description="auto = free-run when not triggered; normal = only on trigger; single = once"),
    ] = None | Configure an edge trigger (source, level, slope) and the sweep mode |
| `measure` | read | channel: Annotated[int, Field(ge=1, le=4)]; items: Annotated[
        list[MeasurementName],
        Field(min_length=1, max_length=18, description="Parameters to measure"),
    ] = ["vpp", "vmax", "vmin", "vavg", "vrms", "frequency", "period"]; # noqa: B006 - copied by pydantic | Read the scope's automatic measurements for one channel: voltages (Vpp, Vmax, Vmin, Vtop, Vbase, Vamp, Vavg, Vrms, overshoot, preshoot) and timing (period, frequency, rise/fall time, +/- width, +/- duty) |
| `capture_waveform` | read | channel: Annotated[int, Field(ge=1, le=4)]; mode: Annotated[
        Literal["screen", "memory"],
        Field(description="'screen' = the displayed points (1000-1200); 'memory' = full acquisition memory (scope must be stopped)"),
    ] = "screen"; max_points: Annotated[int, Field(ge=10, le=20_000, description="Max points returned (the data is downsampled)")] = 500; save_path: Annotated[
        str | None, Field(description="Write every point (time_s, volts) to this new .csv file (never overwritten)")
    ] = None | Capture a channel's waveform, scaled to volts and seconds with the scope's waveform preamble, and return statistics plus a downsampled trace; optionally save all points to CSV. |
| `screenshot` | read | save_path: Annotated[
        str | None,
        Field(description="New image file to write (.png; .bmp on the MSO5000; never overwritten); default: a "
                          "timestamped file in the system temp folder"),
    ] = None; include_image: Annotated[bool, Field(description="Also return the image to the client (PNG only)")] = True | Save a screenshot of the oscilloscope display (PNG; BMP on the MSO5000) and return its path |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### ASTM LIS Analyzer Receiver (`labmcp-astm-lis`, servers/health/astm-lis-analyzer/src/labmcp_astm_lis/server.py, 8 catalog tools / 5 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_connection_status` | read | (none) | Report the analyzer link state, the messages and results received, and link-layer error counters. |
| `list_received_messages` | read | limit: Annotated[int, Field(ge=1, le=500, description="Most recent messages to return")] = 20; since: Annotated[str | None, Field(description="Only messages received at/after this ISO 8601 time (UTC if no zone)")] = None | List the most recent ASTM messages received (newest last). |
| `get_results` | read | sample_id: Annotated[str | None, Field(max_length=64, description="Sample / specimen ID (case-insensitive exact match)")] = None; patient_id: Annotated[str | None, Field(max_length=64, description="Patient ID as sent by the analyzer (exact match)")] = None; test_code: Annotated[str | None, Field(max_length=32, description="Analyzer test code, e.g. WBC, GLU, TSH (case-insensitive)")] = None; since: Annotated[str | None, Field(description="Only results received at/after this ISO 8601 time (UTC if no zone)")] = None; abnormal_only: Annotated[bool, Field(description="Only results with an abnormal flag other than N")] = False; limit: Annotated[int, Field(ge=1, le=2000, description="Most recent matching results to return")] = 200 | Return received results, filtered by sample ID, patient ID, test code, time received or abnormal flag (newest last). |
| `get_result_detail` | read | result_id: Annotated[str, Field(max_length=32, description="result_id from get_results, e.g. '3-12'")] | Show every decoded field of one result, with its comments, order and patient context, raw records and message header. |
| `clear_results` | control | (none) | Delete all received messages and results from this server's memory (e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Bluetooth LE Health Sensors (`labmcp-ble-health`, servers/health/ble-health-sensors/src/labmcp_ble_health/server.py, 11 catalog tools / 8 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `scan_devices` | read | timeout_s: Annotated[float, Field(ge=1, le=30, description="How long to listen for advertisements")] = 8.0; service: Annotated[ServiceFilter, Field(description="Only list devices advertising this service")] = "any"; name_contains: Annotated[str | None, Field(max_length=40, description="Case-insensitive name filter")] = None | Scan for nearby Bluetooth LE devices: address, name, signal strength and advertised health services |
| `get_device_info` | read | (none) | Read the device's identity (manufacturer, model, serial, firmware), the standard health services it exposes and its features. |
| `read_battery` | read | (none) | Read the device's battery level (Battery Service, 0-100 %). |
| `record_heart_rate` | read | duration_s: Annotated[float, Field(ge=5, le=3600, description="Recording length in seconds")] = 60.0; max_points: Annotated[int, Field(ge=10, le=5000, description="Max points returned per series")] = 300; save_path: Annotated[
        str | None, Field(description="Optional new .csv file for the full recording (never overwrites a file)")
    ] = None | Record heart rate for `duration_s`: bpm series, RR intervals and time-domain HRV (mean HR, SDNN, RMSSD, pNN50). |
| `read_pulse_oximetry` | read | mode: Annotated[
        Literal["auto", "continuous", "spot_check"],
        Field(
            description="auto: connect, then continuous if the oximeter supports it, else wait for a spot-check. "
            "spot_check: wait for a reading even if the oximeter is not advertising yet"
        ),
    ] = "auto"; duration_s: Annotated[float, Field(ge=1, le=600, description="Continuous mode: seconds to average over")] = 10.0; timeout_s: Annotated[float, Field(ge=5, le=1800, description="Spot-check mode: seconds to wait")] = 60.0 | Read SpO2 (%) and pulse rate from a pulse oximeter, averaged over a few seconds or as a single spot-check reading. |
| `wait_for_blood_pressure` | read | timeout_s: Annotated[float, Field(ge=5, le=1800, description="Seconds to wait for a measurement")] = 120.0 | Wait for a blood pressure monitor to send a measurement: systolic, diastolic and mean arterial pressure (mmHg) and pulse rate. |
| `read_temperature` | read | timeout_s: Annotated[float, Field(ge=5, le=1800, description="Seconds to wait for a measurement")] = 60.0; accept_intermediate: Annotated[
        bool, Field(description="If no final measurement arrives before the timeout, return the latest Intermediate Temperature value (probe still settling) instead of an error")
    ] = False | Wait for a thermometer to send a temperature measurement and return it in °C. |
| `read_weight` | read | timeout_s: Annotated[float, Field(ge=5, le=1800, description="Seconds to wait for a measurement")] = 60.0 | Wait for a scale to send a weight measurement (kg), with BMI and height if the scale sends them. |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### BrainFlow Biosensing Boards (`labmcp-brainflow`, servers/health/brainflow-biosensors/src/labmcp_brainflow/server.py, 12 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `list_supported_boards` | read | (none) | List common BrainFlow boards: the `--option board=` alias, BrainFlow board id, and which connection detail `--address` must hold (serial port, Bluetooth MAC, IP address or serial number) |
| `get_board_info` | read | preset: PresetName = "default" | Describe the connected board: channel names by type (EEG/EMG/ECG/EOG share the EXG rows on most boards), sampling rate, available presets (data buffers) and streaming state. |
| `start_streaming` | control | buffer_duration_s: Annotated[
        float, Field(ge=1, le=3600, description="Size of BrainFlow's ring buffer, in seconds of data")
    ] = 300 | Start continuous acquisition into BrainFlow's ring buffer (the board's radio/LEDs switch on; nothing is applied to the participant) |
| `stop_streaming` | safety | (none) | Stop acquisition (saves battery) |
| `record` | read | duration_s: Annotated[float, Field(gt=0, le=3600, description="Seconds of data to collect")] = 5.0; channel_type: Annotated[ChannelKind, Field(description="Which channels to summarise")] = "exg"; preset: PresetName = "default"; max_points: Annotated[int, Field(ge=10, le=5000, description="Max points per downsampled trace")] = 250; include_traces: Annotated[bool, Field(description="Return downsampled traces")] = True; remove_dc: Annotated[bool, Field(description="Subtract each channel's mean from the traces")] = True; save_path: Annotated[
        str | None,
        Field(description="Write the full-resolution data (all rows) to this new file (.csv; never overwrites a file)"),
    ] = None; save_format: Annotated[
        Literal["csv", "brainflow"],
        Field(description="csv: labelled columns; brainflow: DataFilter.write_file format (replayable)"),
    ] = "csv" | Record `duration_s` seconds and return per-channel statistics, event markers and downsampled traces |
| `get_band_powers` | read | window_s: Annotated[float, Field(ge=1, le=3600, description="Seconds of data to analyse (4 s recommended)")] = 4.0; channel_type: Annotated[ExgKind, Field(description="EXG channel group to analyse")] = "exg" | EEG band powers (delta 1-4, theta 4-8, alpha 8-13, beta 13-30, gamma 30-50 Hz) over the most recent `window_s` seconds: BrainFlow's channel-averaged relative powers plus per-channel absolute (uV^2) and relative powers and the peak frequency |
| `get_signal_quality` | read | window_s: Annotated[float, Field(ge=1, le=3600, description="Seconds of data to assess")] = 4.0 | Check every EXG channel for common electrode problems: flat line (disconnected), railed (amplifier saturated, OpenBCI Cyton boards), strong 50/60 Hz mains noise (poor contact or missing reference), and implausibly high amplitude (movement, muscle, loose electrode). |
| `insert_marker` | control | value: Annotated[
        float, Field(ge=-1e6, le=1e6, description="Event code written to the marker channel; must not be 0")
    ]; preset: PresetName = "default" | Write an event marker into the data stream at the current sample (for event-related experiments: stimulus onsets, condition changes) |
| `configure_board` | hazard | command: Annotated[
        str, Field(min_length=1, max_length=200, description="Board-specific configuration string")
    ] | Send a raw board-specific command to the firmware through BrainFlow's config_board (e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Keithley SourceMeter SMU (`labmcp-keithley-smu`, servers/physics/keithley-smu/src/labmcp_keithley_smu/server.py, 12 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_device_info` | read | (none) | Identify the SMU: manufacturer, model, serial, firmware, command dialect (2400 / 2450 / 2600), channel (2600B) and the model's maximum source voltage and current. |
| `get_status` | read | (none) | Report whether the output is on, the source function (voltage/current), programmed level, compliance limit, whether the source is in compliance, terminals and 2/4-wire sense. |
| `configure_source` | control | source: Annotated[Literal["voltage", "current"], Field(description="Source voltage or current")]; level: Annotated[
        float,
        Field(
            ge=-1100,
            le=1100,
            description="Source level: volts for a voltage source, amps for a current source",
        ),
    ]; compliance: Annotated[
        float,
        Field(
            gt=0,
            le=1100,
            description="Limit: current limit in A (voltage source) or voltage limit in V (current source)",
        ),
    ]; source_range: Annotated[
        float | None, Field(gt=0, le=1100, description="Fixed source range (V or A); omit for auto-range")
    ] = None; nplc: Annotated[
        float,
        Field(
            ge=0.01, le=10, description="Measurement integration time in power-line cycles (1 = 16.7/20 ms)"
        ),
    ] = 1.0 | Set up the source (function, level, compliance, range, measurement speed) while the output is OFF |
| `set_source_level` | hazard | level: Annotated[
        float, Field(ge=-1100, le=1100, description="New level for the configured source: V or A")
    ] | Change the level of the configured source |
| `output_on` | hazard | (none) | Switch the SMU output ON: the configured voltage or current is applied to the DUT |
| `output_off` | safety | (none) | Switch the SMU output OFF immediately (and abort any IV sweep in progress) |
| `measure` | read | (none) | Take one source-measure reading (voltage, current, V/I, power, compliance flag) of the energised DUT |
| `set_4wire` | control | enabled: Annotated[bool, Field(description="True = 4-wire remote sense, False = 2-wire local sense")] | Select 4-wire (remote sense, Kelvin) or 2-wire measurement |
| `run_iv_sweep` | hazard | start: Annotated[
        float, Field(ge=-1100, le=1100, description="First level: V (voltage sweep) or A (current sweep)")
    ]; stop: Annotated[float, Field(ge=-1100, le=1100, description="Last level: V or A")]; points: Annotated[int, Field(ge=2, le=100000, description="Levels from start to stop (inclusive)")]; compliance: Annotated[
        float,
        Field(
            gt=0,
            le=1100,
            description="Current limit in A (voltage sweep) or voltage limit in V (current sweep)",
        ),
    ]; source: Annotated[
        Literal["voltage", "current"], Field(description="Sweep voltage or current")
    ] = "voltage"; spacing: Annotated[Literal["linear", "log"], Field(description="Level spacing")] = "linear"; dual: Annotated[bool, Field(description="Sweep back from stop to start afterwards (hysteresis)")] = False; delay_s: Annotated[float, Field(ge=0, le=60, description="Settling delay at each point, s")] = 0.05; nplc: Annotated[float, Field(ge=0.01, le=10, description="Integration time per measurement, PLC")] = 1.0; stop_on_compliance: Annotated[
        bool, Field(description="End the sweep at the first compliance point")
    ] = False; max_points: Annotated[
        int, Field(ge=10, le=5000, description="Maximum points returned in the reply")
    ] = 200; save_path: Annotated[str | None, Field(description="Optional CSV path for every point")] = None | Run a stepped IV sweep: configure the source, switch the output ON, step through the levels measuring V and I at each, then ALWAYS switch the output OFF (also on errors or when `output_off` is called) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Lake Shore Temperature Controller (`labmcp-lakeshore`, servers/physics/lakeshore-temperature/src/labmcp_lakeshore/server.py, 11 catalog tools / 8 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_temperatures` | read | inputs: Annotated[
        list[InputName] | None, Field(description="Inputs to read (default: all inputs of the model)")
    ] = None | Read every (or the selected) sensor input: kelvin, raw sensor units, sensor type, input name and decoded reading status (invalid, under/overrange) |
| `get_heater_status` | read | output: Annotated[int | None, Field(ge=1, le=4, description="One output (default: all)")] = None | Report each output's control mode and input, heater range, output %, setpoint (and in kelvin), ramp state, PID values and heater errors (open/short) |
| `set_setpoint` | hazard | output: Annotated[int, Field(ge=1, le=4, description="Output / control loop number")]; setpoint_k: Annotated[float, Field(gt=0, le=1500, description="Target temperature in kelvin")] | Set the control setpoint of an output's loop in kelvin |
| `set_ramp` | control | output: Annotated[int, Field(ge=1, le=4, description="Output / control loop number")]; enabled: Annotated[bool, Field(description="Ramp the setpoint instead of stepping it")]; rate_k_per_min: Annotated[
        float, Field(ge=0, le=100, description="Ramp rate in K/min (0.1-100; 350: 0.001-100)")
    ] = 1.0 | Turn setpoint ramping on or off for an output's control loop and set the rate |
| `set_heater_range` | hazard | output: Annotated[int, Field(ge=1, le=4, description="Output number")]; heater_range: Annotated[
        int,
        Field(
            ge=0, le=5, description="0 = off; 335/336: 1 low, 2 medium, 3 high; 350: 1-5; outputs 3/4: 1 = on"
        ),
    ] | Set an output's heater range (each step is ~10x more power) |
| `set_pid` | control | output: Annotated[int, Field(ge=1, le=4, description="Output / control loop number")]; p: Annotated[float, Field(ge=0.1, le=1000, description="Proportional gain")]; i: Annotated[
        float, Field(ge=0.1, le=1000, description="Integral (reset) setting = 1000 / integral seconds")
    ]; d: Annotated[float, Field(ge=0, le=200, description="Derivative (rate) setting, 0-200")] | Set the P, I and D values of an output's control loop (Lake Shore conventions). |
| `wait_for_stable_temperature` | read | output: Annotated[int, Field(ge=1, le=4, description="Control loop whose setpoint and input are used")]; tolerance_k: Annotated[float, Field(gt=0, le=50, description="Allowed |T - setpoint|")] = 0.1; stable_for_s: Annotated[
        float, Field(ge=0, le=3600, description="How long T must stay within tolerance")
    ] = 60; timeout_s: Annotated[float, Field(gt=0, le=7200, description="Give up after this long")] = 1800; poll_interval_s: Annotated[float, Field(ge=0.1, le=60, description="Seconds between readings")] = 2.0 | Wait until the control input of `output` has stayed within `tolerance_k` of the setpoint (and the setpoint is no longer ramping) for `stable_for_s`, or until `timeout_s` |
| `all_heaters_off` | safety | (none) | Turn every output off (heater range 0 on outputs 1-4), like the front-panel All Off key, and stop any running wait |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Pfeiffer Vacuum Gauge Controller (`labmcp-pfeiffer-tpg`, servers/physics/pfeiffer-vacuum-gauge/src/labmcp_pfeiffer_tpg/server.py, 11 catalog tools / 8 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_pressure` | read | channel: Annotated[int, Field(ge=1, le=6, description="Measurement channel (gauge) number")] = 1 | Read one gauge channel: status, pressure in the display unit and in mbar. |
| `read_all_pressures` | read | (none) | Read every channel of the controller at once (PRX), with status per channel. |
| `get_gauge_types` | read | (none) | List the gauge connected to each channel (TID), what kind it is, and whether it is an ionisation gauge that is currently switched on or off (SEN). |
| `get_errors` | read | (none) | Read (and clear) the controller's ERROR word: controller error, no hardware, inadmissible parameter or syntax error |
| `log_pressure_series` | read | duration_s: Annotated[
        float, Field(gt=0, le=_LOG_MAX_DURATION_S, description="How long to log (at most 2 h per call)")
    ] = 60; interval_s: Annotated[float, Field(ge=0.2, le=3600, description="Seconds between readings")] = 1.0; channels: Annotated[list[int] | None, Field(description="Channels to include (default: all)")] = None; max_points: Annotated[int, Field(ge=2, le=2000, description="Points returned per channel")] = 200; save_path: Annotated[str | None, Field(description="Optional CSV file for every reading")] = None | Log pressures at a fixed interval (e.g |
| `set_unit` | control | unit: Annotated[
        Literal["mbar", "hPa", "Pa", "Torr", "micron", "V"],
        Field(description="Display/interface unit (TPG 26x: mbar, Pa or Torr only)"),
    ] | Change the pressure unit used on the display and interface (UNI) |
| `set_gauge_power` | hazard | channel: Annotated[int, Field(ge=1, le=6, description="Channel of the ionisation gauge")]; on: Annotated[bool, Field(description="True = switch the gauge on, False = off")]; reference_channel: Annotated[
        int | None,
        Field(
            ge=1,
            le=6,
            description="Another gauge on the same vacuum (e.g. a Pirani) used to check the pressure",
        ),
    ] = None | Switch an ionisation gauge (IKR, PKR, PBR, IMR) on or off (SEN) |
| `switch_gauge_off` | safety | channel: Annotated[int, Field(ge=1, le=6, description="Channel of the ionisation gauge to switch off")] | Switch an ionisation gauge off (e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### SRS Lock-in Amplifier (`labmcp-srs-lockin`, servers/physics/srs-lockin/src/labmcp_srs_lockin/server.py, 15 catalog tools / 12 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `read_outputs` | read | include_aux_inputs: Annotated[bool, Field(description="Also read Aux In 1-4 (V)")] = False | Read X, Y, R and θ as one coherent snapshot (SNAP?), with the reference frequency, sensitivity, fraction of full scale and any overloads latched since the last reading. |
| `get_settings` | read | (none) | Report the reference (source, frequency, harmonic, phase), sine output, sensitivity, time constant/slope (with the 99 % settling time), and input configuration. |
| `set_reference` | control | frequency_hz: Annotated[
        float | None, Field(gt=0, le=4e6, description="Internal reference frequency (internal mode only)")
    ] = None; source: Annotated[
        Literal["internal", "external"] | None,
        Field(description="internal = sine output oscillator; external = lock to REF IN"),
    ] = None; harmonic: Annotated[int | None, Field(ge=1, le=19999, description="Detection harmonic n")] = None; phase_deg: Annotated[float | None, Field(ge=-360, le=360, description="Reference phase shift")] = None | Set the reference: source (internal/external), internal frequency, detection harmonic and phase shift |
| `set_amplitude` | hazard | amplitude_v: Annotated[float, Field(gt=0, le=5.0, description="Sine output amplitude in V rms")] | Set the SINE OUT amplitude (V rms), which drives the sample or excitation circuit |
| `set_amplitude_minimum` | safety | (none) | Turn the sine output down to the instrument minimum (the lock-in's closest thing to 'output off') and stop any running frequency sweep |
| `set_sensitivity` | control | full_scale: Annotated[
        float, Field(gt=0, le=1.0, description="Largest signal to measure without overload")
    ]; unit: Annotated[
        Literal["V", "A"], Field(description="V for voltage inputs; A for current inputs (1 V <-> 1 µA)")
    ] = "V"; dynamic_reserve: Annotated[
        Literal["high_reserve", "normal", "low_noise"] | None,
        Field(description="SR810/SR830 only: dynamic reserve mode"),
    ] = None | Set the sensitivity (full-scale range) |
| `set_time_constant` | control | time_constant_s: Annotated[float, Field(ge=1e-6, le=30e3, description="Output filter time constant")]; filter_slope_db_oct: Annotated[
        Literal[6, 12, 18, 24] | None, Field(description="Low-pass filter slope in dB/octave")
    ] = None; sync_filter: Annotated[bool | None, Field(description="Synchronous filter on/off")] = None | Set the output filter time constant (nearest available value), and optionally the filter slope and synchronous filter |
| `set_input` | control | configuration: Annotated[
        Literal["A", "A-B", "I_1MOhm", "I_100MOhm"] | None,
        Field(description="Single-ended A, differential A-B, or current input with 1 MΩ / 100 MΩ gain"),
    ] = None; coupling: Annotated[Literal["AC", "DC"] | None, Field(description="Input coupling")] = None; shield: Annotated[Literal["float", "ground"] | None, Field(description="Input shield grounding")] = None; input_range_v: Annotated[
        Literal[1.0, 0.3, 0.1, 0.03, 0.01] | None, Field(description="SR86x voltage input range")
    ] = None | Configure the signal input: A / A-B / current, AC or DC coupling, shield float/ground and (SR86x) the voltage input range |
| `auto_phase` | control | (none) | Run Auto Phase (APHS): shift the reference phase so that Y ≈ 0 and X ≈ R |
| `auto_gain` | control | (none) | Pick the sensitivity automatically for the present signal (SR830 AGAN / SR86x ASCL) |
| `auto_range` | control | (none) | Optimise the input stage for the present signal: SR830 Auto Reserve (ARSV) or SR86x Auto Range of the voltage input range (ARNG). |
| `frequency_sweep` | hazard | start_hz: Annotated[float, Field(gt=0, le=4e6, description="First frequency")]; stop_hz: Annotated[float, Field(gt=0, le=4e6, description="Last frequency")]; points: Annotated[int, Field(ge=2, le=1000, description="Number of frequencies")] = 51; log_spacing: Annotated[bool, Field(description="Logarithmic instead of linear spacing")] = False; settle_time_constants: Annotated[
        float | None,
        Field(
            ge=1, le=50, description="Wait per point in time constants (default: 99 % settling for the slope)"
        ),
    ] = None; restore_frequency: Annotated[
        bool, Field(description="Return to the starting frequency afterwards")
    ] = True; save_path: Annotated[str | None, Field(description="Optional CSV file for the full sweep")] = None | Step the internal reference (and SINE OUT drive) from start_hz to stop_hz, waiting for the output filter to settle at each point, and record X/Y/R/θ vs frequency - e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### SRS Residual Gas Analyzer (`labmcp-srs-rga`, servers/physics/srs-rga/src/labmcp_srs_rga/server.py, 18 catalog tools / 15 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_status` | read | (none) | Report the RGA state: filament emission, CDEM on/off and voltage, ionizer settings, scan settings, stored sensitivity factors, and the decoded error bytes (e.g |
| `read_total_pressure` | read | (none) | Measure the total pressure with the RGA acting as an ionisation gauge (TP?, Faraday cup) |
| `measure_masses` | read | masses: Annotated[
        list[int],
        Field(min_length=1, max_length=50, description="m/z values to measure, e.g. [2, 18, 28, 32, 40, 44]"),
    ] | Measure partial pressures at a list of m/z values (peak-locked single-mass measurements, MR) |
| `analog_scan` | read | start_mass: Annotated[int, Field(ge=1, le=300, description="First m/z")] = 1; stop_mass: Annotated[int, Field(ge=1, le=300, description="Last m/z")] = 50; points_per_amu: Annotated[int, Field(ge=10, le=25, description="Steps per amu (SA)")] = 10; max_points: Annotated[int, Field(ge=20, le=5000, description="Spectrum points returned")] = 500; save_path: Annotated[
        str | None, Field(description="Optional CSV path for the full spectrum (must not exist yet)")
    ] = None | Record an analog mass spectrum (SC1): the quadrupole steps through the mass range and the full peak shapes are returned (downsampled) with a peak list, the total pressure measured at the end of the scan, and optionally the full data as CSV |
| `histogram_scan` | read | start_mass: Annotated[int, Field(ge=1, le=300, description="First m/z")] = 1; stop_mass: Annotated[int, Field(ge=1, le=300, description="Last m/z")] = 50; save_path: Annotated[str | None, Field(description="Optional CSV path (must not exist yet)")] = None | Record a bar-graph spectrum (HS1): one peak-locked value per integer m/z plus the total pressure |
| `leak_check` | read | duration_s: Annotated[float, Field(gt=0, le=7200, description="How long to monitor")] = 120; interval_s: Annotated[float, Field(ge=0.1, le=60, description="Seconds between readings")] = 1.0; mz: Annotated[int, Field(ge=1, le=300, description="Tracer gas m/z (4 = helium)")] = 4; threshold_factor: Annotated[
        float, Field(ge=1.5, le=1000, description="Signal/baseline ratio that counts as a leak response")
    ] = 3.0; baseline_points: Annotated[int, Field(ge=3, le=100, description="Readings used for the baseline")] = 5; max_points: Annotated[int, Field(ge=10, le=5000, description="Points returned")] = 300; save_path: Annotated[
        str | None, Field(description="Optional CSV file for every reading (must not exist yet)")
    ] = None | Helium leak check: monitor m/z 4 while someone sprays helium on suspect joints, and report the baseline, every response above `threshold_factor` x baseline (start/end time, peak) and the time series |
| `identify_residual_gases` | read | stop_mass: Annotated[int, Field(ge=20, le=300, description="Scan m/z 1 up to this mass")] = 50 | Run a histogram scan and assign the major peaks to likely gases with a simple lookup of standard 70 eV fragment patterns: H2 (2), He (4), CH4 (16/15), H2O (18/17), N2 or CO (28, split with 14 and 12), O2 (32), Ar (40), CO2 (44), plus diagnoses such as an air leak or hydrocarbon contamination |
| `set_scan_parameters` | control | noise_floor: Annotated[
        int | None, Field(ge=0, le=7, description="0 = slowest/lowest noise ... 7 = fastest (default 4)")
    ] = None; electron_energy_ev: Annotated[int | None, Field(ge=25, le=105, description="Default 70 eV")] = None; ion_energy: Annotated[
        Literal["low", "high"] | None, Field(description="low = 8 eV, high = 12 eV")
    ] = None; focus_voltage_v: Annotated[
        int | None, Field(ge=0, le=150, description="Focus plate, default 90 V")
    ] = None; points_per_amu: Annotated[
        int | None, Field(ge=10, le=25, description="Analog scan steps per amu")
    ] = None | Change measurement settings: noise floor (speed vs detection limit), electron energy, ion energy, focus plate voltage and analog-scan resolution |
| `set_filament` | hazard | emission_current_ma: Annotated[
        float, Field(ge=0.02, le=3.5, description="Electron emission current; 1.0 mA is the standard setting")
    ] = 1.0; external_pressure_torr: Annotated[
        float | None,
        Field(gt=0, description="Pressure just read on a separate vacuum gauge, confirmed by the user"),
    ] = None | Switch the filament on (or change its emission current) |
| `set_cdem` | hazard | voltage_v: Annotated[
        int,
        Field(ge=10, le=2490, description="CDEM high voltage (magnitude); typical 1000-1600 V, default 1400"),
    ] = 1400; external_pressure_torr: Annotated[
        float | None,
        Field(gt=0, description="Pressure just read on a separate vacuum gauge, confirmed by the user"),
    ] = None | Switch the electron multiplier (CDEM) on at a given high voltage for ~100-10000x more signal |
| `degas` | hazard | minutes: Annotated[int, Field(ge=1, le=20, description="Degas time incl. the 1-minute ramp")] = 3; external_pressure_torr: Annotated[
        float | None,
        Field(gt=0, description="Pressure just read on a separate vacuum gauge, confirmed by the user"),
    ] = None | Start an ionizer degas (DG): 20 mA of 400 eV electrons clean the ion source by electron stimulated desorption |
| `calibrate` | hazard | kind: Annotated[
        Literal["zero", "electrometer"],
        Field(
            description="zero = CA (re-zero detector + mass axis correction); electrometer = CL (full I-V)"
        ),
    ] = "zero" | Calibrate the detector: `zero` (CA) re-zeroes the ion detector at the present noise floor and detector and corrects the RF scan table for temperature drift (seconds); `electrometer` (CL) recalibrates the electrometer's full I-V response (longer, clears all zero offsets) |
| `filament_off` | safety | (none) | Switch the filament off (FL0), stopping a degas first if one is running |
| `cdem_off` | safety | (none) | Switch the electron multiplier off (HV0) and return to Faraday-cup detection |
| `all_off` | safety | (none) | Put the RGA in a safe state: abort any degas, CDEM off (HV0), filament off (FL0) and quadrupole RF/DC off (MR0) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Thorlabs Optical Power Meter (`labmcp-thorlabs-pm`, servers/physics/thorlabs-power-meter/src/labmcp_thorlabs_pm/server.py, 11 catalog tools / 8 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_device_info` | read | (none) | Identify the meter console and the attached sensor head (model, serial, type, wavelength range, power ranges), and report the present wavelength, averaging, range and zero value. |
| `read_power` | read | (none) | Read the optical power once (in W, plus dBm and a formatted string) |
| `log_power_series` | read | count: Annotated[int, Field(ge=2, le=100000, description="Number of readings")] = 60; interval_s: Annotated[
        float, Field(ge=0, le=3600, description="Seconds between readings (0 = as fast as the meter allows)")
    ] = 1.0; max_points: Annotated[
        int, Field(ge=10, le=5000, description="Maximum readings returned in the reply")
    ] = 200; save_path: Annotated[str | None, Field(description="Optional CSV path for every reading")] = None | Log a series of power readings to characterise laser stability, warm-up or drift |
| `set_wavelength` | control | wavelength_nm: Annotated[float, Field(gt=0, le=100000, description="Source wavelength in nm")] | Set the correction wavelength (nm) the meter uses to convert sensor signal to power |
| `set_averaging` | control | count: Annotated[
        int,
        Field(ge=1, le=10000, description="Samples averaged per reading (PM100: ~3 ms per sample)"),
    ] | Set how many samples the meter averages for each reading |
| `set_range` | control | mode: Annotated[Literal["auto", "manual"], Field(description="auto-ranging or a fixed range")] = "auto"; range_w: Annotated[
        float | None, Field(gt=0, description="For manual mode: the highest power you expect, in W")
    ] = None | Select auto-ranging, or a fixed power range that fits `range_w` (the meter picks the most sensitive range that can hold that power) |
| `zero_sensor` | control | (none) | Dark-zero the sensor (removes dark current / thermal offset) |
| `read_sensor_temperature` | read | (none) | Read the sensor head temperature in °C (thermal sensors and other heads with a built-in temperature sensor only). |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### EPICS Channel Access (`labmcp-epics`, servers/protocols/epics/src/labmcp_epics/server.py, 10 catalog tools / 7 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_pv` | read | name: PVName; max_elements: Annotated[int, Field(ge=1, le=10000, description="Arrays longer than this are downsampled")] = 100 | Read one PV with its metadata: value, units, precision, alarm severity/status, IOC timestamp and age, display/alarm/warning/control limits and enum state names |
| `get_pvs` | read | names: Annotated[list[PVName], Field(min_length=1, max_length=200, description="PV names")]; max_elements: Annotated[int, Field(ge=1, le=10000, description="Arrays longer than this are downsampled")] = 20 | Read many PVs at once (e.g |
| `pv_info` | read | name: PVName | Connection details of a PV: serving IOC host:port, native type, element count, and whether this client has read/write access (EPICS access security) and passes the put allow-list. |
| `monitor_pv` | read | name: PVName; duration_s: Annotated[float, Field(gt=0, le=3600, description="How long to collect updates")] = 5.0; max_updates: Annotated[int, Field(ge=1, le=100000, description="Stop after this many updates")] = 1000; max_points: Annotated[int, Field(ge=0, le=5000, description="Updates to include in the reply (evenly thinned)")] = 200 | Subscribe to a PV and collect every value change for `duration_s` seconds (or until `max_updates`), then return statistics (min/max/mean/std, drift rate) and the updates. |
| `put_pv` | hazard | name: PVName; value: Annotated[
        float | int | str | list[float | int | str],
        Field(description="New value: number, enum state name or index, string, or array for waveforms"),
    ]; wait: Annotated[bool, Field(description="Wait for the IOC's put-completion (put-callback)")] = True; timeout_s: Annotated[float, Field(gt=0, le=3600, description="Max seconds to wait for completion")] = 10.0 | Write a PV |
| `put_pvs` | hazard | writes: Annotated[list[PVWrite], Field(min_length=1, max_length=100, description="PV writes, applied in order")]; wait: Annotated[bool, Field(description="Wait for each put-completion before the next write")] = True; timeout_s: Annotated[float, Field(gt=0, le=3600, description="Max seconds to wait per write")] = 10.0 | Write several PVs in order (e.g |
| `apply_safe_state` | safety | (none) | Emergency action: write the scientist-configured safe-state PVs (--option safe_state, e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Modbus TCP/RTU Device (`labmcp-modbus`, servers/protocols/modbus/src/labmcp_modbus/server.py, 13 catalog tools / 10 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `list_points` | read | (none) | Describe the loaded register map: device, every named point (table, 0-based address, type, scaling, unit, writable, min/max, enum) and the safe-state steps |
| `read_points` | read | names: Annotated[
        list[str] | None, Field(max_length=200, description="Point names from list_points; omit for all points")
    ] = None | Read named points from the register map, decoded and scaled into engineering units (e.g |
| `read_registers` | read | address: Annotated[int, Field(ge=0, le=65535, description="0-based start address")]; count: Annotated[int, Field(ge=1, le=MAX_READ_REGISTERS, description="Number of registers")] = 1; table: Annotated[Literal["holding", "input"], Field(description="holding (FC03) or input (FC04)")] = "holding"; decode_as: Annotated[_DECODE, Field(description="Also decode the registers as this type")] = "none"; word_order: Annotated[Literal["big", "little"], Field(description="32/64-bit: big = high word first")] = "big"; byte_order: Annotated[Literal["big", "little"], Field(description="big = standard Modbus byte order")] = "big" | Read raw holding or input registers (unsigned 16-bit), optionally decoded as int16, 32-bit or 64-bit values |
| `read_coils` | read | address: Annotated[int, Field(ge=0, le=65535, description="0-based start address")]; count: Annotated[int, Field(ge=1, le=MAX_READ_BITS, description="Number of coils")] = 1 | Read coils (FC01): single-bit outputs such as run/stop or relay states. |
| `read_discrete_inputs` | read | address: Annotated[int, Field(ge=0, le=65535, description="0-based start address")]; count: Annotated[int, Field(ge=1, le=MAX_READ_BITS, description="Number of inputs")] = 1 | Read discrete inputs (FC02): single-bit, read-only status such as alarms or limit switches. |
| `write_point` | hazard | name: Annotated[str, Field(description="A writable point from list_points")]; value: Annotated[
        float | bool | str, Field(description="Engineering value (e.g. 37.5 for °C), true/false, or an enum label")
    ] | Write a named point from the register map (setpoint, mode, output enable...) |
| `write_register` | hazard | address: Annotated[int, Field(ge=0, le=65535, description="0-based holding-register address")]; value: Annotated[int, Field(ge=-32768, le=65535, description="Raw value (negative = int16 two's complement)")] | Write one raw holding register (FC06) |
| `write_registers` | hazard | address: Annotated[int, Field(ge=0, le=65535, description="0-based start address")]; values: Annotated[
        list[Annotated[int, Field(ge=-32768, le=65535)]],
        Field(min_length=1, max_length=MAX_WRITE_REGISTERS, description="Raw 16-bit values"),
    ] | Write consecutive raw holding registers (FC16), e.g |
| `write_coil` | hazard | address: Annotated[int, Field(ge=0, le=65535, description="0-based coil address")]; value: Annotated[bool, Field(description="true = ON (0xFF00), false = OFF")] | Switch one raw coil (FC05) |
| `apply_safe_state` | safety | (none) | Put the device into the safe state defined in the register map (e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### Generic SCPI Instrument (`labmcp-scpi`, servers/protocols/scpi-instrument/src/labmcp_scpi/server.py, 15 catalog tools / 12 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `scpi_primer` | read | (none) | Concise SCPI syntax guide (long/short forms, queries, compound commands, common commands, error queue, binary blocks) plus this server's active command policy |
| `identify` | read | (none) | Ask the instrument who it is (*IDN?): manufacturer, model, serial number, firmware. |
| `list_visa_resources` | read | query: Annotated[str, Field(max_length=100, description="VISA resource filter")] = "?*::INSTR"; backend: Annotated[
        Literal["@py", "@ivi"], Field(description="@py = pyvisa-py, @ivi = NI-VISA / Keysight / R&S VISA")
    ] = "@py" | List GPIB / USBTMC / LAN (VXI-11, HiSLIP) instruments visible to VISA on this computer |
| `scpi_query` | read | command: Annotated[
        str, Field(max_length=500, description="One SCPI query, e.g. '*IDN?', 'MEAS:VOLT:DC?', 'VOLT? MAX'")
    ]; timeout_s: Annotated[float | None, Field(ge=0.1, le=300, description="Reply timeout (default: server's)")] = None; max_chars: Annotated[int, Field(ge=100, le=1_000_000, description="Truncate longer replies")] = 20_000 | Send ONE read-only SCPI query and return the instrument's text reply. |
| `query_binary_block` | read | command: Annotated[str, Field(max_length=500, description="One SCPI query answering with #<n><len><data>")]; decode_as: Annotated[
        Literal["none", "int8", "uint8", "int16", "uint16", "int32", "uint32", "float32", "float64"],
        Field(description="Interpret the payload as an array of this type (see the instrument's FORMat)"),
    ] = "none"; byte_order: Annotated[
        Literal["big", "little"], Field(description="big = SCPI FORMat:BORDer NORMal, little = SWAPped")
    ] = "big"; max_points: Annotated[int, Field(ge=1, le=10_000, description="Max decoded values returned inline")] = 200; max_inline_bytes: Annotated[
        int, Field(ge=0, le=262_144, description="Return the raw payload as base64 only up to this size")
    ] = 4096; save_path: Annotated[
        str | None,
        Field(
            max_length=1000,
            description="Write the full data here: .csv with decode_as writes index,value rows; .bin/.dat/.raw/"
            ".txt/.png/.bmp/.jpg/.gif/.tif (or .csv with decode_as none) get the raw payload",
        ),
    ] = None; overwrite: Annotated[bool, Field(description="Allow replacing an existing save_path")] = False; timeout_s: Annotated[float, Field(ge=0.1, le=300, description="Timeout for the whole transfer")] = 10.0 | Read an IEEE 488.2 definite-length binary block (waveforms, trace data, screenshots, FORMat REAL/INTeger readings) |
| `get_errors` | read | max_errors: Annotated[int, Field(ge=1, le=100, description="Stop after this many entries")] = 20 | Read and clear the instrument's error/event queue (SYSTem:ERRor? until 0,"No error") |
| `wait_operation_complete` | read | timeout_s: Annotated[float, Field(ge=0.1, le=3600, description="Longest time to wait")] = 30.0 | Wait until the instrument has finished all pending operations (*OPC? returns 1), e.g |
| `scpi_write` | hazard | command: Annotated[
        str, Field(max_length=2000, description="SCPI program message, e.g. 'VOLT 5;CURR 0.1' or 'OUTP ON'")
    ]; timeout_s: Annotated[float | None, Field(ge=0.1, le=300, description="Reply timeout if it contains a query")] = None | Send any SCPI program message (settings, compound 'A;B' messages, queries with side effects) and then read the error queue |
| `scpi_batch` | hazard | steps: Annotated[
        list[Annotated[str, Field(max_length=2000)]],
        Field(min_length=1, max_length=50, description="Commands/queries to send in order"),
    ]; stop_on_error: Annotated[bool, Field(description="Stop at the first step that reports an error")] = True; delay_between_s: Annotated[float, Field(ge=0, le=10, description="Pause between steps")] = 0.0; timeout_s: Annotated[float | None, Field(ge=0.1, le=60, description="Reply timeout per query")] = None | Run a short sequence of SCPI commands and queries in order, checking the error queue after every step |
| `reset_instrument` | hazard | clear_status: Annotated[bool, Field(description="Also send *CLS (clear status and error queue)")] = True | Reset the instrument to its default settings (*RST, then *CLS), wait for completion and check errors |
| `device_clear` | safety | send_abort: Annotated[bool, Field(description="Also send ABORt (stop sweeps / measurements)")] = True | Recover a stuck or confused instrument: VISA device clear (or discard unread input on socket/serial links), report the error queue, optionally ABORt, then *CLS |
| `apply_safe_state` | safety | (none) | Put the instrument into the lab's configured safe state (the `--option safe_state` program message, e.g |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |

### SiLA 2 Bridge (`labmcp-sila2`, servers/protocols/sila2/src/labmcp_sila2/server.py, 13 catalog tools / 9 registered defs)

| Tool | Kind | Parameters (name: default/type) | Description (from catalog.json) |
|---|---|---|---|
| `get_server_info` | read | (none) | Identify the connected SiLA server (name, type, UUID, version, vendor, description), list its features, and say whether commands can be cancelled and which commands this bridge may call. |
| `list_features` | read | feature: Annotated[str | None, Field(description="Only this feature (identifier); default all")] = None; include_fdl: Annotated[bool, Field(description="Also return the raw Feature Definition XML")] = False; include_core: Annotated[bool, Field(description="Include the SiLAService core feature")] = False | Describe the server's features from their Feature Definitions: every command (observable or not, parameters, responses, intermediate responses, defined errors, and whether the allow-list permits calling it) and every property, with types rendered as JSON-schema-like dicts including units and constraints. |
| `get_property` | read | feature: FeatureName; property: Annotated[str, Field(min_length=1, max_length=255)]; metadata: Metadata = None | Read the current value of a SiLA property (for observable properties: the first value of a subscription). |
| `subscribe_property` | read | feature: FeatureName; property: Annotated[str, Field(min_length=1, max_length=255)]; duration_s: Annotated[float, Field(gt=0, le=MAX_WAIT_S, description="How long to collect updates")] = 10.0; max_updates: Annotated[int, Field(ge=1, le=10000, description="Stop after this many updates")] = 500; poll_interval_s: Annotated[float, Field(ge=0.1, le=60, description="Polling interval for unobservable properties")] = 1.0; metadata: Metadata = None | Collect a property's values for `duration_s` seconds (SiLA subscription for observable properties, polling otherwise) and summarise them (e.g |
| `call_command` | hazard | feature: FeatureName; command: Annotated[str, Field(min_length=1, max_length=255, description="Command identifier")]; parameters: Annotated[
        dict[str, Any], Field(description="Parameter identifier -> value, as described by list_features")
    ] = {}; # noqa: B006 - pydantic copies defaults
    wait_s: Annotated[
        float, Field(ge=0, le=MAX_WAIT_S, description="Observable commands: wait up to this long for completion (0 = return at once)")
    ] = 0.0; metadata: Metadata = None | Run a SiLA command |
| `get_command_status` | read | execution_id: Annotated[str, Field(min_length=1, max_length=64)] | Status of an observable command started with `call_command`: waiting / running / finishedSuccessfully / finishedWithError, progress, estimated remaining time and the latest intermediate response. |
| `get_command_result` | read | execution_id: Annotated[str, Field(min_length=1, max_length=64)] | Responses of a finished observable command, or the SiLA execution error it finished with |
| `list_executions` | read | (none) | All observable command executions started in this session, newest last, with their status. |
| `cancel_command` | safety | execution_id: Annotated[
        str | None, Field(description="Execution to cancel; omit (or all_commands=true) to cancel everything")
    ] = None; all_commands: Annotated[bool, Field(description="Cancel every running command on the server")] = False | Cancel a running observable command, or all commands, through SiLA's CancelController feature |
| `discover_servers` | read | (builtin/core-provided; not a def in server.py) | Find SiLA 2 servers on the local network with SiLA Server Discovery (mDNS `_sila._tcp`) |
| `get_command_log` | read | (builtin/core-provided; not a def in server.py) | Return the most recent raw commands sent to / replies received from the instrument (newest last) |
| `get_connection_info` | read | (builtin/core-provided; not a def in server.py) | Report which instrument is connected (identity, address, simulated or real), whether the server is read-only, and the active safety limits |
| `reconnect` | safety | (builtin/core-provided; not a def in server.py) | Close and re-open the connection to the instrument (e.g |


### 1.4 Resources / prompts exposed

**NOT FOUND.** Grepping all 32 `server.py` files for decorator usage found only `@mcp.tool` (no `@mcp.resource`, no `@mcp.prompt`, no `resources=`/`prompts=` capability declaration). The only MCP "instructions" surface is the per-server `instructions=` string built by `InstrumentServer._build_instructions()` (`packages/labmcp/src/labmcp/server.py:226`, `_COMMON_INSTRUCTIONS` at `:98-108`).

### 1.5 Files implementing the shared behaviours

Shared instrument base class / abstraction:
- `packages/labmcp/src/labmcp/server.py` (533 lines) — `class InstrumentServer(Generic[D])` at `:182`, docstring "An MCP server for one instrument (or one family of compatible instruments)." Module docstring: "``InstrumentServer``: the scaffolding every LabMCP server is built on." / "* a ``--simulate`` mode backed by a wire-level simulator," / "* ``--read-only`` mode that hides every state-changing tool," / "* configurable safety limits and a command audit trail," / "* three built-in tools (``get_connection_info``, ``get_command_log``, ``reconnect``)".
  Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/packages/labmcp/src/labmcp/server.py#L1-L60 and #L182-L264
- `__init__` signature `server.py:199-226`: `name`, `connect: Callable[[ConnectContext], D]`, `instructions`, `limits: list[Limit]`, `package`, `address_help`, `option_help`, `connect_on_start: bool = False`. Lazy thread-safe `driver` property `:231-264`. Limit check helper `check(limit, value, what)` `:270`.
- `Settings` dataclass `:111-121` (`address`, `simulate: bool = False`, `read_only: bool = False`, `timeout`, `audit_log`, `limits: dict[str, float]`, `options`); `ConnectContext` `:124-179`.
- Transport layer: `packages/labmcp/src/labmcp/transports/{base.py, serial.py, tcp.py, visa.py, sim.py}` (`sim.py` = `ByteSimulator` / `LineSimulator` + `SimulatedTransport`).

Simulate vs real execution switch:
- `ConnectContext.open_transport(*, simulator=None, **defaults)` at `server.py:154-179` — with `simulate=True` it builds `SimulatedTransport(simulator(), audit=self.audit, …)`, otherwise `open_transport(self.require_address(), …)`. Link: …/server.py#L154-L179
- `ConnectContext.require_address()` `:146` raises `InstrumentConnectionError` with message (verbatim):
  > "No instrument address configured. Start the server with `--address <address>` (or set LABMCP_ADDRESS), or use `--simulate` to try it without hardware."
- Env configuration `configure_from_env()` `:334-362` reads `LABMCP_ADDRESS`, `LABMCP_SIMULATE`, `LABMCP_READ_ONLY`, `LABMCP_TIMEOUT`, `LABMCP_AUDIT_LOG`, `LABMCP_LIMITS`, `LABMCP_OPTIONS`.
- `docs/architecture.md:24-30` layer table: Driver = "Pure protocol implementation. No MCP code, so it can be reused from scripts and notebooks."; Simulator = "Emulates the instrument **at the wire level**, so `--simulate` and the tests exercise the same parsing code as real hardware." Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/docs/architecture.md#L24-L30

Read-only / safe mode:
- `server.py:325-330`:
  > `if read_only:`
  > `    self.mcp.disable(tags={"control"})`
  > `else:`
  > `    self.mcp.enable(tags={"control"})`
  i.e. read-only is implemented by FastMCP tool-tag filtering, not by per-tool `if` checks. Link: …/server.py#L325-L330
- `docs/architecture.md:41-56` documents `--read-only` / `LABMCP_READ_ONLY=1` as "Hide all CONTROL and HAZARD tools".
- `docs/writing-a-server.md:75-84` tool-kind table + rule "If a HAZARD tool exists, a matching SAFETY tool (stop/off) must exist too." Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/docs/writing-a-server.md#L75-L84

Safety limits:
- `packages/labmcp/src/labmcp/safety.py` (127 lines): docstring "Limits are checked *before* a command is sent to the instrument, and the scientist can tighten or relax them at launch with ``--limit name=value`` or the ``LABMCP_LIMITS`` environment variable"; `@dataclass(frozen=True) class Limit` `:20-36` (`name`, `default: float`, `unit`, `description`, `kind: "max" | "min"`), `class SafetyLimits` `:47` with `override` `:52`, `check` `:70`, `as_dict` `:95`, `parse_limit_args` `:108`. Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/packages/labmcp/src/labmcp/safety.py#L1-L127
- `docs/writing-a-server.md:86-97` rule 3: "Typed inputs with bounds: `Annotated[float, Field(ge=0, le=2000, description=\"…\")]`. Bounds that are hardware maxima go in `Field`; bounds a lab might want to tighten go in `Limit`s checked with `server.check(...)` **before** anything is sent."
- `SAFETY.md:5-11`: "They are **not** certified safety systems or medical devices, and they are **not** validated for GxP, clinical, or diagnostic use." / "**Supervising** any agent that controls hazardous equipment. Never leave an agent running hazardous equipment unattended." / "Software limits add to hardware protection. They never replace it." Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/SAFETY.md#L5-L11

Confirmation / approval gating:
- No server→client elicitation. `docs/architecture.md:37` (verbatim):
  > "**No server→client callbacks.** The MCP `2026-07-28` protocol is sessionless, so tools never depend on mid-call elicitation. Confirmation for hazardous actions comes from the client, via the `destructiveHint` annotation, and safety comes from server-side limits that the model can't override."
  Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/docs/architecture.md#L37
- Client-side policy guidance, `SAFETY.md:25-33` item 4: "**Keep a human in the loop** for hazard tools. Don't auto-approve destructive tools in your MCP client for hazardous instruments."
- Hard parameter gating (only real hardware-confirmation parameter found): `servers/biology/opentrons/src/labmcp_opentrons/server.py:737-749`
  > `start_run(protocol_id, deck_confirmed: Annotated[bool, Field(description="True only after the user confirmed the deck matches get_protocol's layout …")])`
  and refusal: `if not deck_confirmed: raise … "Refused: deck not confirmed. Call get_protocol, show the user the required deck layout, and call start_run again with deck_confirmed=true once they confirm the deck is set up and clear."`
  Server instructions at `:80-82`: "Before `start_run` or `home_robot`, ask the user to confirm the deck matches the layout from … Only then pass `deck_confirmed=true`."; `:366` "Analysis OK. Show the user the deck layout and get confirmation before start_run."
  Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/servers/biology/opentrons/src/labmcp_opentrons/server.py#L737-L749
- Instruction-level gating (prompt text, not enforced parameters) appears in server `instructions` strings, e.g. `servers/chemistry/ocean-spectrometer/src/labmcp_ocean_spectrometer/server.py:502-503, 523-524, 633, 657`; `servers/physics/thorlabs-power-meter/.../server.py:388-389`; `servers/physics/keithley-smu/.../server.py:66, 298`; `servers/chemistry/palmsens-potentiostat/.../server.py:72`; `servers/engineering/labjack/.../server.py:80, 521`; `servers/engineering/ni-daqmx/.../server.py:53, 384`; `servers/protocols/epics/.../server.py:75` "Only write PVs the user has explicitly asked you to"; `servers/biology/micro-manager/.../server.py:87-88, 444`; `servers/biology/atlas-ezo-sensors/.../server.py:78`; `servers/biology/new-era-syringe-pump/.../server.py:73`; `servers/chemistry/thermo-iapi/.../server.py:102`; `servers/physics/srs-rga/.../server.py:361, 843, 877, 911`; `servers/engineering/bench-power-supply/.../server.py:132, 346-352` (`all_off_confirmed` parameter).
- README safety narrative, `README.md:240-246`: "1. **Practise first** with `--simulate`. 2. **Start in read-only mode** with `--read-only`… 3. **Set limits for your experiment**… 4. **Approve risky actions yourself.** Actions that heat, move, dispense or switch on power are marked as hazardous (⚠️), so your AI app asks before running them. Don't turn on \"always allow\" for these. 5. **Keep the instrument's own safety features on**". Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/README.md#L240-L246

Audit logging:
- `packages/labmcp/src/labmcp/audit.py` (103 lines): docstring "Every byte exchanged with an instrument is recorded … The most recent entries are kept in memory (exposed through the ``get_command_log`` tool) and, when a path is configured, appended to a JSON Lines file"; `Direction = Literal["write", "read", "event"]` `:19`; `MAX_ENTRY_CHARS = 2048` `:24`; `class AuditLog` `:28` with `__init__(path=None, maxlen=500)`, `record` `:38`, `event` `:70`, `recent` `:74`. Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/packages/labmcp/src/labmcp/audit.py#L1-L103
- Built-in read tool exposing it: `server.py:465` `@mcp.tool(**READ) def get_command_log(limit: int = 20) -> list[dict[str, Any]]`. Other built-ins: `:459 get_connection_info() -> dict[str, Any]` (READ), `:471 reconnect() -> dict[str, Any]` (SAFETY). Link: …/server.py#L456-L477

### 1.6 Long-running operations

- Job-id + poll + cancel pattern exists only in the SiLA 2 bridge: `servers/protocols/sila2/src/labmcp_sila2/server.py`
  - `call_command(feature, command, parameters, wait_s: Annotated[float, Field(ge=0, le=MAX_WAIT_S, description="Observable commands: wait up to this long for completion (0 = return at once)")] = 0.0, metadata)` → returns an `execution_id` for observable commands.
  - `get_command_status(execution_id)` → "waiting / running / finishedSuccessfully / finishedWithError, progress, estimated remaining time and the latest intermediate response."
  - `get_command_result(execution_id)`, `list_executions()`, and `cancel_command(execution_id, all_commands=False)` declared as `safety` kind ("Cancel a running observable command, or all commands, through SiLA's CancelController feature") — always available, even in read-only mode.
  Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/servers/protocols/sila2/src/labmcp_sila2/server.py (tool table in 1.3)
- Opentrons server: `upload_protocol(..., wait_for_analysis_s: Annotated[float, Field(ge=0, le=300, …)] = 90)`, `list_runs(limit 1..50)`, `get_run_status(run_id, recent_commands 1..50)`, `pause_run`, `resume_run`, `stop_run` — status polling, no job id. Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/servers/biology/opentrons/src/labmcp_opentrons/server.py
- General rule for all other servers (blocking with a bounded deadline), `docs/writing-a-server.md:92` rule 5 (verbatim):
  > "Long operations: keep total runtime bounded by a `Limit` and enforce that deadline in the driver … Don't rely on the tool's `timeout=`: FastMCP can't interrupt a sync tool running in a worker thread, so the call still runs to completion (and keeps holding the transport lock)."
  Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/docs/writing-a-server.md#L86-L97
- Concurrency model, `docs/architecture.md:36`: "Sync drivers, threaded tools. … FastMCP runs sync tools in a thread pool … each transport serialises access with a re-entrant lock … `reconnect` closes the transport without waiting for the lock".
- Large data handling, `docs/writing-a-server.md:92-97` rules 6-7: downsample + `max_points` + `save_path` + `labmcp.prepare_save_path`; no NaN/inf in returned numbers.

### 1.7 Tests

Test files found in the tree (41 blobs):
- `packages/labmcp/tests/test_core.py` (31777 B) — core package tests.
- `servers/<domain>/<instrument>/tests/test_server.py` for all 27 server directories.
- `servers/protocols/modbus/tests/test_register_map.py`.
- Data fixtures: `servers/chemistry/ms-data/tests/data/dda_test.d/analysis.tdf{,_bin}`; `servers/chemistry/ms-worklist/tests/golden/{agilent_masshunter.csv, sciex_os.csv, thermo_xcalibur.csv, waters_masslynx.csv}`.
- CI: `.github/workflows/ci.yml`, `.github/workflows/release.yml`.

Tests run against the built-in simulator, not hardware. `docs/writing-a-server.md:99-106` (verbatim requirements): driver-level tests against the simulator including one **error reply**; an MCP round-trip through `labmcp.testing.simulated_client(server)`; `read_only=True` hides every CONTROL/HAZARD tool and keeps SAFETY tools; each safety limit refuses an out-of-range request. Helper: `packages/labmcp/src/labmcp/testing.py:26-43` `simulated_client(server, **settings)` — docstring "Configure ``server`` in simulate mode and yield an in-memory FastMCP client" (defaults `simulate=True, read_only=False`, `from fastmcp import Client`). Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/packages/labmcp/src/labmcp/testing.py#L26-L43

### 1.8 Catalog + metadata mechanics

- `packages/labmcp/src/labmcp/catalog.json` (mirrored at repo root, 164306 B): keys `schema = 1`, `repository`, `servers[32]`. Each server entry: `name`, `domain`, `category`, `vendor`, `models[]`, `interfaces[]`, `protocol`, `summary`, `status`, `package`, `version`, `description`, `registry_description`, `command`, `module`, `path`, `extras`, `tools[]`. Tool entries: `{name, kind, description}` only.
- **All 32 server entries have `status = "simulated"`.** `docs/writing-a-server.md:114`: "New servers start as `status = \"simulated\"`; change it to `hardware-verified` only after someone has tested it on real hardware" + `[tool.labmcp]` metadata keys.
- Generator script: `scripts/build_catalog.py` (12157 B); scaffold generator `scripts/new_server.py` (7673 B).
- Other documented conventions: `docs/writing-a-server.md:86-97` rule 1 "scientist-level verbs", rule 2 "units in names", rule 4 structured outputs via Pydantic, rule 8 "Docstrings are prompts.", rule 9 `instructions=` 3–8 bullets, rule 10 `connect_on_start=True` for pushing instruments. `docs/architecture.md:38` address URIs: `serial:///dev/ttyUSB0?baudrate=9600`, `tcp://10.0.0.5:5025`, `visa://GPIB0::22::INSTR`. `docs/architecture.md:39` "Everything is data-first. Measurements come back as Pydantic models with units in the field names and timestamps."
- `README.md` section headings (verbatim, with lines): `:31 Contents`, `:46 How it works`, `:61 Try it in 10 minutes`, `:116 Supported instruments` (`:128 Biology`, `:138 Chemistry`, `:152 Physics`, `:163 Health`, `:171 Engineering`, `:181 Universal Protocols`), `:197 Connect your real instrument`, `:238 Safety`, `:258 What every connector includes`, `:275 Example requests`, `:288 FAQ`, `:310 Contributing`, `:347 Roadmap`, `:362 License & citation`.
- `docs/clients.md` (117 lines) headings: Claude Code `:22`, Claude Desktop `:31`, Cursor/Windsurf `:52`, VS Code `:56`, OpenAI Codex CLI `:72`, Environment variables `:82`, "Remote / shared instruments (HTTP)" `:98`, Troubleshooting `:108`. `docs/official-servers.md` (34 lines): Instrument control `:7`, Related official servers `:16`, Watch list `:28`.

### 1.9 Tecan-relevant entries inside this repo

- `servers/biology/tecan-cavro-pump/` — "Tecan Cavro Syringe Pump", package `labmcp-cavro`, module `labmcp_cavro.server`, 9 tools (`aspirate_ul`, `dispense_ul`, `get_command_log`, `get_connection_info`, `get_status`, `initialize`, `reconnect`, `set_valve`, `terminate`). README (107 lines) headings: Try it without hardware `:15`, Connect your pump `:23`, Add to your MCP client `:38`, Tools `:60`, Safety limits `:76`, Example prompts `:85`, Notes `:93`, Hardware verification `:102`. Link: https://github.com/K-Dense-AI/lab-instrument-mcps/tree/HEAD/servers/biology/tecan-cavro-pump
- `servers/protocols/sila2/` — "SiLA 2 Bridge — MCP Server", 13 tools incl. `discover_servers` (mDNS `_sila._tcp`) and `list_features` (reads SiLA Feature Definitions); `fdl.py` 31916 B. README (135 lines) headings: Try it without hardware `:15`, Connect your SiLA server `:31`, Add to your MCP client `:42`, Tools `:64`, Safety limits `:84`, Example prompts `:105`, Notes `:114`, Hardware verification `:130`. Link: https://github.com/K-Dense-AI/lab-instrument-mcps/tree/HEAD/servers/protocols/sila2
- `servers/chemistry/ms-worklist/` — "LC-MS Worklist Builder (MassLynx, SCIEX OS, MassHunter, Xcalibur)", `formats.py` 40571 B; README has a "Not implemented" section at `:137`; export does not start an acquisition. Link: https://github.com/K-Dense-AI/lab-instrument-mcps/blob/HEAD/servers/chemistry/ms-worklist/README.md#L137


## 2. yerbymatey/opentrons-mcp

- Repo: https://github.com/yerbymatey/opentrons-mcp — **7 stars**, last commit/push **2025-06-24T21:37:13Z**, default branch `main`, language JavaScript, not a fork.
- Complete git tree is 8 entries only: `.gitignore`, `LICENSE` (1067 B), `README.md` (7169 B, 249 lines), `images/chat.png`, `index.js` (75481 B, 2295 lines), `package-lock.json`, `package.json` (609 B). Link: https://github.com/yerbymatey/opentrons-mcp/tree/HEAD

### 2.1 Library + transport

- `package.json`: `name "opentrons-mcp"`, `version 1.0.23`, description "MCP server for Opentrons HTTP API documentation and endpoint discovery", `"type": "module"`, `bin ./index.js`, deps `"@modelcontextprotocol/sdk": "latest"`, `axios ^1.6.0`, `nats ^2.19.0` (`nats` is not imported in `index.js`), `engines.node >= 18`. Link: https://github.com/yerbymatey/opentrons-mcp/blob/HEAD/package.json
- Official TypeScript SDK, stdio only: `index.js:4` `import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"`; `index.js:2288-2290` `const transport = new StdioServerTransport(); await this.server.connect(transport);`. Link: https://github.com/yerbymatey/opentrons-mcp/blob/HEAD/index.js#L2288-L2290
- Low-level `Server` (not the high-level MCP server class), capabilities = tools only: `index.js:13-23` `new Server({name: "opentrons-mcp", version: "1.0.0"}, {capabilities: {tools: {}}})` → **no resources, no prompts declared**. Link: …/index.js#L13-L23
- Talks to the robot over plain HTTP: `http://${robot_ip}:31950/...` (e.g. `POST /runs` at `index.js:1734`, `POST /runs/{run_id}/actions` at `:1778`, `GET /runs` at `:1822`, `GET /protocols` at `:1667`).

### 2.2 Full tool list

Tool array literal: `index.js:33-234` (14 tool objects); dispatch `switch` at `index.js:241-272`.

| Tool | index.js lines | Parameters (from `inputSchema`) | Description (verbatim) |
|---|---|---|---|
| `search_endpoints` | 34-61 | `query`, `method`, `tag`, `include_deprecated` | "Search Opentrons HTTP API endpoints by functionality, method, path, or any keyword" |
| `get_endpoint_details` | 62-79 | `method`, `path` | — |
| `list_by_category` | 80-101 | `category` | — |
| `get_api_overview` | 102-110 | none | — |
| `upload_protocol` | 111-125 | `robot_ip` str, `file_path` str (".py or .json"), `protocol_kind` enum ["standard","quick-transfer"] default "standard", `key` str ("Optional client tracking key (~100 chars)"), `run_time_parameters` object; required `[robot_ip, file_path]` | — |
| `get_protocols` | 126-137 | `robot_ip`, `protocol_kind` enum; required `[robot_ip]` | — |
| `create_run` | 138-150 | `robot_ip`, `protocol_id`, `run_time_parameters` object; required `[robot_ip, protocol_id]` | — |
| `control_run` | 151-163 | `robot_ip`, `run_id`, `action` enum ["play","pause","stop","resume-from-recovery"]; all three required | "Control run execution (play, pause, stop, resume)" |
| `get_runs` | 164-174 | `robot_ip` | — |
| `get_run_status` | 175-186 | `robot_ip`, `run_id` | — |
| `robot_health` | 187-197 | `robot_ip` | — |
| `control_lights` | 198-209 | `robot_ip`, `on` boolean | — |
| `home_robot` | 210-222 | `robot_ip`, `target` enum ["robot","pipette"] default "robot", `mount` enum ["left","right"] | — |
| `poll_error_endpoint_and_fix` | 223-233 | `json_filename` (default `"error_report_20250622_124746.json"`), `original_protocol_path` (default `"/Users/gene/Developer/failed-protocol-5.py"`) — hardcoded developer defaults | — |

Implementation methods: `makeApiRequest` `index.js:1476`, `uploadProtocol` ~`:1500+`, `getProtocols` `:1661`, `createRun` `:1718`, `controlRun` `:1766`, `getRuns` `:1817`, `getRunStatus` `:1871`, `homeRobot` `:2015`, `pollErrorEndpointAndFix` + fix generation `~:2230-2285`.

### 2.3 Run lifecycle split across tools

Upload → `upload_protocol`; list → `get_protocols`; create → `create_run`; start/pause/resume/stop → **one** tool `control_run` with an `action` enum (`play`, `pause`, `stop`, `resume-from-recovery`); status → `get_runs` / `get_run_status`; health → `robot_health`; homing → `home_robot`. There is no separate `start_run`/`stop_run` tool pair.

### 2.4 Gating before starting a physical run

**NOT FOUND.** No confirmation parameter, no dry-run gate, no read-only mode, no safety-limit check anywhere in `index.js`: `create_run` and `control_run(action: "play")` issue the HTTP call directly (`index.js:1718-1760`, `:1766-1810`). The only pre-flight-ish tool is `robot_health` (`index.js:187-197`, `:1990+`), which the model may or may not call.

Adjacent finding: the server contains an LLM call inside the server itself — `index.js:2240-2280` builds a prompt and calls `https://api.anthropic.com/v1/messages` with model `"claude-3-5-sonnet-20241022"` (requires `anthropicKey`) to regenerate a failed Flex protocol; `index.js:2234-2237` injects "RUN CONTEXT: - Run ID … - Successfully completed steps: ${lastCompletedStep}".

Hardcoded API knowledge: `loadApiEndpoints()` at `index.js:276+` builds `this.endpoints` (objects `{method, path, summary, description, tags, responses, parameters}`) from "Comprehensive endpoint data extracted from Opentrons API docs and source code" (`index.js:277`); category strings include "Data files Management", "Simple Commands", "Flex Deck Configuration" (`index.js:92`), `'Flex Deck Configuration': 'Flex-specific deck setup and configuration'` (`index.js:1421`).

## 3. K-Dense-AI/scientific-agent-skills

- Repo: https://github.com/K-Dense-AI/scientific-agent-skills — **47859 stars**, last commit/push **2026-10-05T09:39:11Z**, default branch `main`, language Python, not a fork. Homepage `arxiv.org/abs/2609.00065`.
- GitHub description (verbatim): "177 ready-to-use validated skills plus 100+ scientific databases"

### 3.1 Top-level layout and categorization

From `GET /repos/K-Dense-AI/scientific-agent-skills/git/trees/HEAD?recursive=1` (3497 entries):

| Path | Notes |
|---|---|
| `.github/` | 13 entries |
| `AGENTS.md` | 18546 B |
| `CITATION.cff`, `CODE_OF_CONDUCT.md` (5600 B), `CONTRIBUTING.md` (21147 B), `LICENSE.md` (1068 B), `SECURITY.md` (6009 B) | |
| `README.md` | 84156 B |
| `docs/` | 365 entries, incl. `docs/skills.md` (80994 B — the skill index), `docs/examples.md` (336584 B), `docs/security-report.json` (1094172 B), `docs/security-report.md` (50273 B), `docs/security-triage.md` (16066 B), `docs/open-source-sponsors.md` (11304 B) |
| `plugin.json` | 672 B |
| `pyproject.toml` | 900 B |
| `scan_skills.py` | 23346 B (skill scanner); `scan_pr_skills.py` |
| `skills/` | **2556 entries = 177 immediate subdirectories, one per skill; flat, no category subdirectories** |
| `tests/` | 549 entries, incl. `tests/skill-requirements.toml` (25191 B) |

Categorization mechanism: no folder taxonomy — categorization lives in the index document `docs/skills.md` and in each skill's frontmatter `description`. Lab-automation-related skill directories found by name: `benchling-integration`, `opentrons-integration`, `protocolsio-integration`, `pylabrobot`. Link: https://github.com/K-Dense-AI/scientific-agent-skills/tree/HEAD/skills

### 3.2 skills/pylabrobot/

File tree (19 files): `SKILL.md` 11895 B (**244 lines**); `assets/protocol-manifest.schema.json` 8070 B; `references/analytical-equipment.md` 7501 B, `references/hardware-backends.md` 7662 B, `references/liquid-handling.md` 8896 B, `references/material-handling.md` 8588 B, `references/resources.md` 9339 B, `references/review.md` 6406 B, `references/visualization.md` 6611 B; `scripts/__init__.py` 72 B, `scripts/_common.py` 27774 B, `scripts/check_deck_geometry.py` 1301 B, `scripts/generate_simulation_plan.py` 4793 B, `scripts/inspect_backends.py` 7482 B, `scripts/plan_transfers.py` 1556 B, `scripts/validate_manifest.py` 1391 B.
Link: https://github.com/K-Dense-AI/scientific-agent-skills/tree/HEAD/skills/pylabrobot

SKILL.md frontmatter, verbatim (lines 1-12):

> ```
> ---
> name: pylabrobot
> description: Develops and reviews PyLabRobot lab-automation resources, liquid-handling plans, offline simulations, and supported-device integrations. Supports PyLabRobot protocols and API questions; keep physical execution behind an explicit operator safety gate.
> license: MIT
> compatibility: Verified against PyLabRobot 0.2.2 on Python 3.9+. Bundled planning CLIs require only Python 3.11+ and make no serial, USB, or network connections. Physical devices need model-specific extras, configuration, calibration, and trained operator approval.
> allowed-tools: Read Write Edit Bash
> metadata:
>   version: "1.5"
>   skill-author: "K-Dense Inc."
>   pylabrobot-version: "0.2.2"
>   last-reviewed: "2026-10-01"
> ---
> ```

Section headings, verbatim (line numbers): `:14 # PyLabRobot`, `:20 ## Verified snapshot`, `:36 ## Non-negotiable hardware boundary`, `:71 ## Required intake`, `:90 ## Reproducible install`, `:104 ## Offline-first workflow`, `:136 ## Verified software-only example`, `:185 ## API rules that prevent stale code`, `:206 ## References`, `:221 ## Dated upstream sources`, `:229 ## Citing Scientific Agent Skills`.
Link: https://github.com/K-Dense-AI/scientific-agent-skills/blob/HEAD/skills/pylabrobot/SKILL.md

Reference files and coverage (as listed by the skill's own `## References` section, `:206-219`): `liquid-handling.md` (liquid handling API), `resources.md` (resource tree / deck / labware), `hardware-backends.md` (supported devices and backend classes), `analytical-equipment.md` (plate readers and analytical devices), `material-handling.md` (deck operations / gripper / carriers), `visualization.md` (Visualizer), `review.md` (review ledger used when auditing a protocol).

Simulation-first / do-not-run-on-hardware text, verbatim:
- `:16-18` "Use PyLabRobot's hardware-agnostic frontends, resource tree, trackers, and device-specific backends to develop laboratory automation. Default to local manifest validation, bookkeeping, and the software-only chatterbox backend."
- `:38-41` "Never connect to, initialize, home, move, heat, shake, spin, pump, open/close, or otherwise command physical equipment automatically. Do not turn a simulation plan into a live backend merely by changing an environment variable, config value, or import."
- `:43` "Before any separately authorized live run, require a trained human to:" followed by six numbered gates (`:45-58`): confirm backend/device identity, firmware, transport, deck and protocol revision; reconcile the physical deck against the resource tree; verify calibration, teaching, motion envelopes and collision risks; review volumes, dead volume, tip type and liquid class; confirm guards, doors, waste capacity, emergency-stop readiness and PPE; approve a slow dry run or non-hazardous commissioning run when anything is new or changed.
- `:60-69` "Tracker state is **bookkeeping**, not sensing. It cannot prove that liquid or a tip is physically present." … "The Visualizer renders resource/tracker events; it does not model physics. Chatterbox prints planned operations; it does not prove calibration, reachability, collision freedom, liquid behavior, or device state."
- `:99-101` "Do not install hardware extras until the user names the device and explicitly approves its transport"
- `:106-108` "Every bundled CLI uses strict, bounded UTF-8 JSON/CSV, local non-symlink paths, fixed allowlists, and JSON output. None can select a live backend."
- `:138` "The exact backend below is software-only. Do not substitute a hardware backend."
- `:187-189` "Current names are `STARBackend`, `VantageBackend`, `EVOBackend`, and `OpentronsOT2Backend`; do not use stale `STAR`, `TecanBackend`, `OpentronsBackend`, or `ChatterboxBackend` imports."
- `:196-198` "There is no generic `from pylabrobot.liquid_handling import LiquidClass` in 0.2.2. Stable liquid classes are vendor-specific, for example `pylabrobot.liquid_handling.liquid_classes.hamilton.HamiltonLiquidClass`."
- `:221-227` "Dated upstream sources" reviewed 2026-10-01 against PyPI 0.2.2 files, `docs.pylabrobot.org/stable/`, `github.com/PyLabRobot/pylabrobot`.

MCP references: **none** — grep for "MCP" in this SKILL.md returns nothing. The skill invokes its bundled scripts as plain CLI commands through the `Bash` allowed-tool, e.g. `:110-127`:
> `python3 skills/pylabrobot/scripts/validate_manifest.py --input tests/pylabrobot/fixtures/protocol_manifest.json`
> `python3 skills/pylabrobot/scripts/check_deck_geometry.py …`
> `python3 skills/pylabrobot/scripts/plan_transfers.py --manifest … --transfers tests/pylabrobot/fixtures/transfers.csv`
> `python3 skills/pylabrobot/scripts/generate_simulation_plan.py …`
> `python3 skills/pylabrobot/scripts/inspect_backends.py --expected-version 0.2.2 --strict`

### 3.3 skills/opentrons-integration/

File tree (18 files): `SKILL.md` 15207 B (**354 lines**); `references/api_reference.md` 12935 B, `references/liquid_handling.md` 13245 B, `references/migration-api-2-19-to-2-29.md` 8711 B, `references/modules_and_deck.md` 12156 B, `references/protocol_authoring.md` 11685 B, `references/sources.md` 9295 B, `references/validation_and_operations.md` 10398 B; `requirements-flex.txt` 18 B, `requirements-ot2.txt` 17 B; `scripts/absorbance_reader_template.py` 2329 B, `scripts/basic_protocol_template.py` 1851 B, `scripts/ot2_basic_protocol_template.py` 1706 B, `scripts/pcr_setup_template.py` 4774 B, `scripts/runtime_parameters_template.py` 3295 B, `scripts/serial_dilution_template.py` 3420 B.
Link: https://github.com/K-Dense-AI/scientific-agent-skills/tree/HEAD/skills/opentrons-integration

SKILL.md frontmatter, verbatim (lines 1-11):

> ```
> ---
> name: opentrons-integration
> description: Authors, reviews, migrates, simulates, and troubleshoots official Opentrons Python Protocol API v2 protocols for Flex and OT-2 robots. Use for robot-specific liquid handling, deck and labware setup, pipettes, modules, runtime parameters, liquid classes, and Opentrons App analysis. Use pylabrobot instead when one workflow must support multiple robot vendors.
> license: MIT
> compatibility: Requires Python 3.10+ and uv for local simulation. Flex examples target opentrons 10.0.0 and API 2.29 (documented robot maximum 2.30); the separate OT-2 line targets API 2.28 and uses opentrons 9.0.0 as its local compatibility simulator. Physical execution requires compatible hardware, current robot software, and the appropriate Opentrons App.
> allowed-tools: Read Write Edit Bash
> metadata:
>   version: "2.3"
>   last-reviewed: "2026-10-01"
>   skill-author: "K-Dense Inc."
> ---
> ```

Section headings, verbatim: `:13 # Opentrons Integration`, `:15 ## Overview`, `:38 ## Safety Boundary`, `:61 ## Choose the Right Interface`, `:72 ## Required Intake`, `:93 ## Install and Simulate`, `:123 ## Protocol Skeletons`, `:125 ### Flex, API 2.29`, `:160 ### OT-2, API 2.28`, `:189 ## Authoring Workflow`, `:191 ### 1. Select robot and API level`, `:211 ### 2. Build the deck explicitly`, `:223 ### 3. Select pipettes and tips`, `:238 ### 4. Choose a liquid-handling layer`, `:257 ### 5. Add setup information and runtime controls`, `:268 ### 6. Budget resources`, `:279 ## Common Failure Modes`, `:312 ## Bundled Templates`, `:327 ## Reference Guide`, `:339 ## Citing Scientific Agent Skills`.

Safety / simulation-first text, verbatim:
- `:40-41` "Opentrons protocols control physical equipment. Never treat successful Python syntax or local simulation as permission to run on a robot."
- `:43-55` six-step pre-execution list: "1. Simulate locally with the same pinned `opentrons` version used for authoring. 2. Import the protocol into the correct Opentrons App and require successful analysis. 3. Verify robot model, software, pipettes, mounts, modules, adapters, labware definitions, deck fixtures, tip count, source volumes, dead volumes, and destination capacity. 4. Review the run preview and deck map with the operator. 5. Perform a slow dry run with nonhazardous liquid when geometry, custom labware, partial tip pickup, or gripper moves are new. 6. Keep the emergency stop accessible and follow site-specific biosafety, chemical-safety, and contamination-control procedures."
- `:57-59` "Simulation cannot verify physical calibration, liquid properties, meniscus behavior, labware manufacturing tolerances, cap or seal removal, tubing, or all possible collisions."
- `:67` "Use **PyLabRobot** for a hardware-agnostic workflow spanning vendors."
- `:98` simulation command: `uv run --no-project --isolated --python 3.12 --with "opentrons==10.0.0" opentrons_simulate protocol.py`
- `:312-321` "Bundled Templates" table (6 scripts, one purpose line each); `:327-337` "Reference Guide" table (7 reference files with "Use it for" one-liners).

MCP references: **none** (grep found none); execution is via Bash-invoked Python scripts/templates.

Other lab-automation skills in this repo: `skills/benchling-integration/`, `skills/protocolsio-integration/` (not read in detail — time box).

## 4. mosabutey/bioskills

- Repo: https://github.com/mosabutey/bioskills — **0 stars**, last commit/push **2026-01-25T00:47:19Z**, default branch `main`, language Python, **a GitHub fork of GPTomics/bioSkills** (upstream: 1215 stars, pushed 2026-08-15T13:48:11Z).

### 4.1 Count and directory taxonomy

From `GET /repos/mosabutey/bioskills/git/trees/HEAD?recursive=1` (1854 entries): **322 `SKILL.md` files**. No top-level `SKILL.md`. Layout is two levels:

```
<category>/<skill-name>/SKILL.md
<category>/<skill-name>/usage-guide.md
<category>/<skill-name>/examples/*.{py,R,sh}
```

46 category directories at repo root (entry counts from the tree): `alignment-files` 49, `alignment` 37, `atac-seq` 32, `chip-seq` 41, `clinical-databases` 27, `clip-seq` 27, `copy-number` 22, `data-visualization` 43, `database-access` 71, `differential-expression` 40, `epitranscriptomics` 30, `experimental-design` 22, `expression-matrix` 22, `flow-cytometry` 42, `genome-assembly` 42, `genome-intervals` 44, `hi-c-analysis` 42, `imaging-mass-cytometry` 32, `long-read-sequencing` 45, `metabolomics` 42, `metagenomics` 40, `methylation-analysis` 22, `microbiome` 32, `multi-omics-integration` 22, `pathway-analysis` 38, `phasing-imputation` 22, `phylogenetics` 37, `population-genetics` 32, `primer-design` 17, `proteomics` 47, `read-alignment` 22, `read-qc` 38, `reporting` 28, `restriction-analysis` 27, `ribo-seq` 27, `rna-quantification` 26, `sequence-io` 55, `sequence-manipulation` 52, `single-cell` 80, `small-rna-seq` 27, `spatial-transcriptomics` 58, `structural-biology` 41, `tcr-bcr-analysis` 27, `variant-calling` 68, `workflow-management` 22, `workflows` 147.
Other root files: `.gitignore`, `LICENSE`, `README.md`, `install-claude.sh`, `install-codex.sh`, `install-gemini.sh`.

`workflows/` holds 28 pipeline skills named `*-pipeline` (`atacseq-pipeline`, `chipseq-pipeline`, `clip-pipeline`, `cnv-pipeline`, `crispr-screen-pipeline`, `cytometry-pipeline`, `expression-to-pathways`, `fastq-to-variants`, …), same file shape.
Example skill dir contents: `single-cell/clustering/{SKILL.md, usage-guide.md, examples/cluster_scanpy.py, examples/cluster_seurat.R}`, `single-cell/batch-integration/examples/{harmony_integration.R, harmony_integration.py}`, `single-cell/cell-communication/examples/{cellchat_analysis.R, liana_analysis.py}`, `single-cell/data-io/examples/{load_10x_scanpy.py, load_10x_seurat.R}`, plus `single-cell/README.md`.
Link: https://github.com/mosabutey/bioskills/tree/HEAD

### 4.2 Naming convention

Folder = `<category>/<short-kebab-name>`; frontmatter `name:` = `bio-<category>-<short-name>` (e.g. `bio-single-cell-clustering`). The installer confirms the prefix: `install-claude.sh` option `--uninstall` "Remove all bio-* prefixed skills".

### 4.3 Representative SKILL.md

`mosabutey/bioskills/single-cell/clustering/SKILL.md` — **297 lines**. Link: https://github.com/mosabutey/bioskills/blob/HEAD/single-cell/clustering/SKILL.md

Frontmatter, verbatim (lines 1-6):

> ```
> ---
> name: bio-single-cell-clustering
> description: Dimensionality reduction and clustering for single-cell RNA-seq using Seurat (R) and Scanpy (Python). Use for running PCA, computing neighbors, clustering with Leiden/Louvain algorithms, generating UMAP/tSNE embeddings, and visualizing clusters. Use when performing dimensionality reduction and clustering on single-cell data.
> tool_type: mixed
> primary_tool: Seurat
> ---
> ```

Headings, verbatim: `:8 # Single-Cell Clustering`, `:12 ## Scanpy (Python)`, `:14 ### Required Imports`, `:21 ### PCA`, `:34 ### Determine Number of PCs`, `:44 ### Compute Neighbors`, `:51 ### Clustering (Leiden - Recommended)`, `:64 ### Clustering (Louvain)`, `:71 ### UMAP`, `:84 ### tSNE`, `:94 ### Complete Clustering Pipeline`, `:118 ### Exploring Different Resolutions`, `:131 ### PAGA (Trajectory Inference)`, `:144 ## Seurat (R)`, `:146 ### Required Libraries`, `:153 ### PCA`, `:167 ### Determine Number of PCs`, `:179 ### Find Neighbors`, `:186 ### Find Clusters`, `:197 ### Exploring Different Resolutions`, `:211 ### UMAP`, `:224 ### tSNE`, `:234 ### Complete Clustering Pipeline`, `:258 ### Access Embeddings`, `:274 ## Parameter Reference`, `:283 ## Method Comparison`, `:293 ## Related Skills`.

Notable structures: `## Parameter Reference` table (`:274-282`: `n_pcs` 10-50, `n_neighbors` 10-30, `resolution` 0.2-2.0, UMAP `min_dist` 0.1-0.5); `## Method Comparison` table (`:283-291`: `sc.tl.pca` ↔ `RunPCA`, `sc.pp.neighbors` ↔ `FindNeighbors`, `sc.tl.leiden` ↔ `FindClusters`, `sc.tl.umap` ↔ `RunUMAP`, `sc.tl.tsne` ↔ `RunTSNE`); `## Related Skills` (`:293-297`, verbatim):
> - preprocessing - Data must be preprocessed before clustering
> - markers-annotation - Find markers for each cluster
> - data-io - Save clustered results

No MCP tools referenced; content is R/Python code blocks.

### 4.4 Index / router

No dedicated router or meta-skill file (no top-level `SKILL.md`). The index is the README category table: `mosabutey/bioskills/README.md` (226 lines), `## Skill Categories` at `:81`, a 47-row table (Category | Skills | Primary Tools | Description), e.g. `:94` "| **single-cell** | 13 | Seurat, Scanpy, Pertpy, Cassiopeia | scRNA-seq QC, clustering, trajectory, communication, annotation, perturb-seq, lineage tracing |" and `:114` "| **workflows** | 28 | Various (workflow-specific) | End-to-end pipelines: RNA-seq, variants, ChIP-seq, scRNA-seq, spatial, Hi-C, proteomics, microbiome, CRISPR, metabolo… |". `README.md:133` (verbatim): "**Total: 322 skills across 47 categories**".
README headings: `:1 # bioSkills`, `:5 ## Project Goal`, `:11 ## Requirements`, `:44 ## Installation` (`:46 ### Claude Code`, `:59 ### Codex CLI`, `:69 ### Gemini CLI`), `:81 ## Skill Categories`, `:135 ## Example Usage`, `:202 ## Contributing`, `:212 ## Quality Assurance`, `:224 ## License`.
Link: https://github.com/mosabutey/bioskills/blob/HEAD/README.md#L81-L133

Installer: `mosabutey/bioskills/install-claude.sh` (381 lines), usage header verbatim "Install bioSkills to Claude Code"; options `--global` "Install to ~/.claude/skills/ (default)", `--project [PATH]`, `--list` "List available skills", `--validate` "Validate all skills before installing", `--update`, `--uninstall` "Remove all bio-* prefixed skills", `--verbose`, `--help`. Link: https://github.com/mosabutey/bioskills/blob/HEAD/install-claude.sh

Upstream GPTomics/bioSkills (for comparison): `README.md` is 372 lines and line 1 verbatim "# This repo is archived. The structuring is still valid, but we encourage branching and self customization to encode your own expertise into the skill". Upstream has more categories (incl. `epidemiological-genomics`, `temporal-genomics`, `machine-learning`) and splits installers into `install-claude.sh` (38 lines) + shared `install-common.sh` (`source "$SCRIPT_DIR/install-common.sh"`, `print_common_options`, `run_installer "$@"`, `--categories "single-cell,variant-calling"` selective install); `copy_skill_files` copies `SKILL.md` + `usage-guide.md` only. Upstream README headings include `## Performance` `:13` and installers for Claude Code `:55`, Codex CLI `:69`, Antigravity CLI `:81`, OpenCode `:95` ("OpenCode also auto-discovers Agent Skills from `~/.claude/skills/` and `~/.agents/skills/`"), OpenClaw `:109`. Link: https://github.com/GPTomics/bioSkills/blob/HEAD/README.md#L1

## 5. QinLab/claude-scientific-skills and jaechang-hits/SciAgent-Skills

### 5.1 QinLab/claude-scientific-skills

**FORK/COPY of #3.** GitHub API reports `fork: true` with `parent.full_name = "K-Dense-AI/scientific-agent-skills"`. 1 star, last commit/push 2026-03-03T16:29:58Z. Stopping here as instructed.
Link: https://github.com/QinLab/claude-scientific-skills

### 5.2 jaechang-hits/SciAgent-Skills

- Repo: https://github.com/jaechang-hits/SciAgent-Skills — **370 stars**, last commit/push **2026-09-29T03:07:14Z**, default branch `main`, language Python. **Not a GitHub fork** (`fork: false`), but its skill folder names overlap K-Dense's (`benchling-integration`, `protocolsio-integration`, `pylabrobot`, `opentrons-integration`) → relationship is a copy/derivative by folder-name overlap, not a fork flag.
- GitHub description (verbatim): "197 bioinformatics & life science skills … BixBench 92.0% accuracy … Powers OmicsHorizon"

Top-level layout (tree, 613 entries): `.claude-plugin/{marketplace.json 940 B, plugin.json 837 B}`; `.claude/skills/sciagent-skill-creator/{SKILL.md 9263 B, scripts/scaffold.py 9386 B, scripts/validate_description.py 2903 B}` (meta-skill that scaffolds and validates skills); `AGENTS.md` 32315 B; `CLAUDE.md` 11 B; `README.md` 13357 B; `registry.yaml` 124494 B; `scripts/validate_registry.py` 3422 B; `scripts/blind_knowledge_test.py` 12682 B; `blind_test_results.csv` 35867 B; `assets/benchmark.png`; `templates/{SKILL_TEMPLATE.md 6568 B, SKILL_TEMPLATE_PROSE.md 4720 B, SKILL_TEMPLATE_TOOLKIT.md 9889 B}`; `references/{format-rules-detail.md 7330 B, migration-rules.md 21837 B, quality-checklist-by-type.md 13879 B, tool-type-adaptations.md 18078 B}`; `integration-templates/{AGENTS.md 1379 B, cursor-rules.md 937 B, windsurf-rules.md 945 B}`; `tests/{test_registry.py 4346 B, test_skill_quality.py 27809 B}`; `pixi.toml`, `pixi.lock`; `legacy/{opentrons-integration/SKILL.md 17797 B, plotly-interactive-visualization/SKILL.md 18178 B, seaborn-statistical-visualization/SKILL.md 16506 B, single-cell-annotation/SKILL.md 12811 B}`.

Unlike #3, `skills/` **is** categorized: `skills/<category>/<skill-name>/SKILL.md`. 12 categories: `biostatistics`, `cell-biology`, `data-visualization`, `genomics-bioinformatics` (nested sub-dirs `alignment/`, `annotation/`, `databases/`, `interval-ops/`, `qc/`, `rnaseq/`, `single-cell/`, `variant/`), `lab-automation`, `medical-imaging`, `molecular-biology`, `proteomics-protein-engineering`, `scientific-computing`, `scientific-writing`, `structural-biology-drug-discovery`, `systems-biology-multiomics`.

Lab-automation skills (`skills/lab-automation/`, 5 skills, each a single `SKILL.md` with no `references/` or `scripts/` dirs):
| Skill | Path | Size |
|---|---|---|
| Benchling integration | `skills/lab-automation/benchling-integration/SKILL.md` | 17243 B |
| Opentrons Protocol API | `skills/lab-automation/opentrons-protocol-api/SKILL.md` | 30944 B |
| Protocols.io integration | `skills/lab-automation/protocolsio-integration/SKILL.md` | 17216 B |
| PyLabRobot | `skills/lab-automation/pylabrobot/SKILL.md` | 16200 B |
| Western blot quantification | `skills/lab-automation/western-blot-quantification/SKILL.md` | 20819 B |

Links: https://github.com/jaechang-hits/SciAgent-Skills/tree/HEAD/skills/lab-automation , https://github.com/jaechang-hits/SciAgent-Skills/blob/HEAD/legacy/opentrons-integration/SKILL.md

Some skills do carry `references/` (e.g. `skills/genomics-bioinformatics/single-cell/scanpy-scrna-seq/references/{api_reference.md, plotting_guide.md, standard_workflow.md}`) and a few carry `scripts/` (e.g. `skills/data-visualization/molecular-visualization-3dmol/scripts/mol_viewer.py`; `skills/scientific-computing/neb-irc-activation-energy/scripts/{check_result.py, pipeline.yaml, plot_irc.py, setup_env.sh}`).

## 6. raonyguimaraes/bioinformatics-mcp vs the bio-mcp org

### 6.1 raonyguimaraes/bioinformatics-mcp

- Repo: https://github.com/raonyguimaraes/bioinformatics-mcp — **0 stars**, last commit/push **2026-07-27T10:52:43Z**, default branch `main`, language Python, not a fork.
- GitHub description (verbatim): "62 tools for genomics… scanpy, biopython, squidpy, NCBI, UniProt, Ensembl, PubMed"
- `server.json` description (verbatim): "A comprehensive MCP server for bioinformatics — 72 tools for genomics, transcriptomics, epigenetics, variant analysis, and public database queries." `"transport": ["stdio", "sse", "streamable-http"]`, `"capabilities": {"tools": true, "resources": true, "prompts": true}`, `"run": {"command": "python", "args": ["-m", "bioinformatics_mcp"]}`. Link: https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/server.json
- `README.md:102` heading (verbatim): "## Available Tools (70+)" — grouped by category at `:104 Data I/O`, `:115 Quality Control`, `:123 Preprocessing & Clustering`, `:135 Differential Expression`, `:141 Enrichment Analysis`, `:147 Cell Type Annotation`, `:153 Dataset Operations`, `:162 Spatial Transcriptomics`, `:173 Methylation & Epigenetics`, `:183 Public Database Connectors (zero config, no API keys)`, `:201 Sequence Tools`, `:211 Variant Tools`, `:217 Visualization`; `:226 ## Guided Workflows (MCP Prompts)`. Link: https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/README.md#L102-L226

Measured from source: **70 `@mcp.tool`-decorated functions across 13 modules** (count of `^@mcp.tool` lines in `src/bioinformatics_mcp/tools/*.py`). Tool names are **plain function names with no namespace or prefix** — no `blastn_`/`sc_` prefixing, no tool-name remapping in the decorator.

| Module (`src/bioinformatics_mcp/tools/`) | Tools | Registered names (def order) |
|---|---|---|
| `annotation.py` (6308 B) | 2 | `annotate_cell_types`, `list_marker_panels` |
| `data_io.py` (6624 B) | 7 | `load_anndata`, `load_csv`, `load_10x_mtx`, `save_anndata`, `list_datasets`, `inspect_dataset`, `remove_dataset` |
| `databases.py` (35547 B) | 14 | `search_ncbi_gene`, `fetch_ncbi_gene_details`, `fetch_ncbi_sequence`, `search_ncbi_nucleotide`, `search_uniprot`, `fetch_uniprot_entry`, `search_ensembl_genes`, `fetch_ensembl_sequence`, `search_pubmed`, `fetch_pubmed_abstract`, `fetch_gene_ontology`, `search_clinvar`, `get_protein_interactions`, `get_string_enrichment` |
| `dataset_ops.py` (7349 B) | 5 | `subset_dataset`, `merge_datasets`, `rename_clusters`, `export_obs_table`, `export_gene_expression` |
| `differential_expression.py` (3344 B) | 2 | `rank_genes_groups`, `get_marker_genes` |
| `gene_set_enrichment.py` (3266 B) | 2 | `run_enrichment_analysis`, `run_gsea_preranked` |
| `methylation.py` (11547 B) | 6 | `parse_bedgraph`, `parse_methylation_table`, `methylation_summary_by_chromosome`, `find_differentially_methylated_regions`, `cpg_density`, `plot_methylation_distribution` |
| `preprocessing.py` (10353 B) | 8 | `normalize_and_log`, `find_highly_variable_genes`, `scale_data`, `run_pca`, `compute_neighbors`, `run_umap`, `run_leiden_clustering`, `run_standard_pipeline` |
| `quality_control.py` (6426 B) | 4 | `calculate_qc_metrics`, `plot_qc_metrics`, `filter_cells_and_genes`, `detect_doublets` |
| `sequence_tools.py` (6251 B) | 6 | `parse_fasta`, `gc_content`, `translate_sequence`, `reverse_complement`, `find_orfs`, `bam_stats` |
| `spatial.py` (8481 B) | 7 | `compute_spatial_neighbors`, `spatial_autocorrelation`, `neighborhood_enrichment`, `co_occurrence`, `ripley_score`, `plot_spatial`, `plot_spatial_nhood_enrichment` |
| `variant_tools.py` (4407 B) | 2 | `parse_vcf`, `vcf_summary_stats` |
| `visualization.py` (7489 B) | 5 | `plot_umap`, `plot_dotplot`, `plot_heatmap`, `plot_volcano`, `plot_marker_genes` |

Code split / registration mechanism, `src/bioinformatics_mcp/server.py` (1580 B) verbatim:

> ```
> """Main MCP server for bioinformatics-mcp.
> This module creates the FastMCP server instance and registers all tools, resources, and prompts.
> Each tool module auto-registers when imported."""
> from mcp.server.fastmcp import FastMCP
> mcp = FastMCP("bioinformatics-mcp", json_response=True)
> from .prompts import workflows
> from .resources import datasets
> from .tools import (annotation, data_io, databases, dataset_ops, differential_expression,
>                     gene_set_enrichment, methylation, preprocessing, quality_control,
>                     sequence_tools, spatial, variant_tools, visualization)
> ```
Link: https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/src/bioinformatics_mcp/server.json (see `server.json` above) and https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/src/bioinformatics_mcp/server.py

`src/bioinformatics_mcp/tools/__init__.py` (462 B) docstring, verbatim:

> "Tool modules for bioinformatics-mcp. Each module imports ``mcp`` from ``..server`` and decorates functions with ``@mcp.tool()``. Simply importing a module registers its tools."

Transport selection, `src/bioinformatics_mcp/__main__.py:22-27` `parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")`, `:28-38` `--host` default `"0.0.0.0"`, `--port` default `8000`, `:44` imports `.server` lazily ("Import here so the heavy tool registration only happens once we actually want to run."), `:55` `mcp.run(**kwargs)`. Link: https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/src/bioinformatics_mcp/__main__.py#L22-L55

MCP resources and prompts (present here, unlike #1):
- `src/bioinformatics_mcp/resources/datasets.py:13-14` `@mcp.resource("datasets://list") def list_all_datasets() -> str`; `:21-22` `@mcp.resource("datasets://{name}/info") def dataset_info(name: str) -> str`. Link: https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/src/bioinformatics_mcp/resources/datasets.py
- `src/bioinformatics_mcp/prompts/workflows.py` — 7 `@mcp.prompt()` functions: `:14 scrna_standard_analysis(data_path)`, `:43 differential_expression_workflow(dataset_name, group_column)`, `:64 variant_analysis_workflow(vcf_path)`, `:84 sequence_analysis_workflow(fasta_path)`, `:101 spatial_transcriptomics_workflow(data_path)`, `:122 methylation_analysis_workflow(data_path)`, `:140 gene_research_workflow(gene_symbol)`. Link: https://github.com/raonyguimaraes/bioinformatics-mcp/blob/HEAD/src/bioinformatics_mcp/prompts/workflows.py
- Other repo files: `models/common.py` 1555 B, `utils/{data_manager.py 3530 B, imports.py 649 B, plotting.py 810 B}`, `SKILLS.md` 3410 B, `AGENTS.md` 3438 B, `pyproject.toml` 3046 B, `Dockerfile`, `install.sh`, `examples/{claude_desktop_config.json, docker-compose.yml, quickstart.py}`, `notebooks/pbmc3k_tutorial.ipynb`, `tests/{conftest.py 3758 B, tools/test_*.py ×13, integration/test_{database_workflow, mcp_protocol, mcp_server, methylation_workflow, scrna_workflow, sequence_workflow}.py}`.
- `README.md:295` "Lazy imports — heavy deps (scanpy, squidpy, pysam) only load when their tools are called"; `README.md:299` "Multi-stage Docker — slim image for core tools, full image with everything".

### 6.2 github.com/bio-mcp (org)

Org repo list (`GET /orgs/bio-mcp/repos`, 16 repos, all Python, none forks, none archived):

| Repo | Stars | Last push | Description (verbatim) |
|---|---|---|---|
| `bio-mcp-blast` | 10 | 2025-06-29 | "🔍 MCP server for NCBI BLAST sequence similarity search" |
| `bio-mcp-samtools` | 1 | 2025-07-10 | — |
| `bio-mcp-seqkit` | 1 | 2025-07-10 | "SeqKit FASTA/FASTQ manipulation" |
| `bio-mcp-queue` | 1 | 2025-07-01 | "⚡ Distributed job queue system for long-running bioinformatics tasks" |
| `bio-mcp-evo2` | 1 | 2025-07-10 | "evo2 DNA language model" |
| `bio-mcp-template` | 0 | — | "🧬 Template for creating new bioinformatics MCP servers" |
| `bio-mcp-bwa` | 0 | — | — |
| `bio-mcp-docs` | 0 | — | "📚 Comprehensive documentation for the Bio-MCP ecosystem" |
| `bio-mcp-examples` | 0 | — | — |
| `bio` … `bio-mcp` | 0 | — | "🧬 Bio-MCP Organization Profile" |
| `.github` | 0 | — | — |
| `bio-mcp-fastqc` | 0 | — | "FastQC and MultiQC quality control tools" |
| `bio-mcp-interpro` | 0 | — | — |
| `bio-mcp-bedtools` | 0 | — | — |
| `bio-mcp-amber` | 0 | — | "AMBER molecular dynamics suite for PDB relaxation" |
| `bio-mcp-bcftools` | 0 | — | "BCFtools variant calling utilities with intelligent tool detection" |

Tool names in this org are **prefixed with the tool-suite name**: `blastn`, `blastp`, `makeblastdb` (blast suite) and `samtools_view`, `samtools_sort`, `samtools_index`, `samtools_stats`, `samtools_flagstat`, `samtools_depth`.

`bio-mcp-blast/src/server.py` (291 lines) — tool objects built with `Tool(name=…, description=…)` and explicit `inputSchema`:
| Tool | Line | Properties | Required |
|---|---|---|---|
| `blastn` — "Nucleotide-nucleotide BLAST search" | `:40` | `query`, `database`, `evalue`, `max_hits`, `output_format` | `["query", "database"]` (`:70`) |
| `blastp` — "Protein-protein BLAST search" | `:74` | same five | `["query", "database"]` (`:104`) |
| `makeblastdb` — "Create a BLAST database from FASTA file" | `:108` | `input_file`, `database_name`, `dbtype`, `title` | `["input_file", "database_name", "dbtype"]` (`:131`) |
Link: https://github.com/bio-mcp/bio-mcp-blast/blob/HEAD/src/server.py

`bio-mcp-samtools/src/server.py` (541 lines):
| Tool | Line | Properties | Required |
|---|---|---|---|
| `samtools_view` — "View/convert SAM/BAM/CRAM files" | `:81` | `input_file`, `output_format`, `region`, `flags_include`, `flags_exclude`, `quality_min` | `["input_file"]` (`:113`) |
| `samtools_sort` | `:117` | `input_file`, `sort_by`, `output_format` | — |
| `samtools_index` | `:142` | `input_file`, `index_type` | — |
| `samtools_stats` | `:162` | `input_file`, `region`, `reference` | — |
| `samtools_flagstat` | `:184` | `input_file` | — |
| `samtools_depth` | `:198` | `input_file`, `region`, `max_depth` | — |
Link: https://github.com/bio-mcp/bio-mcp-samtools/blob/HEAD/src/server.py

Shared code between org repos: `src/tool_detection.py` is **byte-identical (11300 B, sha256 prefix `8ee1c434dc2537fb`) in `bio-mcp/bio-mcp-samtools`, `bio-mcp/bio-mcp-template/bio-mcp-template`, and `bio-mcp/bio-mcp-template/bio-mcp-blast`** → shared code is copy-paste of a template module, not a published shared package. Links: https://github.com/bio-mcp/bio-mcp-samtools/blob/HEAD/src/tool_detection.py , https://github.com/bio-mcp/bio-mcp-template/blob/HEAD/bio-mcp-template/src/tool_detection.py

Long-running handling in this org: `bio-mcp-blast/src/async_extensions.py` (9127 B) `class AsyncBlastServer(BaseBlastServer)` with `queue_url` default `"http://localhost:8000"`, adding an extra `Tool(name="blastn_async", description="Submit nucleotide BLAST search as background job")` via `_setup_async_handlers()`; `bio-mcp-blast/src/main.py` (1033 B) `argparse --mode choices=["local","queue"] default "local"`, `--queue-url default "http://localhost:8000"` → `BlastServerWithQueue(queue_url=…)` vs `BlastServer()`. Separate repo `bio-mcp-queue` = "Distributed job queue system for long-running bioinformatics tasks". Links: https://github.com/bio-mcp/bio-mcp-blast/blob/HEAD/src/async_extensions.py , https://github.com/bio-mcp/bio-mcp-blast/blob/HEAD/src/main.py , https://github.com/bio-mcp/bio-mcp-queue

Relationship between #6.1 and #6.2: no shared code found between `raonyguimaraes/bioinformatics-mcp` and the `bio-mcp` org (different package layout, different tool-registration style: `@mcp.tool` decorators on functions vs `Tool(name=…)` objects). NOT FOUND: any dependency or import between them.


## 7. Benchling MCP

Requested start page https://www.benchling.com/ai/ecosystem-extensibility → **HTTP 404 (NOT FOUND)** at fetch time. Everything below comes from Benchling help/docs pages and third-party MCP directories; `help.benchling.com` returns HTTP 403 to direct fetches, so its text is quoted from search-result snippets only.

### 7.1 Hosted vs local

- Third-party directory (verbatim): "Benchling MCP is the official, remote Model Context Protocol server hosted by Benchling as part of its AI Connectors offering. It lets external AI tools such as Claude, ChatGPT, or custom agents securely query a tenant's Benchling R&D data and return structured, grounded answers." and "Official, vendor-hosted server with no infrastructure to run or maintain." — https://growthengineer.ai/mcp-servers/benchling
- Hosted endpoints recorded in a second directory: "URL pattern: `https://<tenant>.mcp.benchling.com/mcp`", hosted instance "https://mcp.benchling.com/mcp", labelled "Verified Official / Vendor Maintained / Hosted on benchling.com", "Requires Benchling MCP, API (V3), and Deep Research enabled." — https://apigene.ai/mcp/official/benchling
- Community/local alternatives exist: https://github.com/longevity-genie/benchling-mcp and https://pypi.org/project/benchling-mcp/ — "This server implements the Model Context Protocol (MCP) for Benchling, providing a standardized interface for accessing laboratory data management and research workflows."

### 7.2 Auth method

- "Tenant-scoped OAuth 2.1 authentication respects existing Benchling permissions, governance, and audit logging." — https://growthengineer.ai/mcp-servers/benchling
- Official API auth doc (verbatim): "All calls to Benchling's API are tied to a user or app. Essentially, anything a user has permission to do through the UI, the user's API Key or OIDC token or application's OAuth Bearer token has permission to do through the API." — https://docs.benchling.com/docs/authentication (same text at https://docs.benchling.com/v2-beta/docs/authentication ; the page "covers app credentials, Delegated Auth, personal API keys, and the setup details needed to make secure requests to Benchling.")

### 7.3 Permission scoping

- "MCP queries run on behalf of the authenticated user, inheriting Benchling permissions and audit trails. Data remains within the customer's Benchling tenant at all times. Claude and other AI providers do not use Benchling data for model training." — https://help.benchling.com/hc/en-us/articles/40342713479437-Configure-Benchling-s-MCP-Server-for-other-MCP-clients (search snippet; direct fetch = HTTP 403)
- Permission model is checked across project + registry + schema: "Because registering objects in Benchling technically includes actions on the project, registry, and schema, several things are checked for access simultaneously and if any are missing the user will be blocked." — https://help.benchling.com/hc/en-us/articles/9684210957581-Permissions-overview

### 7.4 Audit

- Same help article as 7.3: MCP queries "inheriting Benchling permissions and audit trails". No separate MCP-specific audit-log page found → **NOT FOUND** for a dedicated Benchling MCP audit page.

### 7.5 Read vs write tools

- Official tool names recorded only in third-party directories. From https://apigene.ai/mcp/official/benchling ("Last updated: March 1, 2026"), 4 tools:

| Tool | Description (verbatim from the directory) |
|---|---|
| `get_entity` | "Get details for a specific Benchling entity (e.g. sequence, plasmid, result)" |
| `query_benchling` | "Query Benchling data using natural language; returns structured insights and traceable results" |
| `retrieve_data` | "Retrieve structured data from Benchling (results, notebook entries, inventory) via agentic APIs" |
| `search_experiments` | "Search and filter experiments and study data" |

- Read/write split is not documented on any official page I could retrieve → **NOT FOUND** for an official read-vs-write tool list. Third-party servers state their own policy explicitly, e.g. "This server is read-only, meaning it cannot create, update, or delete any objects in Benchling." — https://github.com/sahil-2424/mcp-servers/blob/main/benchling-mcp-server/README.md
- A community directory lists write-capable scope for a community server: "query and create notebook entries, manage DNA and amino acid sequences, retrieve assay results and run data, access inventory and sample registries, manage workflow tasks and results, query entity schemas and custom fields, retrieve team and project structures" — https://mymcptools.vercel.app/servers/benchling-mcp
- Official FAQ/help entry linked from that directory: https://help.benchling.com/hc/en-us/articles/40342713479437-Benchling-MCP (HTTP 403 on direct fetch).

## 8. PyLabRobot — resource/deck model only

- Repo: https://github.com/PyLabRobot/pylabrobot — **560 stars**, last commit/push **2026-10-07T12:09:47Z**, default branch `main`, language Python. Docs: https://docs.pylabrobot.org

### 8.1 Resource base class

`pylabrobot/resources/resource.py` (1414 lines), `:135`:

> ```python
> class Resource(SerializableMixin):
>     """Base class for deck resources."""
> ```

`__init__` signature at `:153`: `name: str, size_x: float, size_y: float, size_z: float, rotation: Optional[Rotation] = None, category: Optional[str] = None, model: Optional[str] = None, barcode: Optional[Barcode] = None, preferred_pickup_location: Optional[Coordinate] = None, metadata: Optional[Mapping[str, Any]] = None` — with docstring line "metadata: Free-form metadata. Treated as black box during serialisation."
Imports at `:13`: `from pylabrobot.serializer import SerializableMixin, deserialize, serialize`. Helpers: `_compute_location_from_anchors` `:30`, `_match_type(resource, matcher: TypeMatcher)` `:73`.
Link: https://github.com/PyLabRobot/pylabrobot/blob/HEAD/pylabrobot/resources/resource.py#L135-L153

### 8.2 Deck class

`pylabrobot/resources/deck.py` (82 lines), `:12`:

> ```python
> class Deck(Resource):
>     """Base class for liquid handler decks."""
> ```

`__init__(size_x, size_y, size_z, name="deck", origin=Coordinate(0, 0, 0), category="deck")` sets `self.location = origin`; `serialize()` pops `"model"`; `get_all_resources()` → `get_all_children()`; `clear(include_trash=False)`; `get_trash_area()` raises `ResourceNotFoundError("Trash area not found")`; `summary()` returns `"Deck: {x} x {y} mm"`; `get_trash_area96()` raises `NotImplementedError`.
Link: https://github.com/PyLabRobot/pylabrobot/blob/HEAD/pylabrobot/resources/deck.py#L12

### 8.3 Resource tree shape (class names verbatim)

The tree is `Deck → Carrier/ResourceHolder → Plate → Well`, with `Resource` as the common base and an `ItemizedResource` generic in the middle:

| Class | File:line | Declaration |
|---|---|---|
| `Resource` | `pylabrobot/resources/resource.py:135` | `class Resource(SerializableMixin)` |
| `Container` | `pylabrobot/resources/container.py:12` | `class Container(Liddable, Resource)` |
| `Well` | `pylabrobot/resources/well.py:31` | `class Well(Container)` (plus `WellBottomType` `:10`, `CrossSectionType` `:19`) |
| `ItemizedResource` | `pylabrobot/resources/itemized_resource.py:33` | `class ItemizedResource(Resource, Generic[T], metaclass=ABCMeta)` |
| `Plate` | `pylabrobot/resources/plate.py:29` | `class Plate(Liddable, ItemizedResource["Well"])` |
| `Carrier` | `pylabrobot/resources/carrier.py:19` | `class Carrier(Resource, Generic[S])` |
| `TipCarrier` | `pylabrobot/resources/carrier.py:126` | `class TipCarrier(...)` |
| `ResourceHolder` / `PlateHolder` | `pylabrobot/resources/carrier.py:154` | `class PlateHolder(ResourceHolder)` (with `get_plate_sinking_depth` `:209`) |
| `PlateCarrier` | `pylabrobot/resources/carrier.py:264` | `class PlateCarrier(...)` |
| `MFXCarrier` | `pylabrobot/resources/carrier.py:327` | `class MFXCarrier(Carrier[ResourceHolder])` |
| `TubeCarrier` | `pylabrobot/resources/carrier.py:351` | — |
| `TroughCarrier` | `pylabrobot/resources/carrier.py:379` | — |
| carrier factory methods | `pylabrobot/resources/carrier.py:410`, `:451` | `create_resources`, `create_homogeneous_resources` |
| `Deck` | `pylabrobot/resources/deck.py:12` | `class Deck(Resource)` |
| vendor decks | `pylabrobot/resources/opentrons/deck.py` (9122 B), `pylabrobot/resources/opentrons/flex_deck.py` (15353 B) | — |
| also | `pylabrobot/resources/container_rack.py`, `pylabrobot/resources/trash.py` | — |

`pylabrobot/resources/` contains 175 `.py` files.
Links: https://github.com/PyLabRobot/pylabrobot/tree/HEAD/pylabrobot/resources , https://github.com/PyLabRobot/pylabrobot/blob/HEAD/pylabrobot/resources/carrier.py#L19

### 8.4 Serialization

`pylabrobot/serializer.py` (115 lines), module docstring: `"""A simple JSON serializer."""`
- `:19` `JSON` TypeAlias; `:22` `class SerializableMixin:` with `:25 def serialize(self) -> dict` — iterates `vars(self)`, skips `_`-prefixed keys, and stamps `data["type"] = self.__class__.__name__` at `:31`.
- `:35 def serialize(obj: Any) -> JSON` — non-finite floats become the strings `"nan"` / `"Infinity"` / `"-Infinity"` (`:40-41`), enums become `obj.name`, functions become `{"type": "function", "code": marshal.dumps(...).hex(), "closure": ...}`.
- `:73 def deserialize(data: JSON, allow_marshal: bool = False) -> Any` → resolves the class with `find_subclass(klass_type, cls=SerializableMixin)` at `:105`, then `klass.deserialize(params)` or `klass(**params)` (`:109-111`).
- Test file: `pylabrobot/tests/serializer_tests.py`.
So: resources serialize to a plain dict/JSON with a `"type"` discriminator equal to the class name, and deserialization reconstructs the subclass by name.
Link: https://github.com/PyLabRobot/pylabrobot/blob/HEAD/pylabrobot/serializer.py#L22-L111

### 8.5 Tecan labware that already exists in PyLabRobot (relevant to a Fluent deck model)

`pylabrobot/resources/tecan/__init__.py` imports `.plate_carriers, .plates, .tecan_decks, .tecan_resource, .tip_carriers, .tip_creators, .tip_racks, .wash`:
- `pylabrobot/resources/tecan/tecan_resource.py:6` `class TecanResource(Resource)` (42 lines)
- `pylabrobot/resources/tecan/tecan_decks.py:36` `class TecanDeck(Deck)` (257 lines) with `_RAILS_WIDTH = 25`, `EVO100_NUM_RAILS = 30`, `EVO100_SIZE_X = 940`, `SIZE_Y = 780`, `SIZE_Z = 765`, `EVO150_NUM_RAILS = 45`, `EVO150_SIZE_X = 1315`, `EVO200_NUM_RAILS = 69`, `EVO200_SIZE_X = 1915` — i.e. EVO/FAME dimensions only; **no Fluent deck**.
- `pylabrobot/resources/tecan/plates.py:15` `TecanPlate` (924 lines); `plate_carriers.py:12` `TecanPlateCarrier` (566 lines); `tip_racks.py:74` `TecanTipRack` (1823 lines); `wash.py:13` `TecanWashStation` (82 lines, `Wash_Station*` constants); `trash.py:7` `TecanTrash` (48 lines).
Links: https://github.com/PyLabRobot/pylabrobot/blob/HEAD/pylabrobot/resources/tecan/tecan_decks.py#L36 , https://github.com/PyLabRobot/pylabrobot/tree/HEAD/pylabrobot/resources/tecan

Backends (recorded only as location, per instruction to skip backend internals): `pylabrobot/legacy/liquid_handling/backends/tecan/{EVO_backend.py, EVO_tests.py, errors.py}`; `pylabrobot/legacy/plate_reading/tecan/{infinite_backend.py, spark20m/…}`; `pylabrobot/liquid_handling/backends/tecan/__init__.py` (10 lines) is a DeprecationWarning shim: "Importing from pylabrobot.liquid_handling.backends.tecan is deprecated. Use pylabrobot.legacy.liquid_handling.backends.tecan instead."
Fluent support status: https://github.com/PyLabRobot/pylabrobot/issues/79 "Is it possible to use Tecan Fluent as backend?" — created 2024-03-18, **closed 2024-06-05**, 7 comments. rickwierenga 2024-03-18: "Unfortunately, the Fluent is not supported yet." (plus a pointer to `C:/programdata/tecan/evoware/audittrail/log for the evo`); stefangolas 2024-04-10: "we actually have collaborators interested in doing a Fluent integration at the University of Tennessee"; rickwierenga 2024-06-11: "Sure, we can do a dev branch when someone actually starts working on it. Currently, I don't think anyone has this in progress." GitHub issue search `repo:PyLabRobot/pylabrobot Fluent in:title,body` → `total_count = 1` (that issue). discuss.pylabrobot.org: **NOT FOUND** for a "Fluent" thread through the search path used.

## 9. Tecan prior art (one pass, links only)

GitHub repository search results (`GET /search/repositories`), each row: link | last push | language | what it does | parses/generates xscr / gwl / labware.

Query `"Tecan Fluent"` → `total_count = 12`:

| Link | Last push | Lang | What it does | xscr / gwl / labware |
|---|---|---|---|---|
| https://github.com/leylabmpi/pyTecanFluent | 2024-04-18 | Python | "Python interface to TECAN Fluent liquid handling robot" | unknown (not opened) |
| https://github.com/SiggiSmara/tecanFluentWorklists | 2018-08-31 | Python | worklists built from liquid-level-detection data | gwl: unknown |
| https://github.com/leylabmpi/pyTecanFluentShiny | 2022-01-05 | R | Shiny app on top of pyTecanFluent | unknown |
| https://github.com/leylabmpi/RTecanFluentShiny | 2022-08-16 | R | R Shiny variant | unknown |
| https://github.com/SMD-Bioinformatics-Lund/fluentControl_scripts | 2025-10-15 | Python | "Scripts used in Fluent Control methods on the TECAN Fluent 480 robot" | unknown |
| https://github.com/mberg-arbor/TecanFluent | 2025-04-14 | — | — | unknown |
| https://github.com/aaronslaff/tecan_fluentcontrol | 2025-07-09 | Python | "python scripts to interface with Tecan FluentControl"; file `fluent_control_examples.py` 429 lines / 15.3 KB | unknown |
| https://github.com/SoundAg/automation-Tecan-Fluent | 2024-04-16 | Python | — | unknown |
| https://github.com/angli339/tecan-fluent-api | 2026-08-18 | Python | — | unknown |
| https://github.com/exfab/Tecan_Fluent_ExFAB | 2026-03-23 | Python | "Protocols for the Tecan Fluent at UCR's ExFAB Biofoundry" | unknown |
| https://github.com/ArckLSa6834/Tecan_Fluent_480 | 2023-06-27 | — | — | unknown |
| https://github.com/Porkchop911/fluentvibe | 2026-10-07 | Python | "trying to vibecode tecan fluent protocols" — **this repo** | xscr: yes (see below) |

Query `FluentControl` → `total_count = 9`; mostly unrelated projects (`hedgehog344/FluentControls` 24★ Pascal, `themmhl/FluentControls` C# WPF, `CSharpCoders/FluentControlFlow`, `kvnhck/FluentControlExtensions`, `dReekb/mCompress`). Tecan-relevant hits:
- https://github.com/MarsLuay/Fluent-AI-Assistance | 2026-10-02 | Python | "AI-Workflow for FluentControl to produce importable zeia scripts"; folder `source/01-project-reader` = "Local, dependency-free tooling for reading Tecan FluentControl export archives and script XML. This is Layer 1, the Project Reader, in the Fluent AI-Assistance path" | xscr: no (targets `.zeia` + script XML); gwl: unknown
- https://github.com/dReekb/BarcodesMatchingScript | 2025-04-01 | — | "VB script for FluentControl to match Azenta tubes and generate a worklist" | gwl: yes (generates a worklist)

Query `FluentScript` → `total_count = 3`, all unrelated (`furesoft/FluentScript` C#; `fpfcmsr/FluentScripts` and `hamjared/FluentScripting` = Ansys Fluent).
Query `extension:xscr` → **`total_count = 0`** (GitHub code search does not index the extension token; the only `xscr` text hit anywhere was this repo).
Query `tecansila` → 0. Query `sila2 tecan` → 0.
Query `"Tecan worklist"` → `total_count = 3`:
- https://github.com/slee98/TecanWorklist | 2023-06-24 | JavaScript | — | gwl: unknown
- https://github.com/irahorecka/tecan-worklist-generator | 2026-01-31 | Python | "A DNA and protein dilution worklist generator for TECAN Freedom EVOware" | gwl: unknown (EVOware)
- https://github.com/zoharg2403/Tecan-Worklist-files-for-cherry-picking | 2023-01-26 | Python | "This script create gwl worklist files for Tecan LiHa liquid handler" | gwl: yes
Query `"Fluent SiLA"` → `total_count = 1`:
- https://github.com/Fredrik-Qi/Fork_Fluent_SiLA2_Connector | 2026-02-10 | C# | "Deepwiki for Fluent SiLA2 Connector" (a documentation mirror of the connector) | unknown
Query `"Fluent MCP"` → `total_count = 18`, **none Tecan-related** (`modesty/fluent-mcp` ServiceNow 31★, `carlosrodera/fluent-mcp-servers` WordPress 29★/175 tools, `jiweiqi/fluent-mcp-server` Ansys Fluent 9★, `netflyapp/fluentcrm-mcp-server` 13★, `blendsdk/fluentui-mcp`, `FluentData/fluent_mcp`, `mastermanas805/fluentedi-mcp` "Hosted MCP server + HTTP API: 36 deterministic tools"). → **NOT FOUND**: any Tecan Fluent MCP server on GitHub through these queries.

### 9.1 Does Tecan ship a SiLA 2 interface for FluentControl? — YES

- GitLab project (verbatim): "This Project provides SiLA2 support for the FluentControl API. This allows to use the FluentControl API using programming languages outside of the .NET ecosystem. In particular, a Python…" — https://gitlab.com/tecan/fluent-sila2-connector
- SiLA Standard device registry (verbatim): "A SiLA 2 1.1 compliant connector that enables control of the FluentControl software of the Tecan Fluent pipetting robot. This connector uses the VisionX COM API of the FluentControl software. It provides endpoints to control and monitor the execution of pipetting methods." — https://sila-standard.com/sila_device/unitelabs-tecan-fluentcontrol-connector/ and https://gitlab.com/unitelabs/connectors/tecan-fluentcontrol
- Tecan-hosted open-source connector (verbatim): "SiLA2 Connector for Fluent … exports the functionality of the FluentControl API as SiLA2 Server … also already contains a ready-made Python client" and "Control and manage the FluentControl process running under Windows. With the Fluent Controller, you can start and stop the FluentControl software with various parameters to gain access to a runtime in which you execute and control methods." — https://sila-standard.com/sila_device/tecan-fluent-opensource/
- Vendor docs: https://docs.unitelabs.io/operate/devices/tecan-fluentcontrol/connect-and-run/ ; https://unitelabs.io/hub/tecan-fluent/ — "Can you control a Tecan Fluent with Python? Yes, through FluentControl. With the UniteLabs connector you list and run FluentControl methods from Python, set their variables, and stream commands into a method while it runs."
- Mirror: https://gitlab.com/frameworks9481016/tecan-fluentcontrol
- Note: K-Dense LabMCP already ships a generic SiLA 2 → MCP bridge (§1.9), so a Fluent SiLA2 connector is reachable from MCP without a Fluent-specific server: https://github.com/K-Dense-AI/lab-instrument-mcps/tree/HEAD/servers/protocols/sila2

### 9.2 Fluent file formats (for the "what does a tool parse/emit?" column)

- `.zeia`: "FluentControl ™ includes an Export/Import tool for exchanging scripts, liquid classes, carriers and labware definitions with other installations … The Export/Import tool is accessed from the main FluentControl ™ application window and produces files with the *.zeia extension." — https://www.tecan.com/knowledge-portal/how-to-use-the-export/import-tool-in-fluentcontrol
- EVOware equivalent emits `.exd`: "processes, scripts, robot vectors, liquid classes, and carrier and labware definitions … in *.exd format" — https://www.tecan.com/knowledge-portal/how-to-use-the-export/import-tool-for-evoware
- `.xscr`: the only textual hit found is this repository's own README: "Generated .xscr files must be reviewed and validated before instrument use. The bundled Tecan/FluentControl-facing reference data is under active provenance review; see REVIEW_NOTES.md." — https://github.com/Porkchop911/fluentvibe . No third-party GitHub project found that parses or generates `.xscr` → **NOT FOUND**.
- FluentControl scripting docs (MicroScript): https://www.tecan.com/knowledge-portal/fluentcontrol-microscript-programing ; main manual: https://www.tecan.com/knowledge-portal/fluentcontrol-software-manual ; version compatibility list: https://tecan.com/hubfs/Knowledgebase/Manuals/Fluent%20Control/Compatibility%20List%20FluentControl%203.6.pdf ; "ReadMe%20FluentControl%203.4.pdf" mentions "IoT Client … Tecan Connect app … Introspect".
- labautomation.io: **NOT FOUND** for "FluentControl" / "Fluent API" (search returned unitelabs.io pages instead).
- seal.run vendor pages exist for Tecan: https://seal.run/instruments/manufacturers/tecan and https://seal.run/integrations/tecan-fluent — "FluentControl Sample Tracking PDF reports and optional CSV exports; the add-on must be configured."

## Not found / dead links

| Item | Status |
|---|---|
| K-Dense lab-instrument-mcps: MCP **resources** and **prompts** | NOT FOUND — no `@mcp.resource` / `@mcp.prompt` in any of the 32 `server.py`; only `@mcp.tool` |
| lab-instrument-mcps `catalog.json` tool parameters | NOT FOUND — tool entries carry only `{name, kind, description}`; parameters exist only in `server.py` signatures |
| lab-instrument-mcps: SSE transport | NOT FOUND — only stdio (default) and streamable HTTP (`packages/labmcp/src/labmcp/server.py:393-396`) |
| lab-instrument-mcps: server-initiated confirmation / elicitation | NOT FOUND by design — `docs/architecture.md:37` "No server→client callbacks." |
| lab-instrument-mcps: any Tecan Fluent server | NOT FOUND — only `servers/biology/tecan-cavro-pump` (Cavro syringe pump) |
| yerbymatey/opentrons-mcp: any gate before a physical run | NOT FOUND |
| yerbymatey/opentrons-mcp: resources/prompts | NOT FOUND (capabilities = tools only, `index.js:13-23`) |
| K-Dense skills: MCP tool references inside `skills/pylabrobot/SKILL.md` and `skills/opentrons-integration/SKILL.md` | NOT FOUND (grep for "MCP" returns nothing); they use Bash-invoked scripts |
| mosabutey/bioskills: dedicated router / meta-skill file | NOT FOUND — index is `README.md:81-133` |
| mosabutey/bioskills: lab-automation / Tecan / Opentrons skills | NOT FOUND in the 46 category list (no liquid-handling or lab-automation category) |
| `https://www.benchling.com/ai/ecosystem-extensibility` | **HTTP 404** |
| `https://help.benchling.com/hc/en-us/articles/40342713479437-Benchling-MCP` | **HTTP 403** on direct fetch; text available only via search snippets |
| Benchling: official read-vs-write tool list and MCP-specific audit page | NOT FOUND |
| Benchling: official tool names from a Benchling-hosted page | NOT FOUND — names come only from third-party directories (apigene.ai, growthengineer.ai) |
| PyLabRobot: Fluent deck / Fluent backend | NOT FOUND — `TecanDeck` covers EVO100/150/200 only; issue #79 closed unsupported |
| discuss.pylabrobot.org: "Fluent" thread | NOT FOUND |
| labautomation.io: "FluentControl" / "Fluent API" | NOT FOUND |
| GitHub code search for `extension:xscr` | `total_count = 0` (unauthenticated code-search API returns HTTP 401, so extension-scoped search was not usable) |
| GitHub: any Tecan Fluent MCP server | NOT FOUND across `"Fluent MCP"`, `FluentControl`, `"Tecan Fluent"` queries |
| GitHub: any project that parses or generates `.xscr` other than this repo | NOT FOUND |
| `jaechang-hits/SciAgent-Skills` → K-Dense relationship | No fork flag; only folder-name overlap. Formal provenance statement NOT FOUND |
| Shared code between `raonyguimaraes/bioinformatics-mcp` and the `bio-mcp` org | NOT FOUND |
| `bio-mcp` org: published shared package | NOT FOUND — shared code is a byte-identical copy of `src/tool_detection.py` across repos |
| Tool counts in lab-instrument-mcps `README.md` vs `catalog.json` | Not reconciled in this pass (catalog total = 411 tools / 32 servers; per-server counts in §1 tables come from `@mcp.tool` defs) |

Method note (for the reviewer): all GitHub content was read through `api.github.com` (repos/trees/commits/search) and `raw.githubusercontent.com/.../HEAD/...`; no repository was cloned and no directory was created in this repo. Unauthenticated GitHub **code**-search returns HTTP 401, so code-level searches were replaced by repository search plus direct raw-file reads. Line numbers refer to the file contents as fetched at `HEAD` on the dates recorded in each section.
