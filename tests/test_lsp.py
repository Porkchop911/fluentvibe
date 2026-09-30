"""Copilot Phase 2: the LSP layer over the headless analyzer.

Covers the pure dict->LSP conversion, the protocol heuristic, server
construction, and the real subprocess-isolated analysis path. See
docs/copilot-design.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("pygls")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lsprotocol import types as lsp  # noqa: E402

from fluentvibe.lsp import server as lsp_server  # noqa: E402
from fluentvibe.lsp.convert import (  # noqa: E402
    code_actions_for,
    to_completion_items,
    to_lsp_diagnostic,
    to_lsp_diagnostics,
)

_BAD_PROTOCOL = '''\
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable(name="Short")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    src.fill_all(Reagent("Buffer"), 5.0)

    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    head.dispense(dst, 20.0, liquid_class="Water Free Single")
    return wt
'''


def _diag_dict(**over) -> dict:
    base = {
        "file": "x.py",
        "line": 16,
        "end_line": None,
        "col": None,
        "severity": "error",
        "code": "source_volume_short",
        "message": "Aspirate: well 'A1' short by 15.00 uL",
        "source": "simulate",
        "hint": "Increase the initial fill volume.",
        "repair_options": ["increase_source_initial_volume"],
        "fixes": [],
    }
    base.update(over)
    return base


_MOUNT_FIX = {
    "title": "Insert head.mount_adapter() before this line",
    "kind": "insert_before",
    "line": 15,
    "text": "    head.mount_adapter()",
}


def test_convert_maps_to_zero_based_range_and_severity() -> None:
    d = to_lsp_diagnostic(_diag_dict())
    assert d.range.start.line == 15  # 1-based 16 -> 0-based 15
    assert d.range.start.character == 0
    assert d.severity == lsp.DiagnosticSeverity.Error
    assert d.code == "source_volume_short"
    assert d.source == "fluentvibe"
    assert "short by 15.00" in d.message
    assert "hint: Increase the initial fill volume." in d.message


def test_convert_severity_warning_and_info() -> None:
    assert to_lsp_diagnostic(_diag_dict(severity="warning")).severity == lsp.DiagnosticSeverity.Warning
    assert to_lsp_diagnostic(_diag_dict(severity="info")).severity == lsp.DiagnosticSeverity.Information


def test_convert_uses_col_when_present() -> None:
    d = to_lsp_diagnostic(_diag_dict(col=5))
    assert d.range.start.character == 4  # 1-based 5 -> 0-based 4


def test_to_lsp_diagnostics_list() -> None:
    out = to_lsp_diagnostics([_diag_dict(), _diag_dict(severity="warning")])
    assert len(out) == 2
    assert [x.severity for x in out] == [lsp.DiagnosticSeverity.Error, lsp.DiagnosticSeverity.Warning]


def test_diagnostic_carries_fix_data() -> None:
    d = to_lsp_diagnostic(_diag_dict(code="adapter_state", fixes=[_MOUNT_FIX]))
    assert d.data == {"fixes": [_MOUNT_FIX]}


def test_code_actions_builds_insert_edit() -> None:
    uri = "file:///x.py"
    diag = to_lsp_diagnostic(_diag_dict(code="adapter_state", fixes=[_MOUNT_FIX]))
    actions = code_actions_for(uri, [diag])
    assert len(actions) == 2 and actions[1].command.command == "fluentvibe.explainProblem"
    action = actions[0]
    assert action.kind == lsp.CodeActionKind.QuickFix
    assert action.title == _MOUNT_FIX["title"]
    edit = action.edit.changes[uri][0]
    assert edit.new_text == "    head.mount_adapter()\n"
    # insert_before -> zero-width range at the start of the (0-based) target line.
    assert edit.range.start.line == 14
    assert edit.range.start.character == 0
    assert edit.range.end == edit.range.start


def test_code_actions_empty_when_no_fixes() -> None:
    diag = to_lsp_diagnostic(_diag_dict(code="source_volume_short", fixes=[]))
    # No quick fix; only "Explain this problem".
    assert [a.command.command for a in code_actions_for("file:///x.py", [diag])] == ["fluentvibe.explainProblem"]


def test_to_completion_items_builds_precise_edit() -> None:
    comp = {
        "label": "aspirate",
        "kind": "method",
        "insert_text": "aspirate",
        "replace_start": 9,
        "detail": "MCA96Head",
    }
    items = to_completion_items([comp], line=3, cursor_char=11)
    assert len(items) == 1
    item = items[0]
    assert item.label == "aspirate"
    assert item.kind == lsp.CompletionItemKind.Method
    assert item.detail == "MCA96Head"
    assert item.text_edit.new_text == "aspirate"
    assert item.text_edit.range.start.line == 3
    assert item.text_edit.range.start.character == 9
    assert item.text_edit.range.end.character == 11


def test_to_completion_items_catalog_kind() -> None:
    comp = {
        "label": "96 Well Flat",
        "kind": "catalog",
        "insert_text": "96 Well Flat",
        "replace_start": 5,
        "detail": "plate",
    }
    items = to_completion_items([comp], line=0, cursor_char=9)
    assert items[0].kind == lsp.CompletionItemKind.Value


def test_looks_like_protocol() -> None:
    assert lsp_server._looks_like_protocol(_BAD_PROTOCOL)
    assert not lsp_server._looks_like_protocol("import os\nprint('hi')\n")
    assert not lsp_server._looks_like_protocol("from fluentvibe import Worktable\n")  # no factory


def test_create_server_returns_language_server() -> None:
    server = lsp_server.create_server()
    assert server is not None
    assert server.name == "fluentvibe-lsp"


def test_analyze_reports_failure_in_process() -> None:
    """The server's in-process analyze() returns positioned diagnostic dicts."""
    diags = lsp_server.analyze(_BAD_PROTOCOL, "short.py")
    assert len(diags) == 1
    assert diags[0]["code"] == "source_volume_short"
    assert diags[0]["severity"] == "error"
    expected_line = next(
        i for i, t in enumerate(_BAD_PROTOCOL.splitlines(), start=1) if "head.aspirate(" in t
    )
    assert diags[0]["line"] == expected_line


