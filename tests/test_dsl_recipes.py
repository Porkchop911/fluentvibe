from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from fluentvibe.authoring.graph import GraphState, build_authoring_graph
from fluentvibe.authoring.models import AuthoringStatus
from fluentvibe.authoring.repair_lock import RepairLockState
from fluentvibe.authoring.tools import AuthoringToolRegistry
from fluentvibe.catalog.database import TecanDatabase
from fluentvibe.catalog.dsl_recipes import FakeRecipeEmbedder, retrieve_dsl_recipes


def test_dsl_recipe_migration_seeds_idempotently_and_preserves_existing_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "tecan.db"
    db = TecanDatabase(db_path)

    with db._connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        first_count = conn.execute("SELECT COUNT(*) FROM dsl_recipes").fetchone()[0]

    assert {"dsl_recipes", "patterns", "rules", "modules"}.issubset(tables)
    assert first_count >= 8

    db.seed_dsl_recipes()
    with db._connection() as conn:
        second_count = conn.execute("SELECT COUNT(*) FROM dsl_recipes").fetchone()[0]

    assert second_count == first_count


def test_dsl_recipe_migration_preserves_legacy_db_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE patterns (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, description TEXT, pattern_type TEXT, steps TEXT NOT NULL, frequency INTEGER DEFAULT 1, confidence REAL DEFAULT 0.5, created_at TEXT)")
    conn.execute("CREATE TABLE rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, rule_type TEXT NOT NULL, category TEXT, description TEXT NOT NULL, source TEXT NOT NULL, active INTEGER DEFAULT 1)")
    conn.execute("CREATE TABLE modules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL, active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO patterns (name, steps) VALUES ('legacy_pattern', '[]')")
    conn.execute("INSERT INTO rules (name, rule_type, description, source) VALUES ('legacy_rule', 'best_practice', 'keep', 'manual')")
    conn.execute("INSERT INTO modules (name, description) VALUES ('legacy_module', 'keep')")
    conn.commit()
    conn.close()

    TecanDatabase(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM patterns WHERE name='legacy_pattern'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM rules WHERE name='legacy_rule'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM modules WHERE name='legacy_module'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM dsl_recipes").fetchone()[0] >= 8
    finally:
        conn.close()


def test_retrieval_prioritizes_gripper_place_and_plate_subscript(tmp_path: Path) -> None:
    db = TecanDatabase(tmp_path / "tecan.db")
    embedder = FakeRecipeEmbedder()

    gripper = retrieve_dsl_recipes(
        db,
        "Gripper place missing_method",
        object_key="wt.gripper",
        failure_category="missing_method",
        bad_method="place",
        embedder=embedder,
        limit=2,
    )
    assert gripper[0]["object_key"] == "wt.gripper"
    assert "move" in gripper[0]["action"]

    plate = retrieve_dsl_recipes(
        db,
        "plate subscript",
        bad_method="__getitem__",
        embedder=embedder,
        limit=3,
    )
    assert any("plate.well('A1')" in pattern for pattern in plate[0]["good_patterns"])


def test_retrieval_excludes_inactive_recipes(tmp_path: Path) -> None:
    db = TecanDatabase(tmp_path / "tecan.db")
    db.set_dsl_recipe_active("gripper_stack_plate_onto_magnet", False)

    recipes = retrieve_dsl_recipes(
        db,
        "wt.gripper place magnet",
        object_key="wt.gripper",
        bad_method="place",
        limit=8,
    )

    assert "gripper_stack_plate_onto_magnet" not in {recipe["name"] for recipe in recipes}


def test_lookup_api_includes_recipe_examples(tmp_path: Path) -> None:
    tools = AuthoringToolRegistry(output_dir=tmp_path / "out")

    result = tools.lookup_api("wt.gripper")

    assert result["ok"] is True
    assert result["api"]["methods"][0]["signature"] == "move(labware, *, to=None, onto=None)"
    recipes = result["api"]["recipes"]
    assert recipes
    assert any(recipe["bad_pattern"] == "wt.gripper.place(source_plate, magnet_rack)" for recipe in recipes)
    assert any(
        "wt.gripper.move(source_plate, onto=magnet_rack)" in recipe["good_patterns"]
        for recipe in recipes
    )


def test_graph_injects_missing_method_recipe_and_blocks_unrelated_tool(monkeypatch, tmp_path: Path) -> None:
    def fake_simulate(self, source, strict=True):
        return {
            "ok": False,
            "category": "missing_method",
            "message": "'Gripper' object has no attribute 'place'",
            "failure": {
                "category": "missing_method",
                "object": "Gripper",
                "method": "place",
            },
            "repair_options": ["call_lookup_api", "rewrite_using_supported_method"],
        }

    monkeypatch.setattr(AuthoringToolRegistry, "simulate_python_draft", fake_simulate)
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.object_draft_approved = True
    registry.functional_group_plan_approved = True
    registry.declare_protocol_workflow(
        protocol_name="Repair test",
        summary="Repair test",
        variables=[{"name": "RunId", "default": "test"}],
        labware=[],
        groups=[
            {"name": "Variables"},
            {"name": "Labware Placement"},
            {"name": "Move"},
        ],
    )
    source = (
        "def build_worktable():\n"
        "    wt.declare_variable('RunId', 'test')\n"
        "    wt.set_sim_value('RunId', 'test')\n"
        "    wt.group('Labware Placement')\n"
        "    wt.place(source_plate, 'Nest61mm_Pos', 1)\n"
        "    wt.gripper.place(source_plate, magnet_rack)\n"
    )
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "simulate_python_draft", "args": {"source": source}, "id": "sim"}],
        ),
        AIMessage(
            content="",
            tool_calls=[{"name": "search_labware", "args": {"query": "plate"}, "id": "search"}],
        ),
        AIMessage(content="Which gripper repair should I apply?"),
    ]
    graph = build_authoring_graph(
        registry=registry,
        client=FakeMessagesListChatModel(responses=responses),
        output_dir=registry.output_dir,
        retry_budget=2,
        repair_lock=RepairLockState(),
    )
    final = graph.invoke(GraphState(
        messages=[SystemMessage(content="sys"), HumanMessage(content="prompt")],
        iterations=0,
        tool_call_count=0,
        best_code=None,
        last_validation=None,
        result=None,
        prompt="move source plate onto magnet",
    ))

    assert final["result"].status == AuthoringStatus.CLARIFICATION_REQUIRED
    assert [call["name"] for call in registry.calls] == ["simulate_python_draft"]
    assert any(
        isinstance(message, HumanMessage)
        and "You must repair this exact API misuse" in message.content
        and "wt.gripper.move(source_plate, onto=magnet_rack)" in message.content
        for message in final["messages"]
    )
    assert any(
        isinstance(message, HumanMessage)
        and "Repair lock is active for `missing_method`" in message.content
        for message in final["messages"]
    )


