# fluentvibe: demo verification plan and current-use walkthrough

> Historical preparation plan from 2026-09-27. Configuration observations and
> version numbers below belong to that inspection. Recheck them against the
> revision used for your demo; the rehearsal gates remain useful.

Prepared 2026-09-27 against commit `4aaffdc` and the current local configuration. This is a plan, not a completed readiness certificate: live generation, the installed VS Code extension, model serving, and FluentControl interaction have not been exercised for this document.

Snapshot warning: concurrent, uncommitted cancellation and clarification changes appeared during final review. They were not made or validated as part of this task. Freeze and recheck the completed revision before following the walkthrough; specifically retest Stop/cancel, blank-answer behavior, and CLI-to-editor question handling. Do not treat an in-progress checkout as the recording baseline.

Assumption: a 10–15 minute technical demonstration on this Windows machine, using `1080_DEV_TABLE`, a local model, and FluentControl context checking. No physical instrument run. The recommended main interface is VS Code; the web app is a usable alternative and the CLI is the reproducible verification path.

Companion review: [critical UI assessment and UI acceptance tests](demo-ui-review.md). Read its trust/status findings before relying on either interface's success messages.

## 1. What “ready” means

The demo should demonstrate this specific claim:

> Given a reviewed protocol document and explicit instructions, fluentvibe produces inspectable Python, checks the supported requirements and simulated operations, and opens the resulting script in FluentControl for a context check.

It should not claim universal protocol support, complete chemistry verification, guaranteed sub-five-minute authoring, collision-free physical execution, or production readiness.

Record only after these gates pass:

- [ ] One exact repository revision, Python environment, extension build, model configuration, document, request, and deck profile are frozen for the rehearsal and recording.
- [ ] Three consecutive runs of the chosen main example complete without unplanned repairs; every run has fresh evidence, not a previous run's result.
- [ ] The generated protocol agrees with a human-reviewed, source-to-operation checklist, including waits, temperatures, repetitions, sample identity, and recovery of the intended output.
- [ ] Strict simulation succeeds; opaque/unmodeled commands are absent from the main example.
- [ ] Instruction checks are nonempty, cover the actual request, and contain no failed or unverified requirements for the automated-check claim.
- [ ] FluentControl actually opens the matching artifact and reports no InfoPad errors. An unavailable check is not a pass.
- [ ] The editor catches a deliberately introduced instruction violation, and the repaired file passes again.
- [ ] The replay, displayed source, compiled artifact, and InfoPad result refer to the same revision of the draft.
- [ ] The recording has a rehearsed recovery path and a clearly labeled pre-generated example.

Three rehearsals establish a practical recording baseline, not a statistical reliability estimate. A software demo pass is not authorization to execute the protocol on a robot.

## 2. Important current-state findings

These affect how to prepare the demo today.

| Finding | Consequence for preparation |
|---|---|
| `build/workspaces/1080_Dev/workspace_profile.json` names `1080_DEV_TABLE` and instrument `TECAN,FLUENT,2203009762`. | Use this profile for the requested worktable, subject to a freshness check. |
| The older demo guide defaults to `sat_1080_test`, naming `Worktable_Global_Dev_1_nikop-Copy 1` and instrument `TECAN,FLUENT,2002004902`. The two profiles share a workspace GUID but have different saved source hashes. | They are not interchangeable just because the GUID matches. Do not mix profile files or use old benchmark results as verification of the new deck. |
| Generation produces `requirements.md`, but the current CLI generation paths do not automatically save the `<draft-stem>.requirements.json` sidecar used by editor diagnostics. | Create and review that sidecar explicitly before demonstrating requirement checks on save. A Markdown report alone does not enable them. |
| VS Code generation commands apply the extension's model settings; the language server inherits the VS Code process environment instead. | Launch VS Code with the same model and profile environment. Otherwise generation and Ctrl+I/live checks can use different settings. |
| Web Fast generation creates its model client from the server environment. Its request does not include the legacy session's Model, Lab scope, Retry budget, or Response limit fields. | Set the server environment before starting the web app. Changing the Model text box is not sufficient to retarget Fast generation. |
| Fast authoring's CLI exit condition accepts `fc_ok: null` and does not make every instruction verdict a hard failure. The web job's `ok` is based on its stage. | Inspect the result fields and human checklist. Do not use exit code, a green toast, or `stage: done` alone as acceptance. |
| `fc-open` patches the persistent script named `shell`, without requesting a backup, and leaves it open for editing. Its JSON command can return exit code zero even when the script did not open successfully. | Back up the dedicated shell beforehand. Inspect `opened`, `load_error`, findings, and the actual FC window. Never use a valuable production script as this shell. |
| Pull FluentControl edits reports changes and source locations; it does not apply the changes to Python. | Show it as an assisted review/mapping workflow, followed by a manual or explicitly requested AI edit. |
| Editing a file clears its previously displayed FluentControl diagnostics; it does not rerun FluentControl. | Clearing squiggles is not evidence that an edit passed FC. Reopen/recheck explicitly. |
| The default `python` observed during inspection is Python 3.14.2 and emits a LangChain/Pydantic compatibility warning. The documented blocking CI targets are 3.11 and 3.12. | Prefer a separately verified Python 3.12 environment. Do not change interpreters on recording day. |

