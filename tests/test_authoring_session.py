from __future__ import annotations

import json
from pathlib import Path

import pytest

import fluentvibe.authoring.grounding as grounding
from fluentvibe.authoring.models import AuthoringResult, AuthoringStatus
from fluentvibe.authoring.tools import AuthoringToolRegistry
from fluentvibe.authoring.session import PromptAuthoringSession
from tests.test_prompt_authoring import _valid_draft


@pytest.fixture(autouse=True)
def _baseline_lab_scope(monkeypatch):
    """These tests script the baseline (off) session loop — grounding, approval,
    clarification, repair-lock. ``skills`` is the global default now, so pin this
    module to ``off`` to exercise the baseline mechanics it was written for."""
    monkeypatch.setenv("FLUENTVIBE_LAB_SCOPE", "off")


def _workflow_args(groups=None):
    return {
        "protocol_name": "Test Protocol",
        "summary": "Staged test workflow",
        "variables": [{"name": "RunId", "default": "test_run", "sim_value": "test_run"}],
        "labware": [{"label": "SourcePlate"}, {"label": "DestPlate"}, {"label": "Tips"}],
        "groups": groups or [
            {"name": "Variables", "objective": "Declare runtime variables"},
            {"name": "Labware Placement", "objective": "Place all labware"},
        ],
    }


def _object_draft_args():
    return {
        "protocol_name": "Test Protocol",
        "summary": "Transfer test workflow",
        "workspace": {
            "name": "SAT_Fluent_780_Rev3",
            "workspace_guid": "291ba293-6361-4f8f-aa8d-7c2643d3f096",
        },
        "variables": [{"name": "RunId", "default": "test_run", "sim_value": "test_run"}],
        "reagents": [{"name": "Water", "role": "source liquid"}],
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": [
            {
                "label": "SourcePlate",
                "role": "source",
                "python_class": "Plate96",
                "catalog_name": "96_ABgene_SuperPlate_Thermo_AB2800",
                "location": "Nest61mm_Pos",
                "position": 1,
            },
            {
                "label": "DestPlate",
                "role": "destination",
                "python_class": "Plate96",
                "catalog_name": "96_ABgene_SuperPlate_Thermo_AB2800",
                "location": "Nest61mm_Pos",
                "position": 2,
            },
            {
                "label": "Tips",
                "role": "mca_tips",
                "python_class": "MCA100Box",
                "catalog_name": "MCA96, 100ul, Box",
                "location": "Nest61mm_Pos",
                "position": 4,
            },
        ],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_messages = []

    def complete(self, *, messages, tools):
        self.seen_messages.append(list(messages))
        if not self.responses:
            raise AssertionError("No fake response configured")
        return self.responses.pop(0)


def _write_snapshot(path: Path) -> None:
    path.write_text(
        """
SCHEMA_VERSION = 1
WORKSPACE = {
    "name": "SAT_Fluent_780_Rev3",
    "guid": "291ba293-6361-4f8f-aa8d-7c2643d3f096",
    "base_worktable_name": "780 Base Unit",
    "base_worktable_guid": "11111111-1234-aaaa-ffff-000000000222",
}
VALID_SLOTS = [
    ("Nest61mm_Pos", 1),
    ("Nest61mm_Pos", 2),
    ("Nest61mm_Pos", 3),
    ("Nest61mm_Pos", 4),
    ("Nest61mm_Pos", 5),
    ("Nest61mm_Pos", 6),
    ("Nest61mm_Pos", 7),
    ("Nest61mm_Pos", 8),
    ("Nest61mm_Pos", 9),
    ("Nest61mm_Pos", 10),
]
POSITION_SUMMARY_BY_LOCATION = {"Nest61mm_Pos": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
POSITIONS = [{"location": "Nest61mm_Pos", "position": 1, "site_path": "28/0/0", "occupied_by": None}]
OCCUPANTS = [{"catalog_name": "Eccentric[001]"}]
COMPATIBILITY_BY_OCCUPANT = {"Eccentric[001]": {"ok": True}}
ACCEPTS_BY_OCCUPIED_CARRIER_SITE = {}
""".strip(),
        encoding="utf-8",
    )


