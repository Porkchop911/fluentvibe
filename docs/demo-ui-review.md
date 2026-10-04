# Demo UI assessment and improvement opportunities

> Historical assessment from 2026-09-27. The findings below describe the
> inspected revision, not a fresh assessment of the current release. Use its
> acceptance tests to check subsequent improvements.

2026-09-27. Companion to the [verification plan and walkthrough](demo-readiness-plan.md). Recommendations only; no UI implementation changes were made.

Review baseline: commit `4aaffdc` and the UI source inspected during this review. Uncommitted backend cancellation/clarification work appeared during final checks; it was not authored or validated here. Reconcile the findings against that work when complete, especially cancellation, question rendering, and blank-answer/cancel semantics across CLI, web, and editor. Backend support alone does not establish a working user-facing control.

## Verdict and review limits

**The workbenches provide a useful foundation for an operator-led technical demo.** At the reviewed revision, result status still required expert interpretation: generation finishing was not consistently distinguished from an artifact passing the required checks. Clearer verdicts and artifact identity would make the workflow easier for new users to assess.

For an operator-led technical recording, use VS Code as the main surface with the explicit evidence gates in the runbook. It already provides source navigation, requirement/FC diagnostics, replay, and an FC handoff. For a claim that an unfamiliar user can upload a document and confidently recognize a verified result, address the P1 issues below first.

This is a source-backed interaction and information-design review. Browser inspection was attempted, but the browser runtime failed to start because of a Windows sandbox permission/lock error. Rendered appearance, screenshots, responsive layout, keyboard behavior, and the installed extension's runtime behavior have **not** been verified. Code-derived failure sequences below are tests to execute, not claims of UI reproductions.

## P1 — Trust and demo-blocking issues

### UI-01: Success styling does not mean verification succeeded

Evidence: web Fast assigns its success style from `sm.stage === 'done'`, while its adjacent summary can report FC findings or instructions NOT met. Absent FC evidence is described as compiled and simulated, without distinguishing an intentionally skipped check from an unavailable one. The editor handles explicit instruction failures better, but its information/warning decision still permits unknown requirements, TODO steps, and absent FC evidence.

Sources: [web results](../fluentvibe/workspace_app/static/index.html), [editor results](../editors/vscode/src/workflow.ts).

Impact: the most prominent state can contradict the actual readiness of the protocol. Viewers will reasonably interpret a successful headline before reading a lower-level warning.

Recommendation: show separate persistent states for generation, source completeness, instruction verification, simulation coverage, and FC context validation. Each needs Passed / Failed / Not run / Unverified / Stale. Only show an overall Ready for review after the required gates pass; never imply safe to run. A skipped FC check must remain visibly Not run.

Acceptance: test result payloads containing failed instructions, unknown instructions, TODO steps, FC false, FC null, and an empty checklist. None may produce an unqualified verified/ready state. Test completed jobs whose domain result is a failure, not just HTTP exceptions.

### UI-02: A failed new run can leave an old draft and replay available

Evidence: starting Fast clears progress, summary, instructions, and spec, but not the shared `generatedCode` field or enabled replay button. A failed request returns without replacing them. Replay reads the shared code and currently selected profile. Fast also does not update the shared `authorResult` pane used by conversational authoring.

Sources: [run initialization/failure](../fluentvibe/workspace_app/static/index.html), [replay input](../fluentvibe/workspace_app/static/index.html), [shared outputs](../fluentvibe/workspace_app/static/index.html).

Impact: a new request, an old draft, a different profile, and an older raw result can coexist on the screen. The replay may convincingly demonstrate the wrong artifact.

Recommendation: bind output to an immutable run record containing run ID, source version/hash, request, document, effective model settings, profile hash, and check timestamps. Label a retained previous run explicitly. Replay should use the selected run's snapshot, not mutable global input fields. Source/profile edits must mark dependent evidence stale.

Acceptance: complete A; start B and force a connection failure; switch the profile; click replay. The UI must prevent ambiguity or explicitly identify A and A's configuration. Raw results must match the displayed run. Editing code after success must invalidate the displayed checklist/FC evidence.

### UI-03: Controls do not identify the effective configuration reliably

Evidence: Model, Lab scope, Retry budget, and Response limit appear above the Fast workflow, but Fast does not send those fields. Its server client reads the server environment. In VS Code, CLI commands map extension model settings into their subprocess; the language server instead inherits the VS Code process environment.

Sources: [web controls](../fluentvibe/workspace_app/static/index.html), [Fast request](../fluentvibe/workspace_app/static/index.html), [server client](../fluentvibe/workspace_app/service.py), [editor settings](../editors/vscode/src/workflow.ts), [LSP startup](../editors/vscode/src/extension.ts).

