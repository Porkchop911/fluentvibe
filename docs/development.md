# Development

## Repo layout

```
fluentvibe\
├── pyproject.toml          ← Build config + console_scripts entry
├── README.md
├── docs\                   ← This documentation set (markdown)
├── examples\
│   └── simple_transfer.py  ← End-to-end example, exercised by parity test
├── fluentvibe\               ← The package
│   ├── __init__.py         ← Public API + first-import index build
│   ├── reagent.py          ← Reagent dataclass (33 LOC)
│   ├── worktable.py        ← Worktable + from_workspace + place/group/compile (261 LOC)
│   ├── gripper.py          ← Gripper (52 LOC)
│   ├── cli.py              ← `fluentvibe` CLI (161 LOC)
│   ├── labware\            ← 10 behavioral families (~511 LOC total)
│   │   ├── base.py         ← Labware + Layer + Well + offline-synthesis (290 LOC)
│   │   ├── plates.py       ← Plate, Plate96, Plate96Deep, Plate384 (37 LOC)
│   │   ├── troughs.py      ← Trough, Trough25mL, Trough100mL, Waste (47 LOC)
│   │   ├── tipboxes.py     ← TipBox, MCA*Box, FCA*Box (40 LOC)
│   │   ├── adapters.py     ← Adapter, EvaAdapter (21 LOC)
│   │   ├── magnet.py       ← MagnetRack (17 LOC)
│   │   ├── tuberack.py     ← TubeRack (20 LOC)
│   │   └── deckitems.py    ← WashStation, WasteChute, Hotel, FixedDeck (39 LOC)
│   ├── heads\              ← Pipetting heads
│   │   └── mca96.py        ← MCA96Head + Tip (105 LOC)
│   ├── ir\                 ← Pydantic step models
│   │   └── schema.py       ← (611 LOC)
│   ├── compiler\           ← XML renderer
│   │   └── renderer.py     ← (1787 LOC)
│   ├── decompiler\         ← Phase B: .xscr → .py
│   │   ├── xscr_parser.py  ← XML → Pydantic Protocol IR (~330 LOC)
│   │   └── codegen.py      ← Protocol → Python source string (~280 LOC)
│   ├── catalog\            ← v1.1 catalog system
│   │   ├── catalog.py      ← SQL queries (~210 LOC)
│   │   ├── indexer.py      ← Walk install + write rows (~210 LOC)
│   │   ├── inference.py    ← Category rules (157 LOC)
│   │   ├── xcmp.py         ← .xcmp / .xwsp parser (597 LOC)
│   │   ├── xlqc.py         ← .xlqc liquid-class loader (Phase C.2, ~60 LOC)
│   │   ├── database.py     ← Legacy recipe database and lookup helpers
│   │   ├── fc_install.py   ← Bridge to fluentcontrol_core (78 LOC)
│   │   └── install_index.db   ← Built artifact (gitignored)
│   ├── _assets\            ← Templates / reference / config
│   └── simulator\          ← The IR walker
│       ├── walk.py         ← Simulator class (346 LOC)
│       ├── snapshots.py    ← Snapshot dataclass (53 LOC)
│       └── invariants.py   ← Exception hierarchy (40 LOC)
└── tests\                  ← 61 tests across 10 files
    ├── fixtures\
    │   └── simple_transfer_fluentdsl_reference.py   ← Parity reference (fluentdsl flat-fn)
    ├── test_catalog_index_build.py                  ← v1.1: index build against real install
    ├── test_inference_known_samples.py              ← v1.1: 23 parametrized inference cases
    ├── test_physical_invariants.py                  ← v1: 7 simulator invariants
    ├── test_plate_construction_from_catalog.py      ← v1.1: catalog-driven labware
    ├── test_simple_transfer_parity.py               ← v1: byte-equal XML parity
    ├── test_snapshot_introspection.py               ← v1: layered well + magnet stacking
    ├── test_worktable_from_workspace.py             ← v1.1: from_workspace + InvalidSlotError
    ├── test_examples.py                             ← Phase A: pinning tests for the 4 example protocols
    ├── test_xscr_roundtrip.py                       ← Phase B: .xscr → .py → .xscr byte-equal parity
    ├── test_auto_rebuild.py                         ← Phase C.1: fingerprint drift triggers rebuild
    └── test_liquid_class_index.py                   ← Phase C.2: 38 .xlqc rows + GUID lookup
```

## Conventions

### Coding style

- Python ≥ 3.11. Module-level `from __future__ import annotations` everywhere.
- Pydantic v2 for IR schema; dataclasses for everything else.
- Public types are `frozen=True` when they represent values (Reagent,
  XcmpComponent, CatalogEntry).
- Module imports at top; lazy imports inside functions only when needed to
  break a circular dependency (e.g. `Labware.is_magnetized` lazily imports
  `MagnetRack` to avoid a circular import).

