# fluentvibe

fluentvibe is a Python object model for authoring, simulating, compiling, and
inspecting Tecan FluentControl protocols.

The public API is built around `Worktable`, labware, reagents, and pipetting
heads. Method calls emit protocol IR steps; the simulator walks that IR to
reconstruct liquid and deck state; the compiler renders `.xscr` XML that
FluentControl can load.

```text
Python authoring code
  -> Worktable / Labware / Reagent / Head methods
  -> Protocol IR
  -> Simulator snapshots and physical invariant checks
  -> .xscr XML for FluentControl
```

## Review Status

This repository is being prepared for technical feedback from the lab
automation community. It is not a production release.

- Source is visible for review, but no open-source license has been granted
  yet. See [NOTICE.md](NOTICE.md).
- Some features require a locally licensed FluentControl installation.
- Generated `.xscr` files must be reviewed and validated before instrument use.
- The bundled Tecan/FluentControl-facing reference data is under active
  provenance review; see [REVIEW_NOTES.md](REVIEW_NOTES.md).

## What Works Today

- Python authoring API for worktables, labware, reagents, MCA96/LiHa-style
  liquid handling, grouping, loops, and conditionals.
- Simulator with per-step snapshots and physical checks such as occupied
  slots, missing tips/adapters, overdraws, and insufficient volume.
- `.xscr` compiler for authored protocols.
- `.xscr` decompiler for recovering a fluentvibe-style Python representation
  from supported FluentControl XML.
- Catalog indexing against a local FluentControl install for labware,
  workspaces, sites, and liquid classes.
- CLI entry points for compile, simulate, decompile, catalog, and prompt
  authoring flows.
- A local workspace setup web app (`fluentvibe workspace-app`) for choosing a
  workspace, laying out labware, saving a profile, and driving the authoring,
  simulate/compile, decompile, catalog, and deploy flows from one browser tab.

## What Needs Review

The most useful feedback is on the domain model and workflow fit:

- Does the API match how FluentControl users think about protocols?
- Are worktables, labware, reagents, tips, and heads modeled at the right
  level?
- Which FluentControl commands or workflow patterns are missing?
- Are simulator checks catching useful mistakes?
- Would decompile-to-Python help with reviewing or modernizing existing
  methods?

## Quickstart

```bash
python -m pip install -e .
python -m pytest tests/ -q
```

Minimal authoring example:

```python
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box

input_dna = Reagent("Input gDNA")

wt = Worktable(name="Simple transfer", comment="Move liquid from one plate to another")

wt.group("Setup")
src = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Nest", 1)
dst = wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Nest", 2)
tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)

src.fill_all(input_dna, 50.0)

wt.group("Transfer")
head = wt.mca96
head.mount_adapter()
head.pick_up(tips)
head.aspirate(src, 20.0, liquid_class="Water Free Single")
head.dispense(dst, 20.0, liquid_class="Water Free Single")
head.return_tips(tips)
head.drop_adapter()

wt.simulate()
print(wt.snapshots[-1].labware("DestPlate").well("A1").layers)

wt.compile("simple_transfer.xscr")
```

A working version is in `examples/simple_transfer.py`.

## FluentControl Dependency

fluentvibe can run some authoring and simulator paths without FluentControl, but
install-backed catalog and workspace features need a local FluentControl
database. By default the catalog indexer looks for:

```text
C:\ProgramData\Tecan\VisionX\Database
```

Override this with `FLUENTVIBE_FC_INSTALL` or the relevant CLI flag. If no install
is reachable, catalog-backed tests should skip or fall back rather than making
the package impossible to import.

## CLI

```bash
fluentvibe compile examples/simple_transfer.py
fluentvibe simulate examples/simple_transfer.py
fluentvibe check examples/simple_transfer.py
fluentvibe decompile path/to/protocol.xscr -o protocol.py
fluentvibe catalog info
fluentvibe catalog find magnet
fluentvibe catalog refresh
```

`fluentvibe check <protocol.py>` analyzes a protocol and prints diagnostics —
Python build errors and simulator failures — positioned at the authoring line
that caused them, each with a repair hint and, where mechanical, a suggested
fix. Add `--json` for machine-readable output, or `--explain` for a
plain-language LLM explanation per diagnostic (needs a reachable LM endpoint;
see `FLUENTVIBE_LM_ENDPOINT`). `fluentvibe edit <protocol.py> --start L --end L -m "..."` rewrites a line range
from a plain-language instruction (LLM) and re-validates the result, and
`fluentvibe complete <protocol.py> --line L --col C` lists catalog/API
completions. These are the headless core of the editor copilot (see
[docs/copilot-design.md](docs/copilot-design.md)); the VS Code extension under
[editors/vscode/](editors/vscode/) wraps them (diagnostics, quick-fixes,
autocomplete, Ctrl+I inline edit). Example:

```text
my_protocol.py:16: [error] Aspirate: well 'A1' on 'Source' short by 15.00 uL
    hint: The source well does not contain enough liquid for the requested
    aspirate. Increase the initial fill volume on the source labware ...
```

### Prompt authoring

The `author` and `chat` flows talk to any OpenAI-compatible chat endpoint
(LM Studio, Ollama, vLLM, …). Point them at your local server with environment
variables or per-command flags:

```bash
export FLUENTVIBE_LM_ENDPOINT="http://localhost:1234/v1/chat/completions"
export FLUENTVIBE_LM_MODEL="your-model-name"
fluentvibe author "Transfer 20 uL from a source to a dest 96-well plate"

# or override per run:
fluentvibe author "..." --endpoint http://localhost:1234/v1/chat/completions --model your-model-name
```

## Documentation

- [Reviewer guide](docs/reviewer-guide.md)
- [Architecture](docs/architecture.md)
- [Authoring API](docs/authoring.md)
- [Workspace app](docs/workspace-app.md)
- [Catalog system](docs/catalog.md)
- [Simulator](docs/simulator.md)
- [Compile path](docs/compile-path.md)
- [Deployment](docs/deployment.md)
- [CLI](docs/cli.md)
- [Decompiler](docs/decompile.md)
- [Development](docs/development.md)
- [Glossary](docs/glossary.md)

## Repository Layout

```text
docs/       Documentation for reviewers and developers
examples/   Example authored protocols
fluentvibe/   Python package
tests/      Unit, integration, and regression tests
scripts/    Local development and validation helpers
```

## License

No license has been granted yet. The code is source-visible for review only.
See [NOTICE.md](NOTICE.md).
