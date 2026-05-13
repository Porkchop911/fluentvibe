# Review Notes

This repository is being polished for community feedback, not released as a
production-ready package.



## Current reviewer expectations

- just have fun

## Remaining audit items

- `fluentvibe/_assets/config/generation.yaml` still carries the current default
  workspace binding used by the renderer. Decide whether to keep it as a
  functional local default, replace it with a neutral placeholder, or make it a
  required user configuration before a broader public release.
- Several tests intentionally reference a real installed workspace name so they
  can exercise install-backed lookup behavior. Keep these out of marketing
  claims and revisit them if the review branch should be fully synthetic.
