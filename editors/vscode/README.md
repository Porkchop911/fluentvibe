# fluentvibe — VS Code extension

Live diagnostics for fluentvibe protocol authoring. As you open or save a
protocol `.py`, the extension surfaces **build errors** (syntax, bad import,
unknown method) and **simulator failures** (insufficient volume, missing
tips/adapter, overdraw, occupied slot, …) as squiggles on the exact authoring
line, each with a repair hint.

It is a thin LSP client: it launches `python -m fluentvibe lsp`, which runs the
headless analyzer (`fluentvibe/copilot`) in an isolated subprocess per file. All
the analysis logic lives in Python — see [`docs/copilot-design.md`](../../docs/copilot-design.md).

## Prerequisites

Install fluentvibe with the language-server extra into the Python interpreter you
want the extension to use:

```bash
python -m pip install -e ".[lsp]"
```

## Develop / run locally

```bash
cd editors/vscode
npm install
npm run compile
```

Then press **F5** in VS Code (with this folder open) to launch an Extension
Development Host. Open a fluentvibe protocol `.py` and save it to see diagnostics.

## Settings

- `fluentvibe.pythonPath` — interpreter used to launch the server (default
  `python`). Point this at the venv where you installed fluentvibe.
- `fluentvibe.enable` — turn the language server on/off.

## Scope (current)

- Diagnostics on **open and save**. Live-as-you-type and quick-fixes (code
  actions) are planned next — see the phased plan in the design doc.
- The server only analyzes files that import `fluentvibe` and define
  `build_worktable()`, so ordinary Python files are left alone.
