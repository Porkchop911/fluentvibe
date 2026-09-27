"""Bench Spec: what the protocol *is*, separate from how the deck runs it.

Stage A of the authoring pipeline in docs/authoring-strategy.md (W2). A model
reads the source document once and fills in a small structured spec: samples,
reagents, and the ordered steps with volumes, incubations and — crucially —
where each step happens (``deck``, ``off_deck`` or ``manual``). A person can
check and edit that spec cheaply before any code is written, and later stages
(mapping, composing, checking) work from the spec instead of the 40-page PDF.

This module is deterministic and model-free except for :func:`extract_bench_spec`,
which takes any client with a ``complete(messages=..., tools=...)`` method.

Deterministic checks on a spec:

* **schema** — required fields, known ``op`` and ``location`` values, unique ids;
* **numbers** — every number in the spec must appear in the source text, so an
  extracted "960 µl" can be traced and an invented "1.8×" is flagged;
* **locations** — steps whose text names an off-deck device (thermal cycler,
  centrifuge, Qubit …) but are marked ``deck`` are flagged;
* **open values** — a deck step missing a number its physics needs (an ``add``
  without a volume) is an open question, not a default.

Steps are physical primitives (``add``, ``transfer``, ``remove``, ``mix``,
``separate``, ``incubate``, ``manual`` …) with no chemistry in them, so any
protocol is a sequence of known steps. ``bead_cleanup`` and ``pool`` are
macros for the common bind-wash-elute clean-up and column pooling. A number
the document does not give but the model chose is listed in the step's
``proposed``; it is reviewed, not checked against the document.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .offdeck import OFF_DECK_PATTERN

SPEC_VERSION = 1
SPEC_MARKER = "APPROVED BENCH SPEC"

OPS = (
    # Physical primitives.
    "add",           # reagent from a source into the working wells
    "transfer",      # working wells into new labware, 1:1 (the new labware becomes the working plate)
    "remove",        # liquid out of the working wells into waste (supernatant, wash)
    "mix",           # mix the working wells in place (resuspend beads, mix a reaction)
    "separate",      # magnet on (engage=true) or off (engage=false)
    "incubate",      # temperature and/or time
    "measure",       # e.g. Qubit, NanoDrop
    "manual",        # operator-only step (no liquid handling on the deck)
    "repeat",        # repeat the steps first_step..last_step `times` more times
    # Macros: a fixed order of primitives.
    "pool",          # many samples into one container
    "normalize",     # every sample to target_ng in volume_ul, diluted with `reagent` (per-well volumes)
    "bead_cleanup",  # SPRI-type clean-up: bind, wash, elute off the magnet, recover eluate
    "custom",        # no primitive fits; described in text, authored by hand
)
MACROS = ("pool", "bead_cleanup", "normalize")
LOCATIONS = ("deck", "off_deck", "manual")
ROLES = ("sample", "reagent", "bead_carrier", "wash", "eluent", "per_sample", "product")

_NUMERIC_STEP_FIELDS = ("volume_ul", "ratio", "washes", "elute_ul", "wash_ul", "residual_ul", "cycles", "target_ng",
                        "temp_c", "minutes")


@dataclass
class SpecReagent:
    id: str
    name: str
    role: str = "reagent"
    liquid_type: str | None = None
    # What the kit supplies: µl per vial (or per well for plated reagents) and
    # how many vials/wells. Both as written in the document; None if not stated
    # (e.g. lab-stock reagents). Used by the reagent-budget check.
    supply_ul: float | None = None
    supply_count: int | None = None


@dataclass
class SpecStep:
    id: str
    op: str
    text: str
    location: str = "deck"
    reagent: str | None = None
    target: str | None = None
    volume_ul: float | None = None
    ratio: float | None = None
    washes: int | None = None
    wash_ul: float | None = None
    elute_ul: float | None = None
    temp_c: list[float] = field(default_factory=list)
    minutes: list[float] = field(default_factory=list)
    source_quote: str | None = None
    # remove: µl left in each well; mix: cycles; separate: magnet on (True) / off (False).
    residual_ul: float | None = None
    cycles: int | None = None
    engage: bool | None = None
    # Numeric fields the model chose because the document leaves them open.
    proposed: list[str] = field(default_factory=list)
    # add: which head dispenses, when the request says so ("fca" / "mca").
    head: str | None = None
    # repeat: the block of earlier steps to run again, and how many more times.
    first_step: str | None = None
    last_step: str | None = None
    times: int | None = None
    # normalize: DNA mass per sample (ng) in volume_ul.
    target_ng: float | None = None


@dataclass
class BenchSpec:
    title: str
    sample_count: int
    sample_volume_ul: float | None
    reagents: list[SpecReagent]
    steps: list[SpecStep]
    notes: list[str] = field(default_factory=list)
    spec_version: int = SPEC_VERSION
    # The request wants liquid classes as FluentControl string variables.
    liquid_class_variables: bool = False
    # The protocol builds its wells from reagents (no samples at the start);
    # None = not stated (the skeleton infers it).
    starts_empty: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass(frozen=True)
class SpecProblem:
    kind: str       # "schema" | "number" | "location"
    where: str      # e.g. "steps[3].volume_ul"
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "where": self.where, "message": self.message}


# ── JSON schema (also the tool parameters for extraction) ────────────────


def bench_spec_json_schema() -> dict[str, Any]:
    number_list = {"type": "array", "items": {"type": "number"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "sample_count", "reagents", "steps"],
        "properties": {
            "title": {"type": "string"},
            "sample_count": {"type": "integer", "minimum": 1},
            "sample_volume_ul": {"type": ["number", "null"]},
            "reagents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "name", "role"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "role": {"type": "string", "enum": list(ROLES)},
                        "liquid_type": {"type": ["string", "null"]},
                        "supply_ul": {"type": ["number", "null"]},
                        "supply_count": {"type": ["integer", "null"]},
                    },
                },
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "op", "text", "location"],
                    "properties": {
                        "id": {"type": "string"},
                        "op": {"type": "string", "enum": list(OPS)},
                        "text": {"type": "string"},
                        "location": {"type": "string", "enum": list(LOCATIONS)},
                        "reagent": {"type": ["string", "null"]},
                        "target": {"type": ["string", "null"]},
                        "volume_ul": {"type": ["number", "null"]},
                        "ratio": {"type": ["number", "null"]},
                        "washes": {"type": ["integer", "null"]},
                        "wash_ul": {"type": ["number", "null"]},
                        "elute_ul": {"type": ["number", "null"]},
                        "temp_c": number_list,
                        "minutes": number_list,
                        "residual_ul": {"type": ["number", "null"]},
                        "target_ng": {"type": ["number", "null"]},
                        "cycles": {"type": ["integer", "null"]},
                        "engage": {"type": ["boolean", "null"]},
                        "head": {"type": ["string", "null"], "enum": ["fca", "mca", None]},
                        "first_step": {"type": ["string", "null"]},
                        "last_step": {"type": ["string", "null"]},
                        "times": {"type": ["integer", "null"]},
                        "proposed": {"type": "array", "items": {"type": "string",
                                                                 "enum": list(_NUMERIC_STEP_FIELDS)}},
                        "source_quote": {"type": ["string", "null"]},
                    },
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
            "liquid_class_variables": {"type": "boolean"},
            "starts_empty": {"type": ["boolean", "null"]},
        },
    }


# ── Parsing and deterministic checks ─────────────────────────────────────


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bench_spec(raw: dict[str, Any]) -> tuple[BenchSpec | None, list[SpecProblem]]:
    """Build a :class:`BenchSpec` from a dict, collecting schema problems.

    Returns ``(None, problems)`` only when the spec is unusable (no title, no
    steps, or a non-object input); otherwise a spec plus any problems found.
    """
    problems: list[SpecProblem] = []
    if not isinstance(raw, dict):
        return None, [SpecProblem("schema", "$", "spec must be a JSON object")]
    title = str(raw.get("title") or "").strip()
    steps_raw = raw.get("steps")
    if not title:
        problems.append(SpecProblem("schema", "title", "title is required"))
    if not isinstance(steps_raw, list) or not steps_raw:
        problems.append(SpecProblem("schema", "steps", "at least one step is required"))
        return None, problems
    try:
        sample_count = int(raw.get("sample_count") or 0)
    except (TypeError, ValueError):
        sample_count = 0
    if sample_count < 1:
        problems.append(SpecProblem("schema", "sample_count", "sample_count must be a positive integer"))

    reagents: list[SpecReagent] = []
    for i, item in enumerate(raw.get("reagents") or []):
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            problems.append(SpecProblem("schema", f"reagents[{i}]", "reagent needs id and name"))
            continue
        role = str(item.get("role") or "reagent")
        if role not in ROLES:
            problems.append(SpecProblem("schema", f"reagents[{i}].role", f"unknown role {role!r}"))
        supply_count = item.get("supply_count")
        reagents.append(SpecReagent(
            id=str(item["id"]), name=str(item["name"]), role=role,
            liquid_type=item.get("liquid_type"),
            supply_ul=_num(item.get("supply_ul")),
            supply_count=int(supply_count) if isinstance(supply_count, (int, float)) else None,
        ))
    reagent_ids = {r.id for r in reagents}

    steps: list[SpecStep] = []
    seen: set[str] = set()
    for i, item in enumerate(steps_raw):
        where = f"steps[{i}]"
        if not isinstance(item, dict):
            problems.append(SpecProblem("schema", where, "step must be an object"))
            continue
        sid = str(item.get("id") or f"s{i + 1}")
        if sid in seen:
            problems.append(SpecProblem("schema", f"{where}.id", f"duplicate step id {sid!r}"))
        seen.add(sid)
        op = str(item.get("op") or "custom")
        if op not in OPS:
            problems.append(SpecProblem("schema", f"{where}.op", f"unknown op {op!r}"))
        location = str(item.get("location") or "deck")
        if location not in LOCATIONS:
            problems.append(SpecProblem("schema", f"{where}.location", f"unknown location {location!r}"))
        reagent = item.get("reagent")
        if reagent and reagent_ids and reagent not in reagent_ids:
            problems.append(SpecProblem("schema", f"{where}.reagent", f"reagent {reagent!r} is not declared"))
        washes = item.get("washes")
        cycles = item.get("cycles")
        engage = item.get("engage")
        proposed = [str(f) for f in item.get("proposed") or [] if str(f) in _NUMERIC_STEP_FIELDS]
        steps.append(SpecStep(
            id=sid, op=op, text=str(item.get("text") or ""), location=location,
            reagent=reagent, target=item.get("target"),
            volume_ul=_num(item.get("volume_ul")), ratio=_num(item.get("ratio")),
            washes=int(washes) if isinstance(washes, (int, float)) else None,
            wash_ul=_num(item.get("wash_ul")), elute_ul=_num(item.get("elute_ul")),
            temp_c=[v for v in (_num(x) for x in item.get("temp_c") or []) if v is not None],
            minutes=[v for v in (_num(x) for x in item.get("minutes") or []) if v is not None],
            source_quote=item.get("source_quote"),
            residual_ul=_num(item.get("residual_ul")),
            target_ng=_num(item.get("target_ng")),
            cycles=int(cycles) if isinstance(cycles, (int, float)) and not isinstance(cycles, bool) else None,
            engage=engage if isinstance(engage, bool) else None,
            proposed=proposed,
            head=str(item.get("head")).lower() if str(item.get("head") or "").lower() in {"fca", "mca"} else None,
            first_step=str(item["first_step"]) if item.get("first_step") else None,
            last_step=str(item["last_step"]) if item.get("last_step") else None,
            times=int(item["times"]) if isinstance(item.get("times"), (int, float)) and not isinstance(item.get("times"), bool) else None,
        ))
    if not title:
        return None, problems
    spec = BenchSpec(
        title=title,
        sample_count=max(sample_count, 1),
        sample_volume_ul=_num(raw.get("sample_volume_ul")),
        reagents=reagents,
        steps=steps,
        notes=[str(n) for n in raw.get("notes") or []],
        liquid_class_variables=bool(raw.get("liquid_class_variables", False)),
        starts_empty=raw.get("starts_empty") if isinstance(raw.get("starts_empty"), bool) else None,
    )
    return spec, problems


_NUMBER_IN_TEXT = re.compile(r"(?<![\d.])(\d{1,3}(?:[,\s]\d{3})+|\d+)(?:[.,](\d+))?(?![\d])")


def _numbers_in_text(text: str) -> set[float]:
    """All numbers written in ``text`` (handles "1,200", "1 200", "1.5", "1,5")."""
    found: set[float] = set()
    for match in _NUMBER_IN_TEXT.finditer(text):
        whole, frac = match.group(1), match.group(2)
        digits = re.sub(r"[,\s]", "", whole)
        try:
            found.add(float(f"{digits}.{frac}" if frac else digits))
            if frac and "," in match.group(0) and len(frac) == 3:
                found.add(float(digits + frac))  # "1,200" read as thousands
        except ValueError:
            continue
    return found


def check_numbers(spec: BenchSpec, source_text: str) -> list[SpecProblem]:
    """Numbers in the spec that never appear in the source document.

    Volumes and times are compared as written, so a spec that says 960 µl
    passes when the document shows "960 μl"; a ratio of 1.8 fails when the
    document never mentions 1.8. Sample counts are not checked (they are often
    a choice, e.g. "up to 96").
    """
    numbers = _numbers_in_text(source_text)
    problems: list[SpecProblem] = []

    def check(where: str, value: float | None) -> None:
        if value is None:
            return
        if not any(abs(value - n) < 1e-6 for n in numbers):
            problems.append(SpecProblem(
                "number", where, f"{value:g} does not appear in the source document",
            ))

    check("sample_volume_ul", spec.sample_volume_ul)
    for i, reagent in enumerate(spec.reagents):
        check(f"reagents[{i}].supply_ul", reagent.supply_ul)
        if reagent.supply_count is not None and reagent.supply_count > 1:
            check(f"reagents[{i}].supply_count", float(reagent.supply_count))
    for i, step in enumerate(spec.steps):
        for name in _NUMERIC_STEP_FIELDS:
            if name in step.proposed:
                continue  # the model's choice for an open value; reviewed, not traced
            value = getattr(step, name)
            values: Iterable[float | None] = value if isinstance(value, list) else [value]
            for j, v in enumerate(values):
                where = f"steps[{i}].{name}" + (f"[{j}]" if isinstance(value, list) else "")
                check(where, float(v) if v is not None else None)
    return problems


def check_locations(spec: BenchSpec) -> list[SpecProblem]:
    """Deck steps whose own text names an off-deck device or action."""
    problems: list[SpecProblem] = []
    for i, step in enumerate(spec.steps):
        if step.location == "deck" and OFF_DECK_PATTERN.search(step.text or ""):
            problems.append(SpecProblem(
                "location", f"steps[{i}].location",
                f"step {step.id!r} mentions an off-deck action but is marked 'deck'",
            ))
    return problems


def expand_repeats(spec: BenchSpec) -> tuple[BenchSpec, list[SpecProblem]]:
    """Unroll ``repeat`` steps into copies of the steps they name (ids get
    ``-2``, ``-3`` … suffixes), so every copy is built, simulated and checked
    like any other step. A repeat naming unknown steps is a problem, not a guess."""
    from dataclasses import replace as _replace

    problems: list[SpecProblem] = []
    out: list[SpecStep] = []
    for i, step in enumerate(spec.steps):
        if step.op != "repeat":
            out.append(step)
            continue
        ids = [s.id for s in out]
        if step.first_step not in ids or step.last_step not in ids                 or ids.index(step.first_step) > ids.index(step.last_step) or not step.times or step.times < 1:
            problems.append(SpecProblem("schema", f"steps[{i}]",
                                        f"repeat {step.id!r} needs first_step/last_step of earlier steps and times >= 1"))
            continue
        block = out[ids.index(step.first_step): ids.index(step.last_step) + 1]
        for n in range(2, step.times + 2):
            out.extend(_replace(s, id=f"{s.id}-{n}") for s in block if s.op != "repeat")
    return _replace(spec, steps=out), problems


def open_values(spec: BenchSpec) -> list[SpecProblem]:
    """Deck steps missing a number the physics needs: questions for the user.

    Only what cannot be derived: an ``add`` without a volume. A ``remove`` or
    ``transfer`` without one takes everything above the residual, a ``mix``
    without one mixes most of the well, so those are not open.
    """
    problems: list[SpecProblem] = []
    has_deck_liquid = any(s.location == "deck" and s.op not in {"incubate", "measure", "manual"}
                          for s in spec.steps)
    # (A plate that starts empty gets its samples from an add step with its own volume.)
    # (Normalisation takes its draws from the sample sheet: the builder sizes the wells.)
    if spec.sample_volume_ul is None and has_deck_liquid and not spec.starts_empty \
            and not any(s.op == "normalize" for s in spec.steps) \
            and any(r.role == "sample" for r in spec.reagents):
        problems.append(SpecProblem(
            "open", "sample_volume_ul",
            "the samples' volume per well is not given: how many µl of sample are in each well at the start?",
        ))
    names = {r.id: r.name for r in spec.reagents}

    def quote(step: SpecStep) -> str:
        text = " ".join(step.text.split())
        return f"“{text[:70]}{'…' if len(text) > 70 else ''}”"

    # One question per reagent, not per step: a buffer added in five washes
    # without a volume is one question, named as the document names it.
    missing_volume: dict[str, list[tuple[int, SpecStep]]] = {}
    for i, step in enumerate(spec.steps):
        if step.location != "deck":
            continue
        # (Converted drafts name no reagents at all; that is a known gap of the
        # converter, not a question for this step.)
        if step.op == "add" and not step.reagent and spec.reagents:
            problems.append(SpecProblem(
                "open", f"steps[{i}].reagent",
                f"{quote(step)} adds a liquid but does not say which: which reagent or buffer?",
            ))
        if step.op == "add" and step.volume_ul is None:
            missing_volume.setdefault(step.reagent or "", []).append((i, step))
    for reagent, steps in missing_volume.items():
        name = names.get(reagent, reagent) or "the reagent"
        label = f"{name} ({reagent})" if reagent and name != reagent else name
        where = "; ".join(f"steps[{i}].volume_ul" for i, _ in steps)
        if len(steps) == 1:
            detail = f"No volume is given where it is added: {quote(steps[0][1])}."
        else:
            detail = f"It is added {len(steps)} times without a volume, first: {quote(steps[0][1])}."
        problems.append(SpecProblem("open", where, f"How many µl of {label} per well? {detail}"))
    return problems


def validate_bench_spec(raw: dict[str, Any], source_text: str | None = None) -> tuple[BenchSpec | None, list[SpecProblem]]:
    """Parse plus every deterministic check that applies."""
    spec, problems = parse_bench_spec(raw)
    if spec is None:
        return None, problems
    if source_text:
        problems += check_numbers(spec, source_text)
    problems += check_locations(spec)
    problems += open_values(spec)
    return spec, problems


# ── Presentation ─────────────────────────────────────────────────────────


def _fmt(value: float | None, unit: str = "") -> str:
    if value is None:
        return ""
    return f"{value:g}{unit}"


def spec_to_markdown(spec: BenchSpec, problems: Iterable[SpecProblem] = ()) -> str:
    """A reviewable table: one row per step, flagged rows marked."""
    flagged: dict[int, list[str]] = {}
    for problem in problems:
        match = re.match(r"steps\[(\d+)\]", problem.where)
        if match:
            flagged.setdefault(int(match.group(1)), []).append(problem.message)
    lines = [
        f"# Bench Spec: {spec.title}",
        "",
        f"Samples: {spec.sample_count}"
        + (f" × {spec.sample_volume_ul:g} µl" if spec.sample_volume_ul else ""),
        "",
        "| # | Step | Op | Where | Reagent | Volume | Conditions | Check |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, step in enumerate(spec.steps):
        conditions = ", ".join(
            filter(None, [
                " → ".join(f"{t:g} °C" for t in step.temp_c),
                " + ".join(f"{m:g} min" for m in step.minutes),
                f"{step.washes} washes" if step.washes else "",
                f"ratio {step.ratio:g}" if step.ratio else "",
                f"elute {step.elute_ul:g} µl" if step.elute_ul else "",
                f"{step.cycles} cycles" if step.cycles else "",
                {True: "magnet on", False: "magnet off"}.get(step.engage, "") if step.op == "separate" else "",
                f"proposed: {', '.join(step.proposed)}" if step.proposed else "",
            ])
        )
        check = "; ".join(flagged.get(i, [])) or "ok"
        lines.append(
            f"| {step.id} | {step.text} | {step.op} | {step.location} | {step.reagent or ''} "
            f"| {_fmt(step.volume_ul, ' µl')} | {conditions} | {check} |"
        )
    lines += ["", "| Reagent | Name | Role | Kit supply |", "|---|---|---|---|"]
    for reagent in spec.reagents:
        supply = ""
        if reagent.supply_ul is not None:
            count = reagent.supply_count or 1
            supply = f"{count} × {reagent.supply_ul:g} µl = {count * reagent.supply_ul:g} µl"
        lines.append(f"| {reagent.id} | {reagent.name} | {reagent.role} | {supply} |")
    other = [p for p in problems if not p.where.startswith("steps[")]
    if other:
        lines += ["", "Other checks:", *[f"- {p.where}: {p.message}" for p in other]]
    if spec.notes:
        lines += ["", "Notes:", *[f"- {note}" for note in spec.notes]]
    return "\n".join(lines) + "\n"


def spec_context_block(spec: BenchSpec) -> str:
    """Compact, approved spec text for the authoring prompt (stages C–F)."""
    return (
        f"{SPEC_MARKER} (authoritative; implement these steps in this order; "
        "deck steps on the deck, off_deck/manual steps as operator pauses; never "
        "load more of a kit reagent than its supply_ul × supply_count). Spec roles are not "
        "Python roles: in code use Reagent(role='analyte') for sample/product, 'bead_carrier' "
        "and 'eluent' as is, and the default role for wash/reagent/per_sample:\n"
        + spec.to_json()
    )


# ── Extraction (stage A) ─────────────────────────────────────────────────

EXTRACTION_TOOL_NAME = "submit_bench_spec"

EXTRACTION_SYSTEM_PROMPT = """You extract a Bench Spec from a laboratory protocol document.

