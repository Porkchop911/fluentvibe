# Development

fluentvibe uses Python 3.11 or newer. CI runs the default offline test suite on
Windows with Python 3.11. Use a verified 3.11 or 3.12 environment for development;
newer interpreters can expose compatibility warnings in model dependencies.

## Install and check

```bash
python -m pip install -e ".[dev,lsp]"
python -m pytest tests/ -q
```

Live model and FluentControl shell tests are excluded by default. Run those
explicitly with `-m live_lm` or `-m fluentcontrol_shell` when their services are
available. Catalog and saved-profile tests depend on local installation data;
missing or stale fixtures can skip. Review skips alongside passes.

## Code map

| Directory | Responsibility |
|---|---|
| `fluentvibe/` | Worktable, reagents, labware, and public Python API |
| `fluentvibe/blocks/` | Reusable protocol operations |
| `fluentvibe/heads/` | Pipetting head methods |
| `fluentvibe/ir/` | Typed protocol steps |
| `fluentvibe/compiler/`, `decompiler/` | FluentControl XML and Python conversion |
| `fluentvibe/simulator/` | Liquid/deck state, snapshots, and invariant checks |
| `fluentvibe/authoring/` | Model-assisted drafting, grounding, tools, and traces |
| `fluentvibe/copilot/`, `lsp/` | Editor analysis and language server |
| `fluentvibe/workspace_app/` | Local browser workbench |
| `editors/vscode/` | VS Code extension source and build configuration |
| `tests/`, `examples/` | Regression coverage and inspectable examples |

## Working conventions

Keep public operations readable and emit typed IR steps so simulation and
compilation share the same representation. Add regression coverage for changes
to liquid accounting, head state, column mapping, or compiled command semantics.
Keep catalog lookup installation-independent wherever possible; do not hard-code
one machine's GUIDs as universal expectations.

Build the editor with `npm install` and `npm run compile` in `editors/vscode`.
Its Python interpreter must contain fluentvibe and the `lsp` extra. See the
[extension guide](../editors/vscode/README.md) for configuration.

## Current verification boundaries

Partial MCA column pickup and shifted transfers are modeled and tested. See
[partial MCA authoring](partial-mca-authoring.md) for constraints and the
unverified FluentControl/hardware boundary. Simulator coverage applies to modeled
operations, not every FluentControl command or physical motion. Compilation,
FluentControl context checking, and instrument method qualification provide
different evidence and should be reported separately.

Model-assisted authoring depends on the selected endpoint, model, profile, and
request. Tool-selection benchmarks measure dispatch behavior, not protocol
correctness; see [Strata evaluation](strata-evaluation.md).

Machine-built catalogs, workspace profiles, generated protocols, assistant
sessions, and temporary evidence are ignored by Git. Keep reusable examples,
source changes, and regression tests in version control.
