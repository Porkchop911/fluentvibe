# Review Notes

This repository is being polished for community feedback, not released as a
production-ready package.

## Provenance posture

- Original tecanlab source and the earlier fluentdsl-derived implementation are
  treated as project-owned code.
- Tecan/FluentControl-facing assets are treated more conservatively. Command
  templates, catalog data, workspace names, GUIDs, and `.xscr` samples may be
  derived from a local FluentControl installation or generated protocols.
- The review branch should keep only the minimum reference material needed to
  make examples and tests meaningful. Real production samples, local scratch
  output, and private handoff notes should stay out of the public surface.

## Current reviewer expectations

- Some tests skip without a local FluentControl installation.
- Generated `.xscr` files are artifacts and are ignored by Git.
- Prompt-authoring and live UI validation flows are development features; they
  are not required for first-pass community review.
- Feedback is most useful on the API model, simulator behavior, and missing
  FluentControl workflow coverage.

## Remaining audit items

- `tecanlab/_assets/config/generation.yaml` still carries the current default
  workspace binding used by the renderer. Decide whether to keep it as a
  functional local default, replace it with a neutral placeholder, or make it a
  required user configuration before a broader public release.
- Several tests intentionally reference a real installed workspace name so they
  can exercise install-backed lookup behavior. Keep these out of marketing
  claims and revisit them if the review branch should be fully synthetic.