def _ampure_snapshot_draft() -> str:
    return '''from fluentvibe import Worktable, Reagent, Layer, Plate96, MCA200Box, MagnetRack


def build_worktable() -> Worktable:
    sample = Reagent("PCR sample")
    beads = Reagent("AMPure XP beads", role="bead_carrier")
    ethanol = Reagent("Ethanol 80%")
    elution = Reagent("Elution buffer")
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="AMPure XP cleanup",
        comment="Deterministic snapshot-backed AMPure cleanup draft",
    )
    wt.declare_variable("RunId", "snapshot_ampure")
    wt.set_sim_value("RunId", "snapshot_ampure")
    wt.group("Labware Placement")
    sample_plate = wt.place(Plate96("SamplePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    elution_plate = wt.place(Plate96("ElutionPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    magnet = wt.place(MagnetRack("Magnet", catalog="LV_Alpaqua_A000350"), "Nest61mm_Pos", 3)
    tips = wt.place(MCA200Box("Tips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4)
    waste = wt.place(Plate96("WastePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 5)
    wash_plate = wt.place(Plate96("WashPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 6)
    sample_plate.fill_all(sample, 20.0)
    for well in sample_plate.wells.values():
        well.layers.append(Layer(reagent=beads, volume_ul=36.0))
    wash_plate.fill_all(ethanol, 200.0)
    elution_plate.fill_all(elution, 50.0)
    wt.group("Bind and clear")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    wt.gripper.move(sample_plate, onto=magnet)
    head.aspirate(sample_plate, 20.0, liquid_class="Water Free Single")
    head.dispense(waste, 20.0, liquid_class="Water Free Single")
    wt.group("Wash")
    head.aspirate(wash_plate, 50.0, liquid_class="Water Free Single")
    head.dispense(sample_plate, 50.0, liquid_class="Water Free Single")
    head.aspirate(sample_plate, 50.0, liquid_class="Water Free Single")
    head.dispense(waste, 50.0, liquid_class="Water Free Single")
    wt.group("Elute")
    wt.gripper.move(sample_plate, to=("Nest61mm_Pos", 1))
    head.aspirate(elution_plate, 30.0, liquid_class="Water Free Single")
    head.dispense(sample_plate, 30.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    return wt
'''


