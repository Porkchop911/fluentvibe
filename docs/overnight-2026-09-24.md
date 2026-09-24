# Overnight run, 24 September 2026

Autonomous development session, about 02:20–07:30. Branch
`review/authoring-quality-experiment`; nothing pushed. Restore points:
`restore/pre-authoring-strategy-2026-09-24` (before any strategy work) and
`overnight/start` (before tonight). Offline suite at the end: 760 passed,
1 skipped.

## Headline

**With an approved Bench Spec, both local models produced a complete, correct
ONT protocol — three runs out of three (27B twice, Flash Next once), each
compiled to `.xscr`, in 26–51 min. Without the spec, no run passed (0.62–0.77).**

| Run (`sat_1080_test`, skills mode; `n27b` = NInfer Qwen3.8-27B) | Input | Result | Rubric vs gold spec | Time | Turns |
|---|---|---|---|---|---|
| `n27b-baseline-phase1` | document | failed: output-token cap hit in turn 1 | – | 14 min | 1 |
| `n27b-blocks-medium-stopped` | document | stopped: block bug (below) | 0.62 | – | 8 |
| `n27b-blocks-doc` | document | "success" | **0.69** – no pooling, tip reuse | 39 min | 4 |
| `n27b-spec-only` | approved spec only | success, compiled | **1.00** | 51 min | 8 |
| `n27b-spec-doc` | document + approved spec | success, compiled | **1.00** | 26 min | 4 |
| Flash Next, 23 Sep (before tonight) | document | "success" | 0.69 | 49 min | 5 |
| `flash-spec-doc` (Flash Next 120K MTP) | document + approved spec | success, compiled | **1.00** | 27 min | 4 |
| `flash-blocks-doc` (Flash Next) | document | "success", compiled | **0.77** – unpaused off-deck steps, reagent carryover | 48 min | 7 |

All scores come from `scripts/rescore_eval.py` with the same rubric, PDF text
and gold spec (`examples/ont_rbk114_spec.json`), so they are comparable. The three
1.00 protocols pass every check: tagged roles, derived volumes, magnet round
trip, eluate recovery, analyte kept out of waste, no tip touching two samples,
real pooling (12 samples per row pool), off-deck steps paused for the operator,
and conformance to the spec's step order.

