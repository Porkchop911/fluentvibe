# Authoring quality strategy

Strategy proposal, 23 September 2026. No implementation changes are included.

**Fixed constraints:** the output is Tecan Fluent protocols (FluentControl `.xscr`), and the authoring model runs locally. Everything else — skills, modules, prompts, tools, knowledge base, pipeline shape — is open to change.

**Companion document:** [`architecture-roadmap-2026-09.md`](architecture-roadmap-2026-09.md) covers the platform: state isolation, a shared build contract and model profiles. This document covers generation quality. Where the two overlap, this one relies on the roadmap's P1 fix. Simulation must stop mutating authored labware before repeated scoring can be trusted.

---

## 1. Summary

The local model is currently asked to do four jobs in one pass:

1. understand a 40-page lab document;
2. decide what belongs on the deck and what does not;
3. write 500–600 lines of low-level head operations;
4. repair them against a simulator.

It fails in two different ways. It either never converges (Qwen 3.6/3.8 27B: 0 of 5+5 correct bead cleanups), or it converges on something that passes every check and is still wrong on the bench (Qwen 3.8 Flash Next, 2026-09-23).

**Thesis:** move correctness out of the model and into deterministic layers. The model's job shrinks to what only a language model can do: read the document, state what the protocol is, and choose parameters. Verified building blocks produce correct liquid handling. Deterministic checks guard what the model says. You confirm the model's understanding at one cheap checkpoint.

Three moves, in priority order:

| # | Move | What it fixes |
|---|---|---|
| 1 | **Verified building-block library + semantic checks** | Tip reuse, faked steps, wrong derived volumes, cleanup chemistry. Output shrinks about 5×. |
| 2 | **Split by kind of reasoning: extract → your checkpoint → map → compose** | Understanding errors (pooling order, off-deck steps) that no code repair can fix |
| 3 | **Harness efficiency** (forced first tool, lookup tool, patch-style repair, reasoning budget) | About 49 min → an estimated 10–15 min per complex run |

---

## 2. Evidence

### 2.1 History

| Date | Model | Setup | Outcome |
|---|---|---|---|
| 2026-06-16 | qwen3.6-27b, q4 KV cache | skills mode, SQK-RBK114 PDF | Bench-incorrect cleanups that varied run to run |
| 2026-06-16 | qwen3.6-27b, f16 KV cache | same | Near-correct, followed the SPRI skill closely |
| 2026-06-18 | qwen3.6-27b, f16 | monolith, tightened SPRI skill, 5 runs | **0/5** correct cleanups; 2 replaced by comments, 2 failed simulation |
| 2026-06-18 | qwen3.6-27b, f16 | staged drafting, hardened, 5 runs (~3.3 h) | **0/5**; 4 hit the retry budget while thrashing; `magnet_roundtrip` 0% |
| 2026-09-23 | qwen3.8-flash-next IQ4_XS, q8 KV, 120K ctx | skills mode, same PDF | Simulates, compiles, **rubric 1.0**, but has 4 bench errors (below) |

### 2.2 The Flash Next run in detail

Artifact: `build/workbench/authored/20260923-215349/`.

**Time budget: 49 minutes, 5 turns.**

| Turn | Tokens read (uncached) | Tokens written | Minutes | What happened |
|---|---|---|---|---|
| 1 | 40,059 | 20,314 | 19.0 | Mostly reasoning. Then called `simulate_python_draft` before declaring a plan → rejected (`workflow_plan_required`) |
| 2 | 91 | 9,474 | 7.3 | `declare_protocol_workflow`, 10 groups |
| 3 | 12,439 | 6,913 | 6.9 | Probe draft through the simulator to learn whether `Well` objects can be pipette targets (no lookup tool in skills mode) |
| 4 | 1,094 | 13,163 | 9.3 | Full draft. Failed contract check: `wt.group` before variables declared |
| 5 | 13,221 | 8,539 | 6.5 | Full re-submission. Passed simulation and compiled |