Implementation references: [CLI](../fluentvibe/cli.py), [Fast authoring](../fluentvibe/authoring/spec_path.py), [web service](../fluentvibe/workspace_app/service.py), [VS Code workflows](../editors/vscode/src/workflow.ts), [language-server startup](../editors/vscode/src/extension.ts), [requirement diagnostics](../fluentvibe/copilot/analyzer.py), and [FC shell integration](../fluentvibe/authoring/fluentcontrol_shell.py).

The main flow should be **Fast document generation**, not Full Python or `wt.add` as a speed showcase. The recorded architecture experiment did not show `wt.add` making model authoring faster. See the [resolver ADR](adr-authoring-resolver.md). Historical timings on another profile are useful planning information, not a promise for this recording.

## 3. Preparation: freeze and configure the machine

### 3.1 Freeze the inputs and collect an evidence folder

Allow a preparation session before recording; do not combine environment repair with the live take.

1. Choose one vendor document and one protocol variant. AMPure is a reasonable first candidate because it has previous Fast-path evidence, but qualify it again on `1080_Dev`.
2. Choose the sample count, input volume, exact labware, liquid-class choices, and output destination in advance. Use values reviewed against the source; this plan does not supply a chemical recipe.
3. Write the exact user request and approved clarification answers into the rehearsal notes. Preserve what the user specified separately from what the vendor specified.
4. Create a new, uniquely named folder under `build/demo/` for evidence. Use separate output folders for rehearsal 1, 2, 3, and the recording. Never overwrite the accepted draft with a negative-test copy.
5. Record `git rev-parse HEAD`, `git status --short`, and any relevant uncommitted diff. Do not reset or discard unrelated work.
6. Record Python version, fluentvibe import location, installed extension version/build, FC version, Windows display scaling and monitor arrangement, and the selected workspace/configuration.
7. Record model ID, server/backend version, quantization, KV-cache format, context limit, sampling settings, and RAM/GPU use. Do not record API keys or a full environment dump.
8. Hash the input document, request file, profile files, accepted draft, and final XSCR. A filename alone does not establish artifact identity.

Profile evidence includes `workspace_profile.json`, `generation.profile.yaml`, `reach.json`, `site_rules.json`, the deck skill, and any approved workspace modules used by the example.

### 3.2 Use one Python environment everywhere

Use an existing, tested Python 3.11/3.12 environment if available. If a new environment is needed, prepare it in advance. For example, only when `.venv-demo` does not already exist:

```powershell
Set-Location D:\python\fluentvibe
uv venv --python 3.12 .venv-demo
uv pip install --python .venv-demo\Scripts\python.exe -e '.[dev]'
```

This installs development, PDF/OCR, and language-server dependencies. Do not overwrite an existing environment blindly. Package installation can require network access; OCR can also require a separately installed OCR executable.

For the commands below, choose the actual verified interpreter:

```powershell
$demoPython = 'D:\python\fluentvibe\.venv-demo\Scripts\python.exe'
$env:FLUENTVIBE_NO_AUTO_REBUILD = '1'
& $demoPython --version
& $demoPython -c "import fluentvibe; print(fluentvibe.__file__)"
& $demoPython -m fluentvibe.cli --help
code --list-extensions --show-versions | Select-String fluentvibe
```

Pass: imports point to the intended checkout; CLI starts; the editor uses this same interpreter. Disabling auto-rebuild assumes the catalog has already been prepared; it does not fix a stale catalog.

### 3.3 Set the profile and local model explicitly

In the PowerShell session from which the web app and VS Code will be launched:

```powershell
$demoProfile = 'D:\python\fluentvibe\build\workspaces\1080_Dev'
$env:FLUENTVIBE_PROFILE_DIR = $demoProfile
$env:FLUENTVIBE_LM_ENDPOINT = 'http://localhost:18020/v1/chat/completions'
$env:FLUENTVIBE_LM_MODEL = 'qwen3.8-27b'
$env:FLUENTVIBE_LM_TEMPERATURE = '0.2'
$env:FLUENTVIBE_LM_REASONING_EFFORT = 'xhigh'
$env:FLUENTVIBE_LM_MAX_TOKENS = '65536'
```

These model settings reproduce the documented demo configuration; they are not a new claim that this sampling configuration is globally optimal. Verify that the server supports the requested reasoning setting and enough context for both input and output. Do not raise limits during a take to conceal truncation.

For the previously configured Ubuntu/vLLM key location, load the key without displaying it:

```powershell
$demoKey = wsl -d Ubuntu -- sh -lc 'cat ~/qwen-serving/api_key.txt'
if ($LASTEXITCODE -ne 0) { throw 'Could not load the serving key; check the configured WSL path.' }
$env:FLUENTVIBE_LM_API_KEY = ($demoKey -join "`n").Trim()
if (-not $env:FLUENTVIBE_LM_API_KEY) { throw 'The serving key is empty.' }
Remove-Variable demoKey
```

If this machine's server uses another credential location, use that instead. Never paste a real key into a repository file, the prompt, a screenshot, or shared logs.

Read-only health checks:

```powershell
Test-NetConnection localhost -Port 18020
$demoHeaders = @{ Authorization = "Bearer $env:FLUENTVIBE_LM_API_KEY" }
(Invoke-RestMethod -Uri 'http://localhost:18020/v1/models' -Headers $demoHeaders).data |
    Select-Object id