def test_session_seeds_workspace_tools_from_current_worktable_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    snapshot_path = tmp_path / "current_worktable.py"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv(grounding.CURRENT_WORKTABLE_ENV, str(snapshot_path))
    monkeypatch.setattr(
        grounding,
        "load_xwsp",
        lambda path: (_ for _ in ()).throw(AssertionError("should use snapshot cache")),
    )
    client = FakeClient([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-workspace",
                    "type": "function",
                    "function": {
                        "name": "lookup_workspace",
                        "arguments": json.dumps({"name_or_guid": "SAT_Fluent_780_Rev3"}),
                    },
                },
                {
                    "id": "call-positions",
                    "type": "function",
                    "function": {
                        "name": "list_valid_positions",
                        "arguments": json.dumps({"location": "Nest61mm_Pos"}),
                    },
                },
            ],
        },
        {"role": "assistant", "content": "Grounding complete.", "tool_calls": []},
    ])
    session = PromptAuthoringSession(
        output_dir=tmp_path / "out",
        client=client,
        retry_budget=1,
    )

    session.send("AMPure XP cleanup for 96 samples.")

    workspace_call = next(call for call in session.tool_calls if call["name"] == "lookup_workspace")
    positions_call = next(call for call in session.tool_calls if call["name"] == "list_valid_positions")
    assert workspace_call["dispatch_source"] == "cache"
    assert workspace_call["result"]["workspace"] == {
        "name": "SAT_Fluent_780_Rev3",
        "guid": "291ba293-6361-4f8f-aa8d-7c2643d3f096",
    }
    # Economized: the heavy current_worktable snapshot (occupants /
    # compatibility maps, ~112 KB) is no longer echoed to the model. The
    # cache is still seeded from the snapshot file (dispatch_source ==
    # "cache", and load_xwsp must not be called), it just returns the lean
    # act-on shape.
    assert set(workspace_call["result"]) == {
        "ok", "workspace", "valid_positions", "default_layout"
    }
    assert "snapshot" not in workspace_call["result"]
    assert positions_call["dispatch_source"] == "cache"
    assert positions_call["result"]["positions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert "snapshot" not in positions_call["result"]


def test_explicit_workspace_override_does_not_bind_current_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    snapshot_path = tmp_path / "current_worktable.py"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv(grounding.CURRENT_WORKTABLE_ENV, str(snapshot_path))

    session = PromptAuthoringSession(
        output_dir=tmp_path / "out",
        client=FakeClient([{"role": "assistant", "content": "done", "tool_calls": []}]),
        workspace_name="Other Workspace",
        workspace_guid="other-guid",
    )

    assert session._registry.workspace_name == "Other Workspace"
    assert session._registry.workspace_guid == "other-guid"
    assert session._registry.cache_lookup(
        "lookup_workspace", {"name_or_guid": "SAT_Fluent_780_Rev3"}
    ) is None


def test_ampure_snapshot_draft_compiles_and_simulates_under_manual_output() -> None:
    out = Path("build") / "manual_coop_test150526" / "pytest_ampure_snapshot"
    tools = AuthoringToolRegistry(output_dir=out)

    result = tools.compile_and_simulate(_ampure_snapshot_draft())

    assert result["ok"] is True
    assert result["compile_ok"] is True
    assert result["strict_simulation_ok"] is True
    python_path = Path(result["python_path"])
    xscr_path = Path(result["xscr_path"])
    assert python_path.exists()
    assert xscr_path.exists()
    assert out in python_path.parents
    assert out in xscr_path.parents


def test_session_returns_clarification_for_model_question() -> None:
    client = FakeClient([
        {"role": "assistant", "content": "What transfer volume should I use?", "tool_calls": []},
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "clarification",
        client=client,
    )

    result = session.send("Transfer from source to destination.")

    assert result.status is AuthoringStatus.CLARIFICATION_REQUIRED
    assert result.clarification_questions[0].question == "What transfer volume should I use?"


def test_session_registry_context_preserves_full_user_history() -> None:
    client = FakeClient([
        {"role": "assistant", "content": "What transfer volume should I use?", "tool_calls": []},
        {"role": "assistant", "content": "What liquid class should I use?", "tool_calls": []},
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "full_context",
        client=client,
    )

    session.send("Author an AMPure cleanup for 96 samples.")
    session.send("20 uL")

    assert session._registry.original_prompt == "Author an AMPure cleanup for 96 samples."
    assert session._registry.latest_user_text == "20 uL"
    assert "AMPure cleanup" in session._registry.user_history_text
    assert "20 uL" in session._registry.current_prompt


def test_session_surfaces_pending_object_approval_over_empty_failure() -> None:
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "pending_approval_fallback",
        client=FakeClient([]),
    )
    draft = _object_draft_args()
    session._registry.object_draft = draft
    session._registry.pending_approval_kind = "objects"
    failure = AuthoringResult(
        status=AuthoringStatus.FAILURE,
        prompt="AMPure cleanup",
        spec=None,
        generated_code=None,
        validation=None,
        compiled_xscr=None,
        failure_category=None,
        failure_message=None,
        attempts=3,
        tool_calls=({"name": "present_object_draft", "result": {"ok": True}},),
    )

    result = session._normalize_pending_approval_result(failure)

    assert result.status is AuthoringStatus.APPROVAL_REQUIRED
    assert result.approval_request is not None
    assert result.approval_request.kind == "objects"
    assert result.approval_request.payload == draft


def test_session_resumes_after_clarification_and_compiles() -> None:
    draft = _valid_draft()
    client = FakeClient([
        {"role": "assistant", "content": "What transfer volume should I use?", "tool_calls": []},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "lookup_workspace",
                        "arguments": json.dumps({"name_or_guid": "SAT_Fluent_780_Rev3"}),
                    },
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "get_labware",
                        "arguments": json.dumps({"name": "96_ABgene_SuperPlate_Thermo_AB2800"}),
                    },
                },
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {
                        "name": "get_labware",
                        "arguments": json.dumps({"name": "MCA96, 100ul, Box"}),
                    },
                },
                {
                    "id": "call-4",
                    "type": "function",
                    "function": {
                        "name": "lookup_liquid_class",
                        "arguments": json.dumps({"name": "Water Free Single"}),
                    },
                },
                {
                    "id": "call-5",
                    "type": "function",
                    "function": {
                        "name": "suggest_deck_layout",
                        "arguments": json.dumps({"resources": _object_draft_args()["labware"]}),
                    },
                },
                {
                    "id": "call-6",
                    "type": "function",
                    "function": {
                        "name": "lookup_rules",
                        "arguments": json.dumps({"protocol_type": "transfer"}),
                    },
                },
                {
                    "id": "call-7",
                    "type": "function",
                    "function": {
                        "name": "present_object_draft",
                        "arguments": json.dumps(_object_draft_args()),
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-groups",
                    "type": "function",
                    "function": {
                        "name": "present_functional_group_plan",
                        "arguments": json.dumps(_workflow_args()),
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-sim",
                    "type": "function",
                    "function": {
                        "name": "simulate_python_draft",
                        "arguments": json.dumps({"source": draft}),
                    },
                }
            ],
        },
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "resume",
        client=client,
    )

    first = session.send("Transfer from source to destination.")
    second = session.send("20 uL across all wells.")
    third = session.send("approved")
    fourth = session.send("approved")

    assert first.status is AuthoringStatus.CLARIFICATION_REQUIRED
    assert second.status is AuthoringStatus.APPROVAL_REQUIRED
    assert second.approval_request is not None
    assert second.approval_request.kind == "objects"
    assert third.status is AuthoringStatus.APPROVAL_REQUIRED
    assert third.approval_request is not None
    assert third.approval_request.kind == "functional_groups"
    assert fourth.status is AuthoringStatus.SUCCESS
    assert fourth.compiled_xscr is not None
    assert fourth.compiled_xscr.exists()
    assert fourth.validation is not None
    assert fourth.validation.strict_simulation_ok is True
    assert fourth.tool_calls[-1]["name"] == "compile_and_simulate"
    assert any(message["content"] == "20 uL across all wells." for message in client.seen_messages[-1])


