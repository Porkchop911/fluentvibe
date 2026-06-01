"""Markdown-only evaluator for AMPure live authoring traces."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOOKUP_TOOLS = {"search_labware", "get_labware"}
OBJECT_DRAFT_NUDGE = "call present_object_draft"
FCA_PATTERNS = re.compile(r"\b(fca\s*96|fca96|fca\s*tip|fca.*labware|fca.*box)\b", re.I)
ALL_WELLS_PATTERN = re.compile(r"\b(all\s+96|96\s+wells|a1\s*[:\-]\s*h12)\b", re.I)


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = ""


def evaluate_ampure_reasoning_runs(root: Path) -> dict[str, Any]:
    """Evaluate every ``run_*`` directory under *root* from Markdown artifacts.

    The model trace JSONL files are intentionally not opened here. This reads
    ``*.readable.md`` files plus ``session_summary.md`` when present.
    """

    run_dirs = sorted(path for path in root.glob("run_*") if path.is_dir())
    runs = [_evaluate_run(run_dir) for run_dir in run_dirs]
    issue_frequency = Counter(
        issue for run in runs for issue in run["issues"]
    )
    tool_frequency = Counter()
    repeated_patterns = Counter()
    for run in runs:
        tool_frequency.update(run["tool_histogram"])
        for item in run["repeated_lookup_queries"]:
            repeated_patterns[item["query"]] += item["count"]

    summary = {
        "root": str(root),
        "run_count": len(runs),
        "runs": runs,
        "cross_run": {
            "issue_frequency": dict(sorted(issue_frequency.items())),
            "tool_histogram": dict(sorted(tool_frequency.items())),
            "repeated_lookup_patterns": dict(repeated_patterns.most_common()),
            "recommended_priority_order": _recommended_priorities(issue_frequency),
        },
    }
    return summary


def write_ampure_reasoning_reports(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    summary = evaluate_ampure_reasoning_runs(root)
    summary_path = root / "summary.json"
    report_path = root / "report.md"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    report_path.write_text(render_ampure_reasoning_report(summary), encoding="utf-8")
    return summary_path, report_path


def render_ampure_reasoning_report(summary: dict[str, Any]) -> str:
    runs = summary.get("runs") or []
    cross = summary.get("cross_run") or {}
    lines = [
        "# AMPure Reasoning Evaluation",
        "",
        f"- runs evaluated: `{summary.get('run_count', 0)}`",
        f"- issue frequency: `{json.dumps(cross.get('issue_frequency') or {}, sort_keys=True)}`",
        "",
        "## Recommended Fix Priority",
        "",
    ]
    priorities = cross.get("recommended_priority_order") or []
    if priorities:
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(priorities, start=1))
    else:
        lines.append("No recurring issue pattern was detected in the Markdown traces.")

    lines.extend(["", "## Per-Run Findings", ""])
    for run in runs:
        lines.append(f"### {run['run_id']}")
        lines.append("")
        lines.append(f"- terminal status: `{run.get('terminal_status') or 'unknown'}`")
        lines.append(f"- rounds: `{run.get('round_count', 0)}`")
        lines.append(f"- model iterations: `{run.get('model_iteration_count', 0)}`")
        lines.append(f"- tool histogram: `{json.dumps(run.get('tool_histogram') or {}, sort_keys=True)}`")
        if run.get("lookup_guard_events"):
            lines.append(f"- lookup guards: `{json.dumps(run.get('lookup_guard_events') or [], sort_keys=True)}`")
        lines.append(f"- issues: `{', '.join(run.get('issues') or []) or 'none'}`")
        if run.get("first_drift"):
            lines.append(f"- first drift: {run['first_drift']}")
        if run.get("final_failure_or_stall_reason"):
            lines.append(f"- final failure/stall: {run['final_failure_or_stall_reason']}")
        excerpts = run.get("representative_excerpts") or []
        if excerpts:
            lines.append("")
            lines.append("Representative excerpts:")
            for excerpt in excerpts[:4]:
                lines.append(f"- `{excerpt}`")
        lines.append("")

    lines.extend([
        "## Fix Assessment",
        "",
        "- Hide `ground_in_parallel` in fast mode when `blocked_fast_mode_tool` appears; blocking it after exposure still spends a model turn.",
        "- Rename readable trace headings so graph-normalized state is not labeled as exact provider payload when `trace_confusion` appears.",
        "- Add a repeated lookup guard when `repeated_lookup_loop` appears, especially for FCA/tip catalog searches.",
        "- Strengthen object-draft pressure when `ignored_stop_nudge` or `missing_object_draft` appears after sufficient grounding.",
        "- Prefer retrievable AMPure/FCA rules or deterministic prefetch when `bad_intent_target` or `fca_confusion` appears.",
        "",
    ])
    return "\n".join(lines)


def _evaluate_run(run_dir: Path) -> dict[str, Any]:
    readable_paths = sorted((run_dir / "model_traces").glob("*.readable.md"))
    summary_path = run_dir / "session_summary.md"
    texts = []
    paths = []
    for path in readable_paths:
        texts.append(path.read_text(encoding="utf-8"))
        paths.append(str(path))
    if summary_path.exists():
        texts.append(summary_path.read_text(encoding="utf-8"))
        paths.append(str(summary_path))
    combined = "\n".join(texts)
    readable_tool_calls = _parse_tool_calls("\n".join(texts[:-1] if summary_path.exists() else texts))
    summary_tool_calls = _parse_session_summary_calls(
        summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    )
    # Session summaries list newly executed tool calls once per round, so they
    # are the least duplicated source for metrics. Readable traces remain the
    # source for requested-but-blocked calls and provider-exposed reasoning.
    tool_calls = summary_tool_calls or readable_tool_calls
    classification_calls = _dedupe_tool_calls([*tool_calls, *readable_tool_calls])
    tool_histogram = Counter(call.name for call in tool_calls)
    repeated = _repeated_lookup_queries(tool_calls)
    lookup_guard_events = _lookup_guard_events(combined)
    issues = _classify_issues(combined, classification_calls, repeated, lookup_guard_events)
    terminal = _extract_terminal_status(combined)
    round_count = _extract_round_count(combined)
    model_iterations = _extract_model_iteration_count(combined)

    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "terminal_status": terminal,
        "round_count": round_count,
        "model_iteration_count": model_iterations,
        "model_iterations_per_user_turn": _extract_iterations_per_round(combined),
        "tool_histogram": dict(sorted(tool_histogram.items())),
        "repeated_lookup_queries": repeated,
        "lookup_guard_events": lookup_guard_events,
        "lookup_guard_active": bool(lookup_guard_events),
        "has_object_draft": bool(tool_histogram.get("present_object_draft")),
        "has_functional_group_plan": bool(tool_histogram.get("present_functional_group_plan")),
        "has_simulation": bool(tool_histogram.get("simulate_python_draft")),
        "has_compile": bool(tool_histogram.get("compile_and_simulate")),
        "issues": issues,
        "first_drift": _first_drift(issues, combined),
        "final_failure_or_stall_reason": _failure_reason(combined, terminal),
        "representative_excerpts": _representative_excerpts(combined, issues),
        "trace_paths": paths,
    }


def _parse_tool_calls(text: str) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []
    pattern = re.compile(r"^####\s+\d+\.\s+`([^`]+)`\s*$", re.M)
    matches = list(pattern.finditer(text))
    for idx, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[match.end():end]
        args = _first_fenced_json(block)
        calls.append(ParsedToolCall(name=name, arguments=args, source=_one_line(block)))
    calls.extend(_parse_session_summary_calls(text))
    return calls


def _dedupe_tool_calls(calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    seen: set[tuple[str, str]] = set()
    unique: list[ParsedToolCall] = []
    for call in calls:
        key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        unique.append(call)
    return unique


def _parse_session_summary_calls(text: str) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []
    for match in re.finditer(r"^-\s+`([^`]+)`\s+args=(.+?)(?:\s+ok=|\s+category=|$)", text, re.M):
        name = match.group(1)
        args_text = match.group(2).strip()
        try:
            args = json.loads(args_text)
        except json.JSONDecodeError:
            args = {}
        calls.append(ParsedToolCall(name=name, arguments=args, source=_one_line(match.group(0))))
    return calls


def _first_fenced_json(text: str) -> dict[str, Any]:
    match = re.search(r"```(?:text|json)?\s*(.*?)```", text, re.S)
    if not match:
        return {}
    payload = match.group(1).strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _classify_issues(
    text: str,
    tool_calls: list[ParsedToolCall],
    repeated: list[dict[str, Any]],
    lookup_guard_events: list[dict[str, Any]],
) -> list[str]:
    names = [call.name for call in tool_calls]
    lowered = text.lower()
    issues: list[str] = []
    if "ground_in_parallel" in names and "fast_mode_subagent_disabled" in lowered:
        issues.append("blocked_fast_mode_tool")
    if repeated and not lookup_guard_events:
        issues.append("repeated_lookup_loop")
    if _ignored_object_draft_nudge(text, tool_calls):
        issues.append("ignored_stop_nudge")
    if "present_object_draft" not in names and _hit_limit_or_terminal_stall(lowered):
        issues.append("missing_object_draft")
    if _bad_intent_target(tool_calls, text):
        issues.append("bad_intent_target")
    if _fca_confusion(tool_calls, text):
        issues.append("fca_confusion")
    if "### Messages Sent" in text:
        issues.append("trace_confusion")
    return issues


def _repeated_lookup_queries(tool_calls: list[ParsedToolCall]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for call in tool_calls:
        if call.name not in LOOKUP_TOOLS:
            continue
        query = str(call.arguments.get("query") or call.arguments.get("name") or "").strip()
        if not query:
            continue
        buckets[(call.name, _normalize_query(query))].append(query)
    repeated = []
    for (tool, normalized), values in sorted(buckets.items()):
        if len(values) >= 2:
            repeated.append({
                "tool": tool,
                "query": normalized,
                "count": len(values),
                "examples": sorted(set(values))[:5],
            })
    return repeated


def _lookup_guard_events(text: str) -> list[dict[str, Any]]:
    categories = (
        "repeated_lookup_guard",
        "profile_grounding_satisfied",
        "grounding_satisfied_present_object_draft_required",
    )
    events: list[dict[str, Any]] = []
    for category in categories:
        count = len(re.findall(re.escape(category), text))
        if count:
            events.append({"category": category, "count": count})
    return events


def _ignored_object_draft_nudge(text: str, tool_calls: list[ParsedToolCall]) -> bool:
    nudge_pos = text.lower().find(OBJECT_DRAFT_NUDGE)
    if nudge_pos < 0:
        return False
    for call in tool_calls:
        if call.name in LOOKUP_TOOLS and text.find(call.source) > nudge_pos:
            return True
    after = text[nudge_pos:].lower()
    return "search_labware" in after or "get_labware" in after


def _bad_intent_target(tool_calls: list[ParsedToolCall], text: str) -> bool:
    for call in tool_calls:
        if call.name != "declare_intent":
            continue
        volume = call.arguments.get("target_volume_ul")
        wells = call.arguments.get("destination_wells")
        try:
            volume_float = float(volume)
        except (TypeError, ValueError):
            volume_float = None
        partial_wells = isinstance(wells, list) and 0 < len(wells) < 96
        if volume_float in {36.0, 56.0} or partial_wells:
            return True
    return "declare_intent" in text and re.search(r"target_volume_ul['\"=: ]+36", text) is not None


def _fca_confusion(tool_calls: list[ParsedToolCall], text: str) -> bool:
    if re.search(r"\b(FCA_TipBox|TBD\s*-\s*FCA|FCA96)\b", text, re.I):
        return True
    for call in tool_calls:
        if call.name in LOOKUP_TOOLS and FCA_PATTERNS.search(json.dumps(call.arguments)):
            return True
    return False


def _hit_limit_or_terminal_stall(lowered: str) -> bool:
    needles = (
        "retry_budget_exhausted",
        "iteration budget",
        "tool-call budget",
        "max rounds",
        "approval_required",
        "clarification_required",
        "failure",
    )
    return any(needle in lowered for needle in needles)


def _extract_terminal_status(text: str) -> str | None:
    patterns = [
        r"terminal status:\s*`?([a-z_]+)`?",
        r"status:\s*`?([a-z_]+)`?",
        r'"status"\s*:\s*"([a-z_]+)"',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.I)
        if matches:
            return matches[-1].lower()
    return None


def _extract_round_count(text: str) -> int:
    rounds = [int(value) for value in re.findall(r"^## Round\s+(\d+)", text, re.M)]
    return max(rounds) if rounds else 0


def _extract_model_iteration_count(text: str) -> int:
    iterations = [int(value) for value in re.findall(r"- iteration:\s*`?(\d+)`?", text)]
    return max(iterations) if iterations else len(re.findall(r"## Model Turn", text))


def _extract_iterations_per_round(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    current = None
    count = 0
    for line in text.splitlines():
        match = re.match(r"## Round\s+(\d+)", line)
        if match:
            if current is not None:
                result[current] = count
            current = match.group(1)
            count = 0
        elif current is not None and line.startswith("- model_iterations:"):
            try:
                count = int(re.findall(r"\d+", line)[0])
            except IndexError:
                count = 0
    if current is not None:
        result[current] = count
    return result


def _first_drift(issues: list[str], text: str) -> str | None:
    for issue in issues:
        if issue == "blocked_fast_mode_tool":
            return "Model attempted `ground_in_parallel` despite fast-mode blocking."
        if issue == "ignored_stop_nudge":
            return "Model continued lookup calls after the object-draft nudge."
        if issue == "repeated_lookup_loop":
            return "Model repeated catalog lookup queries without visible progress."
        if issue == "bad_intent_target":
            return "Model declared an AMPure intermediate volume or partial wells as the target intent."
        if issue == "fca_confusion":
            return "Model pursued FCA tip/labware patterns for the AMPure workflow."
    if "failure" in text.lower():
        return "Run ended in failure before a more specific drift was classified."
    return None


def _failure_reason(text: str, terminal: str | None) -> str | None:
    if terminal == "success":
        return None
    for pattern in (
        r"failure_message:\s*(.+)",
        r"final failure/stall:\s*(.+)",
        r"message:\s*([^`\n].+)",
    ):
        matches = re.findall(pattern, text, re.I)
        if matches:
            return _one_line(matches[-1], limit=300)
    return terminal


def _representative_excerpts(text: str, issues: list[str]) -> list[str]:
    targets = {
        "blocked_fast_mode_tool": "ground_in_parallel",
        "repeated_lookup_loop": "search_labware",
        "ignored_stop_nudge": OBJECT_DRAFT_NUDGE,
        "missing_object_draft": "present_object_draft",
        "bad_intent_target": "declare_intent",
        "fca_confusion": "FCA",
        "trace_confusion": "Messages Sent",
    }
    excerpts = []
    lines = text.splitlines()
    for issue in issues:
        needle = targets.get(issue)
        if not needle:
            continue
        for idx, line in enumerate(lines):
            if needle.lower() in line.lower():
                context = " ".join(lines[max(0, idx - 1): idx + 2])
                excerpts.append(_one_line(context, limit=260))
                break
    return excerpts


def _recommended_priorities(issue_frequency: Counter[str]) -> list[str]:
    order = [
        ("blocked_fast_mode_tool", "In fast mode, remove `ground_in_parallel` from the exposed tool schema."),
        ("trace_confusion", "Separate readable trace headings for `graph_state_messages` and exact `provider_payload`."),
        ("repeated_lookup_loop", "Add a repeated-lookup guard for failed or near-duplicate catalog searches."),
        ("ignored_stop_nudge", "After sufficient grounding, block more search and require `present_object_draft`."),
        ("missing_object_draft", "Make object-draft pressure terminal when the model refuses the checkpoint path."),
        ("bad_intent_target", "Clarify AMPure intent target volume semantics through retrievable rules or prefetch."),
        ("fca_confusion", "Provide AMPure/FCA guidance through rules or deterministic prefetch, not the global prompt."),
    ]
    return [text for issue, text in order if issue_frequency.get(issue, 0)]


def _normalize_query(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _one_line(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value).split())
    return text[:limit] + ("..." if len(text) > limit else "")
