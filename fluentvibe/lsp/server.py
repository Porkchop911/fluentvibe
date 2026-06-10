"""fluentvibe language server (pygls).

Publishes diagnostics for fluentvibe protocol files on open and save. Analysis
runs in an isolated subprocess (``python -m fluentvibe check --json``) with a
timeout, so executing the protocol's ``build_worktable()`` can never hang or
compromise the editor.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from ..copilot.complete import complete_at
from .convert import code_actions_for, to_completion_items, to_lsp_diagnostics

logger = logging.getLogger("fluentvibe.lsp")

_ANALYSIS_TIMEOUT_S = 30


def _looks_like_protocol(source: str) -> bool:
    """Only analyze files that look like fluentvibe protocols.

    Keeps the server quiet on ordinary Python files: requires a fluentvibe
    import and the ``build_worktable()`` authoring contract.
    """
    return "fluentvibe" in source and "build_worktable" in source


def _run_analysis(path: str) -> list[dict]:
    """Run ``fluentvibe check --json`` on ``path`` in an isolated subprocess."""
    env = dict(os.environ)
    env.setdefault("FLUENTVIBE_NO_AUTO_REBUILD", "1")
    proc = subprocess.run(
        [sys.executable, "-m", "fluentvibe", "check", path, "--json"],
        capture_output=True,
        text=True,
        timeout=_ANALYSIS_TIMEOUT_S,
        env=env,
    )
    out = (proc.stdout or "").strip()
    if not out:
        return []
    return json.loads(out)


def create_server() -> LanguageServer:
    server = LanguageServer("fluentvibe-lsp", "v0.1")

    def _validate(ls: LanguageServer, uri: str) -> None:
        doc = ls.workspace.get_text_document(uri)
        diagnostics: list[lsp.Diagnostic] = []
        if _looks_like_protocol(doc.source):
            try:
                diagnostics = to_lsp_diagnostics(_run_analysis(doc.path))
            except subprocess.TimeoutExpired:
                diagnostics = [_whole_file_error("Analysis timed out.")]
            except Exception:
                logger.exception("fluentvibe analysis failed for %s", uri)
                diagnostics = []
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
        )

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def _did_open(ls: LanguageServer, params: lsp.DidOpenTextDocumentParams) -> None:
        _validate(ls, params.text_document.uri)

    @server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
    def _did_save(ls: LanguageServer, params: lsp.DidSaveTextDocumentParams) -> None:
        _validate(ls, params.text_document.uri)

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


def _whole_file_error(message: str) -> lsp.Diagnostic:
    return lsp.Diagnostic(
        range=lsp.Range(
            start=lsp.Position(line=0, character=0),
            end=lsp.Position(line=0, character=0),
        ),
        message=message,
        severity=lsp.DiagnosticSeverity.Warning,
        source="fluentvibe",
    )


def main() -> None:
    """Start the language server over stdio (entry point for `fluentvibe lsp`)."""
    create_server().start_io()


if __name__ == "__main__":  # pragma: no cover
    main()
