from __future__ import annotations

import json
import sys
from pathlib import Path

from fluentvibe.authoring.document_adherence import (
    GATING_CODES,
    coverage_gaps,
    document_adherence_report,
)
from fluentvibe.authoring.lab_scope import LabScope
from fluentvibe.authoring.tools import (
    AuthoringToolRegistry,
    _autoground_labware_classes,
    _check_source_against_profile_labware_classes,
    _labware_class_swap_is_safe,
)


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


def test_profile_labware_class_autocorrects_generic_subclass() -> None:
    # A generic base class (TipBox/Trough) for a profile-pinned catalog is a
    # drop-in subclass rename, so the loop auto-corrects it (and the import)
    # instead of hard-failing and pushing the model to drop the labware.
    classes = {
        "FCA, 1000ul SBS": "FCA1000Box",
        "300ml SBS": "Trough25mL",
    }
    source = (
        "from fluentvibe import Worktable, TipBox, Trough\n"
        "\n"
        "def build_worktable() -> Worktable:\n"
        "    wt = Worktable.from_workspace(\"WS\", workspace_guid=\"g\", auto_place=False)\n"
        "    wt.group(\"Labware Placement\")\n"
        "    wt.place(TipBox(\"FCA_Tips\", catalog=\"FCA, 1000ul SBS\"), \"Nest61mm_Pos\", 3)\n"
        "    wt.place(Trough(\"Waste\", catalog=\"300ml SBS\"), \"WS_100ml_1\", 1)\n"
        "    return wt\n"
    )

    corrected, rewrites = _autoground_labware_classes(source, classes)

    assert [(r["from"], r["to"]) for r in rewrites] == [
        ("TipBox", "FCA1000Box"),
        ("Trough", "Trough25mL"),
    ]
    assert 'FCA1000Box("FCA_Tips", catalog="FCA, 1000ul SBS")' in corrected
    assert 'Trough25mL("Waste", catalog="300ml SBS")' in corrected
    # The required classes are added to the existing fluentvibe import.
    assert "FCA1000Box" in corrected.splitlines()[0]
    assert "Trough25mL" in corrected.splitlines()[0]
    # The corrected source now satisfies the contract.
    assert _check_source_against_profile_labware_classes(corrected, classes) is None


def test_labware_class_swap_safety_guard() -> None:
    # Subclass relationships (either direction) are constructor-compatible.
    assert _labware_class_swap_is_safe("TipBox", "FCA1000Box") is True
    assert _labware_class_swap_is_safe("Trough", "Trough25mL") is True
    # Unrelated classes are not a mechanical rename and must not be swapped.
    assert _labware_class_swap_is_safe("Plate96", "FCA1000Box") is False
    assert _labware_class_swap_is_safe("Plate96", "Trough25mL") is False


def test_profile_labware_class_does_not_rewrite_unrelated_class() -> None:
    # An unrelated class (Plate96 for a tip catalog) is a genuine error, not a
    # rename, so it is left untouched for the contract check to reject.
    classes = {"FCA, 1000ul SBS": "FCA1000Box"}
    source = (
        "from fluentvibe import Worktable, Plate96\n"
        "def build_worktable() -> Worktable:\n"
        "    wt = Worktable.from_workspace(\"WS\", workspace_guid=\"g\", auto_place=False)\n"
        "    wt.place(Plate96(\"X\", catalog=\"FCA, 1000ul SBS\"), \"Nest61mm_Pos\", 3)\n"
        "    return wt\n"
    )
    corrected, rewrites = _autoground_labware_classes(source, classes)
    assert rewrites == []
    assert corrected == source
    err = _check_source_against_profile_labware_classes(corrected, classes)
    assert err is not None and "must use FCA1000Box" in err


def test_profile_labware_class_contract_rejects_unrelated_class(tmp_path: Path) -> None:
    # An unrelated class (not in a subclass relationship with the required one)
    # is a genuine error, not a mechanical rename, so it is still rejected.
    tools = AuthoringToolRegistry(output_dir=tmp_path)
    tools.lab_scope = LabScope(
        mode="skills",
        labware=frozenset({"FCA, 1000ul SBS"}),
        labware_classes={"FCA, 1000ul SBS": "FCA1000Box"},
        liquid_classes=frozenset({"Water Free Single"}),
    )

    bad_source = """
from fluentvibe import Worktable, Plate96

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_1080_Test",
        workspace_guid="test-guid",
        auto_place=False,
    )
    wt.declare_variable("sample_count", "Number", 24)
    wt.set_sim_value("sample_count", 24)
    wt.group("Labware Placement")
    wt.place(Plate96("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 3)
    return wt
"""

    result = tools.simulate_python_draft(bad_source)

    assert result["ok"] is False
    assert result["stage"] == "contract"
    assert "Profile labware class contract violation" in result["message"]
    assert "must use FCA1000Box" in result["message"]


