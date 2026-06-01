# SOP — making or enhancing a skill from Opentrons protocols

How one agent (local LM, Claude subagent, or human) turns **one work package**
into one new or enhanced fluentvibe skill. Read
[opentrons-translation.md](opentrons-translation.md) first; it is the reference
this procedure leans on.

> For the **full-corpus pass** (grind all ~248 protocols, amend on material
> novelty, local LM does the reading) use the ledger-driven harness instead —
> see [README.md](README.md) "Full-corpus pass". This SOP still defines the
> recipe-extraction and validation steps that pass relies on (§3, §4, §7).

A skill is a markdown file with frontmatter:
```markdown
---
name: family-<slug>            # kebab-case; unique
axis: family                   # api | deck | family — MUST match its folder
description: <one rich sentence — the selector sees ONLY this; pack it with
  trigger words: assay/protocol names, labware, the operation>
always_on: false               # new skills are always false
---
<body: workflow, variables, code snippets in fluentvibe idiom>
```

## Procedure

### 1. Take a work package
Pick one entry from [work-packages.yaml](work-packages.yaml). It names: the
target skill path (`mode: create` or `enhance`), the axis, the 2–4
representative Opentrons source dirs, what to `extract`, any `whitelist_additions`,
`capability_notes`, and an `acceptance_prompt`. Do not work the whole corpus —
one package at a time.

### 2. Read the sources (whole files, not grep)
For each source dir read the `README.md` (summary, reagent tables, deck layout,
parameter ranges — the best raw material) and the `*.py` (`metadata`,
`add_parameters()`, and the `run(ctx)` body for step order, volumes, loops,
modules). Skim 1–2 more dirs of the same type to confirm the shape generalizes.

### 3. Extract the recipe
Write down, in fluentvibe terms:
- **Variables** — every volume/count/ratio from `add_parameters()` and derived
  values (e.g. `BEAD_VOLUME = round(BEAD_RATIO * SAMPLE_VOLUME, 1)`).
- **Labware roles** — source plate, dest plate, reservoir(s), waste, tips,
  magnet — and how many.
- **Ordered groups** — the step sequence (Variables → Labware Placement → the
  protocol-specific groups).
- **Mixing / incubation** — mix cycles; delays → `wt.wait` durations.
- **Per-well / CSV logic** — note whether volumes are uniform, column-wise, or
  truly per-well (the last is unsupported; see step 4).

### 4. Map to fluentvibe (the cheatsheet)
- Heads: column/multi-channel → `wt.liha` + column loop; whole-96 → `wt.mca96`.
- Labware: map each to an approved `catalog=` name. If none fits, add it to the
  package's `whitelist_additions` with a **resolved** catalog name
  (`fluentvibe/catalog` `find_components`) and update `generation.yaml`
  `lab_scope.labware` — do not invent names.
- Modules: thermocycler / heater-shaker / temp → `wt.wait(...)` incubation +
  a `#` note of the intended temperature/shake. Magnet → gripper onto/off +
  reagent roles.
- Scalar-volume rule: if the protocol needs different volumes per well, model
  uniform or column-wise and **state the limitation in the body**; do not fake
  per-well vectors.

### 5. Author the skill
- **Optional head start:** `PYTHONPATH=. python scripts/draft_skill_from_package.py <id>`
  drafts the file from the package via the local LM into
  `docs/skill-authoring/_drafts/` (see README "Generator script"). It's a draft
  only — you still do steps 5–7 by hand: review it, fix it, validate, move it in.
- Copy the structure of an existing skill in the same axis as a template
  (e.g. `family/family-serial-dilution.md` for a family).
- Frontmatter: unique `name`, correct `axis` (== folder), `always_on: false`,
  and a `description` dense with trigger words (assay names, labware, the
  operation) — recall depends on it.
- Body: variables block, labware placement, then the protocol groups, using
  fluentvibe idioms (variables by name, native loops, derived volumes, approved
  labware, `wt.wait` for incubations). Keep snippets runnable in shape.
- Single source of truth: reference shared mechanism skills rather than
  duplicating (e.g. point at `api-magnetization-model` for bead behavior).

### 6. Enhance an existing skill (mode: enhance)
Merge the new patterns into the existing file without duplicating content
already there. Tighten the `description` to add the new trigger words. If the
new material is a general mechanism used by several families, put it in an
`api/` skill and reference it.

### 7. Validate
```
PYTHONPATH=. python -c "from pathlib import Path; from fluentvibe.authoring.lab_skills import discover_skills; \
print([s.name for s in discover_skills(Path('fluentvibe/_assets/config/skills'))])"   # your skill appears
PYTHONPATH=. python -m pytest tests/test_lab_skills.py -q                            # integrity (folder==axis, parses)
PYTHONPATH=. python -m fluentvibe.cli author "<package acceptance_prompt>" --lab-scope skills \
    --output-dir .gen-test/<id> --model-trace --json                                # end-to-end
```
Confirm from the model trace that the pre-pass **selected** your skill, and that
the run reports `status: success` and simulates/compiles. If the selector missed
it, enrich the `description` trigger words and re-run.

## Definition of done
- One `.md` under the correct `skills/<axis>/` folder, frontmatter valid.
- Integrity test green.
- A generation run for the acceptance prompt selects the skill and succeeds.
- Any new labware is on the whitelist with a real catalog name.
- Capability gaps (modules, per-well volumes) are stated in the body, not faked.