Without the spec the failures differ by model. Both document-only 27B runs did
per-well clean-ups instead of pooling first; one said so openly
("deck-compatible adaptation of the document's pooled-tube clean-up … performed
per well") — a reasonable choice the checkpoint would let you accept or reject,
but not what the document says. Flash did pool (`pool_columns` into 8 row
pools) but then hand-wrote an on-deck clean-up of the row pools that reuses
sample-touching tips in the ethanol trough, and left the Qubit and thermal
steps as waits despite the repair nudge.

## What changed tonight

| Commit | Change |
|---|---|
| `39fb997` | `fluentvibe/blocks/`: `spri_cleanup`, `stamp`, `add_reagent`, `pool_columns`, `offdeck_step` — whole stages with safe tip use and derived volumes; `BlockError` messages say how to fix the setup |
| `4fcc990` | Gold ONT protocol built from blocks (`examples/ont_rbk114_blocks.py`, ~60 lines of protocol) |
| `fcc8719` | Liquid lineage in the simulator; rubric `pooling_performed` |
| `ce2f2cc` | One repair turn for tip-hygiene / off-deck findings (they never reached the model before) |
| `ce52a22` | `FLUENTVIBE_LM_MAX_TOKENS`; a truncated turn is named instead of reported as empty |
| `1380d1c`, `9c4c4b9`, `abfef9e` | Bench Spec: schema, number/location checks, extraction, `fluentvibe spec`, `author --spec`, gold spec, eval `--spec` / `--spec-only` |
| `5df1618` | Rubric `spec_conformance`; `scripts/rescore_eval.py` |
| `1f6f15f` | Bug: skill selection was sent with every tool attached; the model answered with a tool call and every skill got loaded |
| `7200827` | `edit_draft(old, new)`: small repairs instead of resubmitting the whole file |
| `6be6eb6` | Bug in my blocks: they mixed with a transfer-only liquid class, which FluentControl rejects; `mix_liquid_class` (default `Water Mix`) and compile tests with the profile active |
| `653b4d4` | Clearer `BlockError` when a role is missing; SPRI skill no longer teaches the "plain matrix" fill for block users |
| `eb163b3` | Plan turn also offers `lookup_api` |

Tags: `milestone/first-full-pass` marks the commit of the first 1.00 run.

## What the runs taught

1. **Understanding, not code, was the bottleneck.** Blocks removed most
   hand-written errors, but without the spec each model made its own deck
   adaptations (per-well clean-up; an improvised on-deck row-pool clean-up) and
   wrote the parts no block covers by hand, where the tip and off-deck errors
   came back. With the spec both models followed the document's order, marked
   the tube-scale steps as operator steps, and used only blocks.
2. **`edit_draft` works.** Repairs took 9 s – 4 min instead of 6–10 min per full
   resubmission; the spec+document run finished in 26 min with two edits.
3. **Spec extraction is realistic but needs your checkpoint.** A live run on the
   27B (`build/eval/n27b-spec-extract.md`, ~5 min) produced 31 steps in the right
   order with honest notes on what the document leaves open. It marked the
   tube-scale pooled clean-up as `deck` and several "spin down / on ice" steps as
   `deck`; the location check flagged the latter, and one invented number
   ("720 min" for 12 h) was flagged by the number check.
4. **Speed at long context is similar on both servers.** NInfer's advertised
   70 tok/s decode / 860 tok/s prefill drop to ~20 / ~205 at ~45K tokens of
   context — about Flash's speed. The spec-only prompt is much shorter, but the
   model then spent longer reasoning in turn 1.
5. **Turn 1 is still the slowest step** (13–20 min of reasoning before the plan,
   even at `medium` effort).

## Needs your attention

- **Profile inconsistency (`sat_1080_test`)**: its `common_labware` suggests the
  waste reservoir at `Nest7mm_Pos 4`, but its deck rule only allows troughs on
  `WS_100ml_*` sites. Every model run lost a turn to this. I did not edit your
  profile; the gold example uses `WS_100ml_1 4`.
- **Liquid classes**: the profile whitelists only `Water Free Single` (the
  compile rule separately requires `Water Mix` for mixing), so D4 (one class for ethanol, beads and
  eluate) is forced by the profile, not a model error.
- **Reagent budget**: the document-only 27B draft added the whole RA dilution
  (1.5 µl RA) to every well — 144 µl of RA for 96 wells from a kit that ships
  15 µl. No check catches kit-volume budgets yet.
- **Tip-hygiene findings stay warnings** (your call: reuse is sometimes wanted);
  the model gets one nudge, then the draft is accepted with the warnings.

## Suggested next steps

1. **Spec checkpoint in the web UI** (authoring tab only): extract → show the
   table with flagged rows → edit/approve → author with `--spec`. The runs say
   this is the highest-value piece.
2. **Reagent-budget check** from the spec's reagent list and the document's kit
   volume table.
3. **Fix the profile trough rule vs. `common_labware`** (or tell me which is right).
4. **Shorten turn 1**: try `reasoning_effort=low` for the plan turn only, now
   that the spec carries the understanding.
5. More families: the same spec + blocks approach for normalisation, PCR set-up
   and ELISA, each with a gold spec and gold protocol.

## Files

- Runs: `build/eval/n27b-*`, `build/eval/flash-*` (traces, drafts, `.xscr`).
- Extracted spec: `build/eval/n27b-spec-extract.json` / `.md`.
- Gold: `examples/ont_rbk114_blocks.py`, `examples/ont_rbk114_spec.json`.
