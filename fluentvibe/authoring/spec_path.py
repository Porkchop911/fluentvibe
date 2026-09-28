"""Author a protocol from a document through the Bench Spec.

The model reads the document once and writes a spec of physical primitives
(:mod:`~fluentvibe.authoring.bench_spec`); code writes the protocol from it
(:mod:`~fluentvibe.authoring.skeleton`). When the spec leaves something open
the skeleton must not guess (a volume, a reagent beyond its kit supply, a
volume the deck's plates cannot hold) the questions go to ``ask``; the model
then revises the spec with the answer, and the skeleton is rebuilt. The draft
is compiled and simulated, and optionally checked in FluentControl.

``ask`` is any callable taking the list of questions and returning the answer
text, or ``None`` to stop and hand the questions back. :func:`choose_yourself`
answers for an unattended run whose request says to choose values.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bench_spec import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_TOOL_NAME,
    BenchSpec,
    SpecProblem,
    _drop_untraced_supply,
    _spec_arguments,
    bench_spec_json_schema,
    extract_bench_spec,
    validate_bench_spec,
)

Ask = Callable[[list[str]], "str | None"]

CHOOSE_YOURSELF_ANSWER = (
    "Choose sensible values yourself and mark every number you choose in the step's 'proposed'. "
    "Stay within what the deck and the kit allow: at most ~300 µl per well; a kit reagent may not be "
    "used beyond its stated supply unless the document lists a larger pack size (then set supply_ul / "
    "supply_count to that pack)."
)


def choose_yourself(questions: list[str]) -> str:
    """The answer for an unattended run: the model decides, within the limits."""
    return CHOOSE_YOURSELF_ANSWER


# The answer "nothing to change": keep the assumptions, or let the model decide
# the open values. An ``ask`` returns it for an all-blank answer; None stops.
ACCEPT = "__accept__"


UNDERSTAND_TOOL = "state_understanding"

UNDERSTAND_PROMPT = (
    "You prepare the automation of a lab protocol on a Tecan Fluent liquid handler: the FCA (Flexible "
    "Channel Arm, 8 pipetting channels), the MCA (Multiple Channel Arm, a 96-channel head), the RGA "
    "(gripper), a magnet on the deck, and an operator for off-deck steps. Before the long "
    "work starts, state briefly what you will automate and ask the user only what you genuinely need.\n"
    f"Call {UNDERSTAND_TOOL} once:\n"
    "- understood: 2-4 plain sentences: which protocol/procedure, how many samples and in which "
    "plate, the key volumes, what runs on the deck and what the operator does by hand. Use the "
    "user's request and the document; say 'I will assume ...' for what you would choose yourself.\n"
    "- questions: at most 5 short questions, only where the answer changes the protocol and neither "
    "the request nor the document settles it (e.g. which of several procedures in a datasheet, the "
    "sample count, a volume the document leaves open). Empty when everything is clear. No questions "
    "about things you can decide sensibly yourself.\n"
    "- request_has_instructions: true if the user's request states concrete requirements that can be "
    "checked on the finished protocol (volumes, sample count, which arm or head, liquid classes, "
    "timings, labware); false for a general request such as 'automate this for DNA'."
)


SAMPLE_SHEET_NOTE = (
    "(Attached: a sample sheet with the DNA concentrations of {n} samples. The samples "
    "are NOT normalised yet: where the document prepares an amount per sample (e.g. "
    "\"50 ng in 9 ul\"), the robot does it as a normalize step from these concentrations.)"
)


def understand_request(client: Any, source_text: str, request: str) -> tuple[str, list[str], bool]:
    """One short model call: what the model will automate, what it must ask
    first, and whether the request holds instructions worth a checklist."""
    tool = {
        "type": "function",
        "function": {
            "name": UNDERSTAND_TOOL,
            "description": "State what will be automated and ask what is unclear.",
            "parameters": {
                "type": "object",
                "properties": {
                    "understood": {"type": "string"},
                    "questions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "request_has_instructions": {"type": "boolean"},
                },
                "required": ["understood", "questions", "request_has_instructions"],
            },
        },
    }
    user = (f"User's request: {request or '(none: automate the document)'}\n\n"
            f"Protocol document:\n\n{source_text[:60000]}")
    message = client.complete(
        messages=[{"role": "system", "content": UNDERSTAND_PROMPT}, {"role": "user", "content": user}],
        tools=[tool],
    )
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != UNDERSTAND_TOOL:
            continue
        arguments = function.get("arguments")
        try:
            data = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except (TypeError, ValueError):
            break
        understood = " ".join(str(data.get("understood") or "").split())
        questions = [" ".join(str(q).split()) for q in data.get("questions") or [] if str(q).strip()][:5]
        if understood:
            return understood, questions, data.get("request_has_instructions") is not False
    # No usable call: fall back to the reply text, so the user still sees something to confirm.
    text = " ".join(str(message.get("content") or "").split())[:800]
    return text or "(the model gave no summary)", [], True


_SPEC_CACHE_SIZE = 40


def _spec_cache_key(client: Any, source_text: str, extra_context: str | None) -> str:
    import hashlib

    parts = [
        source_text, extra_context or "",
        str(getattr(client, "model", "")), str(getattr(client, "reasoning_effort", "")),
        EXTRACTION_SYSTEM_PROMPT, json.dumps(bench_spec_json_schema(), sort_keys=True),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _spec_cache_get(path: Path | str | None, key: str) -> tuple[dict[str, Any], str] | None:
    if path is None:
        return None
    try:
        entry = json.loads(Path(path).read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("raw"), dict):
        return None
    return entry["raw"], str(entry.get("when", "an earlier run"))


def _spec_cache_put(path: Path | str | None, key: str, raw: dict[str, Any] | None) -> None:
    if path is None or raw is None:
        return
    path = Path(path)
    try:
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        entries = {}
    entries.pop(key, None)
    entries[key] = {"raw": raw, "when": time.strftime("%d %b %H:%M")}
    while len(entries) > _SPEC_CACHE_SIZE:          # oldest first (insertion order)
        entries.pop(next(iter(entries)))
    try:
        path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def assumptions(spec: BenchSpec, limit: int = 20) -> list[str]:
    """What the model assumed, for the user to confirm: every value it chose
    itself (``proposed``) and every question it left in its notes."""
    unit = {"volume_ul": "{} µl per well", "ratio": "{}× ratio", "washes": "{} washes", "wash_ul": "{} µl wash",
            "elute_ul": "{} µl elution", "residual_ul": "{} µl left in the well", "cycles": "{} mix cycles",
            "temp_c": "{} °C", "minutes": "{} min"}
    out: list[str] = []
    for step in spec.steps:
        for name in step.proposed:
            value = getattr(step, name, None)
            if value in (None, [], ""):
                continue
            shown = ", ".join(f"{v:g}" for v in value) if isinstance(value, list) else (
                f"{value:g}" if isinstance(value, (int, float)) else str(value))
            text = " ".join(step.text.split())
            text = f"{text[:70]}{'…' if len(text) > 70 else ''}"
            out.append(f"“{text}”: {unit.get(name, name + ' = {}').format(shown)} (assumed)")
    out += [" ".join(n.split()) for n in spec.notes if "?" in n]
    return out[:limit]


@dataclass
class SpecPathResult:
    stage: str                       # "done" | "questions" | "spec" | "skeleton" | "gate"
    spec: BenchSpec | None = None
    spec_raw: dict[str, Any] | None = None
    problems: list[SpecProblem] = field(default_factory=list)
    source: str | None = None
    rounds: list[dict[str, Any]] = field(default_factory=list)   # questions asked and answers
    gate: dict[str, Any] | None = None
    fluentcontrol: dict[str, Any] | None = None
    # The request's instructions (a separate model call) and their verdicts.
    requirements: list[dict[str, Any]] = field(default_factory=list)
    requirements_markdown: str | None = None
    custom_steps: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def open_questions(self) -> list[str]:
        return self.rounds[-1]["questions"] if self.stage == "questions" and self.rounds else []

    def summary(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "error": self.error,
            "rounds": self.rounds,
            "gate": bool(self.gate and self.gate.get("success")),
            "fc_ok": (self.fluentcontrol or {}).get("ok"),
            "fc_findings": [f"{f['kind']}: {f['message'][:100]}" for f in (self.fluentcontrol or {}).get("findings", [])],
            "steps": [s.op for s in self.spec.steps] if self.spec else [],
            # Deck steps the skeleton could not map (left as TODO comments): not a finished protocol.
            "todo_steps": (self.source or "").count('wt.add_comment("TODO'),
            "assumed": assumptions(self.spec) if self.spec is not None else [],
            "custom_steps": self.custom_steps,
            "instructions": {
                "total": len(self.requirements),
                "verified": sum(1 for r in self.requirements if r["status"] == "pass"),
                "failed": sum(1 for r in self.requirements if r["status"] == "fail"),
                "unverified": sum(1 for r in self.requirements if r["status"] == "unknown"),
            },
            "timings": {k: round(v, 1) for k, v in self.timings.items()},
        }


def revise_bench_spec(client: Any, raw: dict[str, Any], source_text: str, questions: list[str],
                      answer: str) -> tuple[BenchSpec | None, list[SpecProblem], dict[str, Any] | None]:
    """The model updates its spec with the user's answer (one forced tool call)."""
    tool = {
        "type": "function",
        "function": {
            "name": EXTRACTION_TOOL_NAME,
            "description": "Submit the complete, updated Bench Spec.",
            "parameters": bench_spec_json_schema(),
        },
    }
    user = (
        "Your Bench Spec for the document below cannot be built yet:\n"
        + "\n".join(f"- {q}" for q in questions)
        + f"\n\nThe user answers: {answer}\n\n"
        f"Call {EXTRACTION_TOOL_NAME} once with the COMPLETE updated spec (every step, not only the "
        "changed ones). Change only what the answer requires.\n\nCurrent spec:\n"
        + json.dumps(raw, ensure_ascii=False)
        + "\n\nProtocol document:\n\n" + source_text
    )
    message = client.complete(
        messages=[{"role": "system", "content": EXTRACTION_SYSTEM_PROMPT}, {"role": "user", "content": user}],
        tools=[tool],
    )
    revised, fatal = _spec_arguments(message)
    if revised is None:
        return None, fatal, None
    spec, problems = validate_bench_spec(revised, source_text)
    if spec is None:
        return None, problems, revised
    return spec, _drop_untraced_supply(spec, problems), revised