- **Writing dominates:** about 58k tokens at 21–28 tok/s is about 44 of the 49 minutes. Reading the prompt runs at 160–170 tok/s, and the server reuses the cached prefix, so it is not the bottleneck.
- **Wasted time:** turns 1 and 3 (26 min) produced nothing that reached the final protocol, and turns 4 and 5 each re-sent the full ~30 KB source.

**Rubric result (`eval_rubric.score_protocol` with the PDF text):** score 1.0. `magnet_roundtrip`, `eluate_recovered`, `off_magnet_elution`, `separate_eluate_destination` and `analyte_not_in_waste` all pass. `derived_supernatant` and `derived_eluate` come back **n/a** (a rubric gap; see D5).

**Bench review of `lm_authoring_attempt4.py`:**

| # | Defect | Lines | Why no gate caught it |
|---|---|---|---|
| D1 | **Pooling faked.** 96 libraries are carried forward per well, with a comment claiming this is the "plate-format equivalent of pooling". The source pools first, then does *one* cleanup; the draft does 96 cleanups. | 507–512 | Coverage accepts a comment as justification; no check compares operation order against the document |
| D2 | **Cross-sample tip reuse.** One set of LiHa tips mixes, removes supernatant and removes ethanol across all 12 sample columns; tips that touched samples also re-enter the reagent troughs | 307–349 | The simulator does not track which sample a tip has touched |
| D3 | **Off-deck thermal cycler as a wait.** 30 °C/80 °C tagmentation modelled as `wt.wait` with the plate on deck. `wt.user_prompt` exists and was not used. | 402–406 | No notion of "this step happens off-deck" |
| D4 | **One liquid class for all liquids.** Ethanol, beads and eluate all use `Water Free Single`; the model said so explicitly | turn 1 | Liquid-class checks verify compatibility, not suitability |
| D5 | *(Rubric defect, not a protocol defect — corrected 24 Sep.)* The supernatant (20 + 36 − 5 = 51 µL) and eluate (14 − 5 = 9 µL) volumes **are** derived, via Python constants. `derived_*` returned n/a because it only reads literal numbers in `declare_variable(...)` | 84–89 | Rubric reads literals only |
| D6 | **Existing SPRI helper unused.** `workspace_modules.spri_cleanup` exists, but this profile (`Ribbon_…-Copy 1`) ships no modules, so the model never saw it | — | Modules are profile-scoped, not a standard library |

### 2.3 What the evidence says

1. **Model capability is not the only bottleneck.** A stronger model reached a *passing* state that is still wrong. Raising model quality moves failures from "doesn't converge" to "converges on the wrong thing", and the second is worse, because it looks done.
2. **The checks are the ceiling.** Everything the model got wrong was something no gate measured. Every new deterministic check raises the floor for every model at once.
3. **Decomposing by protocol stage failed (June); decomposing by kind of reasoning has not been tried.** The June staging still asked the model to write low-level code in each stage.
4. **Output volume drives both latency and error count.** 600 lines of head operations is 600 lines to get wrong and about 8k tokens per resubmission.

---

## 3. Target pipeline

```
 PDF / text ──► [A] Extract ──► Bench Spec (YAML) ──► [B] Your checkpoint
                  model             checked against        (edit / approve)
                                    source numbers               │
                                                                 ▼
 .xscr ◄── compile ◄── [E] Check ◄── Python ◄── [D] Compose ◄── [C] Map
                        simulate +      (lowered from  building-block  deck plan:
                        semantic        blocks)        calls           labware, positions,
                        checks vs spec                 (small)         liquid classes
                           │                                           (mostly deterministic)
                           └──► [F] Repair (patch, not resubmit) ──► D
```