def test_repeated_unchanged_missing_method_returns_recipe_backed_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_simulate(self, source, strict=True):
        return {
            "ok": False,
            "category": "missing_method",
            "message": "'Gripper' object has no attribute 'place'",
            "failure": {
                "category": "missing_method",
                "object": "Gripper",
                "method": "place",
            },
            "repair_options": ["call_lookup_api", "rewrite_using_supported_method"],
        }

    monkeypatch.setattr(AuthoringToolRegistry, "simulate_python_draft", fake_simulate)
    registry = AuthoringToolRegistry(output_dir=tmp_path / "out")
    registry.object_draft_approved = True
    registry.functional_group_plan_approved = True
    registry.declare_protocol_workflow(
        protocol_name="Repair test",
        summary="Repair test",
        variables=[{"name": "RunId", "default": "test"}],
        labware=[],
        groups=[
            {"name": "Variables"},
            {"name": "Labware Placement"},
            {"name": "Move"},
        ],
    )
    source = (
        "def build_worktable():\n"
        "    wt.declare_variable('RunId', 'test')\n"
        "    wt.set_sim_value('RunId', 'test')\n"
        "    wt.group('Labware Placement')\n"
        "    wt.place(source_plate, 'Nest61mm_Pos', 1)\n"
        "    wt.gripper.place(source_plate, magnet_rack)\n"
    )
    first = AIMessage(
        content="",
        tool_calls=[{"name": "simulate_python_draft", "args": {"source": source}, "id": "sim-1"}],
    )
    second = AIMessage(
        content="",
        tool_calls=[{"name": "simulate_python_draft", "args": {"source": source}, "id": "sim-2"}],
    )
    graph = build_authoring_graph(
        registry=registry,
        client=FakeMessagesListChatModel(responses=[first, second]),
        output_dir=registry.output_dir,
        retry_budget=2,
        repair_lock=RepairLockState(),
    )
    final = graph.invoke(GraphState(
        messages=[SystemMessage(content="sys"), HumanMessage(content="prompt")],
        iterations=0,
        tool_call_count=0,
        best_code=None,
        last_validation=None,
        result=None,
        prompt="move source plate onto magnet",
    ))

    result = final["result"]
    assert result.status == AuthoringStatus.FAILURE
    payload = json.loads(result.failure_message)
    assert payload["category"] == "missing_method"
    assert payload["object"] == "Gripper"
    assert payload["method"] == "place"
    assert payload["last_draft_source_hash"]
    assert payload["retrieved_recipes"]
    assert any("move(source_plate" in pattern for recipe in payload["retrieved_recipes"] for pattern in recipe["good_patterns"])
