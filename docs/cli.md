# CLI

`tecanlab/cli.py`

The `tecanlab` command is the operational front-end for the package.
Four top-level subcommands plus a `catalog` group.

```
tecanlab compile    <path/to/protocol.py>  [--output OUT]
tecanlab simulate   <path/to/protocol.py>  [--json]
tecanlab decompile  <path/to/script.xscr>  [--output OUT.py] [--strict]
tecanlab catalog refresh [--install <PATH>] [--db <PATH>]
tecanlab catalog info
tecanlab catalog find <pattern> [--category CAT]
```

The `tecanlab` script is registered via `pyproject.toml`'s
`[project.scripts]` block; running `pip install -e .` from the repo root
makes the command available.

## `compile`

```
tecanlab compile examples/simple_transfer.py
tecanlab compile examples/simple_transfer.py -o /tmp/out.xscr
```

Loads the input `.py` file, calls its `build_worktable()` factory (or uses
a top-level `wt: Worktable` if no factory exists), renders the IR via
`Worktable.compile()`, and writes the `.xscr` to the given path (default:
input filename with the `.xscr` suffix).

The protocol script must therefore expose either:

```python
def build_worktable() -> Worktable: ...
```

or

```python
wt = ...      # built at module level
```

The CLI loader is in `cli.py:_load_protocol` (`cli.py:101`).

## `simulate`

```
tecanlab simulate examples/simple_transfer.py
tecanlab simulate examples/simple_transfer.py --json
tecanlab simulate decompiled_protocol.py --strict --fail-on-opaque --coverage
```

Loads the protocol the same way as `compile`, but instead of rendering it
runs `wt.simulate()` and prints a per-step summary.

For decompiled or production-style validation, prefer
`simulate --strict --fail-on-opaque`. `--strict` requires a bound
workspace plus strict slot/catalog semantics. `--fail-on-opaque` upgrades
unmodeled runtime or raw commands from a soft `passed_with_opaque` report
to an exit-1 validation failure.

Default output (text mode):

```
  step   0 AddLabwareStep            labware= 1  tips=  0  tip_vol=0.0 µL
  step   1 AddLabwareStep            labware= 2  tips=  0  tip_vol=0.0 µL
  step   2 AddLabwareStep            labware= 3  tips=  0  tip_vol=0.0 µL
  step   3 GetHeadAdapterStep        labware= 3  tips=  0  tip_vol=0.0 µL
  step   4 PickUpTipsStep            labware= 3  tips= 96  tip_vol=0.0 µL
  step   5 AspirateStep              labware= 3  tips= 96  tip_vol=1920.0 µL
  step   6 DispenseStep              labware= 3  tips= 96  tip_vol=0.0 µL
  step   7 SetTipsBackStep           labware= 3  tips=  0  tip_vol=0.0 µL
  step   8 DropHeadAdapterStep       labware= 3  tips=  0  tip_vol=0.0 µL
```

With `--json`, each snapshot is emitted as a JSON record:

```json
{
  "step_index": 5,
  "step_type": "AspirateStep",
  "labware": ["SourcePlate", "DestPlate", "Tips"],
  "mca_adapter": "EVA[001]",
  "mca_tip_box": "Tips",
  "mca_tip_volume_total_ul": 1920.0
}
```

Useful for piping into `jq` or feeding a downstream tool.

The JSON payload includes the structured simulator report:

- `status`: `passed`, `passed_with_opaque`, or `failed`
- `failure`: `null` on success, otherwise `{category, exception_type, message, ...}`

When `simulate` exits nonzero and a report exists, JSON mode still prints
the report to stdout and returns exit code 1. Text mode prints the failure
category in stderr, for example `Simulation failed [workspace_binding]`.

## `decompile`

```
tecanlab decompile examples/simple_transfer.xscr
tecanlab decompile some_lab.xscr -o some_lab.py
tecanlab decompile some_lab.xscr --strict
```

Inverse of `compile`. Parses the `.xscr` into the Pydantic `Protocol`
IR, then emits a self-contained tecanlab Python module with a
`build_worktable()` factory. Default output path is the input with
`.py` extension.

Output:

```
Decompiled examples/simple_transfer.xscr -> examples/simple_transfer.py
  groups: 2, steps: 9
```

If any step decoded as `GenericStep` (a type the parser doesn't yet
understand), the count is reported. With `--strict`, presence of any
unrecognised step is an error (exit 1). Without `--strict`, the
decompiler emits a `# [decompiler] unsupported step: <name>` comment
in place and continues.

The decompiled `.py` injects a stand-in `default_reagent` and fills
every Plate/Trough so simulation runs cleanly out of the box. Replace
with real `Reagent(...)` instances to model identity (e.g. beads with
`pinned_when_magnetized=True`). See [decompile.md](decompile.md) for
the full per-step emit table and round-trip parity guarantees.

## `catalog refresh`

```
tecanlab catalog refresh
tecanlab catalog refresh --install C:\Custom\Tecan\Database
tecanlab catalog refresh --db /tmp/alt-index.db
```

Drops and rebuilds the SQL catalog index. By default reads from
`C:\ProgramData\Tecan\VisionX\Database` (override with `--install` or the
`TECANLAB_FC_INSTALL` env var) and writes to
`tecanlab/catalog/install_index.db` (override with `--db`).

Output:

```
Catalog index rebuilt:
  components     629
  workspaces     104
  sites          571
  fixed_deck     354
  tip_box        95
  tube_rack      63
  plate          50
  trough         25
  hotel          11
  waste_chute    11
  wash_station   8
  adapter        6
  magnet_rack    6
```

Run this after FluentControl updates that ship new components, or after
adding custom labware to your install.

## `catalog info`

```
tecanlab catalog info
```

```
Install path : C:\ProgramData\Tecan\VisionX\Database
Built at     : 2026-04-27T00:10:15
Fingerprint  : aa6c5febd25e10576c5b211772f7eb19f7922655d856b752c0c58b1b63dc85c8
Component categories:
  fixed_deck     354
  tip_box        95
  ...
```

Quick sanity check: did the index build, when, against which install,
what's its content distribution.

If the index is empty:

```
Catalog index is empty. Run `tecanlab catalog refresh`.
```

(exit code 1)

## `catalog find`

```
tecanlab catalog find magnet
tecanlab catalog find "96 Well" --category plate
```

Substring search (case-insensitive `LIKE %pattern%`) over component names.
Optional `--category` filters by inferred category.

Output:

```
  [magnet_rack  ] 2 Landscape 7mm Nest Magnet Teleshake Segment
  [magnet_rack  ] 24 Magnet Plate
  [magnet_rack  ] Landscape Nest Magnet Teleshake Segment

3 match(es).
```

Exit 0 on hits, exit 1 on no matches.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TECANLAB_FC_INSTALL` | `C:\ProgramData\Tecan\VisionX\Database` | Where the catalog indexer reads from. |

## Running without `pip install`

You can run the CLI directly without installing:

```
PYTHONPATH=. python -m tecanlab.cli catalog info
PYTHONPATH=. python -m tecanlab.cli compile examples/simple_transfer.py
```

This is the pattern the test scripts use.
