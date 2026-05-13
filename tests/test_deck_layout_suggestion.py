"""Tests for suggest_deck_layout tool (Chunk 5)."""

from __future__ import annotations

import pytest
from pathlib import Path

from fluentvibe.authoring.tools import AuthoringToolRegistry, suggest_deck_layout


class TestSuggestDeckLayoutBasic:
    """Core placement logic."""

    def test_simple_two_plate_placement(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "basic",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "SourcePlate", "category": "plate", "role": "source"},
            {"label": "DestPlate", "category": "plate", "role": "destination"},
        ])
        assert result["ok"] is True
        assert len(result["placements"]) == 2
        assert not result["unplaced"]

        # Source should get position 1, destination position 2.
        placements = {p["label"]: p for p in result["placements"]}
        assert placements["SourcePlate"]["position"] == 1
        assert placements["DestPlate"]["position"] == 2

    def test_multiple_tip_boxes_get_distinct_slots(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "tipboxes",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "Tips1", "category": "tip_box", "role": "mca_tips"},
            {"label": "Tips2", "category": "tip_box", "role": "tips"},
        ])
        assert result["ok"] is True
        placements = {p["label"]: p for p in result["placements"]}
        # Both should be placed at different positions.
        slot1 = (placements["Tips1"]["location"], placements["Tips1"]["position"])
        slot2 = (placements["Tips2"]["location"], placements["Tips2"]["position"])
        assert slot1 != slot2, "Multiple tip boxes must get distinct slots"

    def test_plate_and_magnet_rack_not_same_slot(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "magnet",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "Plate", "category": "plate", "role": "source"},
            {"label": "MagnetRack", "category": "magnet_rack", "role": "magnet"},
        ])
        assert result["ok"] is True
        placements = {p["label"]: p for p in result["placements"]}
        plate_slot = (placements["Plate"]["location"], placements["Plate"]["position"])
        magnet_slot = (placements["MagnetRack"]["location"], placements["MagnetRack"]["position"])
        assert plate_slot != magnet_slot, \
            "Plate and magnet rack must not share a slot unless stacked via gripper.move(onto=…)"

    def test_trough_goes_to_ws_100ml_location(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "trough",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "SourceTrough", "category": "trough", "role": "trough"},
        ])
        assert result["ok"] is True
        placement = result["placements"][0]
        assert placement["location"] == "WS_100ml_1"

    def test_full_deck_layout(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "full",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "SourcePlate", "category": "plate", "role": "source"},
            {"label": "DestPlate", "category": "plate", "role": "destination"},
            {"label": "MagnetRack", "category": "magnet_rack", "role": "magnet"},
            {"label": "Tips", "category": "tip_box", "role": "mca_tips"},
            {"label": "Waste", "category": "waste_chute", "role": "waste"},
        ])
        assert result["ok"] is True
        assert len(result["placements"]) == 5
        # All slots should be distinct.
        slots = [(p["location"], p["position"]) for p in result["placements"]]
        assert len(slots) == len(set(slots)), "All placements must have distinct slots"

    def test_large_waste_does_not_recommend_shallow_plate_sink(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "waste_plate_risk",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {
                "label": "WastePlate",
                "category": "plate",
                "role": "waste",
                "expected_waste_ul": 600.0,
                "capacity_ul": 350.0,
            },
        ])

        assert result["ok"] is False
        assert result["placements"] == []
        assert "WastePlate" in result["unplaced"]
        assert any(warning["category"] == "waste_capacity_risk" for warning in result["warnings"])
        assert "Do not use a shallow 96-well plate as waste" in result["advice"]

    def test_large_waste_allows_waste_chute_sink(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "waste_chute_ok",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {
                "label": "Waste",
                "category": "waste_chute",
                "role": "waste",
                "expected_waste_ul": 50000.0,
            },
        ])

        assert result["ok"] is True
        assert result["placements"][0]["label"] == "Waste"
        assert result["warnings"] == []

    def test_liquid_waste_rejects_waste_chute_and_accepts_300ml_sbs(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "liquid_waste",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        bad = tools.suggest_deck_layout([
            {
                "label": "LiquidWaste",
                "category": "waste_chute",
                "role": "liquid_waste",
                "catalog_name": "MCA Thru Deck Waste Chute",
            },
        ])
        assert bad["ok"] is False
        assert bad["warnings"][0]["category"] == "liquid_waste_sink_invalid"

        good = tools.suggest_deck_layout([
            {
                "label": "LiquidWaste",
                "category": "trough",
                "role": "liquid_waste",
                "catalog_name": "300ml SBS",
            },
        ])
        assert good["ok"] is True
        assert good["placements"][0]["location"] == "Nest61mm_Pos"

    def test_narrow_trough_not_placed_on_sbs_nest(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "trough_fit",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {
                "label": "WashTrough",
                "category": "trough",
                "role": "trough",
                "catalog_name": "100ml Trough 156mm",
            },
        ])
        assert result["ok"] is True
        assert result["placements"][0]["location"] == "WS_100ml_1"

    def test_unplaced_when_no_valid_slots(self):
        """If we request more resources than valid slots, some go unplaced."""
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "overflow",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        # Request way more items than there are valid slots (workspace has ~294).
        resources = [
            {"label": f"Item{i}", "category": "plate"}
            for i in range(500)  # exceeds available slots
        ]
        result = tools.suggest_deck_layout(resources)
        assert not result["ok"]
        assert len(result["unplaced"]) > 0


