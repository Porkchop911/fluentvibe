"""Deterministic quick-fixes for analyzer diagnostics.

Given a simulate failure and the protocol source, compute concrete, mechanical
edits a user can apply with one click (LSP code actions) or read in the CLI.
Pure and line-based: each fix either inserts a line before the failing line or
replaces it. No LLM, no guessing beyond what the failure category guarantees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Receiver chain before a method call, e.g. "head" in `head.aspirate(...)` or
# "wt.mca96" in `wt.mca96.pick_up(tips)`.
_RECEIVER_RE = re.compile(r"([A-Za-z_][\w.]*)\.[A-Za-z_]\w*\(")


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


def compute_fixes(code: str, line: int, source: str, lines: list[str]) -> list[Fix]:
    """Return quick-fixes for a diagnostic of category ``code`` at ``line``.

    ``source`` is the diagnostic's source ("build" | "simulate"); ``lines`` is the
    protocol split into lines. Only simulate-category fixes are offered today.
    """
    if source != "simulate":
        return []
    if not (1 <= line <= len(lines)):
        return []
    line_text = lines[line - 1]
    indent = _indent_of(line_text)
    receiver = _receiver(line_text)

    if code == "adapter_state" and receiver:
        return [
            Fix(
                title=f"Insert {receiver}.mount_adapter() before this line",
                kind="insert_before",
                line=line,
                text=f"{indent}{receiver}.mount_adapter()",
            )
        ]
    if code == "tip_state" and receiver:
        return [
            Fix(
                title=f"Insert {receiver}.pick_up(...) before this line",
                kind="insert_before",
                line=line,
                text=f"{indent}{receiver}.pick_up(...)  # TODO: pass the tip box",
            )
        ]
    return []
