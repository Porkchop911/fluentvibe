"""Skills-mode drafting: tool surface, header, and stage decision.

Skills mode declares the workflow always (cheap, anti-punt) and then drafts the
whole protocol in ONE pass — per-group staging is shelved (it cost ~3x latency
for no quality gain; see docs/authoring-quality-experiment.md §7). Enforce stays
one-shot; off/cheatsheet stay always-staged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from fluentvibe.authoring.graph import (
    _requires_workflow_declaration,
    _should_stage,
    _workflow_complete,
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


def test_skills_header_declares_then_drafts_one_pass():
    header = context_header(True, skills=True)
    assert "declare_protocol_workflow" in header
    assert "one pass" in header.lower()
    assert "ONE group at a time" not in header  # per-group staging is shelved


def test_non_skills_header_is_enforce_one_pass():
    header = context_header(True, skills=False)
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


def test_skills_many_groups_do_not_stage(tmp_path):
    """Skills never stages — even a many-group plan drafts in one pass."""
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Add Mastermix", "Add Template", "Seal Plate"])  # 3 non-scaffold
    assert _should_stage(reg) is False


def test_skills_cleanup_named_group_does_not_stage(tmp_path):
    """A bead/cleanup group no longer forces staging (shelved, net-negative)."""
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Bead Cleanup", "Elution", "Barcoding"])
    assert _should_stage(reg) is False


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


def test_skills_multistage_is_complete_after_declare(tmp_path):
    """Even a multi-stage skills plan one-shots: complete right after declare."""
    reg = _registry(tmp_path, "skills")
    _declare(reg, ["Bead Cleanup", "Elution", "Barcoding"])
    reg.staged_drafting = _should_stage(reg)  # False — skills never stages
    assert _workflow_complete(reg, 0) is True  # one-pass allowed immediately


def test_declaration_required_but_missing_is_incomplete(tmp_path):
    reg = _registry(tmp_path, "skills")  # no declare yet
    assert _workflow_complete(reg, 0) is False


# ── Graph integration: skills-simple drafts in one pass ────────────────────

from tests.test_prompt_authoring import _valid_draft  # noqa: E402


def _initial_state():
    from langchain_core.messages import HumanMessage

    from fluentvibe.authoring.graph import GraphState

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


def test_skills_empty_turn_hard_fails(tmp_path):
    """Skills one-shots, so a turn with no tool call and no fenced draft aborts —
    the staging-era empty-turn re-nudge is shelved along with per-group staging."""
    reg = _registry(tmp_path, "skills")
    _grounded(reg)
    from tests.test_authoring_graph import _build

    graph = _build(
        registry=reg,
        responses=[
            AIMessage(content="", tool_calls=[_simple_plan_call()]),       # declare
            AIMessage(content="Let me reason about the layout first..."),  # empty → fail
        ],
    )
    final = graph.invoke(_initial_state())
    assert final["result"].status == AuthoringStatus.FAILURE


def test_enforce_empty_turn_still_hard_fails(tmp_path):
    """Enforce is one-shot (no workflow declaration); an empty turn aborts."""
    reg = _registry(tmp_path, "enforce")
    from tests.test_authoring_graph import _build

    graph = _build(
        registry=reg,
        responses=[AIMessage(content="I am not going to produce a draft.")],
    )
    final = graph.invoke(_initial_state())
    assert final["result"].status == AuthoringStatus.FAILURE


# ── First turn: plan only; API lookup available ────────────────────────────


def test_skills_exposes_deterministic_api_lookup():
    allowed = LabScope(mode="skills").allowed_tools()
    assert "lookup_api" in allowed
    assert "lookup_api" in context_header(True, skills=True)


class _RecordingLegacyClient:
    """Legacy `.complete()` client that records which tools each call offered."""

    def __init__(self) -> None:
        self.offered: list[list[str]] = []

    def complete(self, messages, tools):
        self.offered.append(sorted(t["function"]["name"] for t in tools))
        return {"role": "assistant", "content": "done", "tool_calls": []}


def _skills_nodes(tmp_path, mode="skills"):
    from fluentvibe.authoring.graph import _Nodes, _plan_only_client, adapt_client
    from fluentvibe.authoring.lc_tools import make_lc_tools
    from fluentvibe.authoring.repair_lock import RepairLockState
    from fluentvibe.authoring.tools import tool_definitions
    from fluentvibe.authoring.validator import AuthoringValidator

    reg = _registry(tmp_path, mode)
    allowed = reg.lab_scope.allowed_tools()
    denied = (
        frozenset(d["function"]["name"] for d in tool_definitions()) - allowed
        if allowed is not None
        else None
    )
    legacy = _RecordingLegacyClient()
    client = adapt_client(legacy)
    lc_tools = make_lc_tools(reg, denied=denied)
    nodes = _Nodes(
        registry=reg,
        client_with_tools=client.bind_tools(lc_tools),
        client_plan_only=_plan_only_client(client, lc_tools, reg),
        validator=AuthoringValidator(),
        repair_lock=RepairLockState(),
        output_dir=tmp_path / "out",
        max_iterations=8,
        max_tool_calls=12,
        helpers=None,
    )
    return reg, nodes, legacy


def test_skills_first_turn_offers_only_the_workflow_declaration(tmp_path):
    reg, nodes, legacy = _skills_nodes(tmp_path)
    nodes._client_for_turn().invoke([])
    assert legacy.offered[-1] == ["declare_protocol_workflow", "lookup_api"]

    _declare(reg, ["Transfer"])
    nodes._client_for_turn().invoke([])
    assert {"lookup_api", "simulate_python_draft", "compile_and_simulate"} <= set(
        legacy.offered[-1]
    )


@pytest.mark.parametrize("mode", ["enforce", "cheatsheet"])
def test_other_modes_keep_their_full_first_turn_surface(tmp_path, mode):
    _, nodes, _ = _skills_nodes(tmp_path, mode)
    assert nodes.client_plan_only is None


def test_simulator_findings_get_one_repair_turn_then_the_draft_is_kept(tmp_path, monkeypatch):
    """A draft that simulates but has tip-hygiene findings is not accepted at
    once: the model gets one nudge. If the repair also has findings, the run
    still ends in success with the draft (findings are warnings, not gates)."""
    from langchain_core.messages import HumanMessage

    from tests.test_authoring_graph import _build

    reg = _registry(tmp_path, "skills")
    _grounded(reg)
    draft = _valid_draft()
    hygiene = {"counts": {"cross_sample_tip_reuse": 8}, "examples": [
        {"line": 12, "operation": "Mix", "labware": "Samples", "well": "A2"}], "hint": "fresh tips"}
    real_simulate = reg.simulate_python_draft

    def simulate_with_findings(source, strict=True):
        result = real_simulate(source, strict=strict)
        if result.get("ok"):
            result = dict(result, tip_hygiene=hygiene)
        return result

    monkeypatch.setattr(reg, "simulate_python_draft", simulate_with_findings)
    sim = {"name": "simulate_python_draft", "args": {"source": draft}, "id": "call-sim"}
    sim2 = {"name": "simulate_python_draft", "args": {"source": draft}, "id": "call-sim-2"}
    graph = _build(
        registry=reg,
        responses=[
            AIMessage(content="", tool_calls=[_simple_plan_call(), sim]),
            AIMessage(content="", tool_calls=[sim2]),
        ],
    )
    final = graph.invoke(_initial_state())
    nudges = [m for m in final["messages"]
              if isinstance(m, HumanMessage) and "bench scientist would reject" in m.content]
    assert len(nudges) == 1
    assert "line 12" in nudges[0].content
    assert final["result"].status == AuthoringStatus.SUCCESS


def test_edit_draft_applies_a_unique_replacement_and_resimulates(tmp_path):
    reg = _registry(tmp_path, "skills")
    draft = _valid_draft()
    assert reg.edit_draft("x", "y")["category"] == "edit_draft_no_base"
    assert reg.simulate_python_draft(draft)["ok"] is True
    missing = reg.edit_draft("this text is not in the draft", "y")
    assert missing["category"] == "edit_draft_not_found"
    ambiguous = reg.edit_draft("wt.", "wt.")
    assert ambiguous["category"] == "edit_draft_ambiguous"
    unique = next(line for line in draft.splitlines() if "def build_worktable" in line)
    result = reg.edit_draft(unique, unique + "  # edited")
    assert result["ok"] is True
    assert "# edited" in result["source"]


def test_graph_treats_edit_draft_like_a_full_simulation(tmp_path):
    from tests.test_authoring_graph import _build

    reg = _registry(tmp_path, "skills")
    _grounded(reg)
    draft = _valid_draft()
    broken = draft.replace("def build_worktable", "def build_worktable_broken", 1)
    sim = {"name": "simulate_python_draft", "args": {"source": broken}, "id": "call-sim"}
    fix = {"name": "edit_draft", "args": {"old": "def build_worktable_broken",
                                            "new": "def build_worktable"}, "id": "call-fix"}
    graph = _build(
        registry=reg,
        responses=[
            AIMessage(content="", tool_calls=[_simple_plan_call(), sim]),
            AIMessage(content="", tool_calls=[fix]),
        ],
    )
    final = graph.invoke(_initial_state())
    assert final["result"].status == AuthoringStatus.SUCCESS
    assert "def build_worktable()" in (final["best_code"] or "")


def _spec_prompt():
    import json as _json

    from fluentvibe.authoring.bench_spec import spec_context_block, validate_bench_spec

    raw = _json.loads((Path(__file__).resolve().parent.parent / "examples" / "ont_rbk114_spec.json")
                      .read_text(encoding="utf-8"))
    spec, _ = validate_bench_spec(raw)
    return "Automate the library prep.\n\n" + spec_context_block(spec), spec


def test_approved_spec_declares_the_workflow_before_the_first_model_turn(tmp_path):
    from fluentvibe.authoring.graph import _declare_workflow_from_spec

    reg = _registry(tmp_path, "skills")
    prompt, spec = _spec_prompt()
    update = _declare_workflow_from_spec(reg, prompt)
    assert reg.workflow_plan is not None
    names = [g.name for g in reg.workflow_plan.groups]
    assert names[:2] == ["Variables", "Labware Placement"]
    assert len(names) == 2 + len(spec.steps)
    assert names[2].startswith("s1: ")
    assert any("[off-deck]" in n for n in names)
    assert "already been declared" in update["messages"][0].content
    # Not twice, and not outside skills mode.
    assert _declare_workflow_from_spec(reg, prompt) == {}
    other = _registry(tmp_path / "e", "enforce")
    assert _declare_workflow_from_spec(other, prompt) == {}
    assert _declare_workflow_from_spec(_registry(tmp_path / "n", "skills"), "no spec") == {}


def test_spec_run_drafts_without_a_planning_turn(tmp_path):
    from langchain_core.messages import HumanMessage

    from fluentvibe.authoring.graph import GraphState
    from tests.test_authoring_graph import _build

    reg = _registry(tmp_path, "skills")
    _grounded(reg)
    prompt, _ = _spec_prompt()
    sim = {"name": "simulate_python_draft", "args": {"source": _valid_draft()}, "id": "call-sim"}
    graph = _build(registry=reg, responses=[AIMessage(content="", tool_calls=[sim])])
    state = GraphState(
        messages=[HumanMessage(content=prompt)], iterations=0, tool_call_count=0,
        best_code=None, last_validation=None, current_group_index=0,
        last_accepted_source_hash=None, result=None, prompt=prompt,
        adherence_nudges=0, fallback_result=None,
    )
    final = graph.invoke(state)
    assert final["result"].status == AuthoringStatus.SUCCESS
    assert final["iterations"] == 1  # the first model turn already drafted
