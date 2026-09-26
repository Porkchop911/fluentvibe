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
) -> SpecPathResult:
    """Document -> spec -> (questions -> answers ->) skeleton -> gate (-> FluentControl)."""
    from .skeleton import DeckMismatch, OpenValues, build_skeleton, load_deck

    result = SpecPathResult(stage="spec")
    started = time.monotonic()
    context_parts = [f"Request: {request}"] if request else []
    if examples:
        from .spec_retrieval import retrieval_context

        similar = retrieval_context(source_text)
        if similar:
            context_parts.append(similar)
    spec, problems, raw = extract_bench_spec(client, source_text,
                                             extra_context="\n\n".join(context_parts) or None)
    result.timings["spec_s"] = time.monotonic() - started
    result.spec, result.problems, result.spec_raw = spec, problems, raw
    if spec is None:
        result.error = "; ".join(f"{p.where}: {p.message}" for p in problems)
        return result

    deck = load_deck(profile_dir)
    for _ in range(max_rounds + 1):
        try:
            result.source = build_skeleton(spec, copy.deepcopy(deck))
            break
        except (OpenValues, DeckMismatch) as exc:
            questions = list(getattr(exc, "questions", None) or [str(exc).replace("skeleton: ", "")])
            round_ = {"questions": questions, "answer": None}
            result.rounds.append(round_)
            if ask is None or len(result.rounds) > max_rounds:
                result.stage = "questions"
                return result
            answer = ask(questions)
            if answer is None:
                result.stage = "questions"
                return result
            round_["answer"] = answer
            t0 = time.monotonic()
            revised, problems, revised_raw = revise_bench_spec(client, raw, source_text, questions, answer)
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
    result.gate = registry.compile_and_simulate(result.source)
    result.timings["gate_s"] = time.monotonic() - t0
    if not result.gate.get("success"):
        result.stage = "gate"
        result.error = " ".join(str(result.gate.get("failure_message", "")).split())[:600]
        return result
    if fluentcontrol:
        from .eval_rubric import build_worktable_from_source
        from .fc_feedback import check_in_fluentcontrol

        draft = out / "draft.py"
        draft.write_text(result.source, encoding="utf-8")
        xscr = out / "draft.xscr"
        build_worktable_from_source(result.source, str(draft)).compile(xscr)
        t0 = time.monotonic()
        result.fluentcontrol = check_in_fluentcontrol(xscr, source=result.source, source_file=str(draft))
        result.timings["fc_s"] = time.monotonic() - t0
    result.stage = "done"
    result.timings["total_s"] = time.monotonic() - started
    return result