nvidia-smi
```

Pass: the intended server is reachable and advertises the exact intended model ID; GPU/RAM have headroom for FC and the editor. A successful model listing is not yet proof that generation works; rehearsal supplies that proof.

Only one model-serving workload should own the GPU for the demo. Stop the other model using its established launcher before switching 27B/Flash, and verify it is unloaded. Do not run comparative benchmarks alongside FC automation. LM Studio does not need to be running when the configured endpoint is vLLM; some internal error labels still say “LM Studio.”

### 3.4 Verify `1080_DEV_TABLE`, not merely a profile name

1. Load `1080_Dev` in the web Setup tab.
2. Confirm `1080_DEV_TABLE` and instrument `TECAN,FLUENT,2203009762` against the actual intended FC installation.
3. Compare the saved workspace source hash with the current source file:

```powershell
$demoProfileData = Get-Content "$demoProfile\workspace_profile.json" -Raw | ConvertFrom-Json
$demoProfileData.workspace
$demoProfileData.configuration
$demoActualHash = (Get-FileHash -LiteralPath $demoProfileData.workspace_source.file_path -Algorithm SHA256).Hash
$demoActualHash -ieq $demoProfileData.workspace_source.sha256
```

4. Inspect the deck diagram and common labware. Confirm compatible plates, FCA/MCA tips, troughs, magnet, waste, and required reachable positions.
5. Review `reach.json` and `site_rules.json` for provenance/applicability to this deck. A fresh workspace XML hash does not independently certify reach or fit rules.
6. If the source changed, regenerate/review the profile in Setup, then repeat downstream validation. Do not copy a saved hash to silence the mismatch.

Pass: source identity, instrument identity, labware choices, placement constraints, and intended physical layout agree. A profile that loads successfully is not itself proof of these facts.

### 3.5 Prepare VS Code and FluentControl

VS Code:

1. Use the current extension build. The repo's package version is `0.5.0` and a corresponding VSIX exists; version equality alone does not prove an installed package contains the latest source. Rebuild/package using the extension workflow if the artifact is stale.
2. Install the verified VSIX through **Extensions → Install from VSIX…**, then reload.
3. Set `fluentvibe.pythonPath` to the verified interpreter, `fluentvibe.profile` to the absolute `1080_Dev` directory, and `fluentvibe.outputDir` to the chosen demo output location.
4. Set `fluentvibe.fluentControlCheck = true`, `fluentvibe.checkInstructions = true`, and `fluentvibe.chooseOpenValues = false`.
5. Keep model endpoint/name/key settings empty to inherit the environment, or set matching overrides. Match temperature, reasoning effort, and max tokens too.
6. Save work and fully close existing VS Code processes, then launch `code D:\python\fluentvibe` from the configured PowerShell session. Opening another window of an already-running instance may retain its old environment. Reload Window alone does not import a new parent-shell environment.
7. Open an actual fluentvibe protocol importing fluentvibe and defining `build_worktable()`. Check the Problems panel and `fluentvibe` Output channel.

FluentControl:

1. Use an approved development installation, not an active physical run.
2. Save unrelated work and back up the dedicated `shell` script before the first check. The current default file is `C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\b010c60d-813d-40cf-848a-584d0432f789.xscr`; confirm it really is the disposable shell on this installation before touching it.
3. Open the intended workspace/configuration; match the display arrangement used in rehearsal. The existing demo guide uses a maximized FC window on the left monitor.
4. Dismiss unrelated dialogs and ensure InfoPad is visible. Permit one automation task at a time; leave the desktop unlocked while it operates.
5. Never click the physical Run command as part of this software demonstration.

## 4. Verification sequence

Use an evidence sheet with columns: test ID, date/time, exact inputs/configuration, PASS/FAIL/NOT RUN, duration, artifact path, and notes. “Not run” is not a pass.

### V1 — Offline regression tests

From the frozen environment and repo root:

```powershell
& $demoPython -m pytest tests -q
```

The repo's default pytest configuration excludes `live_lm` and `fluentcontrol_shell`. Record passed, failed, skipped, and deselected counts and the reason for material skips. Do not replace a current run with an old reported test count.

For a focused rerun after a relevant change:

```powershell
& $demoPython -m pytest tests/test_authoring_cli.py tests/test_bench_spec.py tests/test_skeleton.py tests/test_resolver_requirements.py tests/test_document_adherence.py tests/test_volume_variables.py tests/test_worklist_sim.py tests/test_partial_mca.py tests/test_simulator_coverage.py tests/test_lsp.py tests/test_workspace_app.py tests/test_fc_roundtrip.py -q
```

Pass: no unexplained failures in the frozen environment; demo-critical tests actually ran. A passing offline suite does not test the loaded model or actual FC UI. Investigate unrelated baseline failures before deciding whether a narrowly scoped demo can proceed; document the exclusion rather than silently dropping the test.

### V2 — Independently review the source and expected result

Before asking the model to generate anything, prepare this table from the original document and request:

| Source page/step or user instruction | Required action and parameters | Expected destination/state | Generated evidence | Verdict |
|---|---|---|---|---|
| Fill in each source step | Reagent, amount and basis, count, timing, temperature, repetition, head if specified | Sample/product/waste identity and location | Python line(s), resolved operation(s), replay/FC evidence | Pending |

Include every repeated wash, incubation, dry time, transfer, retained fraction, and final collection. Distinguish total reagent supply, per-well dose, and post-transfer residual volume. Record off-deck/manual actions explicitly. Review extraction warnings, tables, units, footnotes, and conditional branches against the PDF itself.

Pass: no unresolved critical source ambiguity. If chemistry decisions are unclear, obtain a reviewer answer or make the clarification exchange part of the demonstrated workflow. Do not enable “choose open values” to make an incomplete specification look complete.

### V3 — Generate the main example three times

In VS Code, use **fluentvibe: Generate protocol from document**, select the source, enter the frozen request, and choose **Fast**. For a reproducible CLI equivalent:

```powershell
$demoDocument = 'D:\path\to\the-reviewed-protocol.pdf'
$demoRequest = 'Replace with the exact reviewed request, including sample count, input volume, required heads and liquid-class-variable instructions.'
$demoRun = 'D:\python\fluentvibe\build\demo\rehearsal-01'
& $demoPython -m fluentvibe.cli author-spec $demoDocument --profile $demoProfile -o $demoRun --request $demoRequest --check-instructions --fc-check
```

Replace the document and request placeholders before running; use a fresh output directory each time. Keep `--choose` absent. Answer questions with the pre-reviewed answers and preserve those answers as evidence.

For each run:

1. Time from submission to final result, including clarification pauses separately. Record model cold/warm state and competing processes.
2. Open `spec.md`, `spec.json`, `draft.py`, `requirements.md`, and `result.json` when present. Missing expected evidence is a failure to investigate, not something to infer from the UI.
3. Require `stage == "done"`, no error, `gate == true`, `fc_ok == true`, and `todo_steps == 0` for this Fast-path acceptance gate.
4. Require a nonzero instruction total, `verified == total`, `failed == 0`, and `unverified == 0` for the automated requirement-check claim.
5. Inspect generated custom/escape-hatch steps and search for TODO, placeholder, pass-only, or comment-only substitutions. A zero TODO counter is not a completeness proof.
6. Compare the full request with the extracted requirements: an instruction absent from the checklist cannot be rescued by a perfect score.
7. Compare every source step with the independent V2 table. Check actual operations, not just comments or the same extracted spec used to generate the code.

If the demo promises “under five minutes,” all three rehearsals must meet that exact definition of time and scope. If they do not, change the claim or use a labeled time-lapse. Do not exclude failed or slow runs from the rehearsal record.

### V4 — Create persistent requirements and rerun deterministic checks

Generation's report and the editor sidecar are separate artifacts today. After selecting a candidate draft:

```powershell
$demoDraft = Join-Path $demoRun 'draft.py'
& $demoPython -m fluentvibe.cli requirements $demoDraft --request $demoRequest --document $demoDocument --profile $demoProfile
```

This makes model calls and writes `draft.requirements.json`. It can overwrite an existing sidecar, so preserve the previously reviewed version before regenerating it. Review the new checklist against V2 and the original request; do not assume extraction is deterministic or complete.

Once reviewed, subsequent rechecks should reuse it without asking the model to reinterpret the requirements:

```powershell
& $demoPython -m fluentvibe.cli requirements $demoDraft --profile $demoProfile
& $demoPython -m fluentvibe.cli check $demoDraft --json
& $demoPython -m fluentvibe.cli simulate $demoDraft --strict --fail-on-opaque --coverage
& $demoPython -m fluentvibe.cli compile $demoDraft -o (Join-Path $demoRun 'verified.xscr')
```

Pass: reviewed, nonempty sidecar; every required verdict passes; no unexplained diagnostics; strict simulation and compilation succeed. These commands execute/build Python locally: use trusted, reviewed files, not arbitrary untrusted uploads.

The completeness check is layered: source review + reviewed extracted requirements + deterministic verification + simulation + FC. None of those alone guarantees chemistry correctness.

### V5 — Inspect replay and quantities

Run **fluentvibe: Show protocol replay**, or:

```powershell
& $demoPython -m fluentvibe.cli replay $demoDraft --profile $demoProfile -o (Join-Path $demoRun 'replay.html')
```

Step through, rather than only watching the animation. Verify:

- Correct sample wells and exactly the requested sample count; no silently invented samples in padded MCA columns.
- Expected source fills, per-well additions/removals, final volumes, and sample identity.
- Enough accessible reagent after dead volume; enough tips and waste capacity.
- Correct head and liquid class for the named reagent; reagent reservoirs compatible with that head.
- Magnet moves, bound/unbound fractions, washing, and output collection agree with the reviewed chemistry.
- Waits and ordering appear as operations where expected. Do not assume a visual replay certifies temperature control or collision geometry.

Pass: replay agrees with the independently reviewed table and current source. Replay is a view of the simulator, not a second independent simulator and not a physical robot recording.

### V6 — Recheck in FluentControl and verify artifact identity

Save the draft, then run **fluentvibe: Open in FluentControl** (`Ctrl+Alt+F`), or:

```powershell
& $demoPython -m fluentvibe.cli fc-open $demoDraft --profile $demoProfile --json
```

Remember that this modifies the persistent shell. In the result require `opened: true`, no `load_error`, and no findings. In the actual window confirm the expected script content, worktable, representative reagent/head choices, and fresh InfoPad result. Save a screenshot and a hash of `draft.fc-base.xscr` and the draft.

Pass: the current artifact was actually loaded, not merely compiled; no checksum/load/modal failure; no InfoPad errors. `fc_ok: null`, an empty report after a load failure, or exit code zero is insufficient. Review warnings rather than hiding them.

This is FluentControl context validation. It is not FC runtime simulation and does not certify physical motion, incubation performance, liquid handling, or runtime expression recomputation on hardware.

### V7 — Negative tests: prove that failure is visible

Use separate copies and keep each copy's matching `<stem>.requirements.json` beside it. Change one thing at a time, retain the original evidence, and restore by returning to the known-good copy. Do not mutate the main accepted artifact.

| Test | Deliberate change | Expected result / recording rule |
|---|---|---|
| N1 — Explicit head | Violate the reviewed “ethanol via FCA” requirement. Prefer a setup otherwise physically valid so this tests the instruction checker specifically. | Requirement failure in CLI and editor; repairing the head clears it after recheck. Required for the live instruction-check segment. |
| N2 — Liquid-class variable | Replace required variable references with a literal; also test declaring an unused variable while leaving the literal in use. | Both must be identified as failing the actual instruction, not accepted solely because a variable name exists. If not detected, disclose the limitation and do not claim that enforcement. |
| N3 — Missing chemistry operation | Remove an incubation/wash operation but retain its explanatory comment. | Source-to-operation review catches it; the supported document requirement should fail. A comment must not count as execution. |
| N4 — Wrong parameter/order | Change a required duration or move a required wait before the wrong transfer. | Deterministic check or independent review identifies the exact discrepancy. Restore and rerun all relevant checks. |
| N5 — Missing checklist | Move/remove only the sidecar in a test copy. | Verify that the absence is understood as no persistent requirement checking, not a pass. This is an acknowledged current workflow gap. |
| N6 — Unreachable model | For an isolated test process, use an invalid local endpoint/port; do not stop a shared production server. | Explicit connection failure and no claim of a newly verified protocol. Existing artifacts must not be presented as the failed run's output. |
| N7 — FC unavailable/load failure | Exercise a controlled unavailable FC case during preparation, with saved work and the shell backed up. | The evidence must say unavailable/failed, never “verified.” The manual acceptance gate must reject null/absent FC evidence even if the current UI/exit status appears successful. |
| N8 — Post-check edit | Change a checked Python file. | Old FC diagnostics may clear; demonstrate that an explicit new FC check is required. No stale green badge is accepted. |
| N9 — Source omissions | Compare an intentionally omitted source step with the original document, not only the model's spec. | V2 review catches an omission even if extraction and generation both omitted it. |

Additional qualification if these features will appear or be claimed:

- **Partial plates:** test 20 samples versus 24 positions, a full plate, and a count beyond supported plate capacity. No silent clamp, dropped wells, or liquid assigned to phantom samples.
- **Amounts and pooling:** test exact per-well transfer/removal, total pooled amount, sample conservation, and documented residuals. Previously problematic examples included requested removal versus silently reduced removal; qualify current behavior afresh rather than assuming fixed or broken.
- **MCA columns:** test mismatched selected plate/tip columns. It must reject the mismatch or handle every selected column; never silently zip away work.
- **Temperature/device work:** test a step that requires actual temperature control. An ordinary delay or a comment is not an equivalent implementation.
- **Worklists:** use a small trusted CSV/GWL with hand-calculated expected well changes; check file resolution, labels, units, missing-file behavior, and replay. Current HEAD simulates supported transfers, but that is not universal support for every worklist command.
- **Runtime volume variables:** change a base `wt.volume(...)` value, rebuild and inspect dependent values and emitted FC expressions. Separately qualify changes made only inside FC; InfoPad acceptance alone does not prove runtime recomputation.
- **Unsupported steps:** request an operation outside the Fast vocabulary. It must surface as a question, a visible unsupported step, or reviewed custom code. Silently dropping it is a fail.

A core failure blocks the corresponding claim. Do not work around missing chemistry by simply leaving it out of the presentation. A feature unrelated to the selected protocol may be explicitly excluded from a narrowly scoped demo, with its limitation recorded.

### V8 — FC edit mapping and optional conversion tests

**FC edit mapping:** open the accepted draft with `fc-open`; in the dedicated shell change one clearly identifiable scalar parameter and save. Then run **fluentvibe: Pull FluentControl edits**, or:

```powershell
& $demoPython -m fluentvibe.cli fc-pull $demoDraft --profile $demoProfile
```

Pass: the `.fc-changes.json` report identifies the actual change and relevant source location where mapping is supported. The Python file remains unchanged. Apply an intentional corresponding Python edit on a working copy, then rerun requirements, simulation, compilation, and FC. Do not reopen/overwrite the shell before pulling the edit you want to compare.

**Opentrons, if shown:** use a small trusted protocol whose expected operations are known. Confirm the separate `.venv-opentrons` interpreter and package are installed; use `--opentrons-python` for a different verified interpreter. Run **fluentvibe: Convert Opentrons protocol**, or:

```powershell
& $demoPython -m fluentvibe.cli opentrons 'D:\path\to\reviewed-opentrons-protocol.py' --profile $demoProfile -o 'D:\python\fluentvibe\build\demo\opentrons-01' --fc-check
```

Pass: independently compare all meaningful source operations and quantities, not just FC success; no unsupported/TODO steps or invisible device substitutions. This conversion route uses the Opentrons simulator and deterministic generation, not an LLM. Executing/simulating input Python requires trusted input.

## 5. How to use each part today

### 5.1 System map

| Component | What it does | How you use it / what to inspect |
|---|---|---|
| Catalog | Indexes FC labware, liquid classes, and related installed assets. | Setup/Catalog tabs or CLI catalog commands. Prepare it before recording. It is not a substitute for measured head reach and site fit. |
| Workspace profile | Packages the chosen worktable, instrument, approved assets, preferences, deck skill, and optional modules. | Load/save in Setup; select the same directory in CLI, VS Code, web, and LSP environment. Revalidate after changing FC workspace data. |
| Local model server | Provides authoring/extraction/editing responses over an OpenAI-compatible endpoint. | Start with the established server launcher; select its exact advertised model ID and credential via environment. fluentvibe does not load the model by changing a dropdown. |
| Document extraction | Turns PDF/text/DOCX inputs into text for authoring. | Attach the document or use `--document`/`author-spec`. Compare extraction against the original, especially tables/scans. UI attachment support differs; PDF/text is the safest common demo input. |
| Bench Spec + Fast generation | Extracts structured steps, asks questions, resolves deck details, and generates Python; may invoke custom-code filling. | Choose Fast or `author-spec`. Review the spec, questions, TODO/custom steps, requirements, and generated source. |
| Full Python authoring | Lets the model use the broader Python/DSL surface and repair drafts. | Choose Full Python, use `author`, or use the legacy chat session. Useful for capability experiments; slower and less predictable for a live take. |
| Skills, retrieval, workspace modules | Supply relevant DSL guidance, examples, deck facts, and approved reusable helpers. | Primarily internal authoring context. For Full Python the `skills` lab scope is available. Review/approve modules in Setup; do not treat model guidance as enforcement. |
| DSL and blocks | Express worktables, labware, heads, transfers, logic, and reusable protocol fragments in Python. | Edit `build_worktable()`. High-level blocks/resolver helpers reduce boilerplate; raw DSL remains available. Inspect `examples/` for patterns, but do not assume an example fits this deck unchanged. |
| Resolver (`wt.add`) | Chooses supported defaults such as placement/head/tips/fill, with explicit requirements such as head or LC variable. | Use where its supported operation matches the need. Do not advertise a universal planner, complete deferred deck solver, or proven model latency improvement. |
| Requirements | Extracts and checks supported user/document constraints against resolved operations. | Read generation's report; explicitly create a sidecar for persistent editor checking; reuse it for deterministic rechecks. Review extraction completeness separately. |
| Simulator | Models supported liquid/tip/sample/bead/contamination behavior and other supported state transitions. | `simulate --strict --fail-on-opaque --coverage`; inspect state and unsupported coverage. It does not certify all physical or chemical behavior. |
| Replay | Visualizes simulator states over the protocol. | Show protocol replay / web Show replay / CLI `replay`. A debugging/explanation aid, not a second independent validation. |
| Compiler | Converts the resolved protocol representation into FC XSCR. | CLI compile, Code Lab, generation, or FC-open. Compilation alone proves neither completeness nor acceptance by FC. |
| FluentControl integration | Loads a generated script through a persistent shell and reads InfoPad. | Enable generation's FC check; use Open in FluentControl for inspection. Track backups, actual load success, and freshness. |
| FC change mapping | Compares the base compiled script with FC's saved edits. | Pull FluentControl edits; review report and implement a deliberate source change. It is not automatic bidirectional synchronization. |
| Decompiler | Converts an XSCR into Python/IR; unknown commands may be retained as raw/generic steps. | Decompile tab or CLI; then check strict simulation coverage and semantics. XML preservation is not full semantic understanding. |
| Opentrons importer | Converts supported simulated Opentrons operations into a spec and generated fluentvibe protocol. | Dedicated editor command or CLI, using a separate Opentrons environment. Review coverage, deck differences, and quantities. |
| Benchmark/evaluation scripts | Measure authoring paths, sampling, corpus coverage, and FC edit-transfer experiments. | Developer tools, run separately from a demo. Preserve failures, timings, machine state, and quality metrics. They are not runtime prerequisites. |
| Deployment | Writes a script into the FC datastore. | Separate deliberate workflow after checks, normally with FC closed. Not required just to show generation and InfoPad. |

### 5.2 VS Code: normal working session

1. Open the repo in the configured VS Code instance.
2. **Generate protocol from document:** choose the reviewed document, type explicit instructions, choose Fast. Keep the instruction request nonempty: current generation workflows condition instruction extraction on having a request. Review/answer questions; wait for the result, not just the opened Python tab.
3. Read `spec.md`, `requirements.md`, and the source. For evidence, open `result.json` too.
4. Create the reviewed persistent sidecar through V4. For a handwritten protocol, **Set instructions for this protocol** offers a shorter prompt-only route. That command does not attach the original vendor document and can replace the sidecar, so do not use it to accidentally discard an existing document checklist.
5. Save and inspect diagnostics. Supported failures appear in Problems/underlines; unverified requirements appear as warnings. Missing sidecars do not automatically produce missing-instruction errors. If external sidecar changes are not picked up immediately, reopen the document or make and save a harmless edit.
6. **Show protocol replay** to inspect the sequence and liquid states.
7. **Open in FluentControl** to check the current saved draft. Recheck after every meaningful edit.
8. Optionally select a small region and use **Edit selection with AI** (`Ctrl+I`). The extension applies the returned edit directly and may warn afterward; it is not an approval-preview workflow. Work on a copy, review the diff, use Undo if wrong, and repeat all affected checks.
9. If you edit in FC, save there and **Pull FluentControl edits** before reopening/recompiling over the shell. Review the report, then reconcile source explicitly.

**Generate**, **Edit selection**, and **Set instructions** can call the model. Normal deterministic requirement rechecks and simulation do not require model inference. FluentControl checks need the local FC application and interactive desktop.

### 5.3 Web app: tab-by-tab

Start in the configured PowerShell session:

```powershell
& $demoPython -m fluentvibe.cli workspace-app --port 8765
```

Open `http://127.0.0.1:8765`. Keep it on loopback: this is a local, unauthenticated workbench capable of executing Python and interacting with local files/FC, not a public hosted service.

