# Skill authoring kit — Opentrons → fluentvibe skills

This directory is the **work-instruction kit** for turning the Opentrons
protocol library at `D:\Opentron_protocols` (~248 protocols) into fluentvibe
`--lab-scope skills` (markdown skills under
`fluentvibe/_assets/config/skills/{api,deck,family}/`).

It is deliberately **not** a one-shot converter. The corpus is parcelled into
independent *work packages* (one per protocol type) so a local agent — the
LM Studio model via the optional generator script, or a Claude subagent, or a
human — can produce or enhance one skill at a time.

## Contents

| File | Purpose |
|---|---|
| [SOP.md](SOP.md) | The step-by-step method for making or enhancing one skill. |
| [opentrons-translation.md](opentrons-translation.md) | Opentrons API → fluentvibe mapping + capability boundary the SOP relies on. |
| [work-packages.yaml](work-packages.yaml) | The ~12 type bundles; each is one self-contained unit of work. |
| [capability-roadmap.md](capability-roadmap.md) | fluentvibe object-model extensions that would unlock the deferred packages. |
| [coverage-ledger.md](coverage-ledger.md) | Generated summary of the full-corpus pass (per-bucket + per-status counts). |
| `coverage-ledger.csv` | The durable per-protocol ledger driving the full-corpus pass (see below). |
| `../../scripts/draft_skill_from_package.py` | Optional generator — drafts a skill from a package via the local LM (see below). |

## Two ways to use the kit

**A. One package at a time** (`work-packages.yaml` + `SOP.md`) — pick a package,
read its 2–4 representatives, author/enhance one skill. Good for targeted work.

**B. Full-corpus pass** (the ledger + the three corpus scripts) — go through ALL
~248 protocols so every one ends with a terminal disposition, amending skills
only on *material novelty*. The **local LM does the reading**; Claude only builds
the harness, audits the verdict log, and validates drafts. Token-cheap by design.

### Full-corpus pass — how it runs
```
PYTHONPATH=. python scripts/triage_corpus.py   # 1. deterministic, NO LM: buckets all 248 -> coverage-ledger.csv
PYTHONPATH=. python scripts/mine_corpus.py     # 2. local LM only: mines every bucket, writes drafts + verdicts
# 3. Claude: audit `mined` rows + validate each draft (SOP §7), move into skills/, commit; flip rows to amended.
```
- **`scripts/triage_corpus.py`** — pure-Python regex over folder name + README +
  `.py`; assigns a family bucket + signals; idempotent (won't overwrite a
  non-`pending` status). Bucketing is a *seed*, not ground truth.
- **`scripts/mine_bucket.py <bucket>`** — feeds the bucket's `pending` protocols
  to the local LM in small batches. Per batch the LM returns a per-protocol
  verdict (`nothing-new` / `novel: …` / `reassign: <bucket>` / `out-of-scope: …`)
  and, for `novel` protocols only, a small **additions fragment** (the new
  variable/technique/labware to fold in). The LM never reproduces the skill — a
  local model truncates a long doc — so fragments accumulate into
  `_drafts/<skill>.additions.md`, a PROPOSAL file Claude integrates by hand. The
  skill body is never machine-rewritten; nothing is written into `skills/`.
- **`scripts/mine_corpus.py`** — drives every bucket in rounds; a `reassign`
  verdict re-buckets a mis-triaged protocol and the next round mines it under the
  correct family. Prints a final summary listing the `novel`-flagged protocols
  (whose fragments Claude integrates) and the `new-family-candidate`s Claude
  reviews by hand.
- **`coverage-ledger.csv`** — `folder, slug, bucket, candidate_tags, signals,
  status, contributed, target_skill`. `status ∈ {pending, mined, amended,
  new-skill, nothing-new, out-of-scope, deferred}`. The pass is **done when no
  `pending` rows remain**; `mined` rows await Claude's validation before becoming
  `amended`.

## Generator script (optional accelerator)

`scripts/draft_skill_from_package.py` produces a **first draft** of a skill so an
agent/human doesn't start from a blank file. It is a drafting aid only — it does
**not** validate, simulate, or write into `skills/`.

```
PYTHONPATH=. python scripts/draft_skill_from_package.py <package-id> \
    [--out-dir docs/skill-authoring/_drafts] [--endpoint URL] [--model NAME]
```

Exactly what it does:
1. Looks `<package-id>` up in `work-packages.yaml` (lists valid ids if missing).
2. Gathers inputs: each source dir's `README.md` (full) + its `.py` (first ~280
   lines), plus `SOP.md`, `opentrons-translation.md`, and one existing skill in
   the target axis as a format template.
3. Makes **one** tool-free call to the local LM (`LMStudioChatClient`).
4. Writes the model's output as a draft to
   `docs/skill-authoring/_drafts/<target>.draft.md` (a gitignored scratch dir).

It never runs the authoring/simulation pipeline. After it writes a draft you
**must** review it and run **SOP §7** (discover + integrity test + an
`author --lab-scope skills` run) before moving the file into
`fluentvibe/_assets/config/skills/<axis>/`. The kit works fine without this script
— a Claude subagent or a human can follow `SOP.md` directly.

## Hard boundary (read first)

fluentvibe exposes `wt.liha`, `wt.mca96`, `wt.gripper`, `MagnetRack`,
plates (incl. 384 + deep-well) / troughs / tip boxes, `wt.wait`, `wt.loop`,
`wt.conditional`, scalar aspirate/dispense volumes for uniform/column-wise work,
**and worklists** (`wt.worklist(csv|gwl)` / the `Gwl` builder) for **per-well
distinct volumes and CSV pick lists** — see the `api-worklists` skill. There is
**no** thermocycler, heater-shaker, or temperature-module abstraction.

So a skill captures a protocol's *liquid-handling recipe* — variables, labware
roles, step sequence, mixing, bead workflow, incubation waits — not the
Opentrons hardware. Module steps become `wt.wait()`/`wt.add_comment(...)`.
Per-well CSV volumes are **no longer** out of scope: they map to a worklist
(the simulator validates/renders worklist steps but does not walk their per-well
volume changes — state that in the skill).

## Gotcha

Skills are auto-discovered by `lab_skills.discover_skills` via `rglob("*.md")`
under the skills dir, and `tests/test_lab_skills.py::test_shipped_catalog_is_well_formed`
asserts every `*.md` there parses as a skill **and** sits in a folder named for
its axis. **Never put kit docs (or drafts) under `skills/`** — keep them here.
