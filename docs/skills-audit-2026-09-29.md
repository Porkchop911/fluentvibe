# Skills catalogue audit, 2026-09-29

Scope: the 28 skills in `fluentvibe/_assets/config/skills/` (api, deck, family), the two monolith prompts
`lab_scope.md` / `lab_scope_reference.md`, the pending drafts in `docs/skill-authoring/_drafts/`, and how
they reach the model (`generation.yaml`, `authoring/lab_scope.py`, `lab_skills.py`, `tools.py`).
Branch `audit/skills-catalogue` (worktree `.worktrees/skills-audit`); nothing is changed yet.

Method: a mechanical check of every code example against the real API (every `wt.*`, head, block and
constructor call and its keyword arguments, via `inspect`), a loader check, rule greps against the lab's
known rules, and reading the skills that the traces and the Dynabeads incident point at.

## What is fine

- **API accuracy: no error.** Every method, block and keyword argument used in the catalogue's code
  examples exists in the code. (The "Never call `wt.aspirate(...)`" warning lists are intended.)
- The FCA/MCA division (reagents by the FCA from slim troughs or tubes, bulk by the MCA from SBS
  reservoirs, never MCA in a slim trough) is stated consistently in `api-blocks` and
  `labware-and-liquid-classes`.
- The always-on core is 23k characters (about 6k tokens), which is a reasonable size.

## Findings

| # | Sev. | Finding | Evidence | Proposed fix |
|---|---|---|---|---|
| 1 | P1 | **Affinity capture always elutes.** Steps 6-7 are "Elute" and "Recover: transfer the eluate". There is no path where the product stays on the beads (streptavidin immobilisation, Dynabeads M-280). No family covers immobilisation, so such a request lands here or in SPRI, and both end in elution: the invented NaOH elution seen on Dynabeads. | `family/family-bead-affinity-capture.md` §Step sequence 6-7, §Variables (`ELUTE_VOLUME_UL`) | Make the end a branch: "keep on beads, resuspend (immobilisation, the document's end state)" vs "elute and recover", taken from the document. Add "only elute when the document says so". Mention streptavidin/biotin capture in the description. |
| 2 | P1 | **The always-on example is a Dynabeads protocol with numbers.** `core-clarify-open-parameters` is loaded for every protocol, and its worked `ask_user` example proposes 20 µl bead slurry, 3 × 100 µl washes, 40 µl 2X B&W + 40 µl probe, 2 × 100 µl washes, 20 µl final. That biases every run, and for Dynabeads it pre-answers the open values. | `api/core-clarify-open-parameters.md` lines 22-33 | Replace with a neutral example (e.g. an ELISA or a generic reagent addition), or a placeholder pattern without chemistry. |
| 3 | P1 | **Skill and tool contradict each other on mixing.** Two skills say mixing must use `Water Mix` ("FluentControl rejects Water Free Single for mixing") and emptying tips `Empty Tip`. In `skills` mode (used by the web app and VS Code), `lookup_liquid_class` rejects anything outside the profile's allow-list, and the 1080 profile and `generation.yaml` list only `Water Free Single`. The model is told one thing by the skill and the opposite by the tool. | `api/labware-and-liquid-classes.md:82-88`, `family/family-bead-cleanup-spri.md:140`; `authoring/tools.py:2616-2630`; `build/workspaces/1080_Dev/workspace_profile.json` `liquid_class`; `generation.yaml` `liquid_classes` | Add `Water Mix` and `Empty Tip` to the allow-list (profile and `generation.yaml`), or let the lookup accept the classes the skills require. |
| 4 | P2 | **`wt.add` is never taught.** `api-add-resolver` has `requires_env: FLUENTVIBE_RESOLVER`, and nothing sets it, so the skill never loads, although `wt.add` is the resolved way to add reagents on this deck (and an example and my pi skill use it). | `api/api-add-resolver.md` frontmatter; `lab_skills.py` `requires_env` | Decide: enable it (set the variable in the apps, or drop the gate), or remove `wt.add` from examples until it is. |
| 5 | P2 | **"FCA" is never defined as `wt.liha`.** The FCA/MCA naming is the model's main source of back-and-forth (49% of the reconsiderations sampled in the reasoning traces; it guessed "Fixed Channel Aspirator, Tecan EVO"). The skills say "FCA (LiHa)" once, and examples use the variable name `head` for the FCA in some places and for the MCA in others. | `labware-and-liquid-classes.md:33`; `core-worktable-api.md`, `api-loops-and-conditionals.md`, `family-pooling.md` (`head.get_tips(fca_tips)`); trace analysis 2026-09-29 | A glossary in an always-on skill (FCA = `wt.liha`, 8-channel arm; MCA = `wt.mca96`; RGA = `wt.gripper`), and name the variables `fca` / `mca` in every example. |
| 6 | P2 | **No "the document decides" rule.** Nothing in the always-on core says not to add steps or reagents the document and the request do not have. The only "do not invent" lines are about liquid-class names and reagent plates. | always-on skills (grep) | Add the rule to the always-on core: every deck step and reagent must come from the document or the request; open values are asked or marked as assumed; nothing added "for completeness". |
| 7 | P2 | **No capacities table; volumes are worked out by hand.** "Does it fit" is the second-largest deliberation topic in the traces (213 samples). Capacities are scattered (a ~300 µl line in core-clarify, trough fills in labware). | traces; `core-clarify-open-parameters.md:41-43` | One table (well working volumes, tip capacities, trough and reservoir fill limits) plus "the simulator checks supplies and capacity; don't compute them by hand". |
| 8 | P3 | **The monoliths hard-code the 780 deck.** `lab_scope.md` / `lab_scope_reference.md` state `SAT_Fluent_780_Rev3` and its GUID. They are used only in the `cheatsheet` / `enforce` modes, not in `skills` mode, so this is stale rather than harmful now. | `lab_scope.md:11-13`, `lab_scope_reference.md:21,97` | Mark them as legacy, or generate the deck part from the active profile like the deck skill. |
| 9 | P3 | **Deck positions hard-coded in family skills** (`"Nest61mm_Pos", 1` 33 times, e.g. SPRI's "move off the magnet to Nest61mm_Pos 1"). Correct for both decks today, but deck-coupled. | family skills (grep) | Refer to "the plate's home nest" and let the deck skill give positions. |
| 10 | P3 | **Nine drafts pending since June.** `docs/skill-authoring/_drafts/*.additions.md` (mined protocol variations: lysis/DNase for bead clean-up, per-wash liquid classes, etc.) were never folded in or rejected. | `docs/skill-authoring/_drafts/` (dated 2026-06-01 / 06-18) | Review each: fold in, or delete with a note. |
| 11 | P3 | **Examples mix raw head calls and blocks.** `api-blocks` (always on) says to use blocks; the family skills' step sequences are written in raw `head.*` calls and `wt.gripper.move`. | e.g. `family-bead-affinity-capture.md` §Step sequence | Rewrite the family step sequences in terms of blocks. |

## Decisions for you

1. **Finding 3:** add `Water Mix` and `Empty Tip` to the 1080 profile's allowed liquid classes? (The skills say
   FluentControl requires them.)
2. **Finding 4:** enable `wt.add` for the model (drop the `FLUENTVIBE_RESOLVER` gate), or keep it off?
3. **Finding 10:** fold in the June drafts, or discard them?

Findings 1, 2, 5, 6, 7 are content fixes I can make on this branch; 8, 9, 11 are cleanups.
