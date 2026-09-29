# Skills catalogue audit, 2026-09-29

Branch `audit/skills-catalogue` (worktree `.worktrees/skills-audit`). Nothing is changed yet.

## What was read, and how

| Material | How |
|---|---|
| 28 skill files in `fluentvibe/_assets/config/skills/` (12 api, 1 deck, 15 family) | **every one read in full**, for code, rules and biology |
| The system prompt the Full-Python mode actually sends (70,837 characters, taken from the 2026-09-29 Dynabeads run: base instructions, lab-scope message, deck section, and the 14 skills selected) | read in full |
| All code examples | checked mechanically as well: every `wt.*`, head, block and constructor call and keyword exists (they all do) |
| `lab_scope.md`, `lab_scope_reference.md` (legacy monoliths, only used in the `cheatsheet`/`enforce` modes the apps do not use) | **only scanned**, not read in full |
| `docs/skill-authoring/_drafts/*` (9 files) | only listed and dated, not read |

Biology is judged against standard practice for each assay. Where I am not certain it says so.

## Verdicts per skill

Legend: **OK** usable as is · **Fix** usable after small corrections · **Wrong** teaches something that fails on
the instrument or in the chemistry · **Off** not applicable to this deck/setup.

### api (12)

| Skill | Loaded | Verdict | Findings |
|---|---|---|---|
| api-blocks | always | **OK** | The best skill: blocks table, "streptavidin capture keeps the beads" stated. Omits `pool_wells`, `transfer_volumes`, `distribute_volumes`. The bead-wash example reuses the sample tips for waste removal and mixing (tips go to the shared waste and back into the samples). |
| core-worktable-api | always | **Fix** | Core is right (`wt.volume`, variables group, native loops). Its first example uses an undefined `head` (the FCA). Stale content for tools skills mode does not have ("object-draft liquid_classes shape", "approved labware across staged groups"). A "Rendering rules" section about internal step types (`pick_up_tips`, `calculate_variable.operation`) the author never writes. |
| labware-and-liquid-classes | always | **Fix** | Wrong: 384-well plate as "`Plate96` (384 layout)" (the class is `Plate384`; the deck contract says so). Contradicts itself: "keep ethanol on the FCA from the `100ml` reservoir" vs its own table and api-blocks (ethanol = MCA from SBS). "Reference volume variables as strings, `set_variable` a float" contradicts core-worktable-api (`wt.volume` objects). Fills via `w.layers.append(Layer(...))` (no import; `layer_all`/`layer_wells` exist). Says reagents come "from tubes" but lists no tube labware. |
| core-clarify-open-parameters | always | **Fix** | The rule is good (ask once with proposals, derive stated ratios). Its only worked example is a **Dynabeads protocol with numbers** (20 µl beads, 3 × 100 µl washes, 40 µl …), shown on every run: anchors all protocols and pre-answers Dynabeads. |
| api-add-resolver | never (env-gated) | **OK, but off** | Clear, complete AMPure example with `wt.add`. Requires `FLUENTVIBE_RESOLVER`, which nothing sets, so the model never learns `wt.add`. Uses a Python `for` for the ethanol washes, against the "never unroll" rule. |
| api-loops-and-conditionals | selected | **Fix** | Correct semantics. Examples use an undefined `head`; one "wash" loop moves liquid from source to waste without touching the sample; internal jargon ("known gap G4"). |
| api-magnetization-model | selected | **Fix (biology)** | Correct for SPRI. States that any `eluent` mixed off the magnet releases the analyte: **wrong for streptavidin–biotin** (effectively irreversible). For immobilisation both the model's reasoning and the simulator then expect a release and an eluate to recover. |
| api-worklists | selected | **OK** | Correct and current. Uses generic `TipBox` (deck contract requires `FCA1000Box`). Never mentions the `transfer_volumes` / `distribute_volumes` blocks the builder uses for per-well volumes. Was loaded for Dynabeads, which has no worklist. |
| device-odtc | selected | **Off** | Uses `Inheco_Pos`, which does not exist on the 1080 deck (not in its valid positions). |
| head-gripper | selected | **Fix** | "NEVER emit a user_prompt asking the user to move plates by hand" contradicts `offdeck_step` (operator takes the plate, e.g. to a rotator). Internal step-type names. |
| head-liha | selected | **Wrong (example)** | The example aspirates the volume **once** and dispenses it into **12 columns** (12× what was aspirated), calls `drop_tips()` twice, uses `TipBox`, and repeats "pass variables BY NAME (strings)". |
| head-mca96 | selected | **Fix** | Partial-column and tip-sorting content is correct (and matches the verified FC rules) but is expert material most protocols never need. The example mixes with `LIQUID_CLASS_BEADS`, not `Water Mix` (FluentControl rejects Free Single for MCA mixing). "BY NAME (strings)" again. |