def test_signature_help_and_hover_mapping() -> None:
    from fluentvibe.lsp.convert import to_hover, to_signature_help

    info = {
        "name": "aspirate",
        "label": "aspirate(target, volume_ul, *, liquid_class, columns=None)",
        "doc": "Aspirate from target.",
        "params": ["target", "volume_ul", "liquid_class", "columns"],
        "active_param": 1,
        "owner": "MCA96Head",
    }
    sh = to_signature_help(info)
    assert sh.signatures[0].label.startswith("aspirate(")
    assert [p.label for p in sh.signatures[0].parameters] == info["params"]
    assert sh.active_parameter == 1
    assert to_signature_help(None) is None

    hover = to_hover(info)
    assert "MCA96Head.aspirate(" in hover.contents.value
    assert "Aspirate from target." in hover.contents.value
    assert to_hover(None) is None


def test_inline_edit_receives_the_argument_dict_through_pygls(monkeypatch):
    """pygls 2 converts each command argument by the handler's annotation: an
    ``args: list`` parameter turned the dict into a list of its keys and every
    Ctrl+I edit failed with "string indices must be integers"."""
    from types import SimpleNamespace

    from lsprotocol import types as lsp
    from pygls.protocol.language_server import _prepare_command_arguments

    from fluentvibe.copilot import edit as edit_mod
    from fluentvibe.lsp.server import create_server

    server = create_server()
    handler = server.protocol.fm.commands["fluentvibe.applyInlineEdit"]
    params = lsp.ExecuteCommandParams(command="fluentvibe.applyInlineEdit", arguments=[
        {"uri": "file:///p.py", "start_line": 2, "end_line": 3, "instruction": "use 200 ul tips"}])
    args, kwargs = _prepare_command_arguments(handler, params, server.protocol._converter)
    seen = {}

    def fake_edit(source, start, end, instruction, path=None):
        seen.update(start=start, end=end, instruction=instruction)
        return SimpleNamespace(to_dict=lambda: {"new_text": "x\n", "start_line": start, "end_line": end})

    workspace = SimpleNamespace(get_text_document=lambda uri: SimpleNamespace(source="a\nb\nc\n", path="p.py"))
    monkeypatch.setattr(server.protocol, "_workspace", workspace)
    monkeypatch.setattr(edit_mod, "edit_region", fake_edit)
    result = handler(*args, **kwargs)
    assert seen == {"start": 2, "end": 3, "instruction": "use 200 ul tips"} and result["new_text"] == "x\n"