Read the document and describe WHAT the protocol does as a sequence of physical
steps, not how a particular robot would do it.
Call submit_bench_spec exactly once with:
- samples: how many samples the document is written for (use the largest plate
  format it supports, e.g. 96) and sample_volume_ul, the volume of sample in
  each well at the start (the request's volume when it states one, e.g. "PCR
  products of 20 ul" -> 20);
- reagents: every reagent used, with a short id (the document's acronym when it
  has one, e.g. AXP, EB), its name and a role (sample, reagent, bead_carrier,
  wash, eluent, per_sample for per-well reagents such as barcodes, product);
  for reagents the kit supplies, supply_ul (fill volume per vial, or per well
  for plated reagents) and supply_count (number of vials or wells) exactly as
  the kit contents table states them;
- steps: every step in the document's order. For each: a short plain text,
  the op, the location (deck = liquid handling a robot can do on its worktable;
  off_deck = needs a device or place away from the worktable, e.g. thermal
  cycler, centrifuge, Qubit, ice; manual = operator-only, e.g. flow-cell
  priming), volumes in µl per well, temperatures, minutes, and a short quote
  from the document as source_quote.

Ops are physical primitives. The working wells are the plate the protocol is
currently working in (the samples at first; after a transfer, the new plate):
- add: a reagent (reagent id) from its source into the working wells; volume_ul per well.
  Beads, buffers, probes, master mix: all "add".
- transfer: the working wells' liquid into NEW labware, well to well; volume_ul
  (null = all of it). The new labware becomes the working plate.
- remove: liquid out of the working wells to waste (supernatant, used wash);
  volume_ul (null = all but residual_ul).
- mix: mix the working wells in place (resuspend beads); cycles.
- separate: magnet. engage=true puts the plate on the magnet (beads collect,
  minutes = settle time); engage=false takes it off so beads can be resuspended.
  The deck has a magnet: separate is a deck step even when the document uses a
  hand-held magnet (DynaMag); so are the removes and adds around it.
- incubate: time and/or temperature (location off_deck when it needs a device).
- repeat: run the earlier steps first_step..last_step again, times = how many MORE
  times (3 washes: write one wash, then repeat it with times=2). Use it instead of
  describing a repetition in text.
- measure / manual: operator steps. Preparing a reagent away from the plate (mixing
  working reagent from A and B, reconstituting a substrate) is manual.
A bead wash is: separate(engage=true), remove, separate(engage=false), add wash buffer, mix.
Macros, only when the document really does exactly this:
- bead_cleanup: a SPRI/AMPure-type clean-up that binds DNA to beads, washes,
  ELUTES off the magnet and recovers the eluate into new labware (ratio, washes,
  wash_ul, elute_ul). Anything else with beads (streptavidin capture, bead
  washes, keeping the beads) is written as primitives.
- pool: samples combined into one container.
- normalize: every sample brought to the same amount (target_ng) in the same
  volume (volume_ul) with a diluent (reagent, e.g. water), when the document
  gives the DNA and water volume per sample from its concentration (e.g. "50 ng
  in 9 ul"). The concentrations come from the user's sample sheet; do not list them.
- custom: only if no primitive fits; say why in text. NOT for a step whose
  reagent, volume or time is unknown: write the primitive, leave that field null,
  and the user is asked.

Instructions in the request about HOW the robot works are part of the spec:
- "head" on an add step: "fca" or "mca" when the request says which head
  (FCA = 8-channel arm, MCA = 96-channel head) dispenses that reagent;
  otherwise null (the deck decides).
- "liquid_class_variables": true when the request asks for the liquid
  classes as (string) variables.

Rules:
- Numbers from the document go in their fields as written.
- If the document leaves a number open (e.g. "desired volume") and the request
  asks you to choose, put your value in the field and list the field name in
  that step's "proposed" (e.g. "proposed": ["volume_ul"]). If you cannot
  choose, leave it null and say what is needed in notes.
- Volumes are per well of a 96-well plate (at most ~300 µl per well); scale
  tube volumes from the document down to one well.
- Keep the document's order. If it pools samples before a cleanup, the pool
  step comes first.
- If the protocol starts from reagents (e.g. beads) rather than samples, set
  "starts_empty": true: the working plate then starts empty and the first add
  fills it. Otherwise set "starts_empty": false.
- The samples are the liquid that differs from well to well (each sample's DNA,
  template, lysate): give that reagent the role sample even when it is added
  later (e.g. DNA onto washed beads); it then comes from a sample plate, one
  well per sample, never from one trough. Barcodes and other per-well kit
  reagents are per_sample.
"""


def extract_bench_spec(client: Any, source_text: str, *, extra_context: str | None = None) -> tuple[BenchSpec | None, list[SpecProblem], dict[str, Any] | None]:
    """Ask ``client`` for a spec via a single forced tool call and validate it.

    Returns ``(spec, problems, raw_arguments)``. ``client`` is any object with a
    ``complete(messages=[...], tools=[...])`` method returning an OpenAI-style
    assistant message dict (e.g. :class:`~fluentvibe.authoring.lm_client.LMStudioChatClient`).
    """
    tool = {
        "type": "function",
        "function": {
            "name": EXTRACTION_TOOL_NAME,
            "description": "Submit the Bench Spec extracted from the document.",
            "parameters": bench_spec_json_schema(),
        },
    }
    user = "Protocol document:\n\n" + source_text
    if extra_context:
        user = extra_context.strip() + "\n\n" + user
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    # An unusable spec (no steps, no call, bad JSON) gets one retry that says why.
    for attempt in range(2):
        message = client.complete(messages=messages, tools=[tool])
        raw, fatal = _spec_arguments(message)
        if raw is not None:
            spec, problems = validate_bench_spec(raw, source_text)
            if spec is not None:
                return spec, _drop_untraced_supply(spec, problems), raw
            fatal = problems
        if attempt == 0:
            messages += [
                {"role": "assistant", "content": message.get("content") or "",
                 **({"tool_calls": message["tool_calls"]} if message.get("tool_calls") else {})},
                {"role": "user", "content": "The spec could not be used: "
                 + "; ".join(f"{p.where}: {p.message}" for p in fatal)
                 + f". Call {EXTRACTION_TOOL_NAME} again with the complete spec, every step included."},
            ]
    return None, fatal, raw


def _spec_arguments(message: dict[str, Any]) -> tuple[dict[str, Any] | None, list[SpecProblem]]:
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != EXTRACTION_TOOL_NAME:
            continue
        arguments = function.get("arguments")
        try:
            return (json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})), []
        except (TypeError, ValueError):
            return None, [SpecProblem("schema", "$", "submit_bench_spec arguments are not valid JSON")]
    return None, [SpecProblem("schema", "$", "the model did not call submit_bench_spec")]


def _drop_untraced_supply(spec: BenchSpec, problems: list[SpecProblem]) -> list[SpecProblem]:
    """A kit supply the document never states is not a supply: models tend to
    invent one for lab-prepared buffers, which then fails the budget check."""
    for problem in problems:
        match = re.match(r"reagents\[(\d+)\]\.supply_(ul|count)$", problem.where)
        if problem.kind == "number" and match:
            reagent = spec.reagents[int(match.group(1))]
            if reagent.supply_ul is not None:
                spec.notes.append(f"{reagent.id}: kit supply {reagent.supply_ul:g} µl is not in the "
                                  f"document; treated as lab stock")
            reagent.supply_ul = None
            reagent.supply_count = None
    return problems


# ── Deterministic plan from an approved spec ─────────────────────────────


def _group_name(step: SpecStep) -> str:
    text = " ".join((step.text or step.op).split())
    if len(text) > 60:
        text = text[:57].rstrip() + "…"
    where = "" if step.location == "deck" else f" [{step.location.replace('_', '-')}]"
    return f"{step.id}: {text}{where}"


def workflow_from_spec(spec: BenchSpec) -> dict[str, Any]:
    """``declare_protocol_workflow`` arguments built from an approved spec.

    The functional groups are the spec's steps in order (after the mandatory
    ``Variables`` / ``Labware Placement`` scaffold), so a model does not have to
    re-derive the plan from the source document.
    """
    slug = "".join(ch if ch.isalnum() else "_" for ch in spec.title.lower()).strip("_")[:40]
    groups: list[dict[str, Any]] = [{"name": "Variables"}, {"name": "Labware Placement"}]
    seen: set[str] = {"Variables", "Labware Placement"}
    for step in spec.steps:
        name = _group_name(step)
        if name in seen:
            name = f"{name} ({step.id})"
        seen.add(name)
        groups.append({
            "name": name,
            "objective": step.text,
            "expected_steps": [f"op={step.op}", f"location={step.location}"],
        })
    return {
        "protocol_name": spec.title,
        "summary": f"{len(spec.steps)} steps from the approved Bench Spec for {spec.sample_count} samples.",
        "variables": [{"name": "RunId", "default": slug or "run", "sim_value": slug or "run"}],
        "labware": [],
        "groups": groups,
    }
