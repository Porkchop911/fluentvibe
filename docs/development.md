# Development

## Supported environment

- Python 3.11 and 3.12 are the blocking CI versions.
- Python 3.14 currently passes the offline suite locally, but LangChain emits a
  compatibility warning. Treat it as best-effort until the dependency warning
  is resolved or CI coverage is expanded.
- Windows is the primary integration platform because FluentControl, its
  datastore, and shell validation are Windows-specific.

Install the package with development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

## Verification commands

Run the blocking local checks:

```powershell
python -m ruff check .
python -m pytest tests -q
python -m pip wheel . --no-deps --wheel-dir build/package-check
```

Mypy is visible but non-blocking while the historical baseline is reduced:

```powershell
python -m mypy fluentvibe
```

The 2026-07-21 baseline is 1,568 errors across 27 files. New work should not
increase that count, and edited safety-critical modules should be tightened as
part of their change.

For isolated supported-version runs with `uv`:

```powershell
$env:FLUENTVIBE_NO_AUTO_REBUILD = "1"
uv run --isolated --python 3.11 --extra dev python -m pytest tests -q
uv run --isolated --python 3.12 --extra dev python -m pytest tests -q
```

The current verified offline result on both versions is:

```text
694 passed, 1 skipped, 6 deselected
```

See [reliability-scorecard.md](reliability-scorecard.md) for the dated baseline.

## Test classes

The default suite excludes tests that need a live model or FluentControl shell:

```toml
addopts = "-m 'not live_lm and not fluentcontrol_shell'"
```

Markers:

- `live_lm`: requires a reachable OpenAI-compatible model endpoint.
- `fluentcontrol_shell`: requires a running local FluentControl UI and shell
  XSCR.
- `slow`: longer-running deterministic tests.
- `integration`: exercises multiple layers end to end.

Install-backed tests skip when the local FluentControl installation is absent.
They must not silently weaken assertions merely because an install is present.

Tests that alter catalog metadata must operate on a copied temporary index.
Never poison or rebuild the package-wide `install_index.db` from a mutation
test: parallel interpreters and editor processes may be reading it.

## Repository layout

```text
fluentvibe/
├── fluentvibe/          Python package
│   ├── authoring/       model orchestration, tools, validation, repair
│   ├── catalog/         FluentControl index and XML parsers
│   ├── compiler/        protocol IR to XSCR
│   ├── copilot/         diagnostics, completion, editing, fixes
│   ├── decompiler/      XSCR to IR and Python
│   ├── heads/           MCA96 and LiHa authoring facades
│   ├── ir/              typed protocol schema and source positions
│   ├── labware/         labware families and liquid state
│   ├── lsp/             language-server integration
│   ├── simulator/       state walker, snapshots, invariants, reports
│   └── workspace_app/   local web service and frontend
├── tests/               offline, integration, and opt-in live tests
├── examples/            executable protocol examples
├── scripts/             evaluation and maintenance harnesses
├── editors/vscode/      VS Code extension
└── docs/                user, reviewer, and developer documentation
```

The maintained subsystem map is [repository-overview.md](repository-overview.md).
Do not add hand-maintained file or line counts here; they become stale quickly.

## Design conventions

- Python modules use `from __future__ import annotations`.
- Pydantic models define protocol IR; dataclasses are preferred for ordinary
  value objects and runtime state.
- Authoring APIs emit IR. They do not render XML or directly mutate simulator
  state.
- The simulator consumes IR and must make modeled, pass-through, warning, and
  opaque behavior distinguishable.
- The compiler and decompiler preserve unsupported behavior explicitly rather
  than silently pretending it was modeled.
- Lazy imports are reserved for real circular dependencies or expensive
  optional integrations.
- Public errors should describe the failed invariant, the relevant object or
  step, and a repair direction.

## Adding or extending a capability

Treat features as vertical slices:

1. Define or reuse the public authoring API.
2. Add or validate the typed IR representation.
3. Implement simulator effects or classify the step explicitly as pass-through
   or opaque.
4. Render the command in the compiler.
5. Parse and emit it in the decompiler where the XSCR carries enough data.
6. Surface diagnostics through the CLI, LSP, and workspace app as appropriate.
7. Add focused unit tests and at least one end-to-end or golden fixture.
8. Update the capability matrix and user documentation.

IR and renderer code originated in the related `fluentdsl` project. Changes to
vendored paths should record whether they are intended to be upstreamed or are
deliberate local divergence.

## Live generation regression testing

The local authoring endpoint defaults to `http://localhost:1234` and the model
defaults to `qwen3.6-27b`. Keep live artifacts outside the package tree, under a
timestamped `build/live-regression/` directory.

Run a small profile-bound smoke case after meaningful authoring changes, then a
more complex benchmark when changes affect workflow planning, skills, repair,
or domain validation. Record:

- model and endpoint configuration;
- profile and lab-scope mode;
- model turns and tool sequence;
- Python build, strict simulation, and compile status;
- repair attempts and repeated/non-advancing calls;
- domain-specific rubric outcomes;
- artifact and model-trace paths.

Model calls default to a 240-second response limit. Override it with
`FLUENTVIBE_LM_TIMEOUT_S` or `--request-timeout`. The evaluation harness also
accepts `--run-timeout` to cap the combined skill-selection and authoring calls;
it always creates `run-status.json` and model traces before generation begins.

Live success is evidence, not a deterministic gate. Offline regression tests
must encode every defect that can be reproduced without the model.

## FluentControl integration

The default install path is:

```text
C:\ProgramData\Tecan\VisionX\Database
```

Override it with `FLUENTVIBE_FC_INSTALL`. Set
`FLUENTVIBE_NO_AUTO_REBUILD=1` for deterministic test runs that should never
refresh the shared catalog automatically.

Deployment and shell validation are opt-in. Generated artifacts must retain an
explicit distinction between structural validation, simulation validation,
FluentControl validation, and hardware validation.

## Current gaps

The maintained feature status and high-priority gaps are in
[capability-matrix.md](capability-matrix.md). Design and product risks are
tracked in [repository-overview.md](repository-overview.md) and
[REVIEW_NOTES.md](../REVIEW_NOTES.md).
