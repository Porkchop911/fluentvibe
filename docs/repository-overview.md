# Repository overview

This document is the current high-level map of `fluentvibe`. It describes the
code as it exists in the repository, while the subsystem documents provide the
detailed API and operational guidance.

## Purpose and maturity

`fluentvibe` is a Python toolkit for authoring, inspecting, simulating,
compiling, decompiling, and deploying Tecan FluentControl protocols. It is a
technical-preview codebase, not a production or instrument-safety-certified
release. Generated methods still require human review and FluentControl or
instrument validation.

The package version is `0.1.0`. Source is available for technical review, but
no open-source license has been granted.

## System map

```text
                                    +------------------------+
Natural-language request ----------> LLM authoring pipeline |
                                    +-----------+------------+
                                                |
Python source / public API ----------------------+
        |
        v
+---------------------+       +-----------------------------+
| Worktable + domain  |------>| Typed protocol IR           |
| objects             |       | groups, steps, variables    |
+---------------------+       +------+----------------------+
                                     |
                      +--------------+---------------+
                      |                              |
                      v                              v
             +------------------+          +------------------+
             | Simulator        |          | XSCR compiler    |
             | snapshots, state |          | FluentControl XML|
             | and invariants   |          +---------+--------+
             +------------------+                    |
                                                       v
                                              validate / deploy

XSCR XML ----------------> decompiler ----------------> Python source

FluentControl install XML <------ catalog index ------> profiles / grounding
```

The protocol IR is the central boundary. Authoring operations emit IR; the
simulator and compiler consume it independently. The decompiler reconstructs IR
from supported XSCR commands before emitting Python.

## Subsystems

| Subsystem | Primary paths | Responsibility |
|---|---|---|
| Domain model | `worktable.py`, `labware/`, `heads/`, `gripper.py`, `reagent.py` | User-facing protocol construction and IR emission |
| Protocol IR | `ir/schema.py`, `ir/source_pos.py` | Typed steps, groups, variables, and source locations |
| Simulator | `simulator/` | Reconstruct deck, liquid, tip, variable, and device-relevant state; report invariant failures and opaque steps |
| Compiler | `compiler/renderer.py`, `_assets/` | Render protocol IR into FluentControl XSCR XML |
| Decompiler | `decompiler/` | Parse supported XSCR into IR and generate reviewable Python |
| Catalog | `catalog/` | Index a local FluentControl install and resolve workspaces, components, sites, grip modes, and liquid classes |
| Authoring | `authoring/` | Prompt workflows, grounding, tools, validation, repair, tracing, attachments, and evaluation |
| Copilot and LSP | `copilot/`, `lsp/`, `editors/vscode/` | Diagnostics, completion, fixes, explanations, inline editing, and editor integration |
| Workspace app | `workspace_app/` | Local browser workflow for setup, profiles, authoring, validation, compilation, decompilation, catalog access, and deployment |
| Deployment | `deployer.py` | FluentControl datastore deployment with identity rewriting and checksum verification |
| Worklists | `worklists.py` | GWL construction, conversion, import, load, and execution support |
| User entry points | `cli.py`, `__main__.py` | Command-line access to the principal workflows |

## Principal user journeys

### Author and validate

```text
Python or natural language
  -> build_worktable()
  -> strict simulation
  -> diagnostics and repair
  -> compile XSCR
  -> FluentControl review
```

### Recover an existing method

```text
XSCR
  -> parse supported commands
  -> preserve/report opaque commands
  -> emit Python
  -> simulate and review
  -> optionally recompile
```

### Configure a real workspace

```text
FluentControl install
  -> catalog index
  -> workspace/profile selection
  -> deck and labware constraints
  -> grounded authoring and validation
```

## External dependencies and boundaries

- Python 3.11 or newer is declared; CI currently exercises Python 3.11 and
  3.12.
- Catalog-backed behavior depends on a locally licensed FluentControl install.
- Checksum rewriting and deployment depend on the local
  `fluentcontrol_core` bridge.
- Prompt authoring uses an OpenAI-compatible model endpoint and is not
  deterministic.
- Live model, FluentControl shell, and installation-backed checks are separated
  from the default offline test suite.

## Reliability model

The repository distinguishes four kinds of confidence:

1. **Structural validity**: Python builds and the IR validates.
2. **Simulation validity**: modeled physical invariants pass.
3. **Compilation validity**: an XSCR artifact is rendered and checksummed when
   the required local bridge is present.
4. **Operational validity**: a human reviews the generated method and validates
   it in FluentControl or on appropriate hardware.

Passing an earlier level does not imply a later one. In particular, simulation
cannot validate opaque commands or physical behavior it does not model.

## Current engineering priorities

1. Keep the offline baseline reproducibly green on every supported Python
   version.
2. Make unsupported and inferred behavior explicit in every user surface.
3. Move safety-critical authoring checks out of model prompts and into
   deterministic validation.
4. Reduce coupling in the largest authoring, workspace, renderer, and simulator
   modules.
5. Complete features as end-to-end slices spanning authoring, IR, simulation,
   compilation, decompilation, diagnostics, UI, tests, and documentation.

See [capability-matrix.md](capability-matrix.md) for the current feature map and
[reliability-scorecard.md](reliability-scorecard.md) for the latest validation
baseline.
