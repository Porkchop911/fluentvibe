# Portability plan: fluentvibe on someone else's Fluent

Goal: a lab with a different Fluent (other instrument, arms, workspace, catalog, FluentControl
version) installs fluentvibe, runs one setup flow, and gets protocols that pass its own
FluentControl InfoPad, without editing code or skills.

Prepared 2026-10-07 from a read of the current tree. Everything under "Current state" was checked
in the code; items marked *unverified* were not.

## Current state

### Already portable

| Need | Mechanism |
|---|---|
| Their labware, carriers, sites, liquid classes | `fluentvibe catalog refresh` indexes the local FluentControl install (`catalog/install_index.db`) |
| Their deck | Workspace-setup app saves a profile from their `.xwsp`: valid positions, common labware, deck skill (`workspace_app/service.py:save_profile`) |
| What their arms reach | `scripts/probe_deck_reach.py` measures reach in their FluentControl (`reach.json`) |
| Final check on their system | `fc-open` + InfoPad (needs the "shell" script, `docs/deployment.md` path B) |
| Wrong-deck protection | `reach.json` / `site_rules.json` ignored on GUID mismatch; save refuses a changed `.xwsp` |

### Blockers and leaks (verified)

| # | Problem | Where | Effect elsewhere |
|---|---|---|---|
| B1 | `fluentcontrol_core` is a sibling folder found by a hardcoded path (`D:\python\fluentcontrol_core`), not a declared dependency | `catalog/fc_install.py:13-17`, `deployer.py:217` | A fresh clone cannot index the catalog or deploy |
| B2 | Default device IDs carry this instrument's serial (`USB:TECAN,FLUENT,2203009762/...`) and the renderer writes them as `AvailableID` into every `.xscr` unless a step overrides it; no profile value overrides them | `_assets/config/generation.yaml` (`device`, `cga_device`, `liha_device`), `compiler/renderer.py:1177-1238` | Scripts reference another instrument's devices. *Unverified* whether FluentControl remaps them or rejects them |
| B3 | Configuration picker prefers this serial | `workspace_app/service.py:137`, `:1662` | Wrong instrument configuration preselected |
| B4 | Default workspace binding is the author's 780 deck | `generation.yaml` `worktable:` (open item in `REVIEW_NOTES.md`) | Without a profile, it silently targets a deck that does not exist there |
| B5 | Arms fixed in code: FCA with 8 channels, MCA96, gripper; MCA384 only in IR/renderer, no facade; nothing reads arms from the instrument configuration | `heads/`, `worktable.py`, `generation.yaml liha_device.num_channels` | An install without an MCA96, with an MCA384, or with a 4-channel FCA cannot be described |
| B6 | Lab and deck facts written into skills as general rules (site and catalog names, reagent policy, waste convention) | `Nest61mm_Pos` in 17 skill/asset files, `WS_100ml_1` in 9, `Nest7mm_Pos` in 7, `sat_780` in 4; `.agents/skills/fluentvibe/SKILL.md` "Heads, tips, liquids" | The model is taught this lab's deck as universal truth |
| B7 | Same site names in package code | `Nest61mm_Pos` in 10 `.py` files (`authoring/tools.py`, `skeleton.py`, `category_agents.py`, `renderer.py`, `worktable.py`, …), `WS_100ml_1` in 3, `Nest7mm_Pos` in 5 | Defaults, prompts and checks that only fit this deck |
| B8 | Liquid-class names fixed (`Water Free Single`, `Water Mix`, `Empty Tip`, plus a GUID) | `generation.yaml liquid_class`, skills | Other installs name or configure classes differently |
| B9 | FluentControl version: renderer emits fixed script/data versions (`script.version 2.0`, `data_version 1`); only tested on this install (FC build 3.5.7 seen in recordings) | `generation.yaml script:`, renderer | *Unverified* on other FC versions |
| B10 | `site_rules.json` is hand-written; no prompt to collect site quirks | per profile | A new deck starts with none of its quirks known |

## Target design

One **profile** per (instrument configuration, workspace) holds everything install-specific.
Code and skills hold only Fluent-general knowledge and read the rest from the profile.