### deck (1)

| Skill | Verdict | Findings |
|---|---|---|
| deck-sat-780 | **Off** | Only for the 780 deck. Waste on `Nest61mm_Pos 5` (the lab rule is a `300ml SBS` on `Nest7mm_Pos`); names trough catalogs not on the approved list. The 1080 deck section is generated from the profile instead and is correct (workspace, positions, class contract), but its class table is included twice and it carries no reach data or default layout. |

### family (15)

| Skill | Verdict | Biology / mechanics |
|---|---|---|
| family-bead-cleanup-spri | **Wrong for non-SPRI; Fix for SPRI** | SPRI chemistry is right (1.8×, ethanol washes, air-dry, elute off the magnet). But it is selected by the words `bead`, `beads`, `magnetic`, `magnet`, so it lands on **every** bead protocol, and declares a bead clean-up "INVALID" unless an eluate is recovered: forces elution onto streptavidin immobilisation. Its ethanol-wash code dispenses without aspirating from a source, and puts ethanol in the slim `100ml` trough the MCA cannot use (and sizes that trough). The DNase variant uses `role="enzyme"` / `role="stop_buffer"`, which **raise an error** (valid roles: plain, bead_carrier, analyte, eluent + aliases). |
| family-bead-affinity-capture | **Wrong for immobilisation** | IP / IMAC shape is plausible, but steps 6–7 "Elute" and "Recover eluate" are unconditional; no "product stays on the beads" path. Streptavidin/biotin DNA immobilisation has no family, so it lands here (and in SPRI). Raw head calls instead of blocks. |
| family-purification (SPE) | **Wrong** | Elution dispenses buffer straight into the collection plate, **bypassing the resin**. Sample loading reuses one tip set across all 12 sample columns (carry-over). Needs filter plates and a vacuum the deck does not have (acknowledged). |
| family-reagent-distribution | **Fix** | Head choice is right (reagents FCA, bulk MCA from SBS). Variants cite Opentrons protocol IDs (`sci-lucif-assay2`, `4a0be6`) the model cannot use. |
| family-simple-transfer | **Fix** | The LiHa column loop aspirates the source without a well address (always column 1 when the source is a plate) and reuses tips across sample columns. |
| family-cherrypicking | **Fix** | Says worklist steps are "validation-only, not simulated", which is stale (they are) and contradicts api-worklists. The fallback loop has no well addressing: every pick is the same well to the same well. |
| family-plate-reformatting | **Fix** | The per-column LiHa example reuses one tip set across all 12 sample columns (its own last line says fresh tips per sample group). |
| family-cell-seeding | **Wrong / Off** | Loops without well addresses (all into one well); 2 mL per well with 1000 µl tips without trips; no resuspension of the cell stock before each aspirate (cells settle); the media-exchange "dispense PBS" has no aspirate from a PBS source. 6/12/24-well plates are not on this deck. |
| family-elisa | **Wrong** | The **wash cycle only aspirates**; it never dispenses wash buffer, so washes do nothing after the first pass. **Samples are taken from a "sample trough"** with one tip set: every well gets the same sample. The MCA "whole-plate wash" alternative also never adds buffer. |
| family-ngs-library-prep | **Wrong** | The **55 °C tagmentation is written as `wt.wait` on the deck** (room temperature) against api-blocks' own rule. **No index/barcode addition** (unindexed libraries). One tip set mixes all 12 sample columns. Reagents from a "reagent plate" the labware skill forbids. |
| family-normalize-to-target | **Wrong (examples)** | Variant A: the **MCA aspirates from a `25ml_short` slim trough** (physically impossible) and mixes with only the sample volume. Variant B: tips that mixed in the samples go back into the shared diluent trough. Stale "worklists not simulated". The builder's actual normalisation (`transfer_volumes`/`distribute_volumes`, pre-dilution) is not mentioned. |
| family-pcr-setup | **Wrong (contamination)** | One tip set moves **all 12 columns of DNA template** and then mixes all reactions: the classic PCR cross-contamination. Thermocycling correctly as an operator step / ODTC. |
| family-pooling | **Wrong (example)** | The main "column pooling" example **copies each column 1:1**, which is not pooling (its header forbids exactly this). The multi-plate example indexes a Python list with a FluentControl loop variable (`source_plates[p]`), which cannot work. Heater-shaker homogenisation as `wt.wait`. Stale worklist note. |
| family-protein-assay | **OK / Fix** | Sensible: working reagent, sample, incubation, 37 °C as an operator step, read off-deck. Mixes with the transfer class (Free Single) rather than `Water Mix`. |
| family-serial-dilution | **OK / Fix** | Dilution math and column addressing are correct. Claims "the MCA96 cannot address one column at a time", which contradicts head-mca96 (`columns=[…]`). Leaves the extra transfer volume in the last column (standard: discard it to equalise volumes). |

