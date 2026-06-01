# FluentControl validation findings — InfoPad inspection of generated protocols

_Evaluation only. No fixes applied here (except the worklist load failure, fixed
separately in commit `612a2c8`). Source evidence: `infopad_results.json` (raw
per-protocol InfoPad dumps). FC build 3.5.7.63142, simulated instrument, workspace
`SAT_Fluent_780_Rev3`._

## Method

The 19 best-effort protocols from the skills evaluation were opened in FluentControl
via the shell validator (`fluentvibe/authoring/fluentcontrol_shell.py`) and the InfoPad
(FC's context-check) was scraped. This is the layer **fluentvibe's own compile +
simulate cannot see**: real labware/liquid-class resolution, deck reachability, and
command deserialization all happen at script-open time inside FC.

## Top-line finding

**Passing compile + simulate does not mean a protocol is usable in FC.** Only
`simple-transfer` and `neg-thermocycler` opened with zero InfoPad errors. The rest
fail in a small number of **systematic** classes — all fixable, none random. The
shell InfoPad pass should become part of the acceptance loop, not just
compile + simulate (which is blind to every class below).

## Per-protocol error tally

| protocol | load-failed | tip-box / not-mounted | liquid Mix/EmptyTips | trough out-of-range | Z-Max / missing-in-tip | offset expr / loop var | labware / connector |
|---|---|---|---|---|---|---|---|
| bead-cleanup | – | | 4 | | 6 | | |
| protein-assay | – | | 1 | | | | |
| reagent-distribution | – | | | 1 | | | |
| serial-dilution | – | 6 | | | | | |
| normalization | – | | 1 | 1 | | | |
| pcr-setup | – | | 1 | | 4 | | |
| ngs | – | 11 | | | | | |
| cell-seeding | – | | | | 2 | | |
| elisa | – | 1 | | | | 12 | |
| purification | – | 19 | | | | | 5 |
| pooling | – | 3 | | | | | |
| simple-transfer | – | clean | | | | | |
| plate-reformatting | – | 9 | | | | | |
| ip-dynabeads | – | | 3 | 1 | | | |
| cherrypicking, cherrypick-csv, normalize-csv, pool-csv | **was load-fail** | | | | | | |

## 0. Worklist load failure — RESOLVED (`612a2c8`)

All four worklist-driven protocols threw *"The load operation failed during
processing the script commands"* and never opened. Root cause: the renderer emitted a
`<UseWellIndexNumbers>` element that `LoadWorklistStatementDataV4` does not define in
this FC build, so the command deserializer aborted. The two ground-truth FC-authored
worklist scripts in the datastore (`131205ca…`, `1954eff2…`) load cleanly and omit the
element; the auto-extracted reference template omits it too. Removed from
`renderer.py`; a regenerated worklist protocol now opens with `load_failed=False`.

**Residual (post-load, now visible)**: the worklist still shows
`Get DiTis: 'TOOLNAME:FCA, 50ul SBS' not found on the workspace` (see class 1) and the
emitted `<LiquidClassName>` is the *unsubstituted variable placeholder*
`LIQUID_CLASS_TRANSFER` rather than a resolved class name — the worklist render path
(`renderer.py` `_render_load_worklist`, `step.liquid_class`) does not run the
variable-substitution that `head.aspirate/dispense` use. Both are follow-ups.

## 1. Tip-box / DiTi labware not on the deck → cascading "not mounted"

**Symptom:** `No DiTi-Labware "MCA96, 200ul, Box" found` / `…"FCA, 1000ul SBS" found`,
followed by many `Tip(s) are not mounted on channels 1–8. Insert Get- or Pickup Tips`.
**Affected:** ngs (11), purification (19), plate-reformatting (9), serial-dilution (6),
pooling (3), elisa (1). Highest-volume class.
**Root cause:** the tip-box catalog names the skills emit are **not present on the
SAT_Fluent_780 worktable**. When the box can't be resolved, the pickup yields nothing,
so every subsequent pipetting step reports "not mounted." Evidence: clean
`simple-transfer` used `MCA96, 100ul, Box` + `mount_adapter()`+`pick_up()` and resolved
fine; failing `serial-dilution` used `MCA96, 200ul, Box` + `get_tips()` and did not.
**Responsible config:** tip-box catalog names across the family skills
(`fluentvibe/_assets/config/skills/family/*.md`) and the labware whitelist in
`fluentvibe/_assets/config/generation.yaml`.
**Recommended fix:** restrict the skills' tip-box names to DiTi labware actually
defined on the 780 worktable (enumerate via `fluentvibe.catalog` against the `.xwsp`),
and standardize on the MCA pickup idiom that worked (`mount_adapter()`+`pick_up()`),
since `get_tips()` on the MCA correlated with the "not mounted" cascade.

## 2. Liquid class lacks Mix / EmptyTips sections

**Symptom:** `Liquid subclass section "Mix"/"EmptyTips" is missing in "MCA384 1" with
"MCA96 DiTi 200µl"`.
**Affected:** bead-cleanup (4), ip-dynabeads (3), protein-assay (1), pcr-setup (1),
normalization (1).
**Root cause:** the default liquid class `"Water Free Single"` has no Mix or EmptyTips
sub-section defined for the MCA tip type, so `head.mix(...)` and `head.empty_tips(...)`
fail the context check.
**Responsible config:** the per-role `LIQUID_CLASS_*` defaults in the family skills
(all default to `"Water Free Single"`) and the catalog liquid-class set.
**Recommended fix:** identify which of the 38 catalog liquid classes define Mix and
EmptyTips sections for the relevant tip types, and default mix/empty operations to one
of those; or, where no suitable class exists, have the skills avoid `head.mix` /
`head.empty_tips` with that class. Verify against the `.xlqc` definitions.

## 3. Trough placed out of arm range

**Symptom:** `<Trough> out of range. Arm cannot move to position. Place object in an
area the arm can access.` (often followed by `volume to pipette is higher than the
available liquid volume: 0`).
**Affected:** reagent-distribution (BrothReservoir), normalization (BufferTrough),
ip-dynabeads (WashBuffer).
**Root cause:** auto-placement assigns the trough to a deck slot outside the
reachable envelope of the relevant arm on the 780 deck.
**Responsible config:** the placement logic / `valid_slots` for troughs
(`fluentvibe/worktable.py` auto-place + the 780 workspace slot set) and any slot hints in
the skills.
**Recommended fix:** constrain trough placement to slots the LiHa/MCA can reach on the
780 workspace; surface a reachable-slot list and have the skills place troughs there.

## 4. Trough Z-Max unreachable / aspiration shortfall

**Symptom:** `Tip N cannot reach Z-Max of labware <Trough>. Please raise Z-Max or use
longer tips.` + `Missing in Tip: Tip-1 … µl … Aspiration …`.
**Affected:** bead-cleanup (6), pcr-setup (4), cell-seeding (2), ip-dynabeads.
**Root cause:** the chosen trough's Z geometry vs. the mounted tip length — the tips
physically cannot reach the trough bottom, so the aspiration comes up short. A
labware-choice/geometry mismatch, not a volume-math error.
**Responsible config:** the trough labware the skills select (e.g. `25ml_short`,
`100ml`, the bead/ethanol/elution reservoirs) vs. the tip type in use.
**Recommended fix:** choose trough labware whose Z-Max is reachable with the standard
tips the protocol mounts (or mount longer tips); validate the trough+tip pairing per
skill.

## 5. Loop-variable well-offset expression invalid

**Symptom:** `Invalid expression: 'col'`, `Enter a valid well offset`,
`'Name of Loop Variable': Invalid expression: 'col'`.
**Affected:** elisa (12). The `well_offset="(col-1)*8"` / `"col*8"` idiom with
`loop_variable="col"`.
**Root cause:** FC's editor does not accept the loop variable inside the well-offset
expression as the skills author it (the loop variable is not recognized in the offset
expression context). Note this idiom is pervasive across skills but only tripped here —
worth determining why elisa surfaced it (likely the loop variable was referenced in the
offset without being declared/exposed the way FC's grammar requires).
**Responsible config:** the `well_offset` expression pattern in the family skills and
the loop authoring in `api-loops-and-conditionals` (`fluentvibe/_assets/config/skills/`).
**Recommended fix:** determine FC's accepted well-offset expression grammar for loop
variables (diff a GUI-authored looped offset) and update the skills' idiom to match.

## Special case — purification: unresolved plate + connector

Beyond the tip-box class, purification also threw `Select a valid labware` (×4) and
`No connector for this rotation at this site available`. The C18 filter/resin plate is
not on the whitelist (flagged needs-extension in `family-purification.md`), so the
labware doesn't resolve and the vacuum/connector site is invalid. Expected until the
filter-plate labware is whitelisted with a real catalog name.

## Recommended fix order

1. **Tip-box names + MCA pickup idiom** (class 1) — unblocks the most protocols
   (ngs, purification, plate-reformatting, serial-dilution, pooling, elisa).
2. **Liquid class with Mix/EmptyTips** (class 2) — unblocks the bead/assay/PCR family.
3. **Trough placement + Z-Max/tip pairing** (classes 3 & 4) — related labware/deck
   issues; fix together.
4. **Well-offset loop expression** (class 5) — pervasive idiom; one grammar fix.
5. **Worklist residuals** — diti_type default + liquid-class variable substitution.
6. **needs-extension labware** (purification filter plate, etc.) — whitelist as scoped.

## Process recommendation

Add the shell InfoPad pass to the generation acceptance loop. Compile + simulate
proved necessary but not sufficient — every class above is invisible to them and only
surfaces when FC actually opens the script. `fluentcontrol_shell.run_shell_validation`
already exists for exactly this.
