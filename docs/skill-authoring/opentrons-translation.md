# Opentrons → fluentvibe translation cheatsheet

The mapping the [SOP](SOP.md) applies when converting an Opentrons protocol's
liquid-handling into a fluentvibe skill. fluentvibe's authoring conventions live in
the existing skills (`api/core-worktable-api.md`,
`api/labware-and-liquid-classes.md`, `api/api-magnetization-model.md`) — this
file maps Opentrons concepts onto them.

## Source files per Opentrons protocol dir

| File | Use for the skill |
|---|---|
| `README.md` | The richest source: human summary, reagent tables, deck layout, parameter ranges → the skill's `description:` and workflow prose. |
| `*.py` | `metadata`/`requirements` (name, intent), `add_parameters()` (the knobs → fluentvibe variables), `run(ctx)` body (step order, volumes, loops, modules). |
| `metadata.json` | Registry only (uuid, slug). Ignore for content. |

## API mapping

| Opentrons | fluentvibe |
|---|---|
| `metadata` / `README` description | skill `description:` frontmatter + summary |
| `add_parameters()` int/float/bool/choice (volumes, counts, ratios) | `wt.declare_variable(NAME, default)` + `wt.set_sim_value(NAME, v)`; reference **by name string** in every call |
| derived value in code (e.g. `bead_vol = ratio*input`) | compute in Python, declare the result, pass the name — see derived-volume rule |
| `load_instrument('*_multi'/'flex_8channel...')` doing column work | `wt.liha` + native `wt.loop(times=12, loop_variable='col')` with `well_offset='(col-1)*8'` |
| `load_instrument('*_single')` | `wt.liha` single-channel |
| whole-96-at-once (96-channel, or "all wells") | `wt.mca96` (mount_adapter → pick_up → aspirate/dispense → return_tips → drop_adapter) |
| `load_labware('*tiprack*')` | approved `MCA100/200/500Box` or `FCA` tip boxes |
| `load_labware` plate / reservoir | `Plate96` (`96_ABgene_SuperPlate_Thermo_AB2800`) / `Trough` (`25ml_short`, `100ml`, `300ml SBS`) |
| `load_labware` deep-well / PCR-skirt / tube-rack | **whitelist addition** — resolve exact catalog name via `fluentvibe/catalog` (`find_components`/`resolve_by_name`) and add to `generation.yaml` `lab_scope.labware` before using |
| `load_module('magnetic…')` + `engage()`/`disengage()` | `wt.gripper.move(plate, onto=magnet)` / `wt.gripper.move(plate, to=(loc, site))`; beads via reagent roles — see `api-magnetization-model` |
| `load_module('thermocyclerModuleV2')`, `heaterShakerModuleV1`, `temperature module` | **no abstraction** → represent the incubation as `wt.wait(duration_seconds=…)`; note set-temp/shake intent in a `#` comment or `wt.add_comment(...)`. See [capability-roadmap](capability-roadmap.md) |
| `transfer(v, src, dst)` / `distribute` | `head.aspirate(src, "V_UL", liquid_class=…)` + `head.dispense(dst, "V_UL", …)` (+ loop for many wells) |
| `mix(reps, vol, well)` | `head.mix(well, "V_UL", cycles=reps, liquid_class=…)` |
| `delay(minutes=m)` / `delay(seconds=s)` | `wt.wait(duration_seconds=m*60)` / `wt.wait(duration_seconds=s)` |
| `pause("msg")` (manual step / centrifuge / reader) | `wt.add_comment("msg")` — author the on-deck steps only; flag the manual step |
| `aspirate(rate=…, .bottom(z=…))`, `touch_tip`, `blow_out`, `air_gap` | **not exposed** → drop; mention as an out-of-scope handling detail in prose if important |
| CSV-driven per-well volumes / pick lists (`configure_for_volume(row_vol)`, per-row volume) | **worklist** — `wt.worklist("picklist.csv")` (or build a `Gwl` and load it). Each record carries its own volume, so per-well distinct volumes and arbitrary source→dest mappings are first-class. See `api-worklists`. (Scalar `aspirate`/`dispense` + `wt.loop` is still the right choice for uniform/column-wise volumes.) |
| `define_liquid` / `load_liquid` (deck state) | initial fills via `labware.fill_all(reagent, vol)` / `well.layers` (author-side only) |

## Always apply (fluentvibe conventions)

- Use **exact approved `catalog=` names**; if the protocol needs labware not on
  the whitelist, either map to the nearest approved item or add it (with a
  resolved catalog name) — never invent a name.
- Pass volumes **and** liquid classes **by name string** (`"BEAD_VOLUME_UL"`,
  `"LIQUID_CLASS_BEADS"`), never the Python value.
- Column iteration uses a **native `wt.loop`**, never a Python `for`.
- Per-well *distinct* volumes / CSV pick lists use a **worklist** (`api-worklists`),
  not a Python `for` over scalar steps.
- Derive dependent volumes from primitives before `declare_variable`.
- No `wt.comment(...)` method — it's a `from_workspace(comment=…)` kwarg; use
  `#` comments or `wt.add_comment(...)` for step notes.
- Fill troughs for the whole run incl. dead volume: `wells × per_well × reps × 1.1`.

## What a converted skill is (and isn't)

It is a **recipe**: the variables, labware roles, ordered groups, mixing, and
incubation waits the Tecan can execute. It is **not** a faithful port of the
Opentrons protocol's module control or flow-rate tuning — those are dropped or
approximated, and the skill says so plainly. (Per-well CSV logic is **no longer**
deferred — it maps to a worklist; see `api-worklists`.)