### Naming

- Internal helpers prefixed with `_`. `_warn_offline_once`, `_walk`,
  `_aspirate_one`.
- IR step types end in `Step` (`AddLabwareStep`, `AspirateStep`).
- Exception types end in `Error` (`MissingTipsError`).
- Behavioral classes are nouns (`Plate`, `Trough`, `MagnetRack`).
- Convenience subclasses fix shape: `Plate96`, `Plate384`, `MCA100Box`.

### Testing

- `pytest`. Tests use `PYTHONPATH=.`-style imports for in-place runs:
  `cd fluentvibe && PYTHONPATH=. python -m pytest tests/ -v`
- Tests that touch the real FluentControl install are guarded with
  `@pytest.mark.skipif(not _install_present(), ...)` so CI runs work
  unmodified.
- Tests should never rebuild the catalog index (the fixture is the
  install-driven build inside `ensure_index`); they query whatever is
  already there.

## Running tests

```
python -m pytest tests/ -v
```

Expected output (truncated):

```
tests\test_catalog_index_build.py ...                           [  6%]
tests\test_inference_known_samples.py .......................   [ 55%]
tests\test_physical_invariants.py .......                       [ 70%]
tests\test_plate_construction_from_catalog.py ......            [ 82%]
tests\test_simple_transfer_parity.py ..                         [ 87%]
tests\test_snapshot_introspection.py ..                         [ 91%]
tests\test_worktable_from_workspace.py ....                     [100%]

============================== 61 passed ==============================
```

The parity test (`test_simple_transfer_parity_xml`) requires access to the
earlier fluentdsl implementation; otherwise it skips. The catalog tests skip
when the install isn't reachable.

## Test inventory

| File | Tests | What it proves |
|---|---|---|
| `test_simple_transfer_parity.py` | 2 | fluentvibe's OO-authored simple_transfer renders to identical XML as fluentdsl's flat-function version (modulo random GUID); IR shape matches expectations. |
| `test_snapshot_introspection.py` | 2 | Layered well contents flow source → tip → dest; magnetized state toggles correctly with gripper stacking. |
| `test_physical_invariants.py` | 7 | Each invariant raises (occupied slot, missing adapter, missing tips, insufficient volume, overdraw, pinned aspirate on magnet). |
| `test_catalog_index_build.py` | 3 | Index builds against real install with expected category counts and known catalog entries. |
| `test_inference_known_samples.py` | 23 | Category inference correctly classifies a curated list of catalog names spanning every category. |
| `test_plate_construction_from_catalog.py` | 6 | Catalog-driven `Plate96` / `Trough100mL` / `MCA100Box` populate from real .xcmp data; offline behavior; error paths. |
| `test_worktable_from_workspace.py` | 4 | `from_workspace` registers valid slots; `InvalidSlotError` fires correctly; valid slots accepted. |

## Adding a new labware family

1. Subclass `Labware` in `fluentvibe/labware/<your_module>.py`.
2. Set `category = "..."`, `taxonomic_grid = (rows, cols)` if applicable,
   `offline_max_well_volume_ul = ...`.
3. Override `_post_populate(...)` if your family has special state to set
   up after the wells/dimensions are populated (see `Trough._post_populate`
   for an example that collapses parsed wells into a single pool).
4. Add the class to `fluentvibe/labware/__init__.py`'s exports + `CATEGORY_TO_CLASS`.
5. Re-export from `fluentvibe/__init__.py` if it should be in the top-level
   public API.
6. Update inference rules in `fluentvibe/catalog/inference.py` if your
   category requires new logic.

## Adding a new IR step type

The IR descends from the earlier project-owned fluentdsl implementation. If
you need a new step type, add it locally and update every consumer:

1. Add the step class to `fluentvibe/ir/schema.py` (Pydantic model with
   `step_type: Literal[StepType.X]`).
2. Add the StepType enum value.
3. Add it to the `Step` discriminated union.
4. Add to `STEP_TO_COMMAND_ID` if the renderer needs a command-ID mapping.
5. Wire a handler in `fluentvibe/simulator/walk.py:_dispatch`.
6. Add an authoring method on the appropriate object (Worktable, head,
   gripper).

The renderer is the part most likely to need updates — extending it means
editing `fluentvibe/compiler/renderer.py` and possibly
`fluentvibe/_assets/reference/commands.yaml`.

## Known limits / v1.2 candidates

These are visible from the v1.1 codebase; documenting so contributors
know what's already been thought through.

### Authoring

- **`Plate96('Source')` without `catalog=`** raises when the catalog index
  is built. This is intentional (refuse-to-guess) but verbose for the
  most-common cases. A future "default catalog per class" mechanism could
  let `Plate96(...)` resolve to a sensible default catalog name when the
  index has multiple matches — opt-in only, never a silent guess.
