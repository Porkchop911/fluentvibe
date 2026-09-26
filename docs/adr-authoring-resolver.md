# ADR: shared resolver + requirements contract for LLM authoring

Status: accepted for a first vertical slice (2026-09-26). Reviewed with an
external model ("Astra"); its amendments are folded in.

## Context

Two authoring paths exist, each with one blocking flaw:

| | A. model writes DSL Python | B. model writes a primitive spec, code writes Python |
|---|---|---|
| Time | 15-60 min | 2-5 min |
| Failure | most effort on deck mechanics (positions, tips, head, fills, names); repair rounds | instructions outside the primitive vocabulary are silently dropped ("dispense ethanol with the FCA", "liquid classes as string variables") |

B's speed comes from **deterministic resolution** of mechanics, not from the
spec format. B's loss comes from an information boundary: `SpecStep` has no
head or liquid-class-variable field, and the skeleton routes wash/ethanol to
the MCA on its own.

FluentControl's InfoPad is authoritative for **FluentControl context
acceptance**, not for chemistry or completeness: 15 missing incubation waits
passed both the simulator and the InfoPad.

## Decision

Two components, built together:

1. **A shared resolver behind high-level Python calls** (`wt.add(...)` first).
   Calls record intent; mechanics (source labware, head, tips, fill volumes)
   are resolved from the deck profile.
   - Explicit choices (`head=`, `source=`, `liquid_class_var=`) are
     **requirements**: impossible combinations raise a conflict, never a
     silent switch.
   - Automatic choices are **explainable defaults**: every decision is
     recorded with the deck facts that justified it (`wt.resolution_report()`).
   - Low-level DSL stays available and contributes to the same protocol.
2. **A requirements ledger**, separate from the program. Each requirement has
   its source clause, a precise condition and evidence taken from the resolved
   operations (not comments, labels or model claims). Verdicts are pass / fail
   / unknown; *unknown blocks a fully verified result*.

Three verdicts stay separate: document/request fidelity (ledger),
modelled feasibility (simulator + InfoPad), experimental validity (not
established by either).

## First slice (falsification experiment)

- `wt.add(reagent, to=, volume_ul=, head=, source=, liquid_class=,
  liquid_class_var=, columns=, name=)`; everything else stays low-level DSL.
- Ledger checks: reagent dispensed with a given head; pipetting uses declared
  liquid-class string variables; a wait of at least N s between two anchors.
- Deterministic negative tests: remove a required wait but keep its comment;
  switch an FCA addition to MCA; replace a variable with a literal; request an
  impossible explicit combination (MCA from a slim trough). All must fail
  verification or raise a conflict.
- A hand-written mixed program for the AMPure protocol first (interface
  test without a model), then paired model runs on one fixed configuration
  (27B, temperature 0.2, reasoning xhigh): baseline DSL path vs DSL + `wt.add`,
  three variants (plain; explicit FCA ethanol + liquid-class variables; one
  step outside the helper), requirements written independently of the model.

Falsified for this scope if: explicit requirements disappear while acceptance
passes; the escape path bypasses verification; the model still solves
placement/tips/fills; or end-to-end time (through the final check) stays
above 5 min.

## Not in this slice

Requirement extraction by a model with per-clause dispositions and an
independent coverage check; full deferred (whole-workflow) resolution;
chemistry completeness as material transformations; the spec path adopting
the ledger.
