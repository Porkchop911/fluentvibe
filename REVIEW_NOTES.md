# Review Notes

This repository is being polished for community feedback, not released as a
production-ready package.



## Current reviewer expectations

The most useful feedback is on domain-model and workflow fit (see the
"What Needs Review" section of [README.md](README.md)):

- Does the authoring API match how FluentControl users think about protocols?
- Are worktables, labware, reagents, tips, and heads modeled at the right level?
- Which FluentControl commands or workflow patterns are missing?
- Are the simulator's physical checks catching useful mistakes?
- Would decompile-to-Python help when reviewing or modernizing existing methods?

Running the suite: the default offline suite needs no FluentControl install or
local LM. Live, install-, and LM-backed tests are excluded by default and opt in
via markers — `pytest -m live_lm`, `-m fluentcontrol_shell`. Some install-backed
tests skip automatically unless a local FluentControl install is reachable.

## Remaining audit items

- `fluentvibe/_assets/config/generation.yaml` still carries the current default
  workspace binding used by the renderer. Decide whether to keep it as a
  functional local default, replace it with a neutral placeholder, or make it a
  required user configuration before a broader public release.
- Several tests intentionally reference a real installed workspace name so they
  can exercise install-backed lookup behavior. Keep these out of marketing
  claims and revisit them if the review branch should be fully synthetic.
