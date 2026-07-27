# Reliability scorecard

Snapshot date: 2026-07-21.

This file records the latest verified repository baseline. Update it when a
quality gate or supported environment changes.

## Automated baseline

| Check | Latest result | Gate |
|---|---|---|
| Ruff | Clean after import-hygiene cleanup | Blocking CI check |
| Focused authoring checks | 41 passed | Required for authoring changes |
| Focused workbench/trace checks | 28 passed | Required for UI/service changes |
| Default offline suite | 694 passed, 1 skipped, 6 deselected | Blocking on Python 3.11 and 3.12 |
| Mypy | 1,568 errors across 27 files | Visible but non-blocking baseline |
| `git diff --check` | Clean | Required before integration |
| CI Python versions | 3.11 and 3.12; both locally verified clean | Declared package floor is 3.11 |
| Local analysis interpreter | 3.14.2 | Outside the current CI matrix; LangChain emits a compatibility warning |
| Wheel build | Passed with `pip wheel --no-deps` | Required packaging smoke check |

Catalog fingerprint mutation tests now use copied temporary indexes. This avoids
cross-process corruption when supported-version suites or editor tasks overlap.

## Live-generation baseline

### Simple 96-well transfer

- Model: `qwen3.6-27b`
- Scope: profile-bound `skills`
- Profile: `build/workspaces/smoke_profile`
- Result: success
- Model turns: 2
- Tool sequence: `declare_protocol_workflow`, `simulate_python_draft`
- Validation: Python build, strict simulation, and compilation all passed
- Artifact: `build/live-regression/baseline-simple-transfer/`
- Observed duration: approximately 112 seconds end to end

The generated protocol placed exact profile labware, filled all 96 source
wells, transferred 20 µL per well with MCA96, returned tips, and produced an
XSCR artifact. This is the initial live regression anchor; it does not exercise
complex repair or bead-cleanup behavior.

### LiHa row transfer and mix

- Model/profile: `qwen3.6-27b`, `build/workspaces/sat_1080_test`, `skills`
- Request: transfer 10 µL across A1:H1 with LiHa/FCA200 tips, then mix
- Before compact-argument normalization: success in 4 turns, approximately 227 seconds
- After normalization: success in 2 turns, approximately 137 seconds
- Validation: strict simulation and compilation passed; all eight destination wells ended at 10 µL
- Artifact: `build/live-regression/post-workflow-normalization-liha/`

The regression led to two deterministic guards: incomplete workflow declarations
are rejected, and compact string-based functional groups plus `default_value`
aliases are normalized before validation.

### Final provider-aware MCA96 smoke

- Model/profile: `qwen3.6-27b`, `build/workspaces/smoke_profile`, `skills`
- Result: success in 2 authoring turns
- Validation: strict simulation and XSCR compilation passed
- Output: full 96-well 20 µL transfer, declared volume/liquid-class variables,
  exact profile labware, and returned tips
- Artifact: `build/live-regression/final-provider-aware-smoke/`

The preceding attempt exposed an OpenAI-compatible SSE error chunk containing
`Model unloaded.`. The client now surfaces provider error chunks explicitly and
retries known transient provider states once; it no longer misreports them as an
empty successful model response.

### Document-backed failure handling

The canonical full-PDF bead-cleanup benchmark initially remained silent for more
than ten minutes and produced no artifact. Model requests now have a configurable
deadline (`FLUENTVIBE_LM_TIMEOUT_S` / `--request-timeout`), evaluation runs have
a shared whole-run budget, and status plus JSONL/readable traces are created before
generation begins.

A live verification with a 30-second request limit and 40-second run budget ended
cleanly in about 44 seconds including extraction/setup. It recorded both the skill
selection and graph calls, preserved `run-status.json`, and reported
`model_authoring_failure` instead of leaving an orphaned process. Artifact:
`build/live-regression/whole-run-budget-pdf/`.

## Known reliability risks

| Area | Risk | Current mitigation |
|---|---|---|
| Complex LLM authoring | Correct-looking drafts can omit required physical stages | Strict simulation, workflow declaration, skills, executable-source rubric, and deterministic cleanup invariants |
| Model responsiveness | Multiple long streamed calls can make a run appear hung | Per-request and whole-run deadlines, live heartbeat, early status files, retained traces |
| Bead cleanup | Existing live experiment achieved no complete correct cleanup | Required-invariant skill plus deterministic bead model; stronger structural validation is planned |
| Opaque XSCR commands | Simulator cannot validate unmodeled effects | Coverage classification and strict opaque-command policy |
| Catalog provenance | Results vary with the local FluentControl install | Install fingerprinting and profile-bound grounding |
| Python compatibility | Declared range extends beyond CI and dependency confidence | Test 3.11/3.12 now; decide whether to add 3.13/3.14 or cap the range |
| Static typing | Large pre-existing mypy baseline limits regression detection | Non-blocking visibility now; introduce per-subsystem and no-regression gates |
| Frontend | Critical workflows lack browser-level automation | Add end-to-end tests with deterministic service fixtures |
| Deployment | Writes into a live proprietary datastore | FC-running guard, fresh identifiers, checksum verification, and human review |

## Release-quality targets

- Ruff and the complete offline suite pass on every supported Python version.
- No new mypy errors; safety-critical modules become fully typed.
- Changed native code carries focused branch coverage and regression tests.
- Every opaque or inferred behavior is visible in CLI, UI, and machine-readable
  reports.
- Golden compile/decompile fixtures are deterministic after documented
  normalization.
- Live authoring benchmarks measure structural completeness, strict simulation,
  compilation, repair convergence, and domain-specific invariants.
- A generated method is never described as instrument-ready without explicit
  FluentControl or hardware-validation status.
