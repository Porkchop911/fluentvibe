"""Convert headless analyzer diagnostics to LSP diagnostics.

Operates on the plain-dict shape emitted by ``Diagnostic.to_dict()`` (and by
``fluentvibe check --json``), so it works on subprocess output without importing
the analyzer. Pure and unit-testable.
"""

from __future__ import annotations

from typing import Any

from lsprotocol import types as lsp

_SEVERITY = {
    "error": lsp.DiagnosticSeverity.Error,
    "warning": lsp.DiagnosticSeverity.Warning,
    "info": lsp.DiagnosticSeverity.Information,
}

# Analyzer lines/cols are 1-based; LSP is 0-based. Without the document text we
# don't know a line's length, so we underline to a large column and let the
# editor clamp to the real end of line.
_END_OF_LINE = 10_000


def to_lsp_diagnostic(d: dict[str, Any]) -> lsp.Diagnostic:
    line0 = max(int(d.get("line") or 1) - 1, 0)
    start_char = max(int(d["col"]) - 1, 0) if d.get("col") else 0
    end_line0 = max(int(d.get("end_line") or (line0 + 1)) - 1, line0)
    message = d.get("message") or ""
    hint = d.get("hint")
    if hint:
        message = f"{message}\n\nhint: {hint}"
    return lsp.Diagnostic(
        range=lsp.Range(
            start=lsp.Position(line=line0, character=start_char),
            end=lsp.Position(line=end_line0, character=_END_OF_LINE),
        ),
        message=message,
        severity=_SEVERITY.get(d.get("severity", "error"), lsp.DiagnosticSeverity.Error),
        code=d.get("code"),
        source="fluentvibe",
    )


def to_lsp_diagnostics(diags: list[dict[str, Any]]) -> list[lsp.Diagnostic]:
    return [to_lsp_diagnostic(d) for d in diags]