def test_session_pushes_to_draft_after_sufficient_grounding() -> None:
    draft = _valid_draft()
    grounding_calls = [
        ("lookup_workspace", {"name_or_guid": "SAT_Fluent_780_Rev3"}),
        ("search_labware", {"query": "ABgene"}),
        ("get_labware", {"name": "96_ABgene_SuperPlate_Thermo_AB2800"}),
        ("get_labware", {"name": "MCA96, 100ul, Box"}),
        ("lookup_liquid_class", {"name": "Water Free Single"}),
        ("lookup_rules", {"protocol_type": "transfer"}),
        ("lookup_api", {"object_or_class": "wt.mca96"}),
        ("suggest_deck_layout", {"resources": _object_draft_args()["labware"]}),
    ]
    responses = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{idx}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for idx, (name, args) in enumerate(grounding_calls, start=1)
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-objects",
                    "type": "function",
                    "function": {
                        "name": "present_object_draft",
                        "arguments": json.dumps(_object_draft_args()),
                    },
                }
            ],
        },
    ]
    client = FakeClient(responses)
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "draft_pressure",
        client=client,
        retry_budget=1,
    )

    result = session.send("Transfer 20 uL across all wells.")

    assert result.status is AuthoringStatus.APPROVAL_REQUIRED
    assert result.approval_request is not None
    assert result.approval_request.kind == "objects"
    seen_before_second_response = client.seen_messages[1]
    assert any(
        message.get("role") == "user"
        and "call present_object_draft" in str(message.get("content"))
        for message in seen_before_second_response
    )


def test_session_change_request_revises_pending_object_checkpoint() -> None:
    revised = _object_draft_args()
    revised["labware"][0] = {**revised["labware"][0], "label": "SamplePlate"}
    client = FakeClient([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-workspace",
                    "type": "function",
                    "function": {
                        "name": "lookup_workspace",
                        "arguments": json.dumps({"name_or_guid": "SAT_Fluent_780_Rev3"}),
                    },
                },
                {
                    "id": "call-objects-1",
                    "type": "function",
                    "function": {
                        "name": "get_labware",
                        "arguments": json.dumps({"name": "96_ABgene_SuperPlate_Thermo_AB2800"}),
                    },
                },
                {
                    "id": "call-objects-2",
                    "type": "function",
                    "function": {
                        "name": "get_labware",
                        "arguments": json.dumps({"name": "MCA96, 100ul, Box"}),
                    },
                },
                {
                    "id": "call-objects-3",
                    "type": "function",
                    "function": {
                        "name": "lookup_liquid_class",
                        "arguments": json.dumps({"name": "Water Free Single"}),
                    },
                },
                {
                    "id": "call-objects-4",
                    "type": "function",
                    "function": {
                        "name": "suggest_deck_layout",
                        "arguments": json.dumps({"resources": _object_draft_args()["labware"]}),
                    },
                },
                {
                    "id": "call-objects-5",
                    "type": "function",
                    "function": {
                        "name": "present_object_draft",
                        "arguments": json.dumps(_object_draft_args()),
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-objects-6",
                    "type": "function",
                    "function": {
                        "name": "present_object_draft",
                        "arguments": json.dumps(revised),
                    },
                },
            ],
        },
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "approval_change",
        client=client,
    )

    first = session.send("Transfer 20 uL with Water Free Single.")
    second = session.send("Change SourcePlate to SamplePlate.")

    assert first.status is AuthoringStatus.APPROVAL_REQUIRED
    assert second.status is AuthoringStatus.APPROVAL_REQUIRED
    assert second.approval_request is not None
    assert second.approval_request.payload["labware"][0]["label"] == "SamplePlate"
    assert any(
        message.get("role") == "user"
        and "Revise that checkpoint" in str(message.get("content"))
        for message in client.seen_messages[-1]
    )