```
profile/
  workspace_profile.json   workspace, deck, common labware          (exists)
  instrument.json          NEW: configuration GUID, serial, FC version,
                           heads present + channel counts, device aliases
                           and AvailableIDs, gripper fingers/nests
  lab_rules.yaml           NEW: lab policy (which head for which liquid,
                           waste labware + site, trough sites, liquid
                           classes for transfer/mix/empty)
  site_rules.json          site quirks (exists; filled by the setup flow)
  reach.json               measured reach (exists)
  deck-<name>.md           generated deck skill, now also rendering
                           instrument.json + lab_rules.yaml (exists, extended)
```

## Phases

Order: P0 first (it measures every later step), then P1-P3 (they unblock a second install), then the
rest. Sizes: S ≤ 1 day, M 2-4 days, L ≥ 1 week.

### P0 — Leak detector and second-deck harness (S)

- `tests/test_portability_lint.py`: fail on site names, catalog names, workspace names/GUIDs and
  instrument serials in `fluentvibe/**/*.py`, `_assets/config/skills/**`, `.agents/skills/**`,
  against an explicit allow-list (generic examples, tests). Starts red with today's counts (B6/B7)
  and is driven to green phase by phase.
- A **synthetic second profile** under `tests/fixtures/profiles/other_deck/` (different workspace
  name/GUID, different site names, no MCA96, 4-channel FCA). Offline tests build, simulate and
  compile a smoke protocol against it.
- A **real second deck** on this machine as proxy: one of the 780 workspaces (decided 2026-10-08),
  profiled through the normal flow. `build/workspaces/sat_780_default` exists; re-save it through
  the app so it carries the new profile files.

Done when: the lint lists every leak, and the smoke suite runs against both profiles.

### P1 — Installable core and `fluentvibe doctor` (M)

- B1: ship `fluentcontrol_core` with fluentvibe (decided 2026-10-08): bring it into this repo as
  a package installed with fluentvibe, so `pip install` gives both. Drop the `D:\python` path and
  the sys.path insertion in `catalog/fc_install.py` and `deployer.py`. Bring its tests along.
- FluentControl install discovery: default `C:\ProgramData\Tecan\VisionX`, env/CLI override,
  clear error when absent.
- `fluentvibe doctor`: Python version, core importable, FC install found + version, catalog index
  present and current, instrument configurations found, profiles found, shell script for `fc-open`
  present. One line per check, with the fix.

Done when: a fresh clone on another Windows machine with FluentControl reaches a green `doctor`
using only the README.

### P2 — Instrument identity from the configuration (M)

- Parse the instrument `.config` (the profile already stores its path) into `instrument.json`:
  serial/ID prefix, devices present (LiHa/FCA with channel count, MCA96, MCA384, CGA/RGA), aliases
  and AvailableIDs.
- Renderer takes `AvailableID`/aliases from the profile's `instrument.json`; `generation.yaml`
  keeps only Fluent-generic defaults (B2).
- Configuration picker: no preferred serial; preselect only when exactly one configuration exists
  (B3).
- Record the FluentControl version in the profile.

Done when: a script compiled for the second profile carries that profile's device IDs, and the
lint finds no serial in code or assets. Verify on the real proxy deck with `fc-open`.

### P3 — No silent defaults (S)

- Remove the 780 workspace binding from `generation.yaml` (B4). No profile means a clear error
  ("run setup"), in CLI, web app, LSP and skills.
- The 780 deck skill becomes an example under `examples/`, not a shipped default.

Done when: running without a profile fails loudly everywhere, with the setup command in the message.

### P4 — Heads as capabilities (L)

- `Worktable` exposes the heads `instrument.json` lists: `wt.liha` with N channels (the simulator,
  `wells=`/`channels=` validation and the FCA blocks use N, not 8); `wt.mca96` and `wt.gripper`
  only if present, otherwise a clear error naming the missing device.
- MCA384: supported (decided 2026-10-08). It is the MCA with a different adapter than the EVA,
  not a separate head. Model it that way: the MCA facade takes the mounted adapter (the existing
  `mount_adapter` / `drop_adapter`), and the adapter sets the geometry (96 vs 384 positions, tip
  boxes, which plates it can address). The existing MCA384 IR/renderer steps become the 384-adapter
  path of the same head. Docs and errors say "MCA with the 384 adapter", never a separate arm.
- Blocks check the heads they need and say which one is missing.
- A read-only capability query for agents and the web app (heads, channels, reach per site, valid
  liquid classes), built from profile files.

Done when: the synthetic "no MCA96, 4-channel FCA" profile authors and simulates the smoke protocol
with the FCA only, and an MCA call fails with a clear message.