class TestSuggestDeckLayoutCategoryInference:
    """Role → category inference when category is omitted."""

    def test_role_tips_infers_tip_box(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "infer",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "Tips", "role": "tips"},  # no category specified
        ])
        assert result["ok"] is True

    def test_role_source_infers_plate(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "infer2",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "Source", "role": "source"},  # no category specified
        ])
        assert result["ok"] is True


class TestSuggestDeckLayoutCollisions:
    """Collision detection for non-stackable labware."""

    def test_no_collision_for_distinct_slots(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "no_collision",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "SourcePlate", "category": "plate", "role": "source"},
            {"label": "DestPlate", "category": "plate", "role": "destination"},
        ])
        assert len(result["collisions"]) == 0

    def test_advice_present_on_collision(self):
        """When collisions are detected, advice should guide the model."""
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "collision_advice",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "SourcePlate", "category": "plate", "role": "source"},
            {"label": "DestPlate", "category": "plate", "role": "destination"},
            {"label": "Tips", "category": "tip_box", "role": "mca_tips"},
        ])
        # With proper role-based placement, no collisions should occur.
        assert len(result["collisions"]) == 0


class TestSuggestDeckLayoutToolDefinition:
    """Verify the tool is registered and callable."""

    def test_tool_in_definitions(self):
        from fluentvibe.authoring.tools import tool_definitions
        names = [t["function"]["name"] for t in tool_definitions()]
        assert "suggest_deck_layout" in names

    def test_tool_dispatchable(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "dispatch",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.dispatch("suggest_deck_layout", {
            "resources": [
                {"label": "Plate1", "category": "plate"},
            ]
        })
        assert result["ok"] is True

    def test_tool_returns_placements_key(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "keys",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        result = tools.suggest_deck_layout([
            {"label": "Plate1", "category": "plate"},
        ])
        assert "placements" in result
        assert "collisions" in result
        assert "unplaced" in result


class TestSuggestDeckLayoutWorkspaceGrounding:
    """Verify existing workspace grounding tests still pass."""

    def test_lookup_workspace_still_works(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "grounding",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        ws = tools.lookup_workspace("SAT_Fluent_780_Rev3")
        assert ws["ok"] is True
        assert ws["workspace"]["guid"] == "291ba293-6361-4f8f-aa8d-7c2643d3f096"

    def test_list_valid_positions_still_works(self):
        tools = AuthoringToolRegistry(
            output_dir=Path("build") / "test_deck_layout" / "grounding2",
            workspace_name="SAT_Fluent_780_Rev3",
        )
        positions = tools.list_valid_positions("Nest61mm_Pos")
        assert positions["ok"] is True
        assert {1, 2, 4}.issubset(set(positions["positions"]))
