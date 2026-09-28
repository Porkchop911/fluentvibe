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
import re
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
    line: int | None = None       # the source line of the operation that breaks it

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


def _norm(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _initial_holders(wt, reagent_match: str) -> set[str]:
    """Labware whose authored initial contents include a reagent matching the name
    (case, spaces and punctuation ignored: "elutionbuffer" matches "Elution buffer")."""
    match = _norm(reagent_match)
    holders = set()
    if not match:
        return holders
    for label, labware in getattr(wt, "_placed", {}).items():
        for well in getattr(labware, "wells", {}).values():
            if any(match in _norm(getattr(layer.reagent, "name", "")) for layer in well.layers):
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
        return Verdict(req.id, FAIL, f"{len(wrong)} of {len(draws)} aspirate(s) of {reagent} use another head: {where}",
                       wrong[0].line)
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
    clarified = ""
    if mix_default is None and default is not None and any(op.action == "mix" for op in pipetting):
        # FluentControl rejects a mix with a transfer-only class, so mixes
        # cannot share the transfer default: they need their own Mix class.
        mixes = {variables.get(op.step.liquid_class) for op in pipetting
                 if op.action == "mix" and op.step.liquid_class in variables}
        mix_default = next(iter(mixes)) if len(mixes) == 1 else None
        if mix_default is not None and mix_default != default:
            clarified = f"; mixes default to {mix_default!r} (FluentControl needs a class with a Mix section)"

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
        return Verdict(req.id, FAIL, "; ".join(problems), literal[0].line if literal else None)
    used = sorted({op.step.liquid_class for op in pipetting})
    return Verdict(req.id, PASS, f"{len(pipetting)} pipetting step(s) reference string variables {used}{clarified}")


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
    # An incubation the operator does (on a rotator, in a thermal cycler)
    # waits too: its prompt states the time ("... 15 min with gentle rotation").
    handed = sum(_stated_seconds(getattr(op.step, "prompt", "") or "")
                 for op in ops[first.index + 1:last.index] if type(op.step).__name__ == "UserPromptStep")
    # Both ends inside one block call (e.g. a clean-up): name the line once.
    where = (f"in the call on line {first.line}" if first.line == last.line
             else f"between line {first.line} and line {last.line}")
    by_operator = f" (+{handed:g} s by the operator)" if handed else ""
    if total + handed + 1e-9 < minimum:
        return Verdict(req.id, FAIL, f"{total:g} s of waiting {where}{by_operator}; {minimum:g} s required",
                       last.line)
    return Verdict(req.id, PASS, f"{total:g} s of waiting {where}{by_operator}")


_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(h|hrs?|hours?|min|mins|minutes?|s|secs?|seconds?)\b", re.IGNORECASE)


def _stated_seconds(text: str) -> float:
    """Durations written in an operator prompt, in seconds ("2 min", "30 s")."""
    factor = {"h": 3600, "m": 60, "s": 1}
    return sum(float(n) * factor[unit[0].lower()] for n, unit in _DURATION.findall(text))


def _check_step_present(wt, ops: list[_Op], req: Requirement) -> Verdict:
    kinds = {str(k) for k in req.params["step_types"]}
    found = [op for op in ops if type(op.step).__name__ in kinds]
    if not found:
        return Verdict(req.id, FAIL, f"no {' / '.join(sorted(kinds))} in the protocol")
    return Verdict(req.id, PASS, f"{len(found)} x {type(found[0].step).__name__}, first at line {found[0].line}")


def _sample_plates(wt) -> list:
    """Labware whose authored initial contents include an analyte (the samples)."""
    out = []
    for labware in getattr(wt, "_placed", {}).values():
        if any(getattr(layer.reagent, "role", "") == "analyte"
               for well in getattr(labware, "wells", {}).values() for layer in well.layers):
            out.append(labware)
    return out


def _check_sample_volume(wt, ops: list[_Op], req: Requirement) -> Verdict:
    want = float(req.params["ul"])
    plates = _sample_plates(wt)
    if not plates:
        return Verdict(req.id, UNKNOWN, "no sample (analyte) labware found")
    volumes = sorted({round(sum(layer.volume_ul for layer in well.layers), 2)
                      for plate in plates for well in plate.wells.values()
                      if any(getattr(layer.reagent, "role", "") == "analyte" for layer in well.layers)})
    if volumes == [round(want, 2)]:
        return Verdict(req.id, PASS, f"every sample well starts with {want:g} ul")
    return Verdict(req.id, FAIL, f"sample wells start with {volumes} ul, not {want:g} ul")


def _check_sample_count(wt, ops: list[_Op], req: Requirement) -> Verdict:
    want = int(req.params["count"])
    plates = _sample_plates(wt)
    if not plates:
        return Verdict(req.id, UNKNOWN, "no sample (analyte) labware found")
    count = sum(1 for plate in plates for well in plate.wells.values()
                if any(getattr(layer.reagent, "role", "") == "analyte" for layer in well.layers))
    status = PASS if count == want else FAIL
    return Verdict(req.id, status, f"{count} sample well(s)" + ("" if status == PASS else f", not {want}"))


def _check_plate_catalog(wt, ops: list[_Op], req: Requirement) -> Verdict:
    marker = str(req.params["contains"]).lower()
    plates = [lw for lw in getattr(wt, "_placed", {}).values() if getattr(lw, "category", "") == "plate"]
    if not plates:
        return Verdict(req.id, UNKNOWN, "no plates placed")
    wrong = [f"{lw.label} ({lw.catalog_name})" for lw in plates if marker not in str(lw.catalog_name).lower()]
    if wrong:
        return Verdict(req.id, FAIL, f"plates of another type: {', '.join(wrong[:4])}")
    return Verdict(req.id, PASS, f"all {len(plates)} plate(s) are {plates[0].catalog_name!r}")


def _check_head_for_other_steps(wt, ops: list[_Op], req: Requirement) -> Verdict:
    head = str(req.params["head"]).lower()
    excepted = set()
    for name in req.params.get("except") or []:
        excepted |= _initial_holders(wt, str(name))
    # A transfer is the aspirate that starts it; its dispense/mix follow the same head.
    pipetting = [op for op in ops if op.action in ("aspirate", "mix")]
    if not pipetting:
        return Verdict(req.id, UNKNOWN, "no pipetting steps")
    others = [op for op in pipetting if not (op.action == "aspirate" and op.labware in excepted)]
    wrong = [op for op in others if op.head != head]
    if wrong:
        return Verdict(req.id, FAIL, f"{len(wrong)} step(s) use another head, e.g. line {wrong[0].line} "
                                     f"({wrong[0].head} {wrong[0].action} on {wrong[0].labware})", wrong[0].line)
    return Verdict(req.id, PASS, f"all {len(others)} other aspirate/mix step(s) use the {head.upper()}")


_CHECKS = {
    "sample_volume": _check_sample_volume,
    "sample_count": _check_sample_count,
    "plate_catalog": _check_plate_catalog,
    "head_for_other_steps": _check_head_for_other_steps,
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


# ── extraction (a separate model call; the author never writes its own) ──

EXTRACTION_TOOL = "submit_requirements"

_EXTRACTION_PROMPT = """You turn a user's request for a liquid-handling protocol into a checklist.

Go through the request clause by clause. Every clause gets one disposition:
- "requirement": the protocol must do this; add a checkable requirement for it;
- "preference": nice to have, not binding;
- "clarification": ambiguous or impossible as written; say what is unclear;
- "excluded": not about the protocol (context, remarks).

Requirements use these kinds (params in brackets):
- head_for_reagent {"reagent": "<distinctive part of the reagent name>", "head": "fca"|"mca"}
  a named head dispenses that reagent (FCA = 8-channel arm, MCA = 96-channel head);
- liquid_class_variables {"default": "<liquid class>"} (add "mix_default": "Water Mix" —
  mixing needs a class with a Mix section) — liquid classes as string variables;
- wait_between {"after": ["add", "<reagent>"], "before": ["magnet_on"] or ["magnet_off"],
  "min_seconds": N} — an incubation between adding a reagent and a magnet step;
- step_present {"step_types": ["WorklistImportStep", "LoadWorklistStep"]} — the request
  asks for a worklist;
- sample_volume {"ul": N} — each sample well starts with N ul;
- sample_count {"count": N} — N samples (a whole 96-well plate = 96);
- plate_catalog {"contains": "<part of the plate type name, e.g. ABgene>"} — plate type;
- head_for_other_steps {"head": "mca"|"fca", "except": ["<reagent>", ...]} — "otherwise use
  the MCA": every other aspirate/mix uses that head (list the reagents named for the other head);
- unchecked {} — a real requirement none of the kinds above can check: it is
  listed, not verified. Do not add an unchecked entry for a clause you already
  turned into checkable requirements.
Also add wait_between requirements for incubations the DOCUMENT states between
adding a reagent and a magnet step. Quote the source clause verbatim in "text".
Call submit_requirements once."""


def _extraction_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["requirements", "dispositions"],
        "properties": {
            "requirements": {"type": "array", "items": {
                "type": "object", "required": ["id", "text", "kind"],
                "properties": {
                    "id": {"type": "string"}, "text": {"type": "string"},
                    "kind": {"type": "string", "enum": [*_CHECKS, "unchecked"]},
                    "params": {"type": "object"},
                }}},
            "dispositions": {"type": "array", "items": {
                "type": "object", "required": ["clause", "disposition"],
                "properties": {
                    "clause": {"type": "string"},
                    "disposition": {"type": "string",
                                    "enum": ["requirement", "preference", "clarification", "excluded"]},
                    "reason": {"type": "string"},
                }}},
        },
    }


def extract_requirements(client: Any, request: str, document: str | None = None
                         ) -> tuple[list[Requirement], list[dict[str, str]]]:
    """Requirements + per-clause dispositions for ``request`` (one forced tool call)."""
    user = f"Request:\n{request.strip()}"
    if document:
        user += f"\n\nProtocol document:\n{document[:60000]}"
    message = client.complete(
        messages=[{"role": "system", "content": _EXTRACTION_PROMPT}, {"role": "user", "content": user}],
        tools=[{"type": "function", "function": {
            "name": EXTRACTION_TOOL, "description": "Submit the checklist.", "parameters": _extraction_schema()}}],
    )
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != EXTRACTION_TOOL:
            continue
        arguments = function.get("arguments")
        data = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        requirements = []
        for i, item in enumerate(data.get("requirements") or []):
            if not isinstance(item, dict) or not item.get("kind"):
                continue
            requirements.append(Requirement(
                id=str(item.get("id") or f"R{i + 1}"), text=str(item.get("text") or ""),
                kind=str(item["kind"]), params=dict(item.get("params") or {}),
            ))
        checked_texts = {r.text.strip().lower() for r in requirements if r.kind != "unchecked"}
        requirements = [r for r in requirements
                        if r.kind != "unchecked" or r.text.strip().lower() not in checked_texts]
        dispositions = [d for d in data.get("dispositions") or [] if isinstance(d, dict)]
        return requirements, dispositions
    return [], []


def sidecar_path(protocol: Path | str) -> Path:
    """Where a hand-written protocol keeps its checklist: ``<name>.requirements.json``."""
    protocol = Path(protocol)
    return protocol.with_name(protocol.stem + ".requirements.json")


def save_requirements(path: Path | str, requirements: Iterable[Requirement]) -> None:
    Path(path).write_text(json.dumps([asdict(r) for r in requirements], indent=1, ensure_ascii=False),
                          encoding="utf-8")


def verify_all(wt, requirements: Iterable[Requirement]) -> list[Verdict]:
    """:func:`verify`, with ``unchecked`` requirements reported as not verified."""
    requirements = list(requirements)
    # "Otherwise use the MCA" excludes every reagent the same checklist assigns
    # to the other head, even when the extractor left it out of "except".
    for req in requirements:
        if req.kind == "head_for_other_steps":
            head = str(req.params.get("head", "")).lower()
            named = [str(r.params.get("reagent")) for r in requirements
                     if r.kind == "head_for_reagent" and str(r.params.get("head", "")).lower() != head
                     and r.params.get("reagent")]
            req.params["except"] = sorted({*map(str, req.params.get("except") or []), *named})
    checked = verify(wt, [r for r in requirements if r.kind != "unchecked"])
    by_id = {v.id: v for v in checked}
    out = []
    for req in requirements:
        out.append(by_id.get(req.id) or Verdict(req.id, UNKNOWN, "listed; no automatic check for this kind"))
    return out


def requirements_markdown(requirements: list[Requirement], verdicts: list[Verdict],
                          dispositions: list[dict[str, str]]) -> str:
    icon = {PASS: "✅", FAIL: "❌", UNKNOWN: "⚪"}
    by_id = {v.id: v for v in verdicts}
    lines = ["# Your instructions, checked on the protocol", "",
             "| | Instruction | Check | Evidence |", "|---|---|---|---|"]
    for req in requirements:
        v = by_id.get(req.id)
        status = v.status if v else UNKNOWN
        lines.append(f"| {icon[status]} | {req.text} | {req.kind} | {(v.evidence if v else '').replace('|', '/')} |")
    other = [d for d in dispositions if d.get("disposition") != "requirement"]
    if other:
        lines += ["", "Other clauses:", ""]
        lines += [f"- *{d.get('disposition')}*: {d.get('clause')}" + (f" — {d['reason']}" if d.get("reason") else "")
                  for d in other]
    return "\n".join(lines) + "\n"


# ── document completeness: the spec's deck steps, in order ──────────────


def _protocol_tokens(wt, ops: list[_Op], reagent_names: list[str]) -> list[tuple[str, float, int | None]]:
    """The protocol as a sequence of physical events: (token, seconds, line)."""
    holders = {name: _initial_holders(wt, name) for name in reagent_names}
    placed = getattr(wt, "_placed", {})

    def category(label):
        return getattr(placed.get(label or ""), "category", "")

    def is_waste(label):
        return "waste" in str(label or "").lower()

    tokens: list[tuple[str, float, int | None]] = []
    last_source = None
    on_magnet: set[str] = set()
    for op in ops:
        kind = type(op.step).__name__
        if op.action == "aspirate":
            last_source = op.labware
        elif op.action == "dispense" and last_source is not None:
            if is_waste(op.labware):
                tokens.append(("waste", 0.0, op.line))
            else:
                reagent = next((n for n, h in holders.items() if last_source in h), None)
                if reagent is not None:
                    tokens.append((f"add:{_norm(reagent)}", 0.0, op.line))
                elif category(last_source) == "plate" and category(op.labware) == "plate" and op.labware != last_source:
                    tokens.append(("transfer", 0.0, op.line))
        elif kind in ("Mca384EmptyTipsStep", "LihaEmptyTipsStep") and is_waste(op.labware):
            tokens.append(("waste", 0.0, op.line))
        elif op.action == "mix":
            tokens.append(("mix", 0.0, op.line))
        elif op.action == "move":
            onto = getattr(op.step, "stack_onto", None)
            if _is_magnet(wt, onto):
                on_magnet.add(op.labware)
                tokens.append(("mag_on", 0.0, op.line))
            elif op.labware in on_magnet:
                on_magnet.discard(op.labware)
                tokens.append(("mag_off", 0.0, op.line))
        elif op.action == "wait":
            step = op.step
            seconds = float(step.delay) / 1000.0 if kind == "DelayStep" else (_seconds(wt, step.duration_seconds) or 0.0)
            tokens.append(("wait", seconds, op.line))
    collapsed: list[tuple[str, float, int | None]] = []
    for token in tokens:
        if collapsed and collapsed[-1][0] == token[0]:
            if token[0] == "wait":
                collapsed[-1] = ("wait", collapsed[-1][1] + token[1], collapsed[-1][2])
            continue
        collapsed.append(token)
    return collapsed


def requirements_from_spec(spec) -> list[Requirement]:
    """The document's deck steps as one ordered completeness requirement."""
    reagents = {r.id: r.name for r in spec.reagents}
    samples = {r.id for r in spec.reagents if r.role == "sample"}
    expected: list[dict[str, Any]] = []

    def push(token, text, seconds=0.0):
        if expected and expected[-1]["token"] == token and token != "wait":
            return
        expected.append({"token": token, "text": text[:80], "seconds": seconds})

    for step in spec.steps:
        if step.location != "deck":
            continue
        text = f"{step.id}: {step.text}"
        if step.op == "add" and step.reagent in samples:
            # The samples come from a sample plate, one well each: a plate-to-plate
            # transfer (the protocol scan does not read sample liquid as a reagent).
            push("transfer", text)
        elif step.op == "add" and step.reagent in reagents:
            push(f"add:{_norm(reagents[step.reagent])}", text)
        elif step.op == "separate":
            push("mag_off" if step.engage is False else "mag_on", text)
        elif step.op == "remove":
            push("waste", text)
        elif step.op in ("transfer", "pool"):
            push("transfer", text)
        elif step.op == "mix":
            push("mix", text)
        elif step.op == "incubate" and step.minutes:
            push("wait", text, sum(step.minutes) * 60.0)
        elif step.op == "bead_cleanup":
            for token in ("mix", "mag_on", "waste", "mag_off", "mix", "mag_on", "transfer"):
                push(token, text)
    return [Requirement(
        id="DOC", text="every deck step of the document, in order", kind="document_sequence",
        # Samples are not a reagent source: moving them is a transfer.
        params={"expected": expected,
                "reagents": [r.name for r in spec.reagents if r.role not in ("sample", "product")]},
    )]


def _check_document_sequence(wt, ops: list[_Op], req: Requirement) -> Verdict:
    expected = req.params.get("expected") or []
    if not expected:
        return Verdict(req.id, UNKNOWN, "the document has no deck steps to check")
    tokens = _protocol_tokens(wt, ops, list(req.params.get("reagents") or []))
    i = 0
    for exp in expected:
        while i < len(tokens) and not (
            tokens[i][0] == exp["token"] and (exp["token"] != "wait" or tokens[i][1] + 1e-6 >= 0.9 * exp["seconds"])
        ):
            i += 1
        if i == len(tokens):
            what = f"a wait of {exp['seconds']:g} s" if exp["token"] == "wait" else exp["token"].replace(":", " ")
            last_line = tokens[-1][2] if tokens else None
            return Verdict(req.id, FAIL, f"missing or out of order: {what} ({exp['text']})", last_line)
        i += 1
    return Verdict(req.id, PASS, f"all {len(expected)} deck steps of the document occur in order")


_CHECKS["document_sequence"] = _check_document_sequence