Impact: this recreates the original targeting problem: the user believes the app uses the loaded model or selected temperature while a different setting actually governs the request. Comparisons become unreliable too.

Recommendation: show effective endpoint host, model ID, temperature, reasoning effort, profile/workspace, and connection status for the active workflow. Share configuration or scope it explicitly. Hide controls ignored by Fast. Never show the key itself. Do not imply changing a model name loads/unloads it on the server.

Acceptance: change a setting through the supported route and inspect sanitized outgoing request/run metadata. Generation and Ctrl+I must use the intended configuration or clearly disclose differences. Profile changes must invalidate incompatible prior results.

### UI-04: The checklist lifecycle has an invisible gap

Evidence: generation writes a Markdown report, whereas live diagnostics require `<stem>.requirements.json`; the generation handlers do not automatically write that sidecar. Missing sidecars yield no requirement diagnostics. Editor/web extraction is also conditional on a nonempty request, although the request is described as optional and instruction checking looks enabled.

Sources: [generation output](../fluentvibe/cli.py), [sidecar checks](../fluentvibe/copilot/analyzer.py), [editor extraction flag](../editors/vscode/src/workflow.ts), [web condition](../fluentvibe/workspace_app/service.py).

Impact: a generated checked protocol can subsequently be edited without those requirements being checked. No diagnostic looks like a pass.

Recommendation: persist reviewed requirements with the run/draft and visibly indicate whether persistent checking is active. Surface missing, empty, or corrupt checklists. Source-document completeness should not silently disappear when an optional user-request field is blank. Replacing a checklist should show a reviewed diff of the contract.

Acceptance: generate, save, violate an explicit instruction, and save again without hidden setup. The violation should fail. Repeat with document-only input and missing/corrupt sidecars. Until fixed, explicitly create the reviewed sidecar using runbook V4.

### UI-05: In-flight edits can make arriving FC feedback stale

Evidence: edits clear previous FC diagnostics, which is useful. However, Open in FluentControl saves/submits the file, waits, and then attaches findings to the editor's current document without comparing its version/hash with the submitted version.

Sources: [clear on edit](../editors/vscode/src/extension.ts), [FC result mapping](../editors/vscode/src/workflow.ts).

Impact: an edit made during the check can receive the previous revision's findings or apparent clean result. Line locations and freshness may be wrong.

Recommendation: capture the submitted document version/hash. If the file changes, label the arriving result as belonging to the previous revision and require a recheck. Clearing diagnostics should leave a visible stale/not-checked state, not simply remove the warning.

Acceptance: submit a check and immediately edit the protocol. The returning result must not certify the new revision.

## P2 — Workflow, recovery, and interaction

### UI-06: Two authoring workflows are mixed into one page

The Author tab has two request areas, shared attachments/output, Start session, and Fast Generate. Users can type into the wrong field, start an unnecessary session, or assume shared controls apply to both.

Recommendation: explicitly choose Document generation or Conversational authoring first. Each mode needs one request box, relevant settings, its own output record, and one clear primary action. Put attachments next to the request they belong to. Explain Fast's unsupported/custom-code behavior without suggesting unlimited vocabulary.

Acceptance: a first-time user can start a document run without being told which nearby controls to ignore. Mode switching preserves drafts but never merges results.

### UI-07: Long jobs lack a user-level cancellation/recovery contract

The editor sets `cancellable: false`. Web polling offers no visible cancel/resume; jobs/sessions are in server memory. Other actions can be started while a model/FC operation is active. Elapsed time alone does not establish useful progress.

Sources: [editor progress](../editors/vscode/src/workflow.ts), [web polling](../fluentvibe/workspace_app/static/index.html), [jobs](../fluentvibe/workspace_app/service.py).

Recommendation: show phase, last-progress age, waiting-for-answer state, and model/FC resource ownership. Add cooperative cancellation with an honest stopping state and preserved artifacts; do not interrupt FC file writes unsafely. Persist job identity/history and make reconnect behavior explicit. Avoid invented percentage-complete estimates.

Acceptance: reload, network loss, unanswered questions, restart, and cancellation have understandable outcomes. Distinguish canceling the UI wait from actually stopping server work. Prevent conflicting shell operations.

### UI-08: Risky actions need context at the point of use

The web places Deploy beside validation and exposes an allow-FC-running override. Its click handler submits directly without a confirmation/review step. Open in FluentControl does not clearly explain persistent shell replacement.

Sources: [FC panel](../fluentvibe/workspace_app/static/index.html), [shell replacement](../fluentvibe/cli.py).

Recommendation: separate Check in FluentControl from Publish to datastore. Show target installation, object, artifact identity, and backup/recovery before publishing. Keep risky overrides out of the normal workflow. Identify the disposable shell and unsaved-work implications before replacement.