**Setup**

Load existing profile → `1080_Dev` → **Load profile**. Inspect instrument, workspace, deck map, common labware, and default liquid class. For a genuinely new worktable: first make it available in the installed FC data/catalog, select the matching instrument and workspace, **Load**, select/review common labware and positions, inspect optional **Scan modules** results, give the profile a new name, and **Save profile**. Review helpers before approval. Saving profiles or rebuilding the catalog is preparation work; avoid changing the demo baseline mid-take.

**Author — Fast**

Select the profile above, use **Attach files** below, then enter the request in **Document → protocol (fast path, instructions checked)**. Keep **check my instructions** checked; explicitly enable **check in FluentControl**, which is unchecked by default in the current web UI. Leave **let the model choose open values** unchecked. Click **Generate protocol**, answer the displayed questions, inspect progress, Bench Spec, instructions, and the Fast summary, then **Show replay**. The shared **Latest structured result** pane is not updated by the current Fast handler; it can still contain an older conversational result. Do not use that pane as evidence for a new Fast run.

You do not need **Start session** for this Fast workflow. The legacy Model/Retry/Response-limit controls are not Fast model configuration. Restart the server after changing its environment. Current web Fast results expose structured evidence in the UI/job response but do not persist the same full `result.json`/`requirements.md`/`spec.json` bundle as the CLI handler; save the raw response and displayed reports explicitly, or use CLI generation for the archival run. Generated drafts live under timestamped `build/workbench/authored/spec-...` folders.

