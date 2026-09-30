# Dynabeads authoring regression: evidence and controlled experiment

Status, 2026-09-29: **not experimentally resolved**. No new model generations have
been run for this comparison. Do not describe this as a successful authoring test.
The initial working-tree test run produced **924 passed, 5 failed, 1 skipped,
6 deselected** in 193 seconds, not the reported 929 passing. All five failures
exercise the pre-existing uncommitted grounding guard. Live LM/FluentControl
tests are excluded by the repository's default pytest marker selection.

After the implementation: **940 passed, the same 5 failed, 1 skipped,
6 deselected** in 171 seconds. All **16 newly added tests passed**. Ruff and
`git diff --check` passed. The pre-existing grounding patch and unrelated user
changes remain intact. Temporary PDF page renders were removed; the original
PDF remains available and can regenerate them.

New controlled authoring trials: **0/12 executed; no pass rate available**.
The runner is prepared, but the missing exact request/clarifications and the
serving-configuration discrepancy must be resolved before attributing results
to temperature. No synthetic replacement request was used.

## What is established, and what is not

1. The cached spec for `spec-20260929-133836-fa3a` (key
   `d18c0f6b1301bf7fcd68b12e5585bf8ff6353d5833ed107114d05bd5005f15ad`,
   recorded “29 Sep 13:50”; initially the last entry) explicitly adds 0.5 M
   NaOH elution, an extra 15-minute incubation, 1 M Tris-HCl pH 8 neutralization,
   and an eluate transfer. The original PDF's DNA branch does not prescribe
   these operations. Its endpoint is low-salt resuspension of DNA-coated beads.
   NaOH on page 1 belongs to the RNA pretreatment column. Tris in B&W is a
   recipe component, not evidence for a separate neutralization operation.
