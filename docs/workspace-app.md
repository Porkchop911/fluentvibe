# Workspace app — the local setup & authoring web UI

`fluentvibe/workspace_app/`

The workspace app is a local, single-page web UI for workspace setup, browsing
existing FluentControl protocols, discussing a selected protocol with a local
model, and exploring catalog objects. It also exposes authoring, simulation,
compilation, decompilation, and FluentControl actions.

It is a thin front-end. Every tab calls the same Python entry points the
[CLI](cli.md) uses (`fluentvibe.authoring`, the simulator, the compiler, the
decompiler, the catalog), via a small JSON API. Nothing instrument-facing
happens in the browser; the server process does the work on `localhost`.

## Launching

```bash
fluentvibe workspace-app                      # http://127.0.0.1:8765
fluentvibe workspace-app --host 0.0.0.0 --port 9000
```

The command (registered in `cli.py`, handler `_cmd_workspace_app`) starts a
stdlib `ThreadingHTTPServer` on `127.0.0.1:8765` by default and prints the URL.
Open it in a browser; `Ctrl+C` in the terminal stops it.

There is no build step and no JavaScript bundler — the entire front-end is the
single, monolithic `fluentvibe/workspace_app/static/index.html` (inline
`<style>` + markup + inline `<script>`). The server (`server.py`) serves that
file and exposes the JSON API; the logic lives in `service.py`.

> **Bind address.** The default `127.0.0.1` is loopback-only. `--host 0.0.0.0`
> exposes the app — and the local FluentControl catalog/datastore operations
> behind it — to your network. There is no authentication; only do this on a
> trusted network.

## What you need

- **A local FluentControl install** for the catalog-backed features (Setup,
  Catalog, profile save, FC deploy). The app reads the same catalog index as the
  CLI; if the index is missing, run `fluentvibe catalog refresh` first (see
  [catalog.md](catalog.md)). Without an install, catalog-dependent tabs are
  empty but the app still loads.
- **A running LM Studio endpoint** for the Author tab's authoring loop (same
  requirement as `fluentvibe chat`; see [authoring.md](authoring.md)). Creating
  a session is local, but sending a message calls the model.
- **A running FluentControl UI** only for the FluentControl tab's shell
  validation / deploy actions (see [deployment.md](deployment.md)).

## The tabs

| Tab | Purpose |
|---|---|
| **Workspace setup** | Choose a workspace + instrument configuration, see the deck map, assign common labware to valid slots, pick a liquid class, and **save a profile**. The initial worktable is previewed on load. |
| **Protocol library** | Browse `.xscr` scripts in the local FluentControl `UserSpecific` directory, inspect a step outline, decompile the selected script to Python, and discuss it through an explicitly configured loopback model endpoint. |
| **Author new** | A chat UI over the LM authoring loop (`PromptAuthoringSession`), scoped to the selected profile. |
| **Code Lab** | Paste/edit fluentvibe Python and run **simulate** or **compile** against it. |
| **Decompile file** | Turn a `.xscr` on disk back into a fluentvibe Python module. |
| **Object explorer** | Search indexed labware, carriers, workspaces, and liquid classes. Object details show recorded properties and a clearly labeled purpose hint inferred from classification. |
| **FluentControl** | Validate / deploy a compiled `.xscr` against a running FluentControl (shell-patch path). |

### Setup → a saved profile

The Setup tab is the front door. You select a workspace (from the catalog
index) and an instrument configuration, the deck map renders the workspace's
slots, and you place common labware into valid slots and choose a default liquid
class. **Save profile** then writes a self-contained profile directory under
`build/workspaces/<name>/`:

| File | What it is |
|---|---|
| `workspace_profile.json` | The profile of record (schema `PROFILE_SCHEMA_VERSION`): configuration, workspace + `workspace_source` checksum, deck/common-labware selections, liquid class. |
| `current_worktable.*` | A worktable snapshot the authoring grounding layer loads. |
| `generation.profile.yaml` | Data-driven `deck_rules` for this workspace (trough family, FC guard flags) consumed by generation. |
| a `--lab-scope skills` **deck skill** | An always-on deck skill bound to *this* workspace's GUID/name, so authoring targets this deck rather than the shipped default. |
| `README.md` | A human summary of the saved profile. |

The save path validates that every placed item sits in a **valid slot** and that
the workspace file hasn't changed since you loaded it (a stale
`workspace_source.sha256` is rejected). Saved profiles are re-listable and
re-loadable for editing.

> The deck map's geometry is computed in JavaScript (absolute slot positions and
> sizes from the workspace arrangement). Treat it as load-bearing; see the
> "Front-end shape" note below.

### Author → the authoring chat

The Author tab is a single-window chat over `PromptAuthoringSession`:

- Pick a **profile**, **lab scope** (`skills` default / `enforce` / `cheatsheet`
  / `off`), **retry budget**, and **model server**, then **Start session**. The
  default uses the existing LM Studio endpoint; **Strata · local test instance**
  uses `http://127.0.0.1:8080/v1/chat/completions` and its advertised
  `qwen3.8-flash-next-q2_0` model ID. The Model ID field can override the
  selected server's default. Starting a session shows the effective endpoint
  and model. Changing servers requires a new session.