**Author — conversational/Full Python path**

Set profile, lab scope, model/session controls, and retry/response limits; click **Start session**; attach source material and **Send** the request. Continue in the same chat to answer questions or request revisions. Review the best/current draft and raw result; do not assume the Fast checklist panel certifies legacy chat output. Use explicit CLI/sidecar checks where needed. Sessions/jobs are held in memory: restarting the server loses that interaction state, although files already written to disk remain. Do not restart during a live job.

**Code Lab**

Choose the matching profile. Supply a trusted Python file path or paste a protocol defining `build_worktable()`. Click **Simulate** or **Compile + simulate**; inspect the full Validation Report and generated paths. This is local build/simulation, not an automatic InfoPad check and not necessarily the full persistent-requirement workflow. Use V4/V6 for those guarantees.

**Decompile**

Enter a trusted XSCR path and enable **fail when unknown commands remain** when you want strict conversion coverage. Click **Decompile** and inspect the Python and metadata. Validate the result and initial state assumptions; retained raw XML is not proof the simulator understands it.

**Catalog**

Use **Refresh info** to read index information and **Search** to find assets. **Rebuild catalog index** is a separate mutating operation against installed FC data; run it only during setup when warranted, then revalidate profiles. Searching an asset does not prove it fits a particular site or is reachable by a particular head.