def author_from_document(
    client: Any,
    source_text: str,
    profile_dir: Path | str,
    output_dir: Path | str,
    *,
    request: str | None = None,
    ask: Ask | None = None,
    max_rounds: int = 3,
    fluentcontrol: bool = False,
    examples: bool = True,
    progress: Callable[[str], None] | None = None,
    check_requirements: bool = False,
    understand: bool = False,
    spec_cache: Path | str | None = None,
    sample_sheet: dict[str, float] | None = None,
) -> SpecPathResult:
    """Document -> spec -> (questions -> answers ->) skeleton -> gate (-> FluentControl).

    ``progress`` receives one short line per stage (for an editor or terminal).
    """
    from .skeleton import DeckMismatch, OpenValues, build_skeleton, load_deck

    def note(message: str) -> None:
        if progress is not None:
            progress(message)

    result = SpecPathResult(stage="spec")
    # Long vendor documents: the model gets the procedure, not the flow-cell
    # loading, data analysis and troubleshooting chapters (it has reasoned for
    # minutes over those without producing a spec).
    from .doc_trim import trim_document

    trimmed = trim_document(source_text)
    if trimmed.left_out:
        source_text = trimmed.text
        note(trimmed.note)
    request_has_instructions = True
    if sample_sheet:
        # The model never sees the concentrations, but it must know they
        # exist: otherwise "50 ng in 9 ul" reads as already done.
        request = ((request or "").rstrip() + "\n\n" + SAMPLE_SHEET_NOTE.format(n=len(sample_sheet))).strip()
    context_parts = [f"Request: {request}"] if request else []
    if understand and ask is not None and ask is not choose_yourself:
        # Generation takes minutes: first make sure the request is understood.
        note("understanding your request (model)")
        t0 = time.monotonic()
        try:
            understood, questions, request_has_instructions = understand_request(client, source_text, request or "")
        except Exception as exc:  # noqa: BLE001
            result.stage, result.error = "model", f"{type(exc).__name__}: {exc}"[:400]
            return result
        result.timings["understand_s"] = time.monotonic() - t0
        answer = ask([f"I understood: {understood}", *questions])
        result.rounds.append({"questions": [understood, *questions], "answer": answer, "kind": "understand"})
        if answer is None:
            result.stage = "questions"
            return result
        if answer == ACCEPT:
            clarification = f"The user confirmed this understanding of the task: {understood}"
        else:
            clarification = (f"Your understanding of the task was: {understood}\n"
                             + ("Your questions were:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions)) + "\n"
                                if questions else "")
                             + f"The user answered: {answer}\n"
                             "Follow the user's answer where it differs from your understanding; decide "
                             "anything left unanswered yourself and mark numbers you choose as proposed.")
            request = f"{request or ''}\n{answer}".strip()   # the answers are instructions too
        context_parts.append(clarification)
    started = time.monotonic()
    note("reading the document and writing the Bench Spec (model)")
    if examples:
        from .spec_retrieval import retrieval_context

        similar = retrieval_context(source_text)
        if similar:
            context_parts.append(similar)
    # The request's instructions are extracted by a separate call, in parallel
    # with the spec: the author never writes its own checklist.
    pending_requirements = None
    # Only when the request says something concrete (as the understanding
    # call judged): a checklist call on "automate this" costs minutes of GPU
    # time for nothing. The document's own steps are checked either way.
    if check_requirements and request and request_has_instructions:
        from concurrent.futures import ThreadPoolExecutor

        from .requirements import extract_requirements

        pool = ThreadPoolExecutor(max_workers=1)
        pending_requirements = pool.submit(extract_requirements, client, request, source_text)
        pool.shutdown(wait=False)
        note("extracting your instructions into a checklist (model, in parallel)")
    extra_context = "\n\n".join(context_parts) or None
    cache_key = _spec_cache_key(client, source_text, extra_context)
    cached = _spec_cache_get(spec_cache, cache_key)
    if cached is not None:
        # Same document, request, answers, model and effort as a finished read:
        # reading it again would take minutes for the same result.
        raw_cached, when = cached
        spec, problems = validate_bench_spec(raw_cached, source_text)
        raw = raw_cached
        if spec is not None:
            problems = _drop_untraced_supply(spec, problems)
            note(f"reading the document: reused the spec read at {when} (same document, request and answers)")
    else:
        spec = None
    if spec is None:
        try:
            spec, problems, raw = extract_bench_spec(client, source_text, extra_context=extra_context)
        except Exception as exc:  # noqa: BLE001 - a model/server failure is a result, not a crash
            result.stage, result.error = "model", f"{type(exc).__name__}: {exc}"[:400]
            return result
        if spec is not None:
            _spec_cache_put(spec_cache, cache_key, raw)
    result.timings["spec_s"] = time.monotonic() - started
    if spec is not None:
        note(f"spec: {len(spec.steps)} steps in {result.timings['spec_s']:.0f} s; building the protocol")
    result.spec, result.problems, result.spec_raw = spec, problems, raw
    if spec is None:
        result.error = "; ".join(f"{p.where}: {p.message}" for p in problems)
        return result

    # Once the user has been asked up front, later gaps are the model's to fill
    # (the chat promised "anything you leave out, the model decides"); they are
    # listed as assumptions with the result instead of interrupting again.
    asked_up_front = any(r.get("kind") == "understand" for r in result.rounds)
    if ask is not None and ask is not choose_yourself and not asked_up_front:
        confirm = assumptions(spec)
        if confirm:
            note("waiting for you to confirm the assumptions")
            answer = ask(confirm)
            result.rounds.append({"questions": confirm, "answer": answer, "kind": "confirm"})
            if answer is None:
                result.stage = "questions"
                return result
            if answer != ACCEPT:
                note("revising the spec with your answers (model)")
                t0 = time.monotonic()
                try:
                    revised, revised_problems, revised_raw = revise_bench_spec(
                        client, raw, source_text, confirm,
                        answer + "\nKeep every other assumption as it is.")
                except Exception as exc:  # noqa: BLE001
                    result.stage, result.error = "model", f"{type(exc).__name__}: {exc}"[:400]
                    return result
                result.timings["revise_s"] = result.timings.get("revise_s", 0.0) + time.monotonic() - t0
                if revised is None:
                    result.stage, result.error = "spec", "revision unusable: " + "; ".join(
                        p.message for p in revised_problems)
                    return result
                spec, raw, problems = revised, revised_raw, revised_problems
                result.spec, result.problems, result.spec_raw = spec, problems, raw

    from .sample_sheet import parse_concentrations
    from .skeleton import SAMPLE_SHEET_QUESTION

    deck = load_deck(profile_dir)
    for _ in range(max_rounds + 1):
        try:
            result.source = build_skeleton(spec, copy.deepcopy(deck), sample_sheet=sample_sheet)
            break
        except (OpenValues, DeckMismatch) as exc:
            questions = list(getattr(exc, "questions", None) or [str(exc).replace("skeleton: ", "")])
            round_ = {"questions": questions, "answer": None}
            result.rounds.append(round_)
            if ask is None or len(result.rounds) > max_rounds:
                result.stage = "questions"
                return result
            if SAMPLE_SHEET_QUESTION in questions and ask is not choose_yourself:
                # Concentrations are the user's data: always asked, never
                # chosen by the model; they go to the builder, not the model.
                answer = ask([SAMPLE_SHEET_QUESTION])
                if answer is None:
                    result.stage = "questions"
                    return result
                found = parse_concentrations(answer)
                round_["answer"] = answer
                if found:
                    sample_sheet = found
                    note(f"sample sheet from your answer: {len(found)} concentration(s)")
                    continue
            answer = CHOOSE_YOURSELF_ANSWER if asked_up_front else ask(questions)
            if answer is None:
                result.stage = "questions"
                return result
            if answer == ACCEPT:
                answer = CHOOSE_YOURSELF_ANSWER
            elif ask is not choose_yourself and answer != CHOOSE_YOURSELF_ANSWER:
                # Blank questions: the model decides those.
                answer += "\nFor anything not answered here: " + CHOOSE_YOURSELF_ANSWER
            round_["answer"] = answer
            note("revising the spec with the answer (model)")
            t0 = time.monotonic()
            try:
                revised, problems, revised_raw = revise_bench_spec(client, raw, source_text, questions, answer)
            except Exception as exc:  # noqa: BLE001
                result.stage, result.error = "model", f"{type(exc).__name__}: {exc}"[:400]
                return result
            result.timings["revise_s"] = result.timings.get("revise_s", 0.0) + time.monotonic() - t0
            if revised is None:
                result.stage, result.error = "spec", "revision unusable: " + "; ".join(p.message for p in problems)
                return result
            spec, raw = revised, revised_raw
            result.spec, result.problems, result.spec_raw = spec, problems, raw
        except ValueError as exc:  # the deck cannot hold the protocol (no magnet, no nest)
            result.stage, result.error = "skeleton", str(exc)
            return result
    if result.source is None:
        result.stage = "questions"
        return result

    from .lab_scope import load_lab_scope
    from .tools import AuthoringToolRegistry

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    registry = AuthoringToolRegistry(output_dir=out)
    registry.lab_scope = load_lab_scope("skills")
    t0 = time.monotonic()
    if 'wt.add_comment("TODO' in result.source:
        t0 = time.monotonic()
        result.source, filled, left = fill_custom_steps(client, result.source, str(out / "draft.py"), note)
        result.timings["custom_s"] = time.monotonic() - t0
        result.custom_steps = {"filled": filled, "left": left}
    note("compiling and simulating")
    result.gate = registry.compile_and_simulate(result.source)
    result.timings["gate_s"] = time.monotonic() - t0
    if not result.gate.get("success"):
        result.stage = "gate"
        result.error = " ".join(str(result.gate.get("failure_message", "")).split())[:600]
        return result
    if pending_requirements is not None or check_requirements:
        from .eval_rubric import build_worktable_from_source
        from .requirements import requirements_markdown, verify_all

        note("checking your instructions on the protocol")
        try:
            reqs, dispositions = (pending_requirements.result(timeout=1800)
                                  if pending_requirements is not None else ([], []))
            from .requirements import requirements_from_spec

            reqs = reqs + requirements_from_spec(result.spec)  # the document's steps, in order
            wt = build_worktable_from_source(result.source, str(out / "draft.py"))
            wt.simulate()
            verdicts = verify_all(wt, reqs)
            by_id = {v.id: v for v in verdicts}
            result.requirements = [
                {"id": r.id, "text": r.text, "kind": r.kind, "params": r.params,
                 "status": by_id[r.id].status, "evidence": by_id[r.id].evidence}
                for r in reqs
            ]
            result.requirements_markdown = requirements_markdown(reqs, verdicts, dispositions)
            # The checklist beside draft.py: the editor re-checks it on every save.
            from .requirements import save_requirements, sidecar_path

            save_requirements(sidecar_path(out / "draft.py"), reqs)
        except Exception as exc:  # noqa: BLE001 - the checklist failing is reported, not fatal
            result.requirements_markdown = f"Instruction check failed: {type(exc).__name__}: {exc}\n"
    if fluentcontrol:
        from .eval_rubric import build_worktable_from_source
        from .fc_feedback import check_in_fluentcontrol

        draft = out / "draft.py"
        draft.write_text(result.source, encoding="utf-8")
        xscr = out / "draft.xscr"
        build_worktable_from_source(result.source, str(draft)).compile(xscr)
        t0 = time.monotonic()
        note("checking in FluentControl (InfoPad)")
        result.fluentcontrol = check_in_fluentcontrol(xscr, source=result.source, source_file=str(draft))
        result.timings["fc_s"] = time.monotonic() - t0
    result.stage = "done"
    result.timings["total_s"] = time.monotonic() - started
    return result