- Selecting a profile binds the session to that workspace (name + GUID) and its
  deck skill, so authoring is grounded in the deck you set up.
- Type a request and press **Enter** to send (Shift+Enter for a newline). The
  log shows your message, a pending indicator while the model works, then the
  result — clarification questions, an approval request, a success summary, or a
  failure — plus the tool calls it made.
- Generated / best-draft code and the raw structured result are available in the
  collapsible **Generated code & raw result** pane; on success the compiled
  `.xscr` path is handed to the FluentControl tab.

This is the same loop as `fluentvibe chat`, with the same `--lab-scope` semantics
described in [cli.md](cli.md). The selected server must be running before a
message is sent.

### Protocol library → inspect and discuss

The library reads `C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\*.xscr`
without changing those files. Its selection API accepts only a script basename
from that directory. The step outline comes from the existing decompiler; any
generic/unsupported command types are marked. **Decompile to Python** writes a
draft under `build/workbench/decompiled/` and makes it available in Code lab.
The draft requires review before compilation or use.

Protocol discussion sends the selected protocol's decompiled Python context to
the selected local model server. **Configured local model** requires
`FLUENTVIBE_LM_ENDPOINT` to explicitly point to a loopback `http(s)://localhost`,
`127.0.0.1`, or `[::1]` chat-completions URL; a nonlocal or missing endpoint
leaves that choice disabled. **Strata · local test instance** targets the fixed
loopback endpoint above. The UI shows the destination and model. Replies explain
source, not physical or chemical validity. Large sources are excerpted and the
UI flags this limit.

## Under the hood — the JSON API

`server.py` exposes a small JSON API consumed by the inline script. Read paths
are synchronous `GET`s; anything that can be slow or contacts the model /
instrument runs as a **background job**.

**Synchronous reads (`GET`)** — `/api/workspaces`, `/api/configurations`,
`/api/configuration?guid=`, `/api/workspace?name=|guid=`,
`/api/labware?query=&category=&limit=`, `/api/liquid-classes`,
`/api/catalog-info`, `/api/protocols`, `/api/protocol?id=`,
`/api/protocol-chat-config`, `/api/objects`, `/api/object?guid=&kind=`,
`/api/profiles`, `/api/profile?name=`,
`/api/suggest-roles`, and `/api/job?id=`.

**Writes (`POST`)** — `/api/save-profile` (the Setup save), and
`/api/jobs/<kind>` to enqueue a job.

**The job queue.** `POST /api/jobs/<kind>` returns a job id; the UI polls
`GET /api/job?id=<id>` (~750 ms) until the job reports `success` or `failure`.
Job kinds (`service._job_handlers`):

| Kind | Backed by | Tab |
|---|---|---|
| `authoring-session` | `PromptAuthoringSession` (create; local, no model call) | Author |
| `authoring-send` | `session.send(message)` (calls the model) | Author |
| `simulate-source` | simulate a Python draft | Code Lab |
| `compile-source` | compile a Python draft to `.xscr` | Code Lab |
| `decompile-xscr` | `.xscr` → fluentvibe Python | Decompile |
| `protocol-chat` | Read-only question about the selected datastore script through a loopback model | Protocol library |
| `catalog-refresh` | rebuild the catalog index | Catalog |
| `fc-validate` | shell-patch validation against a running FC | FluentControl |
| `deploy-xscr` | deploy a compiled `.xscr` | FluentControl |

An unknown job kind raises `ValueError("Unknown job kind …")`. Authoring
sessions live in an in-memory `_SESSIONS` dict for the life of the server
process — they are not persisted across restarts.

## Front-end shape (for maintainers)

The front-end is intentionally **one monolithic file**,
`static/index.html`. The deck map (`renderDeck` / `deckGeometry` / `rectStyle`
and the `.deck-*` / `.slot` / `.grid-*` / `.segment-*` CSS) computes slot
positions as **inline** absolute coordinates; CSS only supplies colours and
borders. Keep it monolithic and scope edits to a single tab — splitting the file
into separate JS/CSS assets has previously broken the deck rendering. The
`pyproject.toml` package-data glob ships `workspace_app/static/*.html`.

## Tests

`tests/test_workspace_app.py` exercises `service.py` directly — listing
workspaces/configurations, the workspace detail (slots, deck coordinates,
sources), the save → list → load profile round-trip (including the emitted
`--lab-scope skills` deck skill and `generation.profile.yaml` deck rules),
profile validation (invalid slot, stale workspace source), and the job queue
(`authoring-session`, `simulate-source`, `decompile-xscr`, unknown kind).
Catalog-backed tests skip when no index is present.

## Related docs

- [CLI](cli.md) — `fluentvibe workspace-app` and the `chat` authoring surface
- [Authoring API](authoring.md) — the LM loop behind the Author tab
- [Catalog system](catalog.md) — the index the Setup/Catalog tabs read
- [Simulator](simulator.md) / [Compile path](compile-path.md) — the Code Lab actions
- [Decompiler](decompile.md) — the Decompile tab
- [Deployment](deployment.md) — the FluentControl shell-patch / deploy path