**Tally:** OK 3 (api-blocks, api-worklists, protein-assay), Fix 13, Wrong 10, Off 2. Every family skill except protein
assay and serial dilution contains at least one example that would fail on the instrument or in the chemistry.

## Cross-cutting problems

1. **The two system messages contradict each other.** Message 1 (about 9k characters) describes a cooperative flow with
   approvals, staged groups with a simulate after each, and about 10 tools; its example and "known-good defaults" are for the **780
   deck**. Message 2 says those tools do not exist and orders "write the COMPLETE protocol as a single
   `build_worktable()` in one pass" and simulate "with the full source", which is exactly the one-shot behaviour that
   got its tool argument cut off. Message 1 wants "one focused question per missing axis", the clarify skill "one
   question with every open number".
2. **Two volume conventions.** `wt.volume` objects passed as values (core-worktable-api) vs "pass variables BY NAME as
   strings" (head-liha, head-mca96, labware, loops, simple-transfer). The model has to pick; the traces show it
   deliberating over exactly this.
3. **Tip boxes: `TipBox(...)` in about 12 skills vs the deck contract "`FCA, 1000ul SBS` must be `FCA1000Box`; anything else
   is invalid".**
4. **Liquid classes: skills require `Water Mix` / `Empty Tip`; the skills-mode lookup rejects both** (the 1080 profile allows
   only `Water Free Single`). Several family examples mix with Free Single anyway.
5. **Tip reuse across samples is the most common biological error** (PCR, NGS, pooling, purification, reformatting,
   simple transfer, normalisation Variant B, the bead-wash examples via the shared waste).
6. **Heating written as `wt.wait`** (NGS tagmentation, pooling homogenisation) against the blocks rule.
7. **Skill selection by keyword** loads SPRI for any "bead"/"magnet" request, and brought worklists into Dynabeads.
   There is no family for "keep the product on the beads" (streptavidin immobilisation, bead-bound library prep).
8. **Stale and foreign material:** "worklists are not simulated" (3 skills), Opentrons protocol IDs, roadmap links
   (`../../docs/skill-authoring/capability-roadmap.md`), "needs whitelist addition" notes the model cannot act on,
   internal jargon (G2, G4, P3/P4), IR step-type names, the 780 deck in the base prompt.
9. **Examples are untested.** None of the family examples is a complete, runnable protocol, and no check runs them
   through the simulator; the mechanical name check passes while half of them are wrong.

## If I had to write these protocols myself

**What I need, in order of importance:**
1. **The procedure, trimmed to the protocol in question**, and the user's facts (sample count, volume, what is
   pre-prepared, e.g. "beads come pre-washed").
2. **The deck as data:** positions, what can go where, which head reaches what, the class for each catalog, capacities
   and fill limits. The generated deck section has most of it; reach and capacities are missing or scattered.
3. **The API, exactly and completely:** the generated `fluentvibe api` reference (the authoring tool's `lookup_api`
   showed 12 of 41 Worktable methods). The blocks with one line each on what they do.
4. **The lab's rules, short and without contradictions:** FCA = `wt.liha` vs MCA; reagents vs bulk; tips per
   sample; mixing / emptying liquid classes; waste; heating, shaking and off-deck steps = operator; the document
   decides, nothing added.