Acceptance: an unfamiliar user can distinguish validation, shell replacement, datastore publication, and physical execution before clicking. A different/stale artifact is never published through an ambiguous action.

### UI-09: Editing and Pull imply more safety/automation than exists

Ctrl+I applies code directly and can warn afterward. Pull FluentControl edits reports changes without applying them. Set instructions can replace rather than merge/review the checklist.

Recommendation: show AI edits as apply/discard diffs and indicate invalidated checks. Rename Pull to Review FluentControl changes until explicit reconciliation exists. Preview changes to the requirement contract as well as the code.

Acceptance: inspection labels do not imply synchronization; edits require a clear review path; Undo does not leave stale validation looking current.

### UI-10: Evidence is fragmented and too developer-facing

The user must combine Markdown previews, Problems, an Output channel, JSON, code, replay, and FC. Web Fast does not persist the same evidence bundle as CLI. Multiple clarification questions are collapsed into one text answer, with weak visible linkage to source steps and resulting requirements.

Recommendation: provide a persistent run summary with artifact links and individual check verdicts. Show clarifications with their source step, unit, options/range when known, and saved answer. Link source step to generated operation to evidence. Keep raw JSON/logs available as technical details rather than the primary completion report.

Acceptance: users can answer what ran, with which configuration, what passed, what was not checked, where the files are, and what to do next without reading console output or remembering a transient notification.

## P3 — Visual hierarchy, accessibility, and presentation

The following are source-indicated risks requiring rendered tests, not confirmed visual defects:

- **Legibility:** base text is 13 px, labels 11 px, and deck labels can be ellipsized. Test actual recording/playback resolution, browser zoom, and display scaling. Consider a larger-text presentation mode with fewer simultaneous panels. [CSS](../fluentvibe/workspace_app/static/index.html)
- **Navigation semantics:** tabs are ordinary buttons toggling classes; the handler does not maintain tab-specific selected-state/keyboard semantics. Add an appropriate accessible navigation pattern and verify it with keyboard/assistive technology. [Tabs](../fluentvibe/workspace_app/static/index.html)
- **Status readability:** requirement rows use emoji without explicit status words in their own cells. Include Passed / Failed / Unverified text and source/code links. Do not rely on color or emoji alone. [Checklist](../fluentvibe/workspace_app/static/index.html)
- **Setup density:** saved-profile users need identity, freshness, and compatibility first. Keep the full deck/asset editor available, but make editing deliberate and distinguish known from unverified reach/fit facts.
- **Focus and announcements:** verify questions and errors are keyboard-reachable and announced without surprising jumps. Some statuses use `aria-live`; that does not establish accessibility across all operations.
- **Terminology:** explain Bench Spec, profile, shell, opaque step, and InfoPad at first use. Prefer specific states to generic done and actionable failures to generic Request failed; redact secrets.
- **Artifact access:** offer Open draft / Open replay / Open evidence folder rather than requiring paths to be copied out of JSON.

Preserve the useful foundations: elapsed progress, explicit questions, textual failure details, mapped FC findings, and inspectable source/spec/replay. Improve the meaning and ownership of these elements rather than adding competing panels.

## Recommended improvement order

1. Unify verdict semantics and expose missing checks.
2. Bind source, replay, raw results, and FC findings to an exact run/revision.
3. Show truthful effective configuration; hide ignored controls.
4. Persist and visibly activate the reviewed checklist.
5. Separate authoring modes and risky publication actions.
6. Add job recovery, artifact links, and structured questions.
7. Perform rendered polish, accessibility, and capture-readability checks.

The first four affect whether the demo tells the truth. Address them before a cosmetic redesign. These changes are recommended, not implemented.

## UI acceptance checklist for recording

- [ ] Inspect Setup, Fast input, progress, questions, success, incomplete, unavailable-FC, and failure states at actual capture size.
- [ ] Inspect equivalent states in the installed extension, not only source.
- [ ] Test UI-01's verdict matrix and headline/detail consistency.
- [ ] Test A succeeds → B fails → replay/profile switch/raw result; prove artifact identity stays explicit.
- [ ] Test document-only generation and missing/corrupt sidecars.
- [ ] Confirm advertised controls affect the selected workflow and effective settings are recorded.
- [ ] Edit during an FC check and verify the new revision is not certified by an old result.
- [ ] Navigate the main flow by keyboard; inspect focus, errors, questions, and status text.
- [ ] Test the recording resolution and a narrower window for clipped essential content.
- [ ] Distinguish replay from execution, context checks from physical validation, and inspection from datastore writes.
- [ ] Keep credentials out of screens, shared configuration, logs, and evidence exports.
- [ ] Rehearse a labeled fallback; never present a previous result as the current run.

Status: source review complete; live/visual acceptance tests **NOT RUN**.
