"""fluentvibe language server (pygls).

Real-time, deterministic authoring support for FluentControl protocols:
- diagnostics as you type (build errors + simulator failures), debounced;
- quick-fixes (code actions);
- autocomplete (catalog names + API);
- signature help (parameter hints) and hover (signature + docstring).

Signature help, hover and completion are pure introspection — instant and safe.
Diagnostics execute the protocol's ``build_worktable()`` in-process (fast, since
the package is already imported) on a short debounce; the server only touches
files that import fluentvibe and define ``build_worktable()``.
"""

from __future__ import annotations

import logging
import threading

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from ..copilot.analyzer import analyze_source
from ..copilot.api_info import hover_at, signature_at
from ..copilot.complete import complete_at
from .convert import (
    code_actions_for,
    to_completion_items,
    to_hover,
    to_lsp_diagnostics,
    to_signature_help,
)

logger = logging.getLogger("fluentvibe.lsp")

# Wait this long after the last keystroke before re-analyzing.
_DEBOUNCE_S = 0.4


def _looks_like_protocol(source: str) -> bool:
    """Only analyze files that look like fluentvibe protocols, so the server
    stays quiet on ordinary Python files."""
    return "fluentvibe" in source and "build_worktable" in source


def analyze(source: str, path: str) -> list[dict]:
    """Run the headless analyzer in-process and return diagnostic dicts."""
    return [d.to_dict() for d in analyze_source(source, path)]


def create_server() -> LanguageServer:
    server = LanguageServer("fluentvibe-lsp", "v0.1")
    debounce: dict[str, threading.Timer] = {}

    def _validate(ls: LanguageServer, uri: str) -> None:
        doc = ls.workspace.get_text_document(uri)
        diagnostics: list[lsp.Diagnostic] = []
        if _looks_like_protocol(doc.source):
            try:
                diagnostics = to_lsp_diagnostics(analyze(doc.source, doc.path))
            except Exception:
                logger.exception("fluentvibe analysis failed for %s", uri)
                diagnostics = []
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
        )

    def _schedule(ls: LanguageServer, uri: str) -> None:
        existing = debounce.get(uri)
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(_DEBOUNCE_S, _validate, args=(ls, uri))
        timer.daemon = True
        debounce[uri] = timer
        timer.start()

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def _did_open(ls: LanguageServer, params: lsp.DidOpenTextDocumentParams) -> None:
        _validate(ls, params.text_document.uri)

    @server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
    def _did_save(ls: LanguageServer, params: lsp.DidSaveTextDocumentParams) -> None:
        _validate(ls, params.text_document.uri)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
    def _did_change(ls: LanguageServer, params: lsp.DidChangeTextDocumentParams) -> None:
        _schedule(ls, params.text_document.uri)

    @server.feature(lsp.TEXT_DOCUMENT_CODE_ACTION)
    def _code_action(
        ls: LanguageServer, params: lsp.CodeActionParams
    ) -> list[lsp.CodeAction]:
        return code_actions_for(params.text_document.uri, params.context.diagnostics)

    @server.feature(
        lsp.TEXT_DOCUMENT_COMPLETION,
        lsp.CompletionOptions(trigger_characters=[".", '"', "'"]),
    )
    def _completion(
        ls: LanguageServer, params: lsp.CompletionParams
    ) -> list[lsp.CompletionItem] | None:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        if not _looks_like_protocol(doc.source):
            return None
        completions = complete_at(doc.source, params.position.line, params.position.character)
        return to_completion_items(
            [c.to_dict() for c in completions],
            params.position.line,
            params.position.character,
        )

    @server.feature(
        lsp.TEXT_DOCUMENT_SIGNATURE_HELP,
        lsp.SignatureHelpOptions(trigger_characters=["(", ","]),
    )
    def _signature_help(
        ls: LanguageServer, params: lsp.SignatureHelpParams
    ) -> lsp.SignatureHelp | None:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        if not _looks_like_protocol(doc.source):
            return None
        info = signature_at(doc.source, params.position.line, params.position.character)
        return to_signature_help(info.to_dict() if info else None)

    @server.feature(lsp.TEXT_DOCUMENT_HOVER)
    def _hover(ls: LanguageServer, params: lsp.HoverParams) -> lsp.Hover | None:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        if not _looks_like_protocol(doc.source):
            return None
        info = hover_at(doc.source, params.position.line, params.position.character)
        return to_hover(info.to_dict() if info else None)

    @server.command("fluentvibe.inlineEdit")
    def _inline_edit(ls: LanguageServer, args: list) -> dict:
        from ..copilot.edit import edit_region

        params = args[0] if args else {}
        doc = ls.workspace.get_text_document(params["uri"])
        result = edit_region(
            doc.source,
            int(params["start_line"]),
            int(params["end_line"]),
            params.get("instruction", ""),
            path=doc.path,
        )
        return result.to_dict()

    return server


def main() -> None:
    """Start the language server over stdio (entry point for `fluentvibe lsp`)."""
    create_server().start_io()


if __name__ == "__main__":  # pragma: no cover
    main()
