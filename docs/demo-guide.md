# fluentvibe demo guide

What works end to end, how to start it, and what to leave out of a live demo.

## Setup (once)

1. Model server: the 27B on vLLM (`http://127.0.0.1:18020/v1/chat/completions`, model
   `qwen3.8-27b`). Endpoint, model, key and reasoning effort are Windows user environment
   variables (`FLUENTVIBE_LM_*`); open a new terminal after changing them.
2. FluentControl open, maximised on the left monitor, workspace **1080_DEV_TABLE**.
3. Web app: `python -m fluentvibe.cli workspace-app --port 8765`. Only one instance: stop an
   old one first (two apps on one port answer unpredictably).
4. VS Code (optional): extension `fluentvibe` 0.5.1; open `D:\python\fluentvibe`; the model
   settings are in the user settings; `profile` = `build/workspaces/1080_Dev`.

## The Author page

One chat, a **Fast | Full Python** toggle, one **Stop**.

- **Fast** (5-10 min): attach the protocol (PDF/text) and, for normalisation, a sample sheet
  (CSV: well or sample number, ng/µl); write the request; **Ctrl+Enter** sends (Enter is a
  new line). After ~30-60 s the model says what it understood and asks at most 5 questions;
  answer in the chat ("ok" keeps everything). The stages then run live (read the document,
  build and simulate, check your instructions, check in FluentControl) with the model's
  thinking shown. The result names the folder, `draft.py` and the `.xscr`, lists what the
  model assumed, and offers **Check in FluentControl** and **Show replay**.
- **Full Python** (15-60 min, many model turns): the model writes the Python itself in the
  same chat. Record it; do not wait for it live.
- The same document with the same request and answers reuses the finished read (seconds).

## Flows

| Flow | Where | Time | What the viewer sees |
|---|---|---|---|
| Document → protocol | Author, Fast (or VS Code *Generate protocol from document* → Fast) | 5-10 min | understanding + questions after ~30-60 s, live stages, "your instructions: n/n verified", FluentControl result |
| Repetitions as loops | same | – | "3 washes" is one FluentControl loop with a count variable (`REPEAT_WASH_TIMES`), not 3 copies |
| Per-sample normalisation | same, with a sample sheet | – | per-well water and DNA volumes (FCA); concentrated samples pre-diluted as in the ONT table |
| Change one volume | `wt.volume("S3_AXP_UL", 36)` → `30` in the Python, or `S3_AXP_UL` in FluentControl's Variables group | save | removals and mixes follow (Set Variable formulas) |
| Replay | result → Show replay (VS Code: editor title button) | seconds | the deck and every well after each step |
| FluentControl check | FluentControl tab: recent protocols or a file; VS Code Ctrl+Alt+F | ~30 s | InfoPad result (squiggles in VS Code) |
| Break an instruction | VS Code: `head="fca"` → `"mca"` in a generated protocol | save | red squiggle "Instruction not met" |
| Edits in FluentControl | VS Code: Pull FluentControl edits | seconds | which Python variable a FluentControl edit changed |
| Opentrons → Tecan | VS Code: Convert Opentrons protocol | ~10 s | a checked FluentControl protocol, no model |

Suggested 90 s cut: Fast on the AMPure IFU (time-lapse) → the questions → checklist n/n →
a loop and the Variables group in FluentControl → Replay → Opentrons → Tecan.

## Leave out of a live demo

- **Full Python** generation live (15-60 min).
- After pooling (ONT), the tube work (AMPure clean-up of the pool, adapter) is one operator
  hand-off; pooling straight into an Eppendorf tube on the deck is in progress.
- Claiming a FluentControl *run* recomputes dependent volumes: the InfoPad parses the Set
  Variable formulas, but no instrument run has confirmed the values yet.
- Worklists in Fast: steps that need a CSV/GWL are not generated from documents yet.

## Numbers (from the benchmarks)

- AMPure IFU on 1080_Dev (27B, xhigh): 4-7 min end to end, FluentControl clean; 7/7
  instructions verified with a concrete request.
- Dynabeads datasheet: understanding after ~33 s; the document read ~5 min at xhigh
  (2-2.5x faster at medium with the same results in the benchmark).
- Opentrons corpus: 141 of 149 deck-fitting protocols pass every check; 140 with no TODO.
