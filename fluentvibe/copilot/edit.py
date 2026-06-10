"""LLM 'edit this region with an instruction' for protocol authoring.

The Continue-style inline edit: select some lines, say what you want ("add a
return-tips step", "use 200 uL tips"), and the model rewrites just that region.
The rewrite is then **re-validated** through the analyzer, so the caller can warn
before applying a suggestion that doesn't build or simulate.

The chat client is injectable (offline-testable); the default uses the
env-configured endpoint. See docs/copilot-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .analyzer import analyze_source

_SYSTEM_PROMPT = (
    "You edit Tecan FluentControl liquid-handling protocols written in the "
    "fluentvibe Python API. You are given the whole file for context, a selected "
    "region, and an instruction. Rewrite ONLY the selected region to satisfy the "
    "instruction. Preserve the surrounding indentation. Use only fluentvibe API "
    "that already appears in the file or is clearly analogous. Return ONLY the "
    "replacement Python for the selected region — no explanations, no markdown "
    "code fences."
)

_FENCE_RE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)


@dataclass
class EditResult:
    new_text: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def introduces_errors(self) -> bool:
        return any(d.get("severity") == "error" for d in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_text": self.new_text,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "diagnostics": self.diagnostics,
            "introduces_errors": self.introduces_errors,
        }


def _strip_fences(text: str) -> str:
    # Detect fences on a whitespace-stripped copy, but return the inner content
    # verbatim so the code's own indentation is preserved.
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1)
    return text.strip("\n")


def _build_messages(source: str, selection: str, instruction: str) -> list[dict[str, str]]:
    user = (
        f"Instruction: {instruction}\n\n"
        f"Full file:\n```python\n{source}\n```\n\n"
        f"Selected region to rewrite:\n```python\n{selection}\n```"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _replace_lines(lines: list[str], start_line: int, end_line: int, new_text: str) -> str:
    head = lines[: start_line - 1]
    tail = lines[end_line:]
    return "\n".join([*head, *new_text.splitlines(), *tail])


def edit_region(
    source: str,
    start_line: int,
    end_line: int,
    instruction: str,
    *,
    client: Optional[Any] = None,
    path: str = "<protocol>",
    revalidate: bool = True,
) -> EditResult:
    """Rewrite lines ``start_line``..``end_line`` (1-based, inclusive) per ``instruction``.

    Returns the replacement text plus, when ``revalidate`` is set, the analyzer
    diagnostics for the file *after* applying the edit — so the caller can refuse
    or warn on a regression.
    """
    lines = source.splitlines()
    start_line = max(1, start_line)
    end_line = min(len(lines), max(start_line, end_line))
    selection = "\n".join(lines[start_line - 1 : end_line])

    if client is None:
        from ..authoring.lm_client import LMStudioChatClient

        client = LMStudioChatClient()
    message = client.complete(
        messages=_build_messages(source, selection, instruction), tools=[]
    )
    new_text = _strip_fences(message.get("content") or "")

    diagnostics: list[dict[str, Any]] = []
    if revalidate and new_text:
        edited = _replace_lines(lines, start_line, end_line, new_text)
        diagnostics = [d.to_dict() for d in analyze_source(edited, path)]

    return EditResult(
        new_text=new_text,
        start_line=start_line,
        end_line=end_line,
        diagnostics=diagnostics,
    )
