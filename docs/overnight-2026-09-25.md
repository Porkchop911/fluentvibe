# Overnight 2026-09-25 — Dynabeads benchmark, clarification, FluentControl

Document: *Dynabeads™ Streptavidin for Target Enrichment* user guide
(MAN0028561). A short tube-scale guide: wash the beads 3× in 1X B&W, resuspend
in 2X B&W, add an equal volume of biotinylated probe, 15 min at room
temperature, wash 2–3×, resuspend for hybridization. **No plate-scale volumes**
("transfer the desired volume", "optimize by titration"), no elution.

## Headline

**4 of 4 runs on tonight's code succeeded** (both models, with and without
the clarification step), each in 23–32 minutes, each opened in FluentControl
with zero InfoPad errors. Before tonight's fixes both models failed on this
document (54 and 60 minutes, no protocol).

Both local models now produce a Dynabeads protocol that FluentControl opens
with **zero InfoPad errors**. The deciding change: when numbers are open, the
model **asks once with deck-feasible proposals** instead of inventing volumes
and fighting the simulator.

| Run (all FC check on, 1 run each) | Model | Result | Wall time | FluentControl |
|---|---|---|---|---|
| Document only, first try (before tonight's fixes) | Flash | failed — reasoning loop, no protocol | 54 min | – |
| Document only, first try | 27B | failed at the 60-min cap, no passing draft | 60 min | – |
| Neutral prompt (27B could not ask yet) | 27B | success, 7 turns | 42 min | 0 errors after the `B&W` escaping fix |
| **Asks → answered → authors** | **Flash** | **success, first draft passed** | **6 + 23 = 29 min** | **0 errors** |
| **Asks → answered → authors** | **27B** | **success, 1 draft + 5 small fixes** | **1.7 + 30.5 = 32 min** | **0 errors** |
| Document only, rerun on tonight's code | Flash | success, first draft passed | 23 min | 0 errors |
| Document only, rerun on tonight's code | 27B | success, 1 draft + 2 fixes | 26 min | 0 errors |

Rubric scores (0.5–0.71) are not meaningful here: most rubric checks are
built for bead clean-ups with elution. Protocols were checked by hand against
the guide instead (below).

**Caveat — n = 1 per condition.** Flash failed and then succeeded on the same
document-only prompt; single runs show what is possible, not a success rate.

## The questions the models asked

Both asked **one** question listing every open number with a proposal tied to
a guide step, and checked deck feasibility themselves.

- **27B** (100 s): 20 µL beads/well (0.2 mg), 3 × 20 µL washes (literal "equal
  volume"), 40 µL 2X B&W ("twice the original volume", correct), 40 µL probe…
- **Flash** (6 min): 20 µL beads, 3 × 60 µL washes ("practical minimum for
  96-channel aspiration"), 2 × 80 µL coated washes, 40 µL final; totals 36 mL
  1X B&W (fits one 60 mL SBS reservoir), 43 mL waste.
  **Slip:** 20 µL 2X B&W instead of 40 (and "→ 80 µL total" does not add up).
  The benchmark auto-answered "yes"; a person answering would catch it —
  exactly what the question step is for.

## Protocol quality (read by hand)

- All steps of sections 1–2 present in both answered protocols: resuspend,
  dispense beads, 3 washes on the magnet, 2X B&W, probe, 15 min incubation
  (operator step / wait — the deck has no rotator), coated washes, final
  resuspension.
- Reagent handling differs:
  - **Flash** follows the rule: beads, 2X B&W and probe by the **FCA from slim
    troughs**; the 1X B&W wash by the MCA from an SBS reservoir.
  - **27B** stamps beads and probe with the MCA from **96-well source plates**
    and adds both B&W buffers with the MCA from SBS reservoirs. The buffers
    are fine under the rule; beads/probe from pre-filled plates mean an
    operator aliquots 96 wells by hand first — debatable. The labware skill now
    says so (afec83a): common reagents from troughs/tubes via the FCA; stamp
    from a plate only when the reagent differs per well or the kit ships it plated.
- Weak spots: much column-by-column FCA work (slow on the instrument);
  supernatant tips reused across columns — acceptable here because before
  hybridization every well holds the same beads/probe.
- **Question for you:** Flash placed the bead trough on `WS_100ml_1` position 1,
  which the profile lists as the FCA thru-deck waste chute. FluentControl did
  not flag it and neither does fluentvibe. Is that position physically usable
  for a trough? If not, I will add a compile check against workspace-occupied
  positions.

## Fixed tonight (all committed, 799 tests)

| Commit | Fix | Found by |
|---|---|---|
| 5581ac2 | **Loop guard**: a streamed reply that repeats one line is stopped and the turn retried once with "act now"; token-limited replies too. | Flash looped on `Let me try: 222 = 80 + 60 + 82?` for 32K tokens |
| 5581ac2, bcfa904, 5dc3a37 | Simulator errors explain the arithmetic: what the tips already hold, what the command adds, what emptied them, what to aspirate. | both models re-deriving "tip would hold 222 µL" / repeating empty-tip dispenses |
| cde8a84 | Skill `core-clarify-open-parameters` + `ask_user` in skills mode. | your request |
| fb793e7 | `ask_user` offered in the planning turn; header asks before planning when numbers are open. | 27B planned with invented volumes (could not ask) |
| 6d74a64 | If the request says "choose values yourself", `ask_user` continues with the model's proposals instead of ending the run. | Flash asked despite "choose sensible values" |
| 0e72d9d | **Renderer escapes free text** (`&`, `<`, `>`) in every template value. | the 27B protocol with "1X B&W" in a comment: FluentControl could not load the file |
| a7ad3d8 | eval saves the model's clarification question. | harness |
| afec83a | Skill: no invented pre-filled reagent plates; troughs/tubes via the FCA. | 27B stamped beads/probe from 96-well plates |

Also raised the output cap to 64K tokens per reply for these runs (per-reply
limit, not per run).

## Next

1. Repeat each condition 3–5× for success rates (≈ 30 min per run per model).
2. The answered protocols are slow on the instrument (column-wise FCA);
   a `wash_beads` block (MCA from an SBS reservoir, magnet round trips) would
   make these bead-prep stages one call.
3. Rubric: a bead-prep profile (no elution) so scores mean something for
   protocols like this one.
4. Decide on `WS_100ml_1` 1 (waste chute) — see question above.
