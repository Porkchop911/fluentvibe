# Capability-extension roadmap

Design-only. The work packages flagged `scope: needs-extension` in
[work-packages.yaml](work-packages.yaml) can only ship liquid-handling
*skeletons* until fluentvibe's object model grows. This file scopes the
extensions in priority order — what each touches and which packages it
converts from `needs-extension` to `now`. Nothing here is implemented yet.

Touch-points referenced:
- IR: `fluentvibe/ir/schema.py` (Pydantic `Step` models)
- Simulator: `fluentvibe/simulator/walk.py` (+ `snapshots.py`, `invariants.py`)
- Renderer: `fluentvibe/compiler/renderer.py` + `_assets/config/commands.yaml`
- Heads / authoring API: `fluentvibe/heads/`, `fluentvibe/worktable.py`
- Round-trip: decompiler (`fluentvibe/decompiler/`) + `tests/test_xscr_roundtrip.py`

## P1 — Per-well volume vectors  ✅ DELIVERED (via worklists, commit 88b4ef4)

**Gap (historical):** `AspirateStep.volume` / `DispenseStep.volume` are scalar
(gap G2) — every channel moves the same amount, so per-well CSV volumes couldn't
be expressed step-by-step.

**Delivered, differently than first scoped:** rather than a vector on the scalar
steps, the FluentControl **worklist DSL** now carries one volume per record —
`wt.worklist("picklist.csv")` (CSV auto-converts to GWL) or the `Gwl` builder.
Per-well distinct volumes and arbitrary source→dest pick lists are first-class.
See the `api-worklists` skill. (The only residual: worklist steps are
VALIDATION_ONLY in the simulator — it renders/validates but does not walk their
per-well volume changes. A scalar `volumes` vector on the existing steps would be
the follow-up if simulator-walked per-well volumes are ever needed.)

**Unlocked:** `cherrypicking`, real `normalization`, volume-equalized `pooling`,
and the CSV-driven distribution/PCR protocols (~45 in the corpus) — now authored
with a worklist instead of being deferred.

## P2 — Incubation / timing primitives

**Gap:** `wt.wait(duration_seconds)` exists but carries no temperature/shake
intent; assays and cell work model incubations only as bare waits.

**Change:** an annotated wait (optional `temperature_c`, `shake_rpm`, `note`) —
renders as a wait + comment now, becomes a real module step once P3/P5 land.
Smallest change; mostly schema + renderer comment.

**Unlocks:** richer `elisa`, `protein-assay`, `cell-seeding` incubations.
**Effort:** low.

## P3 — Heater-shaker module

**Change:** a `HeaterShaker` labware family (`fluentvibe/labware/`), a
set-temp/shake step type in IR, simulator state (no-op on liquid, records
temp/rpm), renderer mapping in `commands.yaml`, and a `wt`-level API to place a
plate on it and run it.

**Unlocks:** bead protocols with HS incubation, `cell-seeding`, parts of
`ngs-library-prep`.
**Effort:** medium.

## P4 — Thermocycler module

**Change:** a `Thermocycler` module + block/lid temperature and a cycle-program
step (denature/anneal/extend × N). Simulator treats it as state + timing;
renderer emits the FC thermocycler block. Largest module by surface.

**Unlocks:** `pcr-setup`, the amplification steps of `ngs-library-prep`.
**Effort:** high.

## P5 — Temperature module (reagent cooling)

**Change:** a cooled-site labware family + set-temperature step; simulator keeps
it as state. Mostly mirrors P3 with no shake.

**Unlocks:** the cold-reagent staging in `pcr-setup` and enzyme protocols.
**Effort:** low-medium.

## Sequencing rationale

P1 first — it unlocks the largest slice of the corpus (all CSV/per-well work)
and needs no new hardware concept. P2 is cheap and improves every assay skill
immediately. P3–P5 are module abstractions ordered by how many packages each
frees and by implementation surface (HS < TC; temp module is small but lower
demand). Each phase ends by flipping the relevant manifest entries to
`scope: now` and authoring those skills via the SOP.