**FluentControl**

Supply the verified XSCR and use **Validate via shell** with the prepared disposable shell. Leave **open XSCR directly** off for the rehearsed shell workflow unless direct opening has been separately qualified. Inspect actual load success and InfoPad evidence. **Deploy XSCR** is a separate datastore write, not another form of simulation. Leave **allow deploy while FluentControl is running** off; deployment is optional and should normally happen with FC closed and a backup/recovery plan.

### 5.4 CLI and advanced authoring

The primary CLI commands are the ones used in V3–V8. Additional workflows:

- `author`: Full Python model authoring. A comparable checked invocation is below. `FLUENTVIBE_FC_CHECK=1` enables its FC gate; unlike `author-spec`, this command does not have `--fc-check`.
- `chat`: terminal conversational authoring; inspect `chat --help` for session options and do not assume its output includes all the Fast reports.
- `spec`: extract/review a Bench Spec separately. `skeleton`: generate Python from a supplied spec and profile. These are useful development stages, not a substitute for final requirements/simulation/FC validation.
- `check --source-doc`: additional document-adherence analysis. `check --explain` uses a model to explain diagnostics; an explanation is not a new verification proof.
- `edit`: AI edit of a specified line range. The CLI requires `--apply` to write the edit; the editor command applies directly. Revalidate either way.
- `decompile input.xscr -o output.py --strict`: strict conversion coverage; follow with strict simulation. Use a new output path, not a source file you want to preserve.
- `catalog info`, `catalog find`, `catalog refresh`: inspect/search/rebuild the installed asset index; check each command's help before using optional flags.
- `deploy`: writes to the FC datastore; there is no `--dry-run` in the current CLI help. Do not use it as a harmless connectivity check or bypass its FC-running guard for the demo.