| Stage | Who does it | Input | Output | Checked by |
|---|---|---|---|---|
| A Extract | model | source text, family skill | **Bench Spec** (steps, reagents, volumes, incubations, `location: deck/off_deck/manual`, ordering) | schema; every number must appear in the source; unit sanity |
| B Checkpoint | you | Bench Spec rendered as a table in the web UI | approved/edited spec | you |
| C Map | code first, model for gaps | spec + profile | deck plan: labware per role, positions, liquid class per reagent | profile whitelist, catalog, capacity math |
| D Compose | model | spec + deck plan + block catalog | a short Python file that is mostly building-block calls | contract, build |
| E Check | code | Python | simulation + semantic checks against the spec | — |
| F Repair | model | check diagnostics | a patch to one block call | same as E |

The extraction context (the 40–50k-token document) is used **only in stage A**. Stages C–F see the spec (~2–4k tokens) instead of the PDF.

---

## 4. Workstreams

Each workstream lists its goal, design, concrete changes, acceptance criteria, rough size and risks. Size: **S** ≈ one focused session, **M** ≈ a few sessions, **L** ≈ a week or more.

### W1 — Verified building-block library

**Goal:** the model never writes a bead cleanup, a pool or a column loop by hand again.

**Design.** Promote `workspace_modules.spri_cleanup` from a profile-scoped module into a standard library, `fluentvibe.blocks`, that every profile gets. Each block:

- takes **roles, not positions** (`sample_plate`, `magnet`, `waste`) plus scientific parameters;
- **derives** dependent volumes itself (supernatant = sample + beads − retain; eluate = elution − retain) and exposes them as declared variables in the `.xscr`;
- uses a **tip policy** argument (`fresh_per_column` default for anything touching samples; `reuse` only for reagent-only dispensing);
- emits its steps inside a named `wt.group` carrying an **origin ID**, so diagnostics point back to the block call (see roadmap §C);
- has a docstring written for the model: preconditions, what it derives, one example call.

**First block set** (from the families that appear in the skill catalog and the ONT case):

| Block | Replaces | Notes |
|---|---|---|
| `spri_cleanup(...)` | ~120 lines of LiHa/MCA/gripper code | exists; harden and promote |
| `pool(source_plate, wells, dest, dest_well, volume_per_well)` | faked pooling (D1) | LiHa multi-aspirate or worklist, chosen by volume |
| `offdeck_step(labware, instruction, return_to=None)` | `wt.wait` substitutes (D3) | gripper to a hand-off position → `user_prompt` → gripper back |
| `stamp(src, dst, volume, head=mca)` | MCA full-plate transfer | tip policy explicit |
| `dispense_reagent(trough, plate, volume, columns)` | column loops with `well_offset` | LiHa, reagent-only, tip reuse allowed |
| `mix_wells(plate, volume, cycles, columns)` | per-column mix loops | |
| `normalize_to_target(...)` | worklist construction | wraps the existing worklist API |
| `thermal_step(plate, program)` | ODTC driver calls | only when the profile has an ODTC; else falls back to `offdeck_step` |

