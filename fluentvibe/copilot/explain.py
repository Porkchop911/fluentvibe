"""LLM-backed 'explain this error' for analyzer diagnostics.

Turns a structured diagnostic (the deterministic analyzer already did the hard
work — category, message, repair hint, exact line) into a plain-language
explanation via an OpenAI-compatible chat model. The model is grounded by the
structured failure, so this stays cheap and on-topic.

The chat client is injectable so this is unit-testable offline; the default uses
the env-configured endpoint (FLUENTVIBE_LM_ENDPOINT). See docs/copilot-design.md.
"""

from __future__ import annotations

from typing import Any, Optional

_SYSTEM_PROMPT = (
    "You are a Tecan FluentControl lab-automation assistant helping a scientist "
    "author a liquid-handling protocol in the fluentvibe Python API. You are given "
    "a diagnostic from the protocol analyzer (a real build error or a physical "
    "simulator failure) and the code around it. Explain, in 2-4 plain sentences, "
    "what went wrong and how to fix it. Be concrete and reference the actual "
    "labware/variables in the snippet. Do not invent API that isn't shown. Do not "
    "repeat the raw error verbatim."
)

# Lines of context to show on each side of the failing line.
_CONTEXT = 4


def _snippet(source: str, line: int) -> str:
    lines = source.splitlines()
    if not (1 <= line <= len(lines)):
        return source.strip()
    start = max(line - _CONTEXT, 1)
    end = min(line + _CONTEXT, len(lines))
    out = []
    for i in range(start, end + 1):
        marker = ">>" if i == line else "  "
        out.append(f"{marker} {i:4d} | {lines[i - 1]}")
    return "\n".join(out)


def build_messages(diagnostic: dict[str, Any], source: str) -> list[dict[str, str]]:
    """Build the chat messages for explaining ``diagnostic`` in ``source``."""
    parts = [
        f"Diagnostic category: {diagnostic.get('code')}",
        f"Severity: {diagnostic.get('severity')}",
        f"Message: {diagnostic.get('message')}",
    ]
    hint = diagnostic.get("hint")
    if hint:
        parts.append(f"Analyzer repair hint: {hint}")
    line = int(diagnostic.get("line") or 1)
    parts.append(f"\nCode around line {line} (>> marks the failing line):\n")
    parts.append(_snippet(source, line))
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]


def explain_diagnostic(
    diagnostic: dict[str, Any],
    source: str,
    *,
    client: Optional[Any] = None,
) -> str:
    """Return a plain-language explanation of ``diagnostic``.

    ``client`` is anything with ``complete(*, messages, tools) -> {"content": str}``
    (the authoring ``LMStudioChatClient`` contract). Defaults to a client on the
    env-configured endpoint.
    """
    if client is None:
        from ..authoring.lm_client import LMStudioChatClient

        client = LMStudioChatClient()
    message = client.complete(messages=build_messages(diagnostic, source), tools=[])
    return (message.get("content") or "").strip()