```powershell
$env:FLUENTVIBE_FC_CHECK = '1'
& $demoPython -m fluentvibe.cli author $demoRequest --document $demoDocument --profile $demoProfile --output-dir 'D:\python\fluentvibe\build\demo\full-python-01' --lab-scope skills --check-instructions --model-trace
```

Full Python remains the broader escape route for loops, worklists, devices, and per-well logic, but Python expressiveness does not mean every operation is implemented or simulated. Inspect explicit DSL calls, available blocks, and coverage for the feature you need.

Named volumes use `wt.volume(...)`; dependent expressions should be expressed in terms of those variables, not duplicated numeric literals. Keep operational liquid quantities distinct from the simulator's analyte/sample marker. Per-well FCA operations and worklists need their own quantity/coverage tests. The recent changes make these worthwhile optional follow-ups, not automatic main-demo claims.

For developer benchmarking, start with [bench_author_spec.py](../scripts/bench_author_spec.py) for Fast generation, [benchmark_qwen38_sampling.py](../scripts/benchmark_qwen38_sampling.py) for the model-written-code sampling comparison, and [fc_roundtrip_eval.py](../scripts/fc_roundtrip_eval.py) for experimental model-assisted FC edit transfer. The sampling benchmark deliberately disables the skeleton; its timings are not Fast timings. The Fast benchmark lets the model answer open-value questions, so it is not equivalent to a human-approved demo rehearsal. Read `--help`, choose isolated output paths, and run only one GPU/FC experiment at a time.