**Changes.** New package `fluentvibe/blocks/`. Move and adapt `spri_cleanup`, keeping a re-export in `workspace_modules`. Add a `blocks` section to the skills context (generated from the docstrings, so it can't drift). Add an `api-blocks.md` skill. Change family skills to say "call `spri_cleanup`" instead of describing the steps.

**Acceptance.** Each block has unit tests covering simulation, derived volumes and tip policy. A hand-written ONT protocol using blocks is under 120 lines, passes all semantic checks (W3) and compiles. Re-running the Flash case with blocks available produces none of D1–D4 or D6.

**Size:** M. **Risks:** blocks can hide head constraints. Mitigate by keeping raw head operations available, and by having blocks fail loud (never silently adapt) when the deck can't express the request.

**New folder:** `fluentvibe/blocks/` is a new folder inside the repo. Needs your approval before creation.

### W2 — Bench Spec extraction and checkpoint

**Goal:** separate "what is the protocol" from "how to run it on this deck", and let you fix the first cheaply.

**Bench Spec schema** (YAML, versioned, abbreviated):

```yaml
spec_version: 1
title: ONT SQK-RBK114 amplicon barcoding
samples: {count: 96, container: plate96, volume_ul: 9, analyte: "amplicon DNA"}
reagents:
  - {id: AXP, name: "AMPure XP beads", role: bead_carrier}
  - {id: EtOH80, name: "80% ethanol", role: wash, liquid_type: ethanol}
  - {id: RB, name: "Rapid Barcodes", role: per_sample, layout: plate96}
steps:
  - {id: s1, op: add, reagent: RB, volume_ul: 1, to: samples, location: deck}
  - {id: s2, op: incubate, temp_c: [30, 80], minutes: [2, 2], location: off_deck, device: thermal_cycler}
  - {id: s3, op: pool, from: samples, volume_ul: 10, to: {container: tube, name: pool}, location: deck}
  - {id: s4, op: bead_cleanup, target: pool, bead: AXP, ratio: 1.0, washes: 2, wash: EtOH80, elute_ul: 15}
  - {id: s5, op: manual, text: "Prime and load flow cell", location: manual}
source_refs: {s3: "p.14 'Pool all barcoded samples…'"}
```

**Extraction design.**

- One model call with **schema-constrained output**. llama.cpp's server supports JSON-schema response formats and GBNF grammars, so malformed specs are impossible.
- Input: document text + the relevant family skill(s) + the schema. **No** deck, API or Python context.
- **Number check:** every numeric value in the spec must appear in the source text (with unit normalization); any that don't are flagged.
- **Ordering check:** the step order is shown to you next to page references.
- Default `location` comes from verbs and devices ("thermal cycler", "Hula mixer", "centrifuge" → `off_deck`), and you can override it.

**Checkpoint UI.** The workspace app shows the spec as an editable table (step, operation, reagent, volume, location, source quote). Buttons: *Approve*, *Edit and approve*, *Re-extract*. Approved specs are saved next to the run (`bench_spec.yaml`) and can be reused. Re-authoring the same protocol later skips stage A.

**Changes.** `fluentvibe/authoring/bench_spec.py` (schema, validation, number check), an extraction prompt, a new graph entry node, `/api/spec` endpoints in the workspace app, and a spec panel in `index.html`. The panel needs your review: the setup/deck-map area is off-limits (see memory), but a new panel in the authoring tab is not.

**Acceptance.** For the ONT PDF, the extracted spec contains pooling before the second cleanup, marks the thermal and flow-cell steps as off-deck or manual, and has no unflagged numbers missing from the source. Extraction takes ≤ 5 min on Flash Next.

**Size:** M–L. **Risks:** the spec schema grows into a second DSL. Mitigate by limiting ops to the families that blocks exist for, plus `manual` and `custom` (free text, which forces a comment or user prompt).

### W3 — Deterministic semantic checks

**Goal:** a protocol that simulates but is wrong on the bench fails, whichever model wrote it.

| Check | Mechanism | Catches |
|---|---|---|
| **Cross-contamination** | Tag each tip with the set of sample identities it has contacted, tracked through the simulator's per-well reagent layers. Aspirating from or dispensing into a well whose sample set differs → violation, unless the tip is reagent-clean and dispensing from above | D2 |
| **Spec conformance** | Map simulated operations back to spec steps via block origin IDs. Every `location: deck` step must have operations; order must match; `pool` must reduce N sources into 1 destination | D1 |
| **Off-deck honesty** | Every `off_deck` or `manual` spec step must lower to `user_prompt` (with labware moves if a device is involved), never `wait` or a comment alone | D3 |
| **Liquid-class suitability** | Map reagent `liquid_type` to acceptable liquid-class name patterns from the install catalog (ethanol → classes containing "Ethanol"/"Alcohol"; beads → "Beads"/"Serum"/"Viscous"). Mismatch → warning; mismatch on a volatile or viscous liquid → error | D4 |
| **Derivation resolution** | Resolve variable values through Python constants and arithmetic (constant folding) before checking derivations; a missing derivation fails instead of n/a when the stage exists | D5 (rubric) |
| **Conservation** | The analyte mass-balance proxy should be about 1 − (expected losses) at the final destination | eluate lost or duplicated |

**Rubric fixes (small, do first):** `derived_*` resolves values through Python constants (constant folding) and fails instead of n/a when the stage exists without a derivation. *Already in place since `7ebd18a`:* comment-only stages don't count as covered, and `eluate_recovered` requires a magnet round trip. Coverage still passed the Flash run because stage detection is keyword-based: the group name "…Clean-up and Pooling" satisfied the pooling stage. Only the semantic pooling check (spec conformance) fixes that.

**Changes.** `simulator/walk.py` (tip contact sets), a new `authoring/semantic_checks.py`, rubric changes in `eval_rubric.py`, and results shown in the web UI next to "simulated / compiled". Depends on the roadmap's **P1** (simulation isolation), otherwise repeated checks see mutated state.

**Acceptance.** Attempt 4 of the Flash run fails with D1–D4 named, and the rubric reports D5's derivations as pass, each with a line number. Hand-written example protocols in `examples/` stay green.

**Size:** M. **Risks:** false positives on legitimate reuse (reagent dispensing from above). Mitigate with a reagent-only tip state and "dispense-from-above" as an explicit policy.

### W4 — Harness efficiency

**Goal:** stop paying for wasted turns and resubmissions.

| Change | Where | Expected saving (Flash run) |
|---|---|---|
| Force `declare_protocol_workflow` on turn 1 (`tool_choice` = that tool, or expose only it) | `graph.py`, `lab_scope.py` | ~19 min wasted turn → gone |
| Restore **`lookup_api`** in skills mode (no model call, deterministic) | `lab_scope._SKILLS_ALLOWED_TOOLS` | probe drafts (turn 3, ~7 min) → one cheap lookup |
| **Patch repair:** `edit_draft(old, new)` tool applied to the stored draft, then re-simulated | `tools.py`, `graph.py` | resubmits of ~8k tokens → ~0.5–1k |
| Contract errors caught **before** simulation with an exact fix-it (e.g. "move `declare_variable` calls above line 215") | `validator.py`, reuse `copilot/fixes.py` | shorter repair turns |
| Reasoning budget per turn type: `high` for extraction, `medium` for composing, `low` for patches | `lm_client.py` (already supports `reasoning_effort`) | turn 1 writing ~20k → ~8k tokens |
| Keep the document out of stages C–F (W2) | prompt assembly | no document re-reads after compaction |
| Show live per-turn tokens and tok/s in the web UI | `trace.py`, `index.html` | makes stalls visible |

**Acceptance.** The ONT case on Flash Next finishes in ≤ 15 min end to end with the checkpoint excluded, and no turn is spent on a tool the pipeline then rejects.

**Size:** S–M. Most items are independent and can land one at a time.

### W5 — Knowledge base

**Goal:** give the model examples rather than prose, and make knowledge measurable.

| Asset | Content | Use |
|---|---|---|
| **Gold protocols** | 1–3 per family, written with blocks, passing W3; stored in `fluentvibe/_assets/examples/` | retrieved as examples for stage D |
| **Decompiled corpus** | your real FluentControl methods, via `fluentvibe decompile`, curated and de-branded | examples for rarely seen patterns; ground truth for W6 |
| **Liquid-class map** | reagent type → recommended class per install, derived from the catalog plus your overrides | stage C, W3 suitability check |
| **Off-deck verb list** | devices and verbs that imply off-deck ("thermal cycler", "Hula mixer", "centrifuge", "Qubit") | stage A defaults |
| **Skill rewrite** | family skills shrink to: when to use, which block, which parameters come from the document, common traps | stage A + D context |

**Skill principle:** prose skills describe *decisions* ("pool before cleanup when the kit says so"). Blocks and checks own *mechanics*. That removes the failure where the model echoes an invariant as a comment but doesn't implement it.

**Customer-data rule:** anything decompiled from your installation must pass the Zzqvpx de-brand gate before it is committed (see memory, fluentvibe port).

**Size:** M, ongoing.

### W6 — Evaluation

**Goal:** know whether a change helped, per invariant, not per overall score.

- **Benchmark set:** about 20 cases in three tiers.
  - T1: simple transfers, stamping, reagent distribution.
  - T2: single-family, e.g. SPRI cleanup, normalization, serial dilution.
  - T3: multi-stage documents, e.g. ONT RBK114, one ELISA, one PCR setup.
  - Each case has a reviewed Bench Spec and a gold protocol.
- **Metrics per run:** spec accuracy (vs. reviewed spec), first-pass W3 pass rate, final W3 pass rate, turns, output tokens, wall time, and **incorrectly accepted** (passed all gates but failed review). The last one is the headline metric.
- **Runner:** extend `scripts/eval_authoring.py` to take a case directory and to run stages independently (e.g. compose-only from a gold spec), so extraction and composition can be measured separately.
- Run each case ≥ 3 times per model; report spread.

**Size:** M.

### W7 — Model strategy (local only)

| Question | Experiment | Decision rule |
|---|---|---|
| Which model per stage? | Run the W6 benchmark with Flash Next and 27B on stage A only and stage D only | Pick per stage; it can be the same model |
| KV precision | Stage A on Flash Next at q8 vs f16 KV (if memory allows) | f16 is the June finding for long-context adherence; confirm on the new model |
| Reasoning budget | low / medium / high per stage | smallest budget that keeps the stage's pass rate |
| Constrained decoding | stage A with vs without JSON schema | expected: fewer malformed specs at no quality cost |
| Fine-tuning (later) | LoRA on (spec step → block call) pairs, synthetic variants checked by the simulator | only after W1–W3; only if W6 shows a stable, specific failure pattern |

The roadmap's P2 already covers profile recording (quantization, runtime, template, context). Reuse it rather than duplicating it.

---

## Implementation status (24 September 2026)

Restore point before any of this: tag `restore/pre-authoring-strategy-2026-09-24` (commit `e9c9b5a`). No live model runs yet; the GPU was unavailable, so everything below is deterministic and covered by the offline suite (726 passed, 1 skipped).

| Commit | Item | Effect on the Flash attempt-4 protocol |
|---|---|---|
| `345fdb5` | W3 rubric: derivations resolve through Python constants, each cleanup checked separately | `derived_*` n/a → pass (D5 was a rubric gap) |
| `345fdb5` | W4: skills mode binds only `declare_protocol_workflow` until a plan exists; `lookup_api` available in skills mode | would have prevented turn 1's rejected draft and turn 3's API probe (not yet measured live) |
| `4e57bc6` | Roadmap P1: simulation no longer mutates authored labware or variables; per-step variables on `Snapshot.variables` | repeated scoring is now deterministic |
| `84ed376` | W3: sample-lineage tracking; `cross_sample_tip_reuse` and `sample_carryover_into_reagent` findings; `tip_hygiene` in simulate results; rubric `no_cross_contamination` | fails: 176 cross-sample events (first line 307) and 384 reagent carryovers (**D2**) |
| `bfef2e6` | W3: mid-run off-deck steps must use `wt.user_prompt`; `offdeck_steps` in simulate results; rubric `offdeck_steps_prompted` | fails: Qubit QC line 386, thermal cycler line 402 (**D3**) |

Rubric on attempt 4 is now **0.818** (was 1.0), with the two failures above. Still open from phases 0–1: per-turn reasoning budget (needs live measurement). D1 (faked pooling) needs the Bench Spec (W2). D4 (liquid classes) needs the catalog map (W5).

New findings from the checks: the shipped `examples/ampure_cleanup.py` reuses MCA tips from sample supernatant into the elution-buffer trough (96 `sample_carryover_into_reagent` warnings). Tip-hygiene and off-deck findings are warnings for the model, not blocking gates. Making them blocking is a decision for after the first live runs.

## 5. Roadmap

| Phase | Content | Exit criterion |
|---|---|---|
| **0 — Quick wins** | W3 rubric fixes; W4 forced first tool + `lookup_api` + reasoning budget | Flash ONT run re-scored → correctly fails; next live run has no wasted turn 1 |
| **1 — Floor** | Roadmap P1 (simulation isolation); W3 cross-contamination + off-deck + derivation checks | Attempt 4 fails with D2 and D3 named; rubric derivations resolve |
| **2 — Blocks** | W1 first block set; skills rewritten to reference blocks; gold ONT protocol | ONT gold protocol < 120 lines, W3-clean; live Flash run uses blocks |
| **3 — Understanding** | W2 Bench Spec + checkpoint UI; W3 spec conformance | Live ONT run: approved spec → W3-clean protocol, ≤ 15 min excluding checkpoint |
| **4 — Measure & tune** | W6 benchmark set; W7 experiments; W4 patch repair | Headline metric (incorrectly accepted) = 0 on the set; per-stage model choice recorded |
| **5 — Scale knowledge** | W5 decompiled corpus, liquid-class map, more blocks | T3 coverage extended to at least 3 document-backed protocols |

Phases 0 and 1 need no new folders and no UI changes. Phase 2 needs approval for `fluentvibe/blocks/`.

---

## 6. Decisions needed from you

1. **Block library location.** Create `fluentvibe/blocks/`, or extend `workspace_modules.py` in place (no new folder)?
2. **Checkpoint.** Is a mandatory spec approval step acceptable in the web UI, or should it be skippable ("trust extraction")?
3. **Off-deck default.** For steps like thermal cycling when the profile has no ODTC: `user_prompt` with a plate hand-off (recommended), or refuse and ask?
4. **Liquid classes.** Auto-assign from the catalog map (recommended) or keep them as declared variables that you fill in?
5. **Decompiled corpus.** Which of your FluentControl methods may be used, and may they be committed after de-branding or only kept local?

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Blocks become a second DSL that diverges from head operations | Blocks lower to existing head calls only; raw operations remain first-class; origin IDs keep diagnostics readable |
| Spec schema can't express an unusual protocol | `custom` op with free text → forces `user_prompt`; schema grows only when a block exists |
| New checks reject valid hand-written protocols | Run the checks against `examples/` and decompiled real methods before enabling them as errors; start as warnings |
| Checkpoint adds friction | Approved specs are cached and reused; checkpoint shows only changed or low-confidence rows on re-runs |
| Local model latency stays high | W4 plus smaller outputs; stage A is the only long-context call and runs once per document |
| Customer identifiers leak via corpus or examples | De-brand gate before commit; keep raw decompiled methods outside git |

---

## Appendix A — Relevant code today

| Area | Location |
|---|---|
| SPRI helper | `fluentvibe/authoring/workspace_modules.py:191` `spri_cleanup` |
| User pause step | `fluentvibe/worktable.py:283` `Worktable.user_prompt` |
| Skills-mode tool list | `fluentvibe/authoring/lab_scope.py:137` `_SKILLS_ALLOWED_TOOLS` |
| Rubric | `fluentvibe/authoring/eval_rubric.py` (`SOURCE_KEYS`, `SEMANTIC_KEYS`, `score_protocol`) |
| Eval harness | `scripts/eval_authoring.py` |
| Simulator walk | `fluentvibe/simulator/walk.py` |
| Quick-fixes to reuse for contract errors | `fluentvibe/copilot/fixes.py` |
| LM client (reasoning effort, bearer key) | `fluentvibe/authoring/lm_client.py` |
| Skills | `fluentvibe/_assets/config/skills/{api,deck,family}/` |

## Appendix B — Flash Next server used

Qwen3.8-Flash-Next UD-IQ4_XS (176.9B total parameters; about 6B active per token), MTP draft Q8_0 (draft max 3), 44 MoE layers on CPU, context 120,000, KV cache q8_0/q8_0, llama.cpp at `127.0.0.1:8080`. Observed: prompt processing 160–172 tok/s, generation 21–28 tok/s.