2. That spec adds 5 µL prepared bead suspension to 20 µL DNA, despite quoting
   the equal-volume binding instruction. For a pure 2 M NaCl suspension and
   DNA in water, these volumes would give 0.4 M NaCl, not the required 1 M
   (nor the spec note's asserted 0.8 M). Actual residual composition must be
   accounted for rather than treated as empty volume.
3. The failed Full run has three malformed `simulate_python_draft` argument
   strings of 1,224, 719 and 318 characters, all reported with
   `finish_reason=tool_calls`. Invalid JSON is proven. A model-side EOS is
   **not** proven: these historical traces do not retain the raw stream and
   generated token IDs needed to exclude a streaming-parser defect.
4. Full-mode turn 1 actually offered elution as option 6(a); turn 2's user
   reply was `1. yes / 2. yes / 3. yes / 4. yes / 5. yes / 6. a`. Thus this
   run includes a user-selected endpoint change. It is not an unchanged
   datasheet replay. That selection still does not establish a valid elution
   recipe. The question also asserted ~2 **mg** DNA capacity for 200 µg beads;
   Table 2's ~10 µg DNA/mg beads gives ~2 **µg**, a 1,000-fold discrepancy.
5. The Fast cache stores `raw` and `when`, keyed by a hash. It does not store
   the request or clarification answers. The requirements sidecar preserves
   extracted clauses, not the exact original request/order/answers. The Full
   request is different: “automate for DNA, input is a full 96 well plate with
   20ul sample. ask questions if something is unclear”. Reconstructing the
   Fast request from sidecar clauses would repeat the reported experimental
   mistake. Exact Fast text and approved clarification facts have been asked
   for; they must be provided or an explicitly new canonical request chosen.
6. The already-running server reports `qwen3.8-27b`, 65,536 context tokens.
   Read-only inspection of its process arguments found `FLASH_ATTN`,
   `--kv-cache-dtype bfloat16`, eight slots, four MTP draft tokens, `qwen3`
   reasoning and `qwen3_xml` tool parsing. No selected KVarN environment
   overrides were found. These are observed launch settings, not proof of
   physical cache precision. They differ from the reported incident setup.
   No server was started, stopped, or reconfigured.

Consequently, temperature causation is neither confirmed nor refuted. The
reported historical 0/6 versus 1/7 invented-chemistry observations are small,
unmatched observations, **not controlled pass rates**. Correctness failures
also exist upstream of code generation, in the model's clarification proposal.

## Sampling decision

The existing 30,000-token reasoning budget remains a characters/4 estimate,
not a token-exact hard ceiling. Instrumentation does not change that policy;
raw usage/token IDs can quantify the discrepancy during trials.

Do not roll out another global default change based on this evidence. Current
sampling defaults remain untouched. The official [Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B#api-usage)
does recommend thinking-mode temperature 1.0 / top_p 0.95 / top_k 20, but that
does not establish fidelity for this application, serving configuration or
long tool arguments.

Evaluate 0.2 as the challenger for extraction and code-writing, with 1.0 as
the current baseline. Keep understanding at the cell's temperature in the
first comparison; changing phase temperatures simultaneously would confound
the result. If the modes react differently, follow with a separate phase-level
ablation. Keep xhigh and the existing single medium recovery unchanged. A
faster result with invented chemistry is a failure, never a speed win.

Even 3/3 clean runs in a cell would be screening evidence only: with zero
observed failures in three trials, the one-sided 95% binomial upper bound on
failure probability is about 63%. It is not a demo-readiness certification.

## Independent acceptance oracle

`tests/fixtures/dynabeads_m280_dna_oracle.json` was written from visual review
of both pages of MAN0014017 Rev. B.0, independently of the generated spec.
It covers prewash, 5 µg/µL bead preparation in 2X B&W, equal-volume DNA binding,
length-dependent incubation, separation, two or three completed 1X B&W washes,
and final bead-bound product resuspension in low-salt buffer. It lists allowed
reagent formulations and procedure-specific exclusions.

Each trial is reviewed against that oracle in two directions:

- Every required operation must have implementation evidence with correct
  parameters. Missing steps and unexplained manual substitutions fail.
- Every additional chemical operation and reagent must have document or
  explicit, separately reviewed amendment evidence. Extras fail even if all
  required operations are also present. Mechanical implementation steps
  (tips, gripper moves, magnet release, necessary mixing) need not have their
  own literal vendor sentence, but need a valid parent operation/state change.

Review the raw spec **and executed operations**, not titles, comments, or the
model's checklist alone. Expand loops. Track vessels, bead mass, liquid
composition and analyte fate across transfers. Operator hand-offs need an
executable instruction with the required time/parameters, not a vague note.
Residual volume is a named component of a mass balance; “equal volume” means
equality between the document's named operands, not every previous liquid in
the current well. A physically necessary mix is not a new chemical treatment.

The checked-in gate is deliberately fail-closed and review-based; it is **not
an implemented general chemistry verifier**. `review.json` needs an independent
reviewer, each oracle check's pass/fail/unknown with concrete artifact/line
evidence, and explicit `extra_steps` and `extra_reagents` arrays. Missing
reviews are unreviewed, not clean. The oracle is never given to the authoring
model. End-to-end acceptance additionally requires compilation, strict
simulation and a positive InfoPad result. An InfoPad pass alone is insufficient.

For a future automatic general verifier, use a source-side operation graph
with stable section/span IDs and reviewed numeric relations; map each authored
operation to that graph or a typed mechanical derivation. Independently verify
both coverage and extra operations. Ambiguous evidence produces a question,
not deletion. Use a separately sourced amendment for any changed chemistry.

## Disposition of the uncommitted patch

**Discard its automatic mutation behavior; rework the idea as review-only
diagnostics until typed provenance and state relations exist.** The working
tree patch has been preserved, not reverted.

- `_grounded_reagent` accepts any substantial shared word and discards
  concentration/formulation context. `_grounded_quote` uses bag-of-words
  coverage, losing section and relational meaning. On this document, NaOH
  and Tris can therefore appear grounded while serving the wrong procedure.
- `grounding_issues` treats a missing quote on necessary magnet release or
  mixing as evidence to delete the operation. It skips manual/off-deck steps,
  where unsupported chemistry can also be placed.
- `equal_volume_issues` compares an add against accumulated well volume rather
  than the named reference volume; residuals and mixed concentrations can make
  that arithmetic invalid.
- `_settle_grounding` defaults to acceptance when `ask is None`, or when the
  model chooses values. Any answer not containing “keep” can cause deletion
  and volume rewriting. This is not meaningful informed confirmation.
- The five baseline failures are `test_spec_path_asks_revises_and_builds`,
  `test_spec_path_hands_questions_back_without_an_answer`,
  `test_spec_path_states_its_understanding_before_the_long_read`,
  `test_normalisation_asks_for_the_sheet_and_takes_it_from_the_chat`, and
  `test_an_attached_sheet_the_spec_does_not_use_is_asked_about` in
  `tests/test_skeleton.py`. Do not weaken these assertions to bless the guard.

An in-memory probe of the actual failing cached spec confirmed a particularly
bad partial repair: with no answer callback, the guard deletes
`elution_incubate`, `neutralize`, `mix_eluate` and `transfer_eluate`, but leaves
`elution_add` (NaOH) in place and returns `stop=False`. It also rewrites the
bead volume. This is neither a valid source-derived protocol nor a safe
clarification policy. Later cache writes occurred during review; identify the
incident by its key/content, not “the last entry”.

The experimental worker bypasses this one uncommitted mutator in memory in
every Fast cell. No patch is reverted. This policy is recorded in the manifest.
The comparison therefore tests the recorded current source with request
routing and diagnostics, not a byte-for-byte replay of the old deployment.

## Controlled trial runner

`scripts/benchmark_authoring_incident.py` schedules three randomized blocks
of Fast/Full × 0.2/1.0, twelve trials minimum, serially on the existing server.
It does not run launchers. It uses the same request, PDF, profile, clarification
facts, sampling controls and reasoning policy throughout. Bench-spec caching
is disabled. Unknown clarification questions or approval requests are not
auto-approved. Each worker gets its own process and records every outcome.

Create a config inside the repository **after** the canonical request and
clarifications have been confirmed. Example (paths are placeholders):

```json
{
  "request_confirmed": true,
  "request_file": "build/incident-inputs/request.txt",
  "document_file": "build/workbench/authored/spec-20260929-133836-fa3a/attachments/turn-001/MAN0014017_Dynabeads_M280_Streptavidin_UG.pdf",
  "profile": "build/workspaces/1080_Dev",
  "facts_file": "build/incident-inputs/approved-facts.txt",
  "answers_file": "build/incident-inputs/exact-answers.json",
  "trial_timeout_s": 7200
}
```

`facts_file` and `answers_file` are optional. The former is a fixed block of
human-approved facts sent in response to clarification questions in either
mode, expressly not blanket approval of a plan. The latter maps **exact full
question text** to explicit human answers; only it can answer approval
checkpoints. No answers means a trial may stop at clarification, correctly
counted as incomplete. If clarification facts change, begin a fresh experiment
with a new output directory; do not merge its rates with the old one.

```powershell
$env:TMP = 'D:\python\fluentvibe\build'
$env:TEMP = 'D:\python\fluentvibe\build'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:FLUENTVIBE_LM_API_KEY = (wsl -d Ubuntu -- cat /home/iko/qwen-serving/api_key.txt).Trim()
python scripts/benchmark_authoring_incident.py --config build/incident-inputs/config.json --out build/incident-comparison --dry-run
python scripts/benchmark_authoring_incident.py --config build/incident-inputs/config.json --out build/incident-comparison
python scripts/benchmark_authoring_incident.py --summarize build/incident-comparison
```

The manifest records input hashes, source/profile hashes, Python version, HEAD,
settings, question policy, model/context metadata and selected non-secret
process arguments. Each trial captures raw SSE, requests, stop reasons and
vLLM token IDs where returned; initial and continued completions are traced.
Code/profile or serving-process changes stop the batch. A 3,600-second call
limit and a default 7,200-second trial limit are held fixed; timeouts remain
failures in the planned denominator. Do not run other model jobs concurrently;
record any system load disturbance before interpreting timing differences.

Review each trial's `review.json` before summarizing. Rates use all planned
trials, with completed/unreviewed counts shown separately. `confirmed_extra_*`
counts are findings, not claims that unreviewed trials have zero extras.
Invalid JSON is measured separately from syntactically invalid Python. A
malformed call is not automatically labeled a model EOS.

The runner's scheduling, path boundary, malformed-call metrics and acceptance
gate have offline tests. **Live end-to-end execution remains unverified.**
The exact historical Fast request/answers are still missing; no substitute
has been silently used and no experiment pass rate has been invented.

## Evidence needed to settle the mechanism

For a malformed tool call, compare raw generated token IDs/text, raw SSE tool
argument deltas, and the final assembled argument string. If token output is
complete but deltas are incomplete, isolate/replay the installed vLLM XML
parser offline. If both stop early, inspect finish/stop reasons and emitted
special tokens. An absent numeric `stop_reason` does not identify which EOS
was produced. Parser replay must use the exact installed version.

Then run the matched temperature comparison on a frozen serving configuration.
Only a subsequent separately approved KV-precision ablation can isolate cache
effects. Keep weights, requests, decoding controls, context length, MTP and
parser versions fixed where feasible, documenting unavoidable differences.
The 64k/BF16 server currently running cannot establish how 262k/4-bit–2-bit
cache behaved. Restoring or changing a server requires the user's go and the
approved launchers in `D:\Projects\qwen38rtx3090`.

## Implemented request-level fix and limitations

`authoring/request_parts.py` recognizes complete source clauses against the
supplied document, tolerating soft wraps, Unicode typography and list markers.
Modified clauses, negation, changed numbers, unknown text and short ambiguous
phrases stay in user instructions. Decimal values and `max.` are not sentence
boundaries. Nothing is silently dropped: source excerpts retain dispositions.

`requirements.extract_requirements` sends separate instruction/document/excerpt
sections and filters exact duplicate *unchecked* source clauses even if the
model ignores the prompt. Checkable source incubation requirements remain.
This does not recognize arbitrary paraphrases or document-only requests with
no independent source. Those require an explicit source-input field or a
reviewable classification; guessing would risk suppressing real instructions.
This routing change does not by itself prove chemistry completeness.

Additional diagnostics are opt-in for vLLM (`capture_token_ids=True`); generic
endpoints receive no new provider-specific request fields by default. Stop
reasons are retained in final traces; raw continuation streams are retained
when raw tracing is enabled. Sampling defaults and recovery effort were not
changed. No commit, push, model-server change or out-of-repository folder
creation was performed.
