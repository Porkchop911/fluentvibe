# fluentvibe — VS Code extension

Live diagnostics for fluentvibe protocol authoring. As you open or save a
protocol `.py`, the extension surfaces **build errors** (syntax, bad import,
unknown method) and **simulator failures** (insufficient volume, missing
tips/adapter, overdraw, occupied slot, …) as squiggles on the exact authoring
line, each with a repair hint.

It is a thin LSP client: it launches `python -m fluentvibe lsp`, which runs the
headless analyzer (`fluentvibe/copilot`) in an isolated subprocess per file. All
the analysis logic lives in Python — see `docs/copilot-design.md` in the
fluentvibe repository.

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

## Features (current)

All deterministic features (diagnostics, completion, signature help, hover) are
pure analysis — no LLM, no cloud. Only the optional Ctrl+I inline edit uses a model.

- **Live diagnostics** as you type (debounced): build errors + simulator failures
  (bad catalog name, overdraw, missing tips/adapter, occupied slot, typos) on the
  exact line, with repair hints.
- **Quick-fixes** (lightbulb 💡): e.g. pipetting before mounting the adapter →
  one-click insert of `head.mount_adapter()`.
- **Autocomplete**: real FluentControl catalog names inside `catalog="..."`, and
  the fluentvibe API after `head.` / `wt.` / `gripper.` etc.
- **Signature help**: type `head.aspirate(` and see the parameters
  (`target, volume_ul, *, liquid_class, columns=None`), with the current argument
  highlighted.
- **Hover**: hover a method to see its signature and docstring.
- **Inline edit (Ctrl+I)**: select lines, describe a change ("add a return-tips
  step", "use 200 uL tips"), and the model rewrites them — re-validated by the
  simulator, with a warning if the edit introduces an error. Needs a reachable
  LM endpoint (set `fluentvibe.pythonPath` to an interpreter whose
  `FLUENTVIBE_LM_ENDPOINT` points at your model).
- The server only touches files that import `fluentvibe` and define
  `build_worktable()`, so ordinary Python files are left alone.

Planned: live-as-you-type diagnostics, an "explain error" hover.
