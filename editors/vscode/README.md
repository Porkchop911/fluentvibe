# fluentvibe for VS Code

Author and inspect FluentControl protocols with Python source, simulator
feedback, protocol discussion, and a FluentControl handoff in one editor.
The extension includes document-based drafting, inline edits, replay, and
mapped diagnostics for supported checks.

## Setup

Install fluentvibe into the interpreter the extension will use:

```bash
python -m pip install -e ".[lsp]"
```

Set `fluentvibe.pythonPath` to that interpreter. A local FluentControl catalog
supports installation-backed lookups; assisted drafting and editing need a
reachable model endpoint. Configure model settings in the extension and ensure
its Python language-server environment uses the intended endpoint and profile.

## Build locally

```bash
cd editors/vscode
npm install
npm run compile
```

The extension entry point is `out/extension.js`, compiled from `src/`. Package
with your normal VS Code extension tooling, or launch an Extension Development
Host targeting this folder.

## Review workflow

Open a fluentvibe protocol that defines `build_worktable()`. Diagnostics help
locate build and modeled simulation errors. Completion, signatures, hover, and
quick fixes support direct Python authoring. Use the Chat panel or document
workflow for assisted drafting, and Ctrl+I for a selected-code edit.

Inspect the resulting source, requirements, and replay before handing the
compiled artifact to FluentControl. Missing, stale, or unavailable checks do
not establish a pass. Replay shows the modeled protocol; FluentControl context
checks and physical method validation remain separate steps. Review suggested
edits before accepting them.

The analyzer runs protocol Python in a subprocess. Use protocols you trust.
Deterministic analysis does not need a model; optional assisted features send
context to the configured endpoint.
