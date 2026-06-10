# fluentvibe Copilot — Design Brainstorm

> Status: design exploration, no code yet. Goal agreed with the user:
> a **VS Code** experience where you write fluentvibe Python and get
> **interactive support** — (1) fast deterministic feedback, (2) LLM
> assistance, (3) inline fix-its.

## 1. The opportunity

Most of the hard machinery for a copilot **already exists** in this repo. We are
not building a protocol analyzer from scratch — we are exposing the analysis the
authoring loop already does, but driven by the *user's* keystrokes instead of an
LLM's. The job is mostly **plumbing + source mapping + an editor client**, not new
domain logic.

What we can reuse directly:

| Need | Already in repo | File |
|---|---|---|
| Run a protocol & get structured results | `Worktable.simulate(strict=…)` → `simulation_report` | `worktable.py:786`, `simulator/walk.py` |
| Structured, serializable diagnostics | `SimulationReport` / `SimulationFailure` (`step_index`, `category`, `repair_options`, `details`, `state_summary`, `to_dict()`) | `simulator/report.py` |
| Physical error taxonomy | `SimulationError` hierarchy (MissingTips, Overdraw, OccupiedSlot, …) | `simulator/invariants.py` |
| Build→compile→simulate→classify pipeline | `AuthoringValidator.validate()` returning `ValidationReport` | `authoring/validator.py` |
| Python build error classification (Import/Name/Attribute → fix options + valid class list) | `_python_build_failure_details()` | `authoring/validator.py:301` |
| Fix-it text, per failure category | `_REPAIR_POLICIES` (`category → options + guidance`) | `authoring/repair_policy.py` |
| Catalog/labware name resolution & search | `resolve_by_name`, `find_components`, `find_sites_for`, … | `catalog/catalog.py` |
| ~40 grounded LLM tools (search labware, lookup API, list valid slots, simulate draft) | `AuthoringToolRegistry` | `authoring/tools.py` |
| LLM loop (grounding → draft → repair) | `PromptAuthoringService`, LangGraph graph | `authoring/service.py`, `authoring/graph.py` |
| Protocol loading convention | `build_worktable()` factory + `_load_protocol()` | `authoring/validator.py:204` |

**The single missing primitive: Python source-position mapping.** The IR's
`line_number` (`ir/schema.py:77`) is the *FluentControl* tree line, auto-assigned
at render time — it has nothing to do with the `.py` the user is editing. Today a
simulator failure says "step 14 overdrew" with no way back to the editor line that
emitted step 14. Bridging that is the core new infrastructure.

## 2. Architecture

A **two-process LSP design**, keeping all domain logic in Python and the editor
client thin.

```
┌─────────────────┐   LSP over stdio    ┌──────────────────────────────┐
│ VS Code ext     │◄───────────────────►│ fluentvibe analysis server      │
│ (TypeScript,    │   diagnostics,      │ (Python; pygls)              │
│  thin client)   │   hovers, code      │                              │
│                 │   actions, cmds     │  ├─ source-tagged execution  │
└─────────────────┘                     │  ├─ reuse simulate/validate  │
                                        │  ├─ reuse repair_policy       │
                                        │  └─ LLM bridge (existing svc)│
                                        └──────────────────────────────┘
```

Why LSP + a Python server (not a pure-TS extension):
- All the analysis (simulate, catalog, validator, repair policy, LLM tools) is
  Python and FluentControl-install-aware. Re-implementing in TS is a non-starter.
- LSP gives us diagnostics, hovers, completions, and **code actions (fix-its)** as
  first-class protocol concepts that VS Code renders natively.
- `pygls` is the standard Python LSP framework; the analysis server is a new thin
  module (`fluentvibe/lsp/`), the VS Code extension is a small separate package.

A **headless mode** falls out for free: the same analysis server can back a
`fluentvibe copilot path.py --json` CLI and the workspace-app Code Lab tab later,
because the editor-agnostic core is just "source string in → diagnostics/actions out".

## 3. Core new primitive — source-position capture

