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
fluentvibe decompile path/to/protocol.xscr -o protocol.py
fluentvibe catalog info
fluentvibe catalog find magnet
fluentvibe catalog refresh
```

## Documentation

- [Reviewer guide](docs/reviewer-guide.md)
- [Architecture](docs/architecture.md)
- [Authoring API](docs/authoring.md)
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
