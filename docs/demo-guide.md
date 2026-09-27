# fluentvibe demo guide

What works end to end, how to start it, and what to leave out of a live demo.

## Setup (once)

1. Model server: the 27B on vLLM (`http://127.0.0.1:18020/v1/chat/completions`, model
   `qwen3.8-27b`, key from `~/qwen-serving/api_key.txt` in WSL). FluentControl open,
   maximised on the left monitor, the 1080 workspace loaded.
2. VS Code: extension `fluentvibe` 0.5.0 (`editors/vscode/fluentvibe-0.5.0.vsix`); open
   `D:\python\fluentvibe` as the workspace folder; *Reload Window* after installing.
3. Settings (search "fluentvibe"): `model.endpoint`, `model.name`, `model.apiKey`;
   `model.reasoningEffort` = `xhigh`; `profile` = `build/workspaces/sat_1080_test`.
4. Web app (optional): start it with the same variables set
   (`FLUENTVIBE_LM_ENDPOINT`, `FLUENTVIBE_LM_MODEL`, `FLUENTVIBE_LM_API_KEY`,
   `FLUENTVIBE_LM_REASONING_EFFORT=xhigh`): `python -m fluentvibe.cli workspace-app --port 8765`.

## Flows

| Flow | Where | Time | What the viewer sees |
|---|---|---|---|
| Document → protocol | VS Code *fluentvibe: Generate protocol from document* → **Fast**; or the web app's Author tab | 3-5 min | live progress, the spec, the Python, **"your instructions: n/n verified"** with the checklist, FluentControl result |
| Open questions | same | +1 model call | a box asks e.g. "beads 4,800 µl needed, kit has 2,000 µl" instead of guessing |
| Replay | *fluentvibe: Show protocol replay* (editor title button); web app *Show replay* | seconds | the deck and every well after each step, play / slider |
| Break an instruction | edit e.g. `head="fca"` → `"mca"` in a protocol with a checklist | save | red squiggle on that line: "Instruction not met" |
| FluentControl check | *fluentvibe: Open in FluentControl* (Ctrl+Alt+F) | ~25 s | FluentControl loads the script; InfoPad errors become squiggles |
| Edits in FluentControl | *fluentvibe: Pull FluentControl edits* | seconds | which Python variable a FluentControl edit changed |
| Opentrons → Tecan | *fluentvibe: Convert Opentrons protocol* | ~10 s | an Opentrons .py becomes a checked FluentControl protocol, no model |
| Instructions for hand-written code | *fluentvibe: Set instructions for this protocol* | 15-60 s | a checklist beside the file, checked on every save |
| Change one volume | edit `S2_..._UL = 36` → `30` in a generated protocol | save | nothing breaks: removals and mixes follow |

Suggested 90 s cut: Generate (time-lapse) → checklist 10/10 → Replay, Play → break
an instruction (squiggle) → Opentrons → Tecan.

## Leave out of a live demo

- **Full Python** generation: 15-25 min; record it, do not wait for it live.
- Protocols whose steps need loops over data, worklists or devices on the fast path:
  such a step becomes model-written code or stays a marked TODO (reported, never hidden).
- FluentControl check timing: ~25 s is FluentControl loading the script.
- The FluentControl-level volume dependencies: edits of a volume *in FluentControl*
  do not update dependent volumes (they do in the Python).

## Numbers (for the post, from the benchmarks)

- AMPure prompt (27B, xhigh): 2.8-3.9 min end to end, FluentControl clean,
  10/10 instructions verified.
- 4 vendor protocols (AMPure XP, BCA, CellTiter-Glo, Dynabeads), fast path: 21/22
  FluentControl clean.
- Opentrons corpus: 136 of 149 deck-fitting protocols convert complete and pass
  every check (no step left as TODO).
