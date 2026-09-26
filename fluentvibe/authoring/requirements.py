"""Requirements ledger: explicit request/document requirements, checked on the
resolved operations of a protocol.

A requirement records the clause it came from and a precise condition; its
verdict is ``pass``, ``fail`` or ``unknown`` with evidence taken from the
operations the protocol actually performs (which head aspirates from a
reagent's source, which liquid class each pipetting step references, whether a
real wait sits between two points). Comments, group names and model claims
are never evidence. ``unknown`` (the operations do not show it either way)
blocks a fully verified result.

The ledger is written independently of the authoring model (by a person, or
later by a separate extraction step with per-clause dispositions); the model
never defines its own success criteria.

Kinds (``params``):

* ``head_for_reagent``: ``reagent`` (name substring), ``head`` ("fca"/"mca") —
  every aspirate from labware that initially holds the reagent uses that head.
* ``liquid_class_variables``: optional ``default`` (and ``mix_default``) —
  every aspirate, dispense and mix references a declared string variable with
  that default (mixes: ``mix_default``, since mixing needs a Mix section).
* ``wait_between``: ``after`` (anchor), ``before`` (anchor), ``min_seconds`` —
  a wait of at least that long lies between the two anchors. Anchors:
  ``["add", reagent]`` (the first dispense after aspirating that reagent),
  ``["magnet_on"]`` (the plate moved onto a magnet), ``["magnet_off"]``.
* ``step_present``: ``step_types`` — the protocol contains one of these
  operations (e.g. a worklist import for a CSV-driven step).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

_MCA = {"AspirateStep": "aspirate", "DispenseStep": "dispense", "Mca384MixStep": "mix"}
_LIHA = {"LihaAspirateStep": "aspirate", "LihaDispenseStep": "dispense", "LihaMixStep": "mix"}


@dataclass
class Requirement:
    id: str
    text: str                      # the clause it came from, verbatim
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    # How the clause was settled: "requirement" (as written), "clarified: …"
    # (the approved reading when the words and the deck disagree), …
    disposition: str = "requirement"


@dataclass
class Verdict:
    id: str
    status: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def load_requirements(path: Path | str) -> list[Requirement]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Requirement(**item) for item in data]


# ── operations ──────────────────────────────────────────────────────────


@dataclass
class _Op:
    index: int
    line: int | None
    head: str | None          # "mca" | "fca" | None
    action: str               # aspirate | dispense | mix | wait | move | other
    labware: str | None
    step: Any


def _operations(wt) -> list[_Op]:
    ops: list[_Op] = []
    for index, step in enumerate(wt._iter_all_steps()):
        kind = type(step).__name__
        pos = getattr(step, "source_pos", None)
        line = getattr(pos, "line", None)
        if kind in _MCA:
            ops.append(_Op(index, line, "mca", _MCA[kind], step.labware_name, step))
        elif kind in _LIHA:
            ops.append(_Op(index, line, "fca", _LIHA[kind], step.labware_name, step))
        elif kind in ("WaitStep", "DelayStep", "WaitForTimerStep"):
            ops.append(_Op(index, line, None, "wait", None, step))
        elif kind == "RgaTransferLabwareStep":
            ops.append(_Op(index, line, None, "move", step.labware_name, step))
        else:
            ops.append(_Op(index, line, None, "other", getattr(step, "labware_name", None), step))
    return ops


def _initial_holders(wt, reagent_match: str) -> set[str]:
    """Labware whose authored initial contents include a reagent matching the name."""
    match = reagent_match.lower()
    holders = set()
    for label, labware in getattr(wt, "_placed", {}).items():
        for well in getattr(labware, "wells", {}).values():
            if any(match in str(getattr(layer.reagent, "name", "")).lower() for layer in well.layers):
                holders.add(label)
                break
    return holders


def _seconds(wt, value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    for source in (getattr(wt, "sim_values", {}), wt.protocol_variables):
        if value in source:
            try:
                return float(source[value])
            except (TypeError, ValueError):
                return None
    return None


def _is_magnet(wt, label: str | None) -> bool:
    labware = getattr(wt, "_placed", {}).get(label or "")
    return labware is not None and getattr(labware, "category", "") == "magnet_rack"


def _anchor(wt, ops: list[_Op], anchor: Iterable[Any], start: int) -> _Op | None:
    anchor = list(anchor)
    kind = anchor[0]
    if kind == "add":
        sources = _initial_holders(wt, str(anchor[1]))
        aspirated = False
        for op in ops[start:]:
            if op.action == "aspirate" and op.labware in sources:
                aspirated = True
            elif aspirated and op.action == "dispense" and op.labware not in sources:
                return op
        return None
    if kind in ("magnet_on", "magnet_off"):
        for op in ops[start:]:
            if op.action != "move":
                continue
            onto = getattr(op.step, "stack_onto", None)
            if kind == "magnet_on" and _is_magnet(wt, onto):
                return op
            if kind == "magnet_off" and not _is_magnet(wt, onto):
                return op
        return None
    raise ValueError(f"unknown anchor {anchor!r}")


# ── checks ──────────────────────────────────────────────────────────────


def _check_head(wt, ops: list[_Op], req: Requirement) -> Verdict:
    reagent, head = str(req.params["reagent"]), str(req.params["head"]).lower()
    sources = _initial_holders(wt, reagent)
    if not sources:
        return Verdict(req.id, UNKNOWN, f"no labware initially holds a reagent named like {reagent!r}")
    draws = [op for op in ops if op.action == "aspirate" and op.labware in sources]
    if not draws:
        return Verdict(req.id, UNKNOWN, f"nothing is aspirated from {sorted(sources)}")
    wrong = [op for op in draws if op.head != head]
    if wrong:
        where = ", ".join(f"line {op.line} ({op.head})" for op in wrong[:5])
        return Verdict(req.id, FAIL, f"{len(wrong)} of {len(draws)} aspirate(s) of {reagent} use another head: {where}")
    return Verdict(req.id, PASS, f"{len(draws)} aspirate(s) of {reagent} from {sorted(sources)}, all {head.upper()}")


def _check_lc_variables(wt, ops: list[_Op], req: Requirement) -> Verdict:
    variables = wt.protocol_variables
    default = req.params.get("default")
    pipetting = [op for op in ops if op.action in ("aspirate", "dispense", "mix")]
    if not pipetting:
        return Verdict(req.id, UNKNOWN, "no pipetting steps")
    literal = [op for op in pipetting if getattr(op.step, "liquid_class", None) not in variables]
    not_string = sorted({op.step.liquid_class for op in pipetting
                         if op.step.liquid_class in variables and not isinstance(variables[op.step.liquid_class], str)})
    # The default applies to transfers; a mix needs a class with a Mix section,
    # so mixes may default to another class (``mix_default``) — still a variable.
    mix_default = req.params.get("mix_default")

    def expected(op: _Op):
        return mix_default if op.action == "mix" and mix_default is not None else default

    wrong_default = sorted({op.step.liquid_class for op in pipetting
                            if expected(op) is not None and op.step.liquid_class in variables
                            and variables[op.step.liquid_class] != expected(op)})
    problems = []
    if literal:
        problems.append(f"{len(literal)} step(s) use a literal liquid class, e.g. line {literal[0].line} "
                        f"({literal[0].step.liquid_class!r})")
    if not_string:
        problems.append(f"not string variables: {not_string}")
    if wrong_default:
        problems.append(f"default is not {default!r}: {wrong_default}")
    if problems:
        return Verdict(req.id, FAIL, "; ".join(problems))
    used = sorted({op.step.liquid_class for op in pipetting})
    return Verdict(req.id, PASS, f"{len(pipetting)} pipetting step(s) reference string variables {used}")


def _check_wait_between(wt, ops: list[_Op], req: Requirement) -> Verdict:
    minimum = float(req.params["min_seconds"])
    first = _anchor(wt, ops, req.params["after"], 0)
    if first is None:
        return Verdict(req.id, UNKNOWN, f"anchor {req.params['after']} not found")
    last = _anchor(wt, ops, req.params["before"], first.index + 1)
    if last is None:
        return Verdict(req.id, UNKNOWN, f"anchor {req.params['before']} not found after line {first.line}")
    waits = [op for op in ops[first.index + 1:last.index] if op.action == "wait"]
    def seconds(op: _Op) -> float:
        if type(op.step).__name__ == "DelayStep":
            return float(op.step.delay) / 1000.0
        return _seconds(wt, op.step.duration_seconds) or 0.0

    total = sum(seconds(op) for op in waits)
    if total + 1e-9 < minimum:
        return Verdict(req.id, FAIL, f"{total:g} s of waiting between line {first.line} and line {last.line}; "
                                     f"{minimum:g} s required")
    return Verdict(req.id, PASS, f"{total:g} s of waiting between line {first.line} and line {last.line}")


def _check_step_present(wt, ops: list[_Op], req: Requirement) -> Verdict:
    kinds = {str(k) for k in req.params["step_types"]}
    found = [op for op in ops if type(op.step).__name__ in kinds]
    if not found:
        return Verdict(req.id, FAIL, f"no {' / '.join(sorted(kinds))} in the protocol")
    return Verdict(req.id, PASS, f"{len(found)} x {type(found[0].step).__name__}, first at line {found[0].line}")


_CHECKS = {
    "step_present": _check_step_present,
    "head_for_reagent": _check_head,
    "liquid_class_variables": _check_lc_variables,
    "wait_between": _check_wait_between,
}


def verify(wt, requirements: Iterable[Requirement]) -> list[Verdict]:
    """Check each requirement on the protocol's resolved operations."""
    wt._finalize_resolution()
    ops = _operations(wt)
    verdicts = []
    for req in requirements:
        check = _CHECKS.get(req.kind)
        if check is None:
            verdicts.append(Verdict(req.id, UNKNOWN, f"no check for kind {req.kind!r}"))
            continue
        try:
            verdicts.append(check(wt, ops, req))
        except Exception as exc:  # noqa: BLE001 - a broken check is not a pass
            verdicts.append(Verdict(req.id, UNKNOWN, f"check failed: {type(exc).__name__}: {exc}"))
    return verdicts


def fully_verified(verdicts: Iterable[Verdict]) -> bool:
    return all(v.status == PASS for v in verdicts)