def test_session_nudges_missing_intent_axes_before_model_call() -> None:
    client = FakeClient([
        {"role": "assistant", "content": "What per-well volume and liquid class should I use?", "tool_calls": []},
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "intent_axis_nudge",
        client=client,
    )

    result = session.send("Run a cleanup on all wells.")

    assert result.status is AuthoringStatus.CLARIFICATION_REQUIRED
    first_messages = client.seen_messages[0]
    assert any(
        message.get("role") == "user"
        and "target per-well volume" in str(message.get("content"))
        and "liquid class" in str(message.get("content"))
        for message in first_messages
    )


def test_session_repair_lock_redirects_unrelated_search(monkeypatch) -> None:
    draft = _valid_draft()
    def fake_simulate(self, source, strict=True):
        return {
            "ok": False,
            "stage": "strict_simulation",
            "category": "source_volume_short",
            "message": "short",
            "failure": {"category": "source_volume_short"},
            "repair_options": ["increase_source_initial_volume"],
        }

    monkeypatch.setattr(AuthoringToolRegistry, "simulate_python_draft", fake_simulate)
    client = FakeClient([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-plan",
                    "type": "function",
                    "function": {
                        "name": "declare_protocol_workflow",
                        "arguments": json.dumps(_workflow_args()),
                    },
                },
                {
                    "id": "call-sim",
                    "type": "function",
                    "function": {
                        "name": "simulate_python_draft",
                        "arguments": json.dumps({"source": draft}),
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-search",
                    "type": "function",
                    "function": {
                        "name": "search_labware",
                        "arguments": json.dumps({"query": "plate"}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "What source fill volume should I use?", "tool_calls": []},
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "repair_lock_redirect",
        client=client,
    )
    assert session._registry.declare_protocol_workflow(**_workflow_args())["ok"] is True
    session._registry.object_draft_approved = True
    session._registry.functional_group_plan_approved = True

    result = session.send("Transfer 20 uL with Water Free Single.")

    assert result.status is AuthoringStatus.CLARIFICATION_REQUIRED
    assert [call["name"] for call in session.tool_calls] == ["declare_protocol_workflow", "simulate_python_draft"]
    assert any(
        message.get("role") == "user"
        and "Repair lock is active for `source_volume_short`" in str(message.get("content"))
        for message in client.seen_messages[-1]
    )


def test_session_repair_lock_stops_repeated_no_progress(monkeypatch) -> None:
    draft = _valid_draft()
    def fake_simulate(self, source, strict=True):
        return {
            "ok": False,
            "stage": "strict_simulation",
            "category": "source_volume_short",
            "message": "short",
            "failure": {"category": "source_volume_short"},
            "repair_options": ["increase_source_initial_volume"],
        }

    monkeypatch.setattr(AuthoringToolRegistry, "simulate_python_draft", fake_simulate)
    repeated_call = {
        "id": "call-sim",
        "type": "function",
        "function": {
            "name": "simulate_python_draft",
            "arguments": json.dumps({"source": draft}),
        },
    }
    client = FakeClient([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-plan",
                    "type": "function",
                    "function": {
                        "name": "declare_protocol_workflow",
                        "arguments": json.dumps(_workflow_args()),
                    },
                },
                repeated_call,
            ],
        },
        {"role": "assistant", "content": None, "tool_calls": [repeated_call]},
    ])
    session = PromptAuthoringSession(
        output_dir=Path("build") / "test_authoring_session" / "repair_lock_terminal",
        client=client,
    )
    assert session._registry.declare_protocol_workflow(**_workflow_args())["ok"] is True
    session._registry.object_draft_approved = True
    session._registry.functional_group_plan_approved = True

    result = session.send("Transfer 20 uL with Water Free Single.")

    assert result.status is AuthoringStatus.FAILURE
    assert result.failure_message is not None
    assert "Repeated `source_volume_short` failure" in result.failure_message
    assert result.failure_category.value != "retry_budget_exhausted"
