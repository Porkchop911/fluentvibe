"""Deterministic quick-fixes for analyzer diagnostics.

Given a simulate failure and the protocol source, compute concrete, mechanical
edits a user can apply with one click (LSP code actions) or read in the CLI.
Pure and line-based: each fix either inserts a line before the failing line or
replaces it. No LLM, no guessing beyond what the failure category guarantees.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

# Receiver chain before a method call, e.g. "head" in `head.aspirate(...)` or
# "wt.mca96" in `wt.mca96.pick_up(tips)`.
_RECEIVER_RE = re.compile(r"([A-Za-z_][\w.]*)\.[A-Za-z_]\w*\(")
# Receiver + method, e.g. ("head", "aspirat") in `head.aspirat(src, 20)`.
_CALL_RE = re.compile(r"([A-Za-z_][\w.]*)\.([A-Za-z_]\w*)\s*\(")
# The runtime variable named in a MissingSimValueError message.
_RUNTIME_VAR_RE = re.compile(r"runtime variable ['\"]([^'\"]+)['\"]")


@dataclass
class Fix:
    """A single mechanical edit offered for a diagnostic."""

    title: str
    kind: str  # "insert_before" | "replace_line"
    line: int  # 1-based line the edit targets
    text: str  # the line to insert/replace with (indentation included, no newline)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "kind": self.kind, "line": self.line, "text": self.text}


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _receiver(line: str) -> str | None:
    m = _RECEIVER_RE.search(line)
    return m.group(1) if m else None


def compute_fixes(
    code: str, line: int, source: str, lines: list[str], message: str = ""
) -> list[Fix]:
    """Return quick-fixes for a diagnostic of category ``code`` at ``line``.

    ``source`` is the diagnostic's source ("build" | "simulate"); ``lines`` is the
    protocol split into lines; ``message`` is the diagnostic message (used to recover
    a name, e.g. the unset runtime variable). Each handler picks which sources apply.
    """
    if not (1 <= line <= len(lines)):
        return []
    line_text = lines[line - 1]
    indent = _indent_of(line_text)
    receiver = _receiver(line_text)

    # State fixes only make sense for simulator failures (the call itself is valid).
    if source == "simulate" and code == "adapter_state" and receiver:
        return [
            Fix(
                title=f"Insert {receiver}.mount_adapter() before this line",
                kind="insert_before",
                line=line,
                text=f"{indent}{receiver}.mount_adapter()",
            )
        ]
    if source == "simulate" and code == "tip_state" and receiver:
        return [
            Fix(
                title=f"Insert {receiver}.pick_up(...) before this line",
                kind="insert_before",
                line=line,
                text=f"{indent}{receiver}.pick_up(...)  # TODO: pass the tip box",
            )
        ]
    # A misspelled API method is usually a build-time AttributeError, but tolerate
    # either source — the fix is the same mechanical rename.
    if code == "missing_method":
        return _did_you_mean_fixes(line, line_text)
    if source == "simulate" and code == "runtime_variable":
        return _set_sim_value_fixes(line, indent, message)
    return []


def _did_you_mean_fixes(line: int, line_text: str) -> list[Fix]:
    """Offer to rename a typo'd method to the closest real API method."""
    from .complete import _class_for_receiver

    m = _CALL_RE.search(line_text)
    if m is None:
        return []
    receiver, method = m.group(1), m.group(2)
    cls = _class_for_receiver(receiver)
    if cls is None:
        return []
    api = [n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n, None))]
    matches = difflib.get_close_matches(method, api, n=1, cutoff=0.6)
    if not matches or matches[0] == method:
        return []
    suggestion = matches[0]
    fixed = line_text.replace(f"{receiver}.{method}(", f"{receiver}.{suggestion}(", 1)
    return [
        Fix(
            title=f"Did you mean {receiver}.{suggestion}()?",
            kind="replace_line",
            line=line,
            text=fixed,
        )
    ]


def _set_sim_value_fixes(line: int, indent: str, message: str) -> list[Fix]:
    """Offer to seed an unset runtime variable with wt.set_sim_value(...)."""
    m = _RUNTIME_VAR_RE.search(message)
    if m is None:
        return []
    name = m.group(1)
    return [
        Fix(
            title=f'Insert wt.set_sim_value("{name}", ...) before this line',
            kind="insert_before",
            line=line,
            text=f'{indent}wt.set_sim_value("{name}", ...)  # TODO: set sim value',
        )
    ]
