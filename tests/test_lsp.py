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
from fluentvibe.lsp.convert import to_lsp_diagnostic, to_lsp_diagnostics  # noqa: E402

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
    }
    base.update(over)
    return base


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


def test_looks_like_protocol() -> None:
    assert lsp_server._looks_like_protocol(_BAD_PROTOCOL)
    assert not lsp_server._looks_like_protocol("import os\nprint('hi')\n")
    assert not lsp_server._looks_like_protocol("from fluentvibe import Worktable\n")  # no factory


def test_create_server_returns_language_server() -> None:
    server = lsp_server.create_server()
    assert server is not None
    assert server.name == "fluentvibe-lsp"


def test_run_analysis_subprocess_reports_failure(tmp_path: Path) -> None:
    """End-to-end: the isolated subprocess analysis returns positioned diagnostics."""
    path = tmp_path / "short.py"
    path.write_text(_BAD_PROTOCOL, encoding="utf-8")
    diags = lsp_server._run_analysis(str(path))
    assert len(diags) == 1
    assert diags[0]["code"] == "source_volume_short"
    assert diags[0]["severity"] == "error"
    # The diagnostic lands on the aspirate line.
    expected_line = next(
        i for i, t in enumerate(_BAD_PROTOCOL.splitlines(), start=1) if "head.aspirate(" in t
    )
    assert diags[0]["line"] == expected_line