# ── source-document coverage gating ──────────────────────────────────────

_BARCODE_SOURCE_DOC = (
    "Attached file context: SQK-RBK114. Add 1 ul Rapid Barcode to 9 ul DNA. "
    "Incubate at 30C then 80C. Pool the barcoded samples. Perform AMPure XP bead "
    "cleanup on a magnet, wash with 80% ethanol, and elute in Elution Buffer. "
    "Dilute the Rapid Adapter with Adapter Buffer."
)


def test_coverage_gaps_returns_gating_codes_only() -> None:
    report = {
        "issues": [
            {"code": "missing_ethanol_wash", "severity": "warning", "message": "x"},
            {"code": "missing_elution", "severity": "warning", "message": "x"},
            # Non-gating: downstream/instrument + info.
            {"code": "missing_flow_cell_loading", "severity": "warning", "message": "x"},
            {"code": "missing_quantification", "severity": "warning", "message": "x"},
            {"code": "missing_volume", "severity": "info", "message": "x"},
        ]
    }
    gaps = coverage_gaps(report)
    codes = {g["code"] for g in gaps}
    assert codes == {"missing_ethanol_wash", "missing_elution"}
    assert codes <= GATING_CODES
    assert coverage_gaps(None) == []
    assert coverage_gaps({}) == []


def test_coverage_comment_satisfies_concept() -> None:
    # A protocol that automates only barcoding but *justifies* every other stage
    # via comments has no gating gaps ("automate or justify").
    justified = (
        "def build_worktable():\n"
        "    head.aspirate(barcode_plate, 1.0); head.dispense(sample_plate, 1.0)\n"
        "    wt.add_comment('Incubate 30C then 80C')\n"
        "    wt.add_comment('Pool all barcoded samples off-deck')\n"
        "    wt.add_comment('AMPure XP bead cleanup on magnet, manual')\n"
        "    wt.add_comment('Wash beads with 80% ethanol')\n"
        "    wt.add_comment('Elute in Elution Buffer (EB)')\n"
        "    wt.add_comment('Dilute Rapid Adapter with Adapter Buffer')\n"
    )
    report = document_adherence_report(
        source_text=_BARCODE_SOURCE_DOC, protocol_source=justified
    )
    assert coverage_gaps(report) == []


def test_coverage_gaps_flag_dropped_stages() -> None:
    # A protocol that only does barcoding and drops the rest yields gating gaps
    # for the omitted library-prep stages.
    thin = (
        "def build_worktable():\n"
        "    head.aspirate(barcode_plate, 1.0); head.dispense(sample_plate, 1.0)\n"
    )
    report = document_adherence_report(
        source_text=_BARCODE_SOURCE_DOC, protocol_source=thin
    )
    codes = {g["code"] for g in coverage_gaps(report)}
    assert {"missing_ethanol_wash", "missing_elution", "missing_magnetic_bead_cleanup"} <= codes
    # Non-gating omissions never appear among gaps.
    assert "missing_flow_cell_loading" not in codes
    assert "missing_quantification" not in codes


def test_has_source_document_context(tmp_path: Path) -> None:
    tools = AuthoringToolRegistry(output_dir=tmp_path)
    tools.current_prompt = "do a transfer"
    assert tools._has_source_document_context() is False
    tools.current_prompt = _BARCODE_SOURCE_DOC
    assert tools._has_source_document_context() is True


def test_accept_with_gaps_prefers_fallback_over_failure() -> None:
    from fluentvibe.authoring.graph import _prefer_fallback
    from fluentvibe.authoring.models import (
        AuthoringResult,
        AuthoringStatus,
        FailureCategory,
    )

    def _mk(status, gaps=()):
        return AuthoringResult(
            status=status, prompt="p", spec=None, generated_code="code",
            validation=None, compiled_xscr=None, coverage_gaps=tuple(gaps),
        )

    success = _mk(AuthoringStatus.SUCCESS)
    failure = AuthoringResult(
        status=AuthoringStatus.FAILURE, prompt="p", spec=None, generated_code=None,
        validation=None, compiled_xscr=None,
        failure_category=FailureCategory.MODEL_AUTHORING_FAILURE,
    )
    fallback = _mk(AuthoringStatus.SUCCESS, gaps=[{"code": "missing_pooling"}])

    # Failure after a nudge dropped a compiling draft -> return the fallback.
    chosen = _prefer_fallback(failure, fallback)
    assert chosen is fallback
    assert chosen.status is AuthoringStatus.SUCCESS
    assert [g["code"] for g in chosen.coverage_gaps] == ["missing_pooling"]

    # A real success is never overridden; no fallback -> failure stands.
    assert _prefer_fallback(success, fallback) is success
    assert _prefer_fallback(failure, None) is failure
    # Both None -> None (caller builds a defensive failure).
    assert _prefer_fallback(None, None) is None
    assert _prefer_fallback(None, fallback) is fallback
