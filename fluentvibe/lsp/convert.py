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
        # Carry the analyzer's quick-fixes so the code-action handler can build
        # edits without re-running analysis.
        data={"fixes": d.get("fixes") or []},
    )


def to_lsp_diagnostics(diags: list[dict[str, Any]]) -> list[lsp.Diagnostic]:
    return [to_lsp_diagnostic(d) for d in diags]


def _text_edit_for_fix(fix: dict[str, Any]) -> lsp.TextEdit | None:
    line = int(fix.get("line") or 0)
    if line < 1:
        return None
    text = fix.get("text") or ""
    kind = fix.get("kind")
    if kind == "insert_before":
        pos = lsp.Position(line=line - 1, character=0)
        return lsp.TextEdit(range=lsp.Range(start=pos, end=pos), new_text=text + "\n")
    if kind == "replace_line":
        return lsp.TextEdit(
            range=lsp.Range(
                start=lsp.Position(line=line - 1, character=0),
                end=lsp.Position(line=line, character=0),
            ),
            new_text=text + "\n",
        )
    return None


_COMPLETION_KIND = {
    "catalog": lsp.CompletionItemKind.Value,
    "method": lsp.CompletionItemKind.Method,
}


def to_completion_items(
    completions: list[dict[str, Any]], line: int, cursor_char: int
) -> list[lsp.CompletionItem]:
    """Map analyzer completions (dict form) to LSP items with precise edits."""
    items: list[lsp.CompletionItem] = []
    for c in completions:
        rng = lsp.Range(
            start=lsp.Position(line=line, character=int(c.get("replace_start") or 0)),
            end=lsp.Position(line=line, character=cursor_char),
        )
        items.append(
            lsp.CompletionItem(
                label=c["label"],
                kind=_COMPLETION_KIND.get(c.get("kind"), lsp.CompletionItemKind.Text),
                detail=c.get("detail") or None,
                text_edit=lsp.TextEdit(range=rng, new_text=c.get("insert_text") or c["label"]),
            )
        )
    return items


def code_actions_for(uri: str, diagnostics: list[lsp.Diagnostic]) -> list[lsp.CodeAction]:
    """Build quick-fix code actions from diagnostics carrying analyzer fix data."""
    actions: list[lsp.CodeAction] = []
    for diag in diagnostics:
        data = getattr(diag, "data", None) or {}
        for fix in data.get("fixes", []):
            edit = _text_edit_for_fix(fix)
            if edit is None:
                continue
            actions.append(
                lsp.CodeAction(
                    title=fix.get("title", "Apply fix"),
                    kind=lsp.CodeActionKind.QuickFix,
                    diagnostics=[diag],
                    edit=lsp.WorkspaceEdit(changes={uri: [edit]}),
                )
            )
    return actions