- **`labware_by_label`** is needed when you don't keep a Python reference
  to placed labware. The `examples/simple_transfer.py` example uses it for
  the destination plate; cleaner authoring captures all `place()` returns.
- **No FluentControl variable references** in fluentvibe authoring. The
  parity test's reference protocol omits `var("PlateType", ...)` for that
  reason. v1.2: add `wt.declare_fc_variable(...)` returning a token that's
  acceptable as `labware_type` in IR steps.

### Simulator

- **`CannotAspirateError` is reserved but currently unused.** Pinned-only
  aspirates on magnetized plates raise `InsufficientVolumeError` because
  the layered loop runs out of skippable layers. v1.2: emit
  `CannotAspirateError` explicitly when the targeted-layer's reagent has
  `pinned_when_magnetized=True` and the plate is magnetized, so the error
  message points at the actual cause.
- **Subroutine descent.** External `.smt` subroutines are not parsed; the
  simulator passes through them as opaque steps. v1.2: descend into
  subroutines whose body is available as IR (locally authored).
- **Snapshot deepcopy cost** is linear per step in slot map + tip count.
  Long protocols with many large `TubeRack`s can produce snapshot lists
  >100 MB. v1.2: structural sharing or copy-on-write snapshots.

### Catalog

- **Auto-rebuild on install drift** — *shipped (Phase C.1).*
  `ensure_index()` consults `fingerprint_matches()` on every import and
  rebuilds when the on-disk install differs from the indexed snapshot.
  Opt out via `FLUENTVIBE_NO_AUTO_REBUILD=1`.
- **Liquid-class catalog** — *shipped (Phase C.2).* Walks
  `SystemSpecific/LiquidClasses/*.xlqc` and populates a `liquid_classes`
  table. Renderer resolves the liquid-class GUID via SQL by name. The
  legacy `_assets/reference/liquid_classes.yaml` (which the renderer
  never actually loaded) was deleted.
- **Multiple FluentControl installs on one machine** isn't handled —
  the index only stores one install_path row. v1.2: keyed index (one
  row set per install_path).
- **Category inference** has no override file. Per the plan, the rules
  use FunctionalGroup + structure + name fallback only. If a real install
  contains a name the rules mis-classify, the only fix today is a code
  change to `inference.py`. v1.2 candidate: optional
  `category_overrides.toml` for user corrections.
- **Connector graph** (`.xcon` files: 14k+ in a typical install) is not
  parsed. Lookups currently rely on the workspace's site/labware
  references being self-consistent.

### Compile path

- **Random `WorkspaceDelta` GUID** in the renderer breaks byte-equal
  parity by 36 bytes per render. The parity test normalizes it. A future
  renderer option to fix the GUID for reproducibility would simplify
  cross-tool diffs.

### Hardware coverage

- **MCA96 head only.** `fluentvibe/heads/` has `mca96.py` and an empty
  `__init__.py`. The IR schema covers MCA384, FCA, and LiHa step types,
  and the renderer handles them — but fluentvibe doesn't expose authoring
  methods for them yet. v1.2: add `MCA384Head`, `FCAHead`, `LiHaHead`
  classes with the same emit-IR pattern.
- **Partial-column tip pickup** isn't modelled. `TipBox.is_full` is a
  bool, not a per-column / per-tip availability. The MCA simulator
  assumes full pickup of 96 tips. Partial pickup is a v2 refinement.

### Tests / CI

- The parity test depends on an out-of-tree copy of the earlier fluentdsl
  implementation. CI should either install that dependency explicitly or pin
  the expected XML as a fixture with the GUID-normalized form.
- `.gitignore` should keep machine-local catalog indexes, generated `.xscr`
  files, build output, agent state, and scratch directories out of Git.

## Quick recipes

### "How do I find the catalog name for a 96-deep-well plate?"

```
fluentvibe catalog find "deep" --category plate
```

### "How do I check what fluentvibe loaded from a specific .xcmp?"

```python
from fluentvibe.catalog import resolve_by_name, load_xcmp
entry = resolve_by_name("96 Well Flat")
comp = load_xcmp(entry.file_path)
print(comp.dim_mm, comp.functional_group, comp.pipettable.cavity.volume_ul)
```

### "How do I rebuild the catalog after a FluentControl update?"

```
fluentvibe catalog refresh
```

### "How do I test offline (no FluentControl install)?"

Set `FLUENTVIBE_FC_INSTALL` to a directory that doesn't exist (or just rename
your install). On next import, `ensure_index` will be a no-op, and labware
classes will use the offline-synthesis path. A `CatalogIndexMissing`
warning fires once per process the first time a labware is constructed.

### "How do I see what state was true at a given step?"

```python
wt.simulate()
for snap in wt.snapshots:
    if type(snap.step).__name__ == "DispenseStep":
        plate = snap.labware("DestPlate")
        print(f"step {snap.step_index}: A1 = {plate.well('A1').layers}")
```