### P5 — Lab and deck facts out of skills and code (M-L)

- Move this lab's rules into `lab_rules.yaml` of the 1080 profile: reagents by FCA, MCA only for
  bulk from SBS reservoirs, waste as `300ml SBS` on a nest, slim troughs on `WS_100ml_1`, MCA cannot
  reach `Nest7mm_Pos` 1-3, the liquid classes for transfer/mix/empty (B8).
- Generated deck skill renders `lab_rules.yaml`, `instrument.json` and `site_rules.json`.
- Core skills (`.agents/skills/fluentvibe/SKILL.md`, `_assets/config/skills/api|family`) say the
  general rule and point to the profile ("waste: the profile's waste labware").
- Package code (B7): go through the 10 + 3 + 5 files; replace site/catalog literals with profile
  lookups, or move them into tests/examples.
- `tests/test_skills_catalogue.py` keeps checking examples, now against both profiles.

Done when: the portability lint is green, and the 1080 Dynabeads/AMPure evals are no worse than
before the move. Measure; don't assume. The skills rewrite showed that wording changes move
results.

### P6 — Setup flow (M)

`fluentvibe setup` (CLI) and the same steps in the workspace app:

1. `doctor`
2. `catalog refresh`
3. Choose the instrument configuration, which writes `instrument.json`
4. Choose the workspace and common labware, which writes the profile (existing)
5. Reach probe (existing script, run from the flow; needs FluentControl + shell)
6. Lab-rules questionnaire: which head for reagents/bulk, waste labware and site, trough sites,
   liquid classes for transfer/mix/empty. Writes `lab_rules.yaml`.
7. Site quirks: offer known patterns ("only slim troughs here"). Writes `site_rules.json`.
8. Verification: generate a smoke protocol for this deck from the profile, compile, `fc-open`,
   report InfoPad. The profile is marked *verified* only when it passes.

Done when: a new workspace on this machine goes from nothing to a verified profile through the
flow alone.

### P7 — FluentControl version compatibility (M, deferred: needs outside data)

No outside install is available (2026-10-08). Do the recording part now (P2 stores the FC version
in the profile, and unknown versions warn); do the rest when `.xscr` files from another version
turn up.

- Record FC version per profile (P2). Collect `.xscr` samples from other FC versions (users,
  Tecan contacts); compare script/data versions and checksum behaviour.
- Renderer selects version-specific values from a small table; unknown versions warn and require
  the `fc-open` check before use.
- Decompiler round-trip tests over the collected samples.

Done when: at least one other FC version is in the table with a passing `fc-open`.

### P8 — Self-pilot on the 780 (S once P1-P6 are done)

No outside pilot is available (2026-10-08), so the 780 stands in for a new lab:

- Delete its profile and set it up again following only the README and `fluentvibe setup`.
  Record every manual step, error and workaround.
- Acceptance: the smoke protocol and one document-authored protocol pass InfoPad on the 780
  workspace, with no code or skill edits. Every manual workaround becomes an issue.
- What this does not cover: a different FC install, instrument configuration or FC version. The
  synthetic profile (P0) covers arms and channels offline; the rest stays untested until someone
  outside tries it.
- A test from a truly fresh clone would need a folder outside the repo; that needs your go first
  (standing rule).

## Risks

| Risk | Mitigation |
|---|---|
| Moving rules out of skills lowers authoring quality on the 1080 | Run the A/B on the existing evals before and after P5 |
| Shipping `fluentcontrol_core` exposes Tecan-derived data or code | Review what it contains before publishing (same provenance question as `REVIEW_NOTES.md`) |
| FluentControl rejects or remaps foreign `AvailableID`s in ways we cannot see offline | Verify P2 on the proxy deck with `fc-open` before relying on it |
| Instrument `.config` format differs across FC versions | Parse defensively; fall back to asking in setup (P6) |
| No access to another FC version or instrument | The 780 covers deck variation and the synthetic profile covers arms; FC-version and other-instrument behaviour stay untested and are labelled as such |

## Decisions (2026-10-08)

1. `fluentcontrol_core`: ship it with fluentvibe.
2. MCA384: support it, as the MCA with a different adapter than the EVA, not a separate head.
3. Proxy deck: one of the 780 workspaces.
4. No outside pilot: P8 is a self-pilot on the 780; P7 is deferred.
