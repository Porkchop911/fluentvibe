# Capability matrix

This matrix records the implementation state visible in the repository. It is
not an instrument-compatibility certification. “Supported” means that a public
or documented path exists and has automated coverage; hardware-backed behavior
may still require local validation.

Status terms:

- **Supported** — implemented through the relevant product path with automated
  tests.
- **Partial** — useful implementation exists, but one or more important paths
  or real-world cases remain incomplete.
- **Opaque** — preserved or reported without full semantic modeling.
- **External** — depends on a local FluentControl installation, model endpoint,
  shell, or proprietary bridge.

## Protocol construction

| Capability | Status | Evidence and boundary |
|---|---|---|
| Worktable creation and workspace binding | Supported / External | Offline worktables work without an install; `from_workspace` requires catalog data |
| Labware placement, removal, and lookup | Supported | Slot occupancy, valid-slot, and stacking behavior are tested |
| Reagents and layered well contents | Supported | Simulator tracks transfers and bead-related layers |
| Variables and simulation values | Supported | Runtime expressions require explicit simulation values when they cannot be resolved |
| Script groups and nested groups | Supported | Represented in IR and handled by compiler and simulator |
| Loops and conditionals | Supported | Authored and simulated; complex decompiled alternates need broader corpus validation |
| Timers, waits, prompts, comments | Supported | Modeled as control/runtime steps; most have no physical simulator effect |
| Generic/raw XML steps | Opaque | Preserved and counted; strict coverage policies can reject them |

## Liquid handling and devices

| Capability | Authoring | Simulation | Compile/decompile | Notes |
|---|---|---|---|---|
| MCA96 full-plate handling | Supported | Supported | Supported | Adapter, tips, aspirate, dispense, and mix paths are covered |
| MCA96 partial-column tips | Supported | Supported | Supported | Includes physical peel-edge constraints and round-trip tests |
| LiHa basic handling | Supported | Supported | Supported | Tip get/drop, aspirate, dispense, mix, and empty-tip helpers exist |
| MCA384 | Partial | Supported IR handlers | Partial | IR and simulator paths exist; no equivalent public head facade is exported |
| RGA/gripper transfer | Supported | Supported | Supported | Includes grip and renderer regression coverage |
| CGA finger operations | Partial | Supported IR handlers | Partial | IR support exists without a broad public authoring surface |
| ODTC and Inheco helpers | Partial / External | Limited semantic effect | Legacy-driver macro path | Requires real FluentControl/device validation |
| Magnetic bead semantics | Partial | Domain model implemented | N/A | Physical model exists; generated cleanup correctness remains an active reliability issue |

## Artifact paths

| Capability | Status | Boundary |
|---|---|---|
| Python to XSCR | Supported / External | Rendering works offline; final checksum behavior depends on the local bridge/configuration |
| XSCR to Python | Partial | Known commands are reconstructed; unknown commands become explicit unsupported/opaque output |
| XSCR round trip | Partial | Covered for curated workflows; broader real-world command corpora are needed |
| Simulation reports | Supported | Includes coverage classification, state summaries, warnings, and unsupported command IDs |
| GWL worklists | Supported | Construction, import, load, execution IR, and format validation exist |
| Datastore deployment | External | Refuses unsafe conditions, rewrites identifiers, and verifies checksum through the local bridge |

## Catalog and workspace integration

| Capability | Status | Boundary |
|---|---|---|
| Component and workspace indexing | Supported / External | Requires readable FluentControl installation data |
| Labware category inference | Supported with heuristic risk | Known samples are tested; ambiguous installations may require code changes |
| Liquid-class indexing | Supported / External | Content depends on the selected installation |
| Install drift rebuild | Supported | Can be disabled for deterministic offline and CI runs |
| Multiple simultaneous installs | Partial | Index state is effectively centered on one active installation |
| Workspace profiles and reusable modules | Supported | Profile-bound deck, whitelist, and approved helper-module paths exist |

## User-facing tools

| Surface | Status | Principal capabilities |
|---|---|---|
| CLI | Supported | Compile, simulate, check, complete, edit, LSP, decompile, author, chat, trace, workspace app, deploy, and catalog |
| VS Code extension | Partial | Diagnostics, fixes, completion, hover/signature help, and inline edit; installation testing remains Windows-focused |
| Workspace web app | Partial | Setup, profiles, jobs, authoring, simulation, compilation, decompilation, catalog, and deployment; frontend automation is limited |
| Prompt authoring | Partial / External | Grounded skills, tools, repair, tracing, attachments, and evaluation exist; output quality remains model-dependent |
| FluentControl shell validation | External | Opt-in and dependent on a running local FluentControl environment |

## High-priority gaps

1. Deterministic completeness checks for complex generated workflows.
2. Broader real XSCR corpus coverage for decompilation and opaque commands.
3. Explicit compatibility testing or an upper Python-version bound.
4. Multiple-install catalog handling and user overrides for inference.
5. Full UI workflow tests, progress reporting, recovery, and accessibility.
6. Public authoring parity for IR capabilities that currently lack a facade.