def fill_custom_steps(client: Any, source: str, path: str = "<draft>",
                      progress: Callable[[str], None] | None = None) -> tuple[str, int, int]:
    """Let the model write DSL code for each step the skeleton left as a TODO.

    Each TODO comment is replaced like a Ctrl+I edit ("implement this step
    with the objects defined above"); the edit is kept only if it adds no new
    errors (build or simulation), otherwise the TODO stays. Returns
    ``(source, filled, left)``.
    """
    from ..copilot.analyzer import analyze_source
    from ..copilot.edit import _replace_lines, edit_region

    filled = left = 0
    tried: set[str] = set()
    while True:
        lines = source.splitlines()
        index = next((i for i, text in enumerate(lines, 1)
                      if text.strip().startswith('wt.add_comment("TODO') and text not in tried), None)
        if index is None:
            return source, filled, left
        tried.add(lines[index - 1])
        step_text = lines[index - 1].strip()[len('wt.add_comment("TODO'):].strip(' ")')
        if progress is not None:
            progress(f"writing code for a step the primitives do not cover: {step_text[:60]}")
        baseline = {(d.line, d.code) for d in analyze_source(source, path) if d.severity == "error"}
        try:
            result = edit_region(
                source, index, index,
                "Implement this protocol step in fluentvibe Python, replacing this TODO comment. Use the "
                "labware, tips and reagents already defined above (place new labware with wt.place only if "
                "needed); keep it to this one step. Step: " + step_text,
                client=client, path=path, revalidate=False,
            )
        except Exception:  # noqa: BLE001 - a failed call leaves the TODO
            left += 1
            continue
        if not result.new_text.strip():
            left += 1
            continue
        candidate = _replace_lines(lines, index, index, result.new_text)
        errors = [d for d in analyze_source(candidate, path) if d.severity == "error"]
        if any((d.line, d.code) not in baseline for d in errors) or 'add_comment("TODO' in result.new_text:
            left += 1
            continue
        source = candidate
        filled += 1
