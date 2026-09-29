# pi test: writing the protocol in one go vs. phase by phase

**Question:** does pi write better Fluent protocols, with shorter thinking and fewer broken writes, when it builds
the file phase by phase and simulates after each phase, like code, instead of planning everything and writing the
whole file at once?

| Arm | Skill | How it writes |
|---|---|---|
| A | `.agents/skills/fluentvibe/SKILL.md` | as it likes (plan, then write the file) |
| B | `.agents/skills/fluentvibe-incremental.md` | skeleton first, then one phase at a time with `edit`, `simulate --strict` after each |

The two skills are identical except for the name and the Workflow section. The variant lives at the root of
`.agents/skills/`, where pi does not auto-discover it, so it only loads when passed with `--skill`.

## Setup (once per terminal, PowerShell)

```powershell
cd D:\python\fluentvibe
$env:FLUENTVIBE_PROFILE_DIR = "build/workspaces/1080_Dev"
```

Use the same model and thinking level for every run, and write them down (e.g. `--provider qwen3090 --model qwen3.8-27b`
or `--provider qwenFlash262k --model Qwen3.8-Flash-Next-UD-IQ4_XS`, `--thinking high`).

## Runs: 3 per arm, alternating A1 B1 A2 B2 A3 B3, each a fresh pi start

Arm A:

```powershell
pi --no-skills --skill .agents/skills/fluentvibe/SKILL.md --provider <p> --model <m> --thinking <level>
```

> /skill:fluentvibe automate the following protocol for the fluent platform. the input is 96 samples 20 ul DNA in a 96 well plate. ask questions if something is unclear. C:\Users\Niko\Pictures\MAN0014017_Dynabeads_M280_Streptavidin_UG.pdf Write the protocol to build/eval/pi-dyna-A-1.py

Arm B:

```powershell
pi --no-skills --skill .agents/skills/fluentvibe-incremental.md --provider <p> --model <m> --thinking <level>
```

> /skill:fluentvibe-incremental (same text, file build/eval/pi-dyna-B-1.py)

If pi asks questions, give the same answer every time:

> Follow the datasheet's "Immobilize nucleic acids" protocol exactly. Choose what it leaves open and list your choices at the end.

A run ends when pi says it is done or gives up. Do not help it beyond that answer.

## Score each run right after it (before starting the next pi)

```powershell
python scripts/score_pi_run.py build/eval/pi-dyna-A-1.py
```

It uses the newest pi session for this folder (pass `--session <file>` for an older one) and prints:

- **PASS/FAIL**: PASS means `simulate --strict` and compile succeed, every datasheet step is present (beads,
  2X B&W, DNA, 15 min binding, magnet, 1X B&W washes, low-salt resuspension), and there is no invented chemistry
  (NaOH/Solution A, elution, neutralisation, 1 M Tris, ethanol/SPRI, PBS/BSA). The step and reagent lists come
  from the datasheet, as in the hand-transcribed oracle `tests/fixtures/dynabeads_m280_dna_oracle.json`.
- **How pi worked**: minutes in total and until the first write, whole-file writes (and the largest in chars),
  edits, simulate runs (and how many showed errors), total thinking and the most thinking in one turn.

Then read the protocol: the scorer catches missing and invented steps by name, not every chemistry mistake.

## Results

| Run | Verdict | Missing | Invented | Minutes | First write (min) | Writes / largest | Edits | Simulate runs (errors) | Max thinking in one turn |
|---|---|---|---|---|---|---|---|---|---|
| A1 | | | | | | | | | |
| B1 | | | | | | | | | |
| A2 | | | | | | | | | |
| B2 | | | | | | | | | |
| A3 | | | | | | | | | |
| B3 | | | | | | | | | |

B is better if it passes at least as often as A, with less thinking in one turn and no large rewrites of the
whole file.
