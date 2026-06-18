"""Skills-mode staged drafting: tool surface, header, and stage decision.

Skills mode declares the workflow always (cheap, anti-punt) but only enforces
per-group checkpoints when the declared plan is multi-stage; enforce stays
one-shot; off/cheatsheet stay always-staged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from fluentvibe.authoring.graph import (
    _requires_workflow_declaration,
    _should_stage,
    _workflow_complete,
    build_authoring_graph,
)
from fluentvibe.authoring.lab_scope import LabScope, context_header
from fluentvibe.authoring.models import AuthoringStatus
from fluentvibe.authoring.tools import AuthoringToolRegistry


def _registry(tmp_path: Path, mode: str | None) -> AuthoringToolRegistry:
    reg = AuthoringToolRegistry(output_dir=tmp_path / "out")
    if mode is not None:
        reg.lab_scope = LabScope(mode=mode)
    return reg


def _declare(reg: AuthoringToolRegistry, stage_names: list[str]) -> None:
    groups = [{"name": "Variables"}, {"name": "Labware Placement"}]
    groups += [{"name": n} for n in stage_names]
    result = reg.declare_protocol_workflow(
        protocol_name="P",
        summary="s",
        variables=[{"name": "RunId", "default": "x", "sim_value": "x"}],
        labware=[{"label": "A"}, {"label": "B"}],
        groups=groups,
    )
    assert result["ok"] is True


# ── Tool surface ──────────────────────────────────────────────────────────


def test_skills_exposes_declare_protocol_workflow():
    allowed = LabScope(mode="skills").allowed_tools()
    assert allowed is not None
    assert "declare_protocol_workflow" in allowed
    assert {"simulate_python_draft", "compile_and_simulate"} <= allowed


def test_enforce_withholds_declare_protocol_workflow():
    allowed = LabScope(mode="enforce").allowed_tools()
    assert allowed == frozenset({"simulate_python_draft", "compile_and_simulate"})


def test_off_and_cheatsheet_have_no_allowlist():
    assert LabScope(mode="off").allowed_tools() is None
    assert LabScope(mode="cheatsheet").allowed_tools() is None


# ── Header ─────────────────────────────────────────────────────────────────


def test_staged_header_instructs_group_by_group():
    header = context_header(True, staged=True)
    assert "declare_protocol_workflow" in header
    assert "ONE group at a time" in header
    assert "do NOT stage" not in header  # not the enforce one-pass header


def test_non_staged_header_is_enforce_one_pass():
    header = context_header(True, staged=False)
    assert "one pass" in header.lower()


# ── _requires_workflow_declaration ─────────────────────────────────────────


@pytest.mark.parametrize("mode,expected", [
    ("enforce", False),
    ("skills", True),
    ("cheatsheet", True),
    ("off", True),
    (None, True),
])
def test_requires_workflow_declaration(tmp_path, mode, expected):
    reg = _registry(tmp_path, mode)
    assert _requires_workflow_declaration(reg) is expected


# ── _should_stage ──────────────────────────────────────────────────────────


def test_skills_simple_plan_does_not_stage(tmp_path):
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Transfer"])  # one non-scaffold group
    assert _should_stage(reg) is False


def test_skills_many_groups_stage(tmp_path):
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Add Mastermix", "Add Template", "Seal Plate"])  # 3 non-scaffold
    assert _should_stage(reg) is True


def test_skills_cleanup_named_group_stages_even_if_few(tmp_path):
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Bead Cleanup"])  # single group but a cleanup → stage
    assert _should_stage(reg) is True


def test_non_skills_modes_always_stage(tmp_path):
    reg = _registry(tmp_path, "cheatsheet")
    _declare(reg, ["Transfer"])
    assert _should_stage(reg) is True


# ── _workflow_complete ─────────────────────────────────────────────────────


def test_enforce_workflow_always_complete(tmp_path):
    reg = _registry(tmp_path, "enforce")
    assert _workflow_complete(reg, 0) is True


def test_skills_simple_is_complete_after_declare(tmp_path):
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Transfer"])
    reg.staged_drafting = _should_stage(reg)  # False
    assert _workflow_complete(reg, 0) is True  # one-pass allowed


def test_skills_multistage_incomplete_until_all_groups(tmp_path):
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Bead Cleanup", "Elution", "Barcoding"])
    reg.staged_drafting = _should_stage(reg)  # True
    assert _workflow_complete(reg, 2) is False   # mid-plan
    assert _workflow_complete(reg, 5) is True    # all 5 groups done


def test_declaration_required_but_missing_is_incomplete(tmp_path):
    reg = _registry(tmp_path, "skills")  # no declare yet
    assert _workflow_complete(reg, 0) is False


# ── Graph integration: skills-simple drafts in one pass ────────────────────

from tests.test_prompt_authoring import _valid_draft  # noqa: E402


def _initial_state():
    from fluentvibe.authoring.graph import GraphState
    from langchain_core.messages import HumanMessage

    return GraphState(
        messages=[HumanMessage(content="author a simple transfer")],
        iterations=0,
        tool_call_count=0,
        best_code=None,
        last_validation=None,
        current_group_index=0,
        last_accepted_source_hash=None,
        result=None,
        prompt="author a simple transfer",
        adherence_nudges=0,
        fallback_result=None,
    )


def test_skills_simple_plan_compiles_in_one_pass(tmp_path):
    reg = _registry(tmp_path, "skills")
    reg.calls = [
        {"name": "lookup_workspace", "arguments": {}, "result": {"ok": True}},
        {"name": "search_labware", "arguments": {}, "result": {"ok": True}},
        {"name": "lookup_liquid_class", "arguments": {}, "result": {"ok": True}},
        {"name": "lookup_rules", "arguments": {}, "result": {"ok": True}},
    ]
    draft = _valid_draft()
    plan_call = {
        "name": "declare_protocol_workflow",
        "args": {
            "protocol_name": "Transfer",
            "summary": "simple",
            "variables": [{"name": "RunId", "default": "x", "sim_value": "x"}],
            "labware": [{"label": "SourcePlate"}, {"label": "DestPlate"}, {"label": "Tips"}],
            "groups": [
                {"name": "Variables"},
                {"name": "Labware Placement"},
                {"name": "Transfer"},
            ],
        },
        "id": "call-plan",
    }
    sim = {"name": "simulate_python_draft", "args": {"source": draft}, "id": "call-sim"}
    from tests.test_authoring_graph import _build

    graph = _build(
        registry=reg,
        responses=[AIMessage(content="", tool_calls=[plan_call, sim])],
    )
    final = graph.invoke(_initial_state())
    assert final["result"].status == AuthoringStatus.SUCCESS
    assert reg.staged_drafting is False  # single non-scaffold group → one-pass
    names = [c["name"] for c in reg.calls]
    assert "compile_and_simulate" in names


def _grounded(reg):
    reg.calls = [
        {"name": "lookup_workspace", "arguments": {}, "result": {"ok": True}},
        {"name": "search_labware", "arguments": {}, "result": {"ok": True}},
        {"name": "lookup_liquid_class", "arguments": {}, "result": {"ok": True}},
        {"name": "lookup_rules", "arguments": {}, "result": {"ok": True}},
    ]


def _simple_plan_call():
    return {
        "name": "declare_protocol_workflow",
        "args": {
            "protocol_name": "Transfer",
            "summary": "simple",
            "variables": [{"name": "RunId", "default": "x", "sim_value": "x"}],
            "labware": [{"label": "SourcePlate"}, {"label": "DestPlate"}, {"label": "Tips"}],
            "groups": [
                {"name": "Variables"},
                {"name": "Labware Placement"},
                {"name": "Transfer"},
            ],
        },
        "id": "call-plan",
    }


def test_skills_empty_turn_renudges_instead_of_failing(tmp_path):
    """A staged turn with no tool call and no fenced draft must re-nudge, not
    abort the run (the local model sometimes 'thinks out loud' mid-plan)."""
    reg = _registry(tmp_path, "skills")
    _grounded(reg)
    from tests.test_authoring_graph import _build

    sim = {"name": "simulate_python_draft", "args": {"source": _valid_draft()}, "id": "call-sim"}
    graph = _build(
        registry=reg,
        responses=[
            AIMessage(content="", tool_calls=[_simple_plan_call()]),       # declare
            AIMessage(content="Let me reason about the layout first..."),  # empty → re-nudge
            AIMessage(content="", tool_calls=[sim]),                        # draft → success
        ],
    )
    final = graph.invoke(_initial_state())
    assert final["result"].status == AuthoringStatus.SUCCESS, final["result"].failure_message


def test_enforce_empty_turn_still_hard_fails(tmp_path):
    """Enforce is one-shot (no workflow declaration), so an empty turn aborts as
    before — the re-nudge is staging-only."""
    reg = _registry(tmp_path, "enforce")
    from tests.test_authoring_graph import _build

    graph = _build(
        registry=reg,
        responses=[AIMessage(content="I am not going to produce a draft.")],
    )
    final = graph.invoke(_initial_state())
    assert final["result"].status == AuthoringStatus.FAILURE
