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
  centrifuge, Qubit …) but are marked ``deck`` are flagged.
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
    "add",           # reagent from a source into sample wells
    "transfer",      # sample liquid from one container to another, 1:1
    "mix",
    "pool",          # many samples into one container
    "bead_cleanup",  # magnetic bead bind / wash / elute / recover
    "incubate",      # temperature and/or time
    "measure",       # e.g. Qubit, NanoDrop
    "manual",        # operator-only step (no liquid handling on the deck)
    "custom",        # anything else; described in text
)
LOCATIONS = ("deck", "off_deck", "manual")
ROLES = ("sample", "reagent", "bead_carrier", "wash", "eluent", "per_sample", "product")

_NUMERIC_STEP_FIELDS = ("volume_ul", "ratio", "washes", "elute_ul", "wash_ul", "temp_c", "minutes")


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


@dataclass
class BenchSpec:
    title: str
    sample_count: int
    sample_volume_ul: float | None
    reagents: list[SpecReagent]
    steps: list[SpecStep]
    notes: list[str] = field(default_factory=list)
    spec_version: int = SPEC_VERSION

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
                        "source_quote": {"type": ["string", "null"]},
                    },
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
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
        steps.append(SpecStep(
            id=sid, op=op, text=str(item.get("text") or ""), location=location,
            reagent=reagent, target=item.get("target"),
            volume_ul=_num(item.get("volume_ul")), ratio=_num(item.get("ratio")),
            washes=int(washes) if isinstance(washes, (int, float)) else None,
            wash_ul=_num(item.get("wash_ul")), elute_ul=_num(item.get("elute_ul")),
            temp_c=[v for v in (_num(x) for x in item.get("temp_c") or []) if v is not None],
            minutes=[v for v in (_num(x) for x in item.get("minutes") or []) if v is not None],
            source_quote=item.get("source_quote"),
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


def validate_bench_spec(raw: dict[str, Any], source_text: str | None = None) -> tuple[BenchSpec | None, list[SpecProblem]]:
    """Parse plus every deterministic check that applies."""
    spec, problems = parse_bench_spec(raw)
    if spec is None:
        return None, problems
    if source_text:
        problems += check_numbers(spec, source_text)
    problems += check_locations(spec)
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

Read the document and describe WHAT the protocol does, not how a robot would do it.
Call submit_bench_spec exactly once with:
- samples: how many samples the document is written for (use the largest plate
  format it supports, e.g. 96) and the per-sample input volume;
- reagents: every reagent used, with a short id (the document's acronym when it
  has one, e.g. AXP, EB), its name and a role (sample, reagent, bead_carrier,
  wash, eluent, per_sample for per-well reagents such as barcodes, product);
  for reagents the kit supplies, supply_ul (fill volume per vial, or per well
  for plated reagents) and supply_count (number of vials or wells) exactly as
  the kit contents table states them;
- steps: every step in the document's order. For each: a short plain text,
  the op (add, transfer, mix, pool, bead_cleanup, incubate, measure, manual,
  custom), the location (deck = liquid handling a robot can do on its worktable;
  off_deck = needs a device or place away from the worktable, e.g. thermal
  cycler, centrifuge, Qubit, ice; manual = operator-only, e.g. flow-cell
  priming), volumes in µl, temperatures, minutes, washes, and a short quote
  from the document as source_quote.

Rules:
- Use only numbers written in the document. If the document leaves a value
  open, leave the field null and say so in notes.
- Keep the document's order. If it pools samples before a cleanup, the pool
  step comes first.
- One bead cleanup (bind, washes, elution, recovery) is ONE bead_cleanup step.
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
    message = client.complete(
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        tools=[tool],
    )
    raw = None
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != EXTRACTION_TOOL_NAME:
            continue
        arguments = function.get("arguments")
        try:
            raw = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except (TypeError, ValueError):
            return None, [SpecProblem("schema", "$", "submit_bench_spec arguments are not valid JSON")], None
        break
    if raw is None:
        return None, [SpecProblem("schema", "$", "the model did not call submit_bench_spec")], None
    spec, problems = validate_bench_spec(raw, source_text)
    return spec, problems, raw


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
