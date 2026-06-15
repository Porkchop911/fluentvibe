from __future__ import annotations

import json
import sys
from pathlib import Path

from fluentvibe.authoring.document_adherence import document_adherence_report
from fluentvibe.authoring.tools import AuthoringToolRegistry


def test_source_protocol_plan_requires_classified_steps(tmp_path: Path) -> None:
    tools = AuthoringToolRegistry(output_dir=tmp_path)

    bad = tools.present_source_protocol_plan(
        protocol_title="Protocol",
        summary="Extracted protocol",
        source_files=[{"name": "protocol.pdf"}],
        steps=[{"description": "Barcode samples"}],
    )

    assert bad["ok"] is False
    assert bad["category"] == "source_protocol_plan_invalid"
    assert tools.pending_approval_kind == "source_protocol"

    ok = tools.present_source_protocol_plan(
        protocol_title="Protocol",
        summary="Extracted protocol",
        source_files=[{"name": "protocol.pdf"}],
        steps=[
            {
                "description": "Add 1 uL Rapid Barcode to 9 uL DNA.",
                "classification": "automated",
                "source_ref": "page 12",
            },
            {
                "description": "Load library onto flow cell.",
                "classification": "manual_off_deck",
                "source_ref": "page 19",
            },
        ],
    )

    assert ok["status"] == "needs_approval"
    assert ok["approval"]["kind"] == "source_protocol"
    tools.approve_pending("source_protocol")
    assert tools.source_protocol_plan_approved is True


def test_graph_blocks_object_draft_until_source_protocol_approved(tmp_path: Path) -> None:
    from fluentvibe.authoring.graph import _approval_stage_block

    tools = AuthoringToolRegistry(output_dir=tmp_path)
    tools.set_authoring_context(
        original_prompt="Author this.\n\nAttached file context:\nprotocol text",
        latest_user_text="Author this.\n\nAttached file context:\nprotocol text",
        user_history_text="Author this.\n\nAttached file context:\nprotocol text",
    )

    blocked = _approval_stage_block(registry=tools, tool_name="present_object_draft")

    assert blocked is not None
    assert blocked["category"] == "source_protocol_approval_required"

    tools.source_protocol_plan = {"steps": []}
    tools.source_protocol_plan_approved = True
    assert _approval_stage_block(registry=tools, tool_name="present_object_draft") is None


def test_document_adherence_flags_missing_pdf_concepts() -> None:
    report = document_adherence_report(
        source_text=(
            "Add Rapid Barcodes to DNA, attach Rapid Adapter, then load the "
            "library on the flow cell. Use 1 ul adapter."
        ),
        protocol_source="wt.group('Cleanup')\n# AMPure bead cleanup only\n",
        source_name="protocol.txt",
    )

    codes = {issue["code"] for issue in report["issues"]}
    assert "missing_barcoding" in codes
    assert "missing_adapter_attachment" in codes
    assert "missing_flow_cell_loading" in codes


def test_cli_check_source_doc_json_includes_adherence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from fluentvibe.cli import main

    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        "from fluentvibe import Worktable\n\n"
        "def build_worktable():\n"
        "    return Worktable(name='empty')\n",
        encoding="utf-8",
    )
    source_doc = tmp_path / "source.txt"
    source_doc.write_text("Attach Rapid Adapter and load the flow cell.", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["fluentvibe", "check", str(protocol), "--source-doc", str(source_doc), "--json"],
    )

    rc = main()
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert "diagnostics" in out
    assert "document_adherence" in out
    codes = {issue["code"] for issue in out["document_adherence"]["issues"]}
    assert "missing_adapter_attachment" in codes
