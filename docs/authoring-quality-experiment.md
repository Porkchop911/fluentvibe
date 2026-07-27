# Authoring quality experiment — handoff for review

**Historical branch:** `partial-mca-pipetting`; the experiment was later
incorporated into `review/authoring-quality-experiment`. Sections 1–6 describe
the state measured on 2026-06-18; section 7 records the current disposition.
**Model:** `qwen3.6-27b` via LM Studio @ `http://127.0.0.1:1234`, **KV cache f16**
**Date:** 2026-06-18
**Canonical case:** Oxford Nanopore SQK-RBK114 V14 rapid amplicon library prep
(PDF in `~/Downloads/rapid-sequencing-v14-...-RAA_9198_v114_revM_17Oct2025-38.pdf`),
authored against the `sat_1080_test` workspace profile in `--lab-scope skills`.

---

## 1. Problem statement

In `--lab-scope skills` mode the authoring pipeline turns a natural-language
request + a source PDF into a `build_worktable()` Python protocol. For the
Nanopore library prep it produces **bench-incorrect or incomplete protocols**.
The dominant, repeatable defect is the **magnetic-bead (SPRI/AMPure) cleanup**:

- the model either **punts** the entire cleanup to `wt.add_comment(...)` ("do it
  manually"), or
- it **attempts** the cleanup but gets the chemistry/bookkeeping wrong:
  - the analyte (DNA) is not declared `Reagent(..., role="analyte")`, so the
    bead model can't track it;
  - no **eluate recovery** — after eluting it never moves the plate back onto the
    magnet to transfer the cleared eluate to a clean plate, so downstream steps
    (barcoding/adapter) run on the bead slurry;
  - **volume accounting errors** (e.g. seeds a reagent trough short of what it
    aspirates → strict simulation fails).

Goal of this work: (a) make "generation quality" **measurable**, and (b) try to
**raise** it. KV precision was confirmed f16 first (an earlier finding showed q4
KV cache materially degrades long-context skill adherence), so these results are
**not** attributable to KV quantization.

---

## 2. Measurement tooling built

### 2.1 Rubric — `fluentvibe/authoring/eval_rubric.py` (new)
Deterministic, two-tier scorer over one generated protocol; returns a
`RubricResult` (per-invariant pass/fail/na + aggregate `score` =
passed/applicable).

- **Source tier** (AST/regex, reuses `document_adherence.coverage_gaps` and the
  `ast` helpers in `tools.py`): `coverage_complete`, `analyte_role_tagged`,
  `derived_supernatant` (`= sample+bead−retain`), `derived_eluate`
  (`= elution−retain`), `off_magnet_elution`, `separate_eluate_destination`
  (≥2 MCA tip boxes).
- **Semantic tier** (executes `build_worktable`, `wt.simulate()`, inspects the
  final snapshot — mirrors `tests/test_ampure_sat_1080.py`):
  `magnet_roundtrip` (plate magnetises → off → on again), `eluate_recovered`
  (the `role="analyte"` reagent ends up as free liquid in a non-magnet,
  non-waste labware), `analyte_not_in_waste`.
- The cleanup plate is identified as **the plate that gets magnetised**, not by
  bead presence (beads also legitimately land in waste during the supernatant
  draw — this tripped the first implementation).

**Validated against real artifacts:** scores the saved `attempt2.py` (a past
generation) at **0.33**, correctly failing `analyte_role_tagged`,
`magnet_roundtrip`, `eluate_recovered`, `separate_eluate_destination` while
crediting the correct supernatant math.

### 2.2 Harness — `scripts/eval_authoring.py` (new)
Drives N real generations of the canonical case through the same
profile-bound path as the CLI (`PromptAuthoringService.author`, mirrors
`cli.py:_activate_profile`/`_cmd_author`), scores each with the rubric, and
writes a timestamped `build/eval/<stamp>/` with per-run `.py`, `scores.csv`, and
`summary.md` (per-invariant pass-rate). Flags: `--runs`, `--retry-budget`,
`--lab-scope`, `--out`, `--pdf`, `--prompt`, `--model`, `--endpoint`,
`--no-simulate`.

### 2.3 KNOWN RUBRIC LIMITATIONS (important for the reviewer)
Two ways the aggregate score **over-credits** an incomplete/punted protocol —
flagged but not yet fixed:
1. `coverage_complete` **passes when a stage is only justified by an
   `add_comment`** (the pipeline's "automate or justify" policy). A protocol that
   comments out the whole bead cleanup still scores `coverage_complete=pass`.
2. `eluate_recovered` **passes trivially on a truncated draft** that never
   magnetises: the analyte sits as free liquid in the source plate, no magnet
   plate exists, so the "free analyte in a non-magnet plate" check passes. Seen
   on partial drafts that scored 0.50 despite doing no real recovery.
   → A reviewer should treat `magnet_roundtrip` (which stayed **0%** everywhere)
   as the more trustworthy signal for "did a real bead cleanup happen."

---

## 3. Interventions tried, in order

### Intervention A — tighten the SPRI skill (prompt-only)
`fluentvibe/_assets/config/skills/family/family-bead-cleanup-spri.md`: added a
leading **"REQUIRED INVARIANTS"** block (tag analyte; elute off-magnet; return to
magnet + transfer eluate to a clean plate; never analyte→waste; separate eluate
tip box) so the eluate-recovery requirement leads instead of hiding in prose.

**Outcome (5-run monolith batch, f16):** the prose *landed* — runs that attempted
the cleanup echoed the invariants (one literally emitted the comment
`"(Invariant: separate tip box for eluate)"`) and did off-magnet elution +
separate tips. **But 0/5 produced a complete, simulating, correct cleanup:**
2 runs punted to comments, 2 attempted it but **failed strict simulation**
(`Aspirate: BeadTrough short by 200µL` — volume bookkeeping), 1 produced no
draft. Net: prompt tightening shifted behaviour toward attempting the cleanup
but did not make it correct.

> Note: the harness initially **under-reported** this batch (it only read
> `validation.python_path`/`generated_code` and discarded `best_draft_code` and
> the on-disk `lm_authoring_attempt*.py` files, so "failed" runs that had
> authored full drafts looked empty). Fixed: the harness now scores the best
> draft actually produced and records `failure_category`.

### Intervention B — staged ("digestible subtask") drafting in skills mode
Hypothesis: the monolithic one-pass is too large; decomposing into per-stage
checkpoints will help. The pipeline **already had** a staged engine
(`declare_protocol_workflow` → draft Variables+Labware → extend one functional
group at a time, each must `simulate_python_draft` before the next), but skills
mode inherited enforce's posture (`enforces=True`): two-tool surface
(`declare_protocol_workflow` withheld) + a "write it all in one pass" header +
`_gates_off` disabling staging.

Changes made (skills only; enforce stays one-shot; off/cheatsheet unchanged):
- `lab_scope.py`: `allowed_tools()` adds `declare_protocol_workflow` for skills;
  new staged header (`context_header(enforces, staged=True)`).
- `lab_skills.py`: `assemble_context` passes `staged=True`.
- `graph.py`: new `_requires_workflow_declaration` (true ≠ enforce) and
  `_should_stage` (skills stages only when the declared plan is multi-stage:
  ≥3 non-scaffold groups **or** a bead/clean/wash/elution group); set
  `registry.staged_drafting` on `declare_protocol_workflow` success; rewired the
  three staging gates accordingly. Per the user's choice, **simple skills
  protocols still one-shot** after a single (cheap) declaration.
- `tools.py`: `AuthoringToolRegistry.staged_drafting` default.

**Decision:** "stage only multi-stage protocols" (user choice) — declare always,
enforce per-group checkpoints only when complex.

**Outcome (first 2 staged runs):** mechanically worked (model declares workflow,
names the bead cleanup as its own group, drafts group-by-group, **tags
analyte/bead/eluent roles up front**), but neither completed: run A truncated at
group 2/7 (`retry_budget_exhausted` — default 10-turn cap), run B **stalled** on
turn 2 with a 6.5-min no-tool-call turn → hard fail, empty output.

### Intervention C — harden the staged loop, then re-measure
Two fixes for the failure modes B exposed:
- **Empty-turn re-nudge** (`graph.py extract_python`): in **skills** mode, a turn
  with neither a tool call nor a fenced draft re-nudges with the current-group
  instruction instead of aborting (enforce/default keep immediate failure). The
  staging-scaled iteration budget is the backstop.
- **Budget scales with plan size** (`graph.py`): `_effective_max_iterations`
  (`max(base, groups*2+8)`) and `_effective_max_tool_calls`
  (`max(base, groups*3+12)`) when `staged_drafting`.

**Outcome (5-run hardened staged batch, f16, ~3.3h wall):**

| run | turns | status | score | notes |
|-----|-------|--------|-------|-------|
| 1 | 24 | fail (budget) | 0.50* | *inflated (analyte unmagnetised); never finished |
| 2 | 30 | fail (budget) | 0.167 | 27 `simulate` calls spinning on one stage |
| 3 | 26 | fail (budget) | 0.375 | stuck repairing, never advanced |
| 4 | 23 | fail (model) | 0.429 | repeated empty turns then exhausted |
| 5 | 11 | success | 0.20 | punted again |

Per-invariant pass-rate: `magnet_roundtrip` **0/5**, `eluate_recovered` 1/5
(the inflated one), `separate_eluate_destination` 0/5, `analyte_role_tagged` 3/5,
`coverage_complete` 3/5. **Mean score 0.33**, versus the saved monolith
batch's scored-run mean of **0.20** (`build/eval/batch-after-skill/`). The
aggregate is not a reliable quality comparison because the rubric over-credits
truncated drafts and the monolith harness only scored 2/5 runs; the stable signal
is still `magnet_roundtrip=0%` and **0/5 correct cleanups**.

The hardening removed the crash/stall modes (every run now produces a draft and
uses the scaled budget) but **did not improve output quality**. The model
**thrashes per stage**: it submits a stage, simulation rejects it, it can't
repair, it resubmits near-identical drafts (run 2: 27 `simulate` calls with
shrinking latency = pure spinning) and exhausts the budget without ever reaching
a complete protocol.

---

## 4. Consolidated outcome

- Monolith (skill-tightened): **0/5** complete correct cleanups.
- Staged (hardened): **0/5** complete correct cleanups; mean score **0.33**
  (not directly comparable to the monolith scored-run mean of **0.20** because
  of the rubric/harness caveats above); ~3× slower; 4/5 runs fail.
- `magnet_roundtrip` correctness: **0%** across every configuration tried.

**Root cause (current read):** the bottleneck is the **model's competence on the
bead-cleanup stage itself**, not task size. Decomposition relocated the failure
into a focused per-stage context and the model *still* could not author a correct
cleanup — instead it thrashed. So the "break it into subtasks" hypothesis is, at
this model scale, **disproven by the data**.

---

## 5. Files involved in the original experiment

**New (this experiment):**
- `fluentvibe/authoring/eval_rubric.py` — rubric
- `scripts/eval_authoring.py` — harness
- `tests/test_eval_rubric.py`, `tests/test_skills_staging.py`

**Modified (this experiment):**
- `fluentvibe/authoring/lab_scope.py` — skills tool surface + staged header
- `fluentvibe/authoring/lab_skills.py` — staged header wiring
- `fluentvibe/authoring/graph.py` — staging predicates, budget scaling,
  empty-turn re-nudge
- `fluentvibe/authoring/tools.py` — `staged_drafting` flag
- `fluentvibe/_assets/config/skills/family/family-bead-cleanup-spri.md` —
  REQUIRED INVARIANTS block
- `tests/test_lab_skills.py` — updated tool-surface assertion

**Related work already present on the branch at the time (not part of this
experiment):** labware auto-rewrite + coverage gate + accept-with-gaps fallback +
timestamped webapp output + brand-neutral skill rename, touching
`graph.py`, `tools.py`, `document_adherence.py`, `models.py`, `service.py`,
`session.py`, `workspace_app/service.py`, and several skill/test files. (This is
why `git diff --stat` shows large `graph.py`/`tools.py` deltas.)

**Tests:** `pytest tests/test_skills_staging.py tests/test_eval_rubric.py
tests/test_authoring_graph.py tests/test_lab_scope.py tests/test_lab_skills.py
tests/test_prompt_authoring.py tests/test_authoring_session.py
tests/test_workspace_app.py tests/test_document_adherence.py` → **193 passed,
1 skipped**.

---

## 6. Reproduce

```bash
# baseline (monolith): force one-shot by NOT staging — revert graph staging or
# use enforce; or just read build/eval/batch-after-skill/
python scripts/eval_authoring.py --runs 5 --retry-budget 4 --out build/eval/repro
# inspect: build/eval/repro/summary.md + scores.csv + run-NN.py
```
Requires LM Studio up at f16. Each staged multi-stage run is ~10–30 model turns
(~minutes each); a 5-run batch is hours.

Saved evidence: `build/eval/batch-after-skill/` (monolith) and
`build/eval/staged-hardened/` (staged).

---

## 7. Recommendation + open questions for the reviewer

> **Update (2026-06-18): staging shelved.** Per-group staging is now **disabled
> in skills mode**. It was making every real generation
> (all bead/SPRI cleanups trip the trigger) run 10–30 model turns instead of one
> — ~3× slower for no quality gain. `graph._should_stage` now returns `False` for
> skills (declare the workflow once, then draft in one pass); the skills empty-turn
> re-nudge and the staged `_SKILLS_HEADER` group-by-group language were removed.
> `off`/`cheatsheet` still stage (unchanged baseline); `enforce` stays one-shot.

**Recommendation:** do **not** enable staging by default — shelve it (code +
tests are sound, behind the skills path, but net-negative here). The evidence
across monolith / skill-tightening / staging points to the only
config-independent lever being a **deterministic guard** that derives & validates
the bead-cleanup invariants (magnet round-trip, eluate recovery, derived
volumes) in the pipeline rather than relying on the model — or a stronger model.
There is already precedent: `tools.py:_enforce_object_draft_volumes`
deterministically fixes the supernatant/eluate **volume** math in enforce mode;
the structural round-trip/recovery checks would be the sibling.

**Open questions for the reviewer to check:**
1. Is the root-cause attribution right — i.e. is this genuinely a model-competence
   ceiling, or is the per-stage **repair feedback** too weak (the model can't act
   on the simulator error and just resubmits)? Look at run 2/3 in
   `build/eval/staged-hardened/run-0{2,3}/lm_authoring_attempt*.py` — are the
   resubmissions actually changing, and is the simulator error message
   actionable?
2. Should the rubric's `coverage_complete` and `eluate_recovered` be tightened
   (see §2.3) before any score is trusted as a quality gate?
3. Staged budget scaling (`groups*2+8`) still exhausted on thrash — should staged
   mode **cap consecutive non-advancing `simulate` calls** (detect "spinning on
   the same stage") and bail to accept-with-gaps earlier instead of burning the
   whole budget?
4. The current implementation shelves skills-mode staging while retaining the
   reusable staging engine for the modes that still exercise it. Revisit that
   decision only with new measured evidence.