5. **A fast honest check:** the simulator on my own file, then FluentControl.
6. **One or two real, complete, verified protocols** as examples (the builder's FluentControl-clean drafts qualify).
   Those teach more than fifteen skeletons, and cannot contradict the rules.

**Which documents help:** api-blocks; the generated deck section; the labware table and the FCA/MCA rules (once
consistent); the clarify rule (without its example); the magnetisation concept (with the streptavidin caveat);
api-add-resolver; protein-assay and serial-dilution as chemistry notes; the generated API reference.

**Which do not:** the family "canonical workflows" as written. They look authoritative, are copied closely by a
model, and ten of them teach a failure. Message 1 of the system prompt (cooperative flow, 780 deck). The legacy
monoliths. Rendering rules, Opentrons IDs, roadmap links, whitelist to-do notes.

## What is clunky

- A 70k-character prompt assembled from pieces written at different times for different modes, so it contradicts
  itself, and a model spends its reasoning reconciling it (in the traces: a "wait / actually" every ~650 characters,
  half about FCA vs MCA).
- Chemistry lives in hand-written family skeletons instead of in the blocks, so the same knowledge (tips per sample,
  mixing class, heating = operator) has to be restated, and drifts, in every family.
- Keyword selection of families; no family for "keep on beads".
- An allow-list, a class contract, a labware table and skill examples that each say something slightly different
  about the same labware.
- One-shot whole-file writing ordered by the prompt, while the tools (`edit_draft`) support incremental work.

## What hinders a local model in particular

- **Length and contradictions.** Every contradiction costs reasoning tokens; long reasoning is where the local models
  cut off or loop, and a low-precision KV cache degrades adherence on long contexts (see the KV-cache note).
- **Imitation of examples.** Local models copy examples closely. A wrong example (tips across samples, a wash without
  buffer) is reproduced faithfully; a Dynabeads example with numbers becomes the answer.
- **Keyword-loaded irrelevant skills** dilute attention (worklists and SPRI in a streptavidin immobilisation).
- **One-shot whole-file output**: a single bad token breaks a 10k-character tool argument.
- **Jargon and to-do notes** (G4, P3, "needs whitelist addition") that the model cannot resolve and may act on.

## Proposed direction (for decision)

1. **Rewrite the always-on core** into one short, consistent block: glossary (FCA = `wt.liha`, MCA = `wt.mca96`, RGA),
   one volume convention (`wt.volume`), labware table matching the deck contract (`FCA1000Box`, `Plate384`), capacities,
   tips per sample, `Water Mix` / `Empty Tip`, heating/shaking = operator, "the document decides".
2. **Replace family skeletons with short chemistry notes** (what the steps mean, what must be preserved, typical
   ranges, what to ask) that point to blocks, and a few **complete, simulator- and FluentControl-verified example
   protocols** generated from the builder.
3. **Add a test** that extracts every example in every skill and runs it through `simulate --strict` on the deck, so
   a wrong example fails CI.
4. **Fix the system prompt:** drop message 1's cooperative flow and 780 defaults in skills mode; replace "one pass"
   with incremental writing (`edit_draft`); one question rule.
5. **Select families from the spec's operations**, not keywords; add an "immobilise / keep on beads" family; never
   auto-attach SPRI to streptavidin work.
6. **Allow-list:** add `Water Mix` and `Empty Tip` to the 1080 profile.
7. **Magnetisation model:** add a "binding is irreversible" option for streptavidin/biotin (no release by eluent).
8. **Retire** the legacy monoliths and the 780 deck skill, or regenerate them; fold in or delete the June drafts.

## Decisions for you

1. Add `Water Mix` and `Empty Tip` to the 1080 profile's allowed liquid classes?
2. Enable `wt.add` for the model (drop the `FLUENTVIBE_RESOLVER` gate), or keep it off?
3. Family skills: rewrite as chemistry notes + verified example protocols (point 2 above), or fix the existing
   skeletons in place?
4. The June drafts: fold in or drop?

## Changes on this branch (rewrite)

| | Before | After |
|---|---|---|
| Skill files | 28 | 30 (+ `core-lab-rules`, + `family-bead-immobilization`) |
| Catalogue size | 130.5k chars | 82.1k chars |
| Always-on size | 27.6k | 25.5k |
| Family skills | 76.6k | 31.0k |
| Old files failing the new consistency test | 17 of 28 | 0 |

- Base prompt for skills mode (`SKILLS_SYSTEM_PROMPT`): the document is binding, no added steps or reagents,
  open values asked or assumed; the skills header no longer lists example stages.
- `Water Mix` / `Empty Tip` always allowed (`REQUIRED_LIQUID_CLASSES`).
- Core and api skills: one vocabulary (FCA = `wt.liha`, MCA = `wt.mca96`, RGA = `wt.gripper`), real classes
  and catalogs, capacities, liquid classes, per-well volume blocks; examples checked in the simulator.
- Family skills: chemistry notes (what the product is, steps to keep, typical values, what to ask, which block),
  no head-call skeletons. New `family-bead-immobilization` (streptavidin/biotin: product stays on the beads,
  final buffer role `plain`, no elution).
- Selection: a skill body that names another skill loads it (`_expand_cross_references`); bodies no longer
  name unrelated families. SPRI triggers no longer fire on bare "bead"/"magnet".
- `tests/test_skills_catalogue.py`: API names and keywords in examples, roles, liquid classes, tip-box classes,
  stale worklist claims, family cross-links, the complete example simulated strict.
- Deleted `docs/skill-authoring/_drafts/*.additions.md` (June proposals; superseded).

Found on the way, not fixed here: the simulator moves the top layer on aspirate and a mix does not
homogenise, so in a serial dilution the stock layer travels intact to the last column (volumes are right,
composition is not).