## 6. Recording sequence and fallback

Recommended 10–15 minute sequence, adjusted to actual rehearsed timings:

1. **0:00–1:00 — Show the contract.** Source document, explicit user instructions, selected `1080_DEV_TABLE` profile, and the software-demo/no-physical-run boundary.
2. **1:00–5:00 — Fast generation.** Submit the frozen request; show any clarification honestly. While it runs, explain source → spec → resolved Python → checks. Do not promise completion at a particular timestamp unless rehearsals support it.
3. **5:00–7:00 — Inspect what was produced.** Show a representative source step, its implementation, nonempty checked requirements, and the final output collection. Explain that human source review still matters.
4. **7:00–9:00 — Replay.** Step through representative additions, washes/waits, and output recovery.
5. **9:00–11:00 — Deliberate failure.** On a prepared copy with a reviewed sidecar, break the explicit head instruction; show the diagnostic; repair and recheck. If the newly generated draft needs sidecar extraction, show that extra step/time rather than implying it was automatic.
6. **11:00–13:00 — FluentControl.** Open the current saved artifact, show actual loaded content and InfoPad, and distinguish context checking from physical execution.
7. **13:00–15:00 — Optional closing feature.** Show FC edit mapping or a prequalified Opentrons conversion, not both if it makes the ending rushed. Close with the limitations and next verification step.

For a 90-second clip, use a clearly labeled time-lapse and include actual elapsed time. Never splice a failed run's start to a different successful artifact without labeling the transition.

Before capture:

- Close unrelated tabs, notifications, documents, terminals containing credentials, and other model/FC jobs.
- Use readable font sizes and the same desktop layout as rehearsal. Check audio, screen boundaries, and that dialogs/InfoPad are captured.
- Keep the original document, accepted draft, checklist, replay, and evidence folder one click away.
- Test the exact capture setup once; UI automation can be sensitive to layout, focus, and modal windows.

Fallback:

- If generation is slow, state that it is still running and switch to a labeled previously verified example. Do not call it the current run.
- If the model is unavailable, demonstrate deterministic checks/replay on the accepted artifact; do not claim live authoring succeeded.
- If FC automation fails, stop the FC-verified claim. A saved screenshot can be labeled as prior rehearsal evidence, not a fresh verdict. Investigate focus, dialogs, shell identity, and configuration off-camera.
- If code, profile, document, model configuration, or extension changes after acceptance, repeat the affected gates. If the change can alter generation/resolution, repeat all three main rehearsals.
- Keep shell backups and the original accepted artifacts. Restore the shell only through a controlled recovery step with FC state understood; do not overwrite files under a running application casually.

## 7. Final sign-off sheet

Copy this into the recording's evidence folder and fill it in:

```text
Recording date / reviewer:
Demo scope and explicitly excluded features:
Repository commit / relevant dirty diff:
Python executable and version:
Installed extension build/version:
FluentControl version / display arrangement:
Profile directory / workspace / instrument:
Profile and workspace source hashes checked:
Model/backend/quantization/KV/context/sampling:
Document hash / request / approved clarification answers:

V1 offline tests: NOT RUN | result/counts/skips/evidence:
V2 independent source review: NOT RUN | reviewer/evidence:
V3 rehearsal 1: NOT RUN | total/model/clarification time/result folder:
V3 rehearsal 2: NOT RUN | total/model/clarification time/result folder:
V3 rehearsal 3: NOT RUN | total/model/clarification time/result folder:
V4 nonempty persistent requirements + strict checks: NOT RUN
V5 replay/quantity/sample review: NOT RUN
V6 current artifact loaded in FC, InfoPad clean: NOT RUN
V7 negative tests: NOT RUN | cases/results/known limits:
V8 optional FC edit mapping / Opentrons: NOT RUN or OUT OF SCOPE
Final draft / XSCR hashes:
Capture setup and labeled fallback checked: NOT RUN

Decision: NO-GO until required gates pass
Accepted limitations and precise permitted demo claims:
Reviewer sign-off:
```

Keep the main story small and well evidenced: one real document, one explicit instruction set, one deck, one complete protocol, one detected mistake, and one fresh FluentControl check. Add other features only after they pass their own qualification.