When an authoring call (`head.aspirate(...)`, `wt.place(...)`, `head.pick_up(...)`)
emits an IR step, capture the **caller's Python line** and attach it to the step.

Two candidate mechanisms (decide in MVP):

- **A. Frame inspection at emit time.** In the step-emitting helpers, walk up
  `sys._getframe()` to the first frame whose file is the user's protocol (skip
  fluentvibe internals) and record `(path, lineno, col)`. Cheap, no parsing, but
  needs a stable "is this the user's file" predicate and one capture point per
  emit path.
- **B. AST instrumentation at load time.** When the server loads the buffer, parse
  it and map authoring call expressions to line numbers, then correlate with the
  emitted step order. More robust to indirection (loops/helpers) and gives us
  completions/hover targets, but more work.

Recommendation: **start with A** (smallest change — add an optional
`source_pos` field to `Step`, populated by a single helper in the emit path), and
layer B later for completion/hover precision. Store as an editor-facing field that
never affects rendering (mirrors how `partial_column_offset` is derived-only).

With `step.source_pos` in place, mapping is trivial: `SimulationFailure.step_index`
→ `report.steps[i]` → `source_pos` → LSP `Diagnostic` range.

## 4. Feature breakdown (mapped to the three support types)

### 4a. Deterministic feedback (no LLM, fast, runs on save/idle)
- **Build/import/attribute errors** → reuse `_python_build_failure_details`; map
  `SyntaxError`/`NameError`/`AttributeError` to the offending line (exceptions
  already carry tracebacks; `tb_lineno` for the user's frame).
- **Simulate-on-save** → `Worktable.simulate(strict=True)`; turn
  `SimulationFailure` into a `Diagnostic` at `source_pos`, message =
  `failure.message`, with `failure.category` driving severity.
- **Catalog validation** → unknown `catalog="…"` names flagged eagerly via
  `resolve_by_name` (don't wait for simulate); the catalog category mismatch path
  (`category == "catalog"`) already exists.
- **Coverage hints** → `report.modeled_coverage` / opaque steps surfaced as
  information-level diagnostics ("this step isn't simulated").
- Debounce + run in a worker; protocols simulate in well under a second for
  typical sizes.

### 4b. Inline fix-its (LSP code actions)
- Each `SimulationFailure.category` already maps to `_REPAIR_POLICIES[category]`
  with `options` + human `guidance`. Surface `guidance` as the code-action title.
- **Deterministic quick-fixes** where the repair is mechanical:
  - unknown catalog name → offer the top `find_components` fuzzy matches as
    "Replace with '<exact name>'" edits.
  - `adapter_state` (MCA pipetting before `mount_adapter()`) → "Insert
    `head.mount_adapter()` before this line".
  - `tip_state` → "Insert `head.pick_up(<box>)`".
  - `runtime_variable` → "Insert `wt.set_sim_value('<name>', …)`".
- **LLM-backed fix-its** for non-mechanical repairs (see 4c): the action calls the
  LLM with the failing step + repair policy as context and proposes a WorkspaceEdit.

### 4c. LLM assistance (reuses the existing authoring stack)
- **Explain this error** (hover/code-action): feed `failure.to_dict()` +
  `repair_policy.guidance` + surrounding source to the LM client; return prose. Cheap
  and grounded because the structured failure does the heavy lifting.
- **"Add a wash step" / "fill column 1 with buffer"** (command on a selection):
  drive a *scoped* version of the authoring loop — reuse `AuthoringToolRegistry`
  (search_labware, lookup_api, list_valid_positions, simulate_draft) so the model
  edits against the *current* worktable, not from scratch.
- **Generate the next group**: same loop, seeded with the existing buffer as
  context; returns a diff the user accepts/rejects.
- Endpoint/model already configurable via `FLUENTVIBE_LM_ENDPOINT` / `--endpoint`
  (just shipped), so the extension inherits local-model config with no new knobs.
- Guardrail: every LLM-proposed edit is **re-validated** through
  `AuthoringValidator` before it's offered, so the copilot never suggests a draft
  that fails simulate. This is the existing repair loop, pointed at user edits.

## 5. Phased plan & milestones

**Phase 0 — Source mapping spike. ✅ SHIPPED.** `SourcePos` +
`capture_source_pos()` (`fluentvibe/ir/source_pos.py`); `Worktable._emit` tags
every step; the simulator forwards the failing step's position onto
`SimulationFailure.source_pos`. Editor-only field, excluded from serialization, so
round-trip/parity are unaffected. Proven by `tests/test_source_pos.py` (capture
accuracy, framework-frame skipping, serialization exclusion, end-to-end
failure→line). *Mechanism A; container-step (group/loop) attribution via contextlib
is best-effort — revisit with mechanism B if needed.*

**Phase 1 — Headless analysis core. ✅ SHIPPED.** `fluentvibe/copilot/analyzer.py`
takes protocol source and returns structured `Diagnostic`s (build errors +
simulate failures), each with a line, severity, category, message, and repair
hint (reusing `repair_policy`). Exposed as `fluentvibe check <file> [--json]`
(`cli.py:_cmd_check`). Covered by `tests/test_copilot_analyzer.py` (clean / short
volume / syntax error / unknown method / missing factory / source==file parity).
*Deferred to later phases: eager catalog-name checks before simulate, and
opaque/coverage info diagnostics.*

**Phase 2 — VS Code extension + LSP server. ✅ SHIPPED.** `fluentvibe/lsp/`
(pygls 2.x server + pure dict→LSP `convert`), launched by `fluentvibe lsp` over
stdio and `python -m fluentvibe`. Analysis runs in an **isolated subprocess**
(reuses `fluentvibe check --json`) with a 30 s timeout, so a malformed/non-
terminating buffer can't hang the editor; the server only touches files that
import fluentvibe and define `build_worktable()`. Thin TS client scaffold under
`editors/vscode/`. Optional `[lsp]` extra (pygls). Tested by `tests/test_lsp.py`
(convert mapping, heuristic, server construction, real subprocess analysis).
*Diagnostics fire on open/save; live-on-change is deferred (needs debounce).*

**Phase 3 — Deterministic fix-its (2–3 days).** Code actions for the mechanical
repairs in 4b, driven by `_REPAIR_POLICIES` + `find_components`. *Exit: unknown
catalog name and missing-adapter both offer one-click fixes that re-validate clean.*

**Phase 4 — LLM assistance (3–5 days).** "Explain error" + "edit-with-prompt"
backed by the existing authoring service and re-validated before offer. *Exit:
"add a return-tips step" produces an accepted edit that simulates clean.*

**Phase 5 — Completions/hover (stretch).** AST mapping (mechanism B) powers
catalog-name and API-method completion from the real catalog + public API surface.

## 6. Open questions / risks

- **Source mapping through loops/helpers.** Mechanism A attributes every iteration
  of a `for col in range(...)` to the same line — acceptable for v1, but
  conditionals/subroutines may need mechanism B sooner. Decide after Phase 0.
- **Side effects of executing the buffer.** `simulate` executes `build_worktable()`;
  a malformed buffer could do arbitrary work. The validator already runs untrusted
  drafts (`_load_protocol`), so we inherit that posture, but the LSP server should
  run protocols in a subprocess with a timeout, not in-process.
- **Catalog availability.** Without a FluentControl install, catalog-backed checks
  fall back to offline synthesis (existing behavior). Diagnostics must degrade
  gracefully (warn, don't error) — mirror `CatalogIndexMissing` handling.
- **Re-sim cost on large protocols.** Snapshot deepcopy is linear per step
  (noted in `docs/development.md`); for very large protocols, debounce + cache the
  last good IR and only re-sim on meaningful change.
- **Two packages to ship.** *Resolved:* the TS extension lives in-repo under
  `editors/vscode/` to stay in lockstep with the Python server.

## 7. Recommended first move

Phase 0 + Phase 1 give the entire deterministic value (live errors mapped to the
right line) **without** any editor or LLM work, and prove the one new primitive
(`source_pos`). Everything after that is incremental and reuses code that already
ships. Suggest we build Phase 0 as the next concrete step and validate it against a
seeded failing protocol before committing to the LSP server.
