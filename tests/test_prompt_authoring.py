from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from fluentvibe.authoring import PromptAuthoringService, PromptAuthoringSession
from fluentvibe.authoring.lm_client import DEFAULT_LM_STUDIO_ENDPOINT, DEFAULT_LM_STUDIO_MODEL
from fluentvibe.authoring.models import AuthoringStatus
from fluentvibe.authoring.tools import AuthoringToolRegistry


def _valid_draft() -> str:
    return '''"""Simple live-authoring validator fixture."""

from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Tool Valid Draft",
        comment="20 uL transfer validator fixture",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    water = Reagent("Water")
    wt.group("Labware Placement")
    source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    source.fill_all(water, 80.0)

    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(source, 20.0, liquid_class="Water Free Single")
    head.dispense(dest, 20.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    return wt
'''


def _invalid_draft() -> str:
    return _valid_draft().replace("source.fill_all(water, 80.0)", "source.fill_all(water, 5.0)")


def _tip_capacity_draft() -> str:
    return (
        _valid_draft()
        .replace("source.fill_all(water, 80.0)", "source.fill_all(water, 300.0)")
        .replace("20.0", "200.0")
    )


def _well_overflow_draft() -> str:
    return (
        _valid_draft()
        .replace("Plate96, MCA100Box", "Plate96, MCA500Box")
        .replace("MCA100Box(\"Tips\"", "MCA500Box(\"Tips\"")
        .replace("MCA96, 100ul, Box", "MCA96, 500ul, Box")
        .replace("source.fill_all(water, 80.0)", "source.fill_all(water, 500.0)")
        .replace("20.0", "400.0")
    )


def _tip_box_empty_draft() -> str:
    return _valid_draft().replace(
        "    head.pick_up(tips)\n",
        "    head.pick_up(tips)\n    head.pick_up(tips)\n",
    )


def _trough_short_volume_draft() -> str:
    return (
        _valid_draft()
        .replace("from fluentvibe import Worktable, Reagent, Plate96, MCA100Box", "from fluentvibe import Worktable, Reagent, Plate96, Trough100mL, FCA1000Box")
        .replace('source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)', 'source = wt.place(Trough100mL("SourceTrough", catalog="100ml Trough 156mm"), "WS_100ml_1", 1)')
        .replace('tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)', 'tips = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)')
        .replace("source.fill_all(water, 80.0)", "source.fill_all(water, 5.0)")
        .replace(
            """    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(source, 20.0, liquid_class="Water Free Single")
    head.dispense(dest, 20.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
""",
            """    head = wt.liha
    head.get_tips(tips)
    head.aspirate(source, 20.0, liquid_class="Water Free Single")
    head.dispense(dest, 20.0, liquid_class="Water Free Single", well_offset=0)
    head.drop_tips()
""",
        )
    )


def _setup_only_draft() -> str:
    return _valid_draft().replace(
        """    head.aspirate(source, 20.0, liquid_class="Water Free Single")
    head.dispense(dest, 20.0, liquid_class="Water Free Single")
""",
        "",
    )


def _lm_studio_available() -> bool:
    models_url = DEFAULT_LM_STUDIO_ENDPOINT.rsplit("/", 2)[0] + "/models"
    try:
        with urllib.request.urlopen(models_url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return any(item.get("id") == DEFAULT_LM_STUDIO_MODEL for item in payload.get("data", []))


def test_authoring_db_tools_return_installed_data() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "tools")

    workspace = tools.lookup_workspace("SAT_Fluent_780_Rev3")
    assert workspace["ok"] is True
    assert workspace["workspace"]["guid"] == "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    assert 1 in workspace["valid_positions"]["Nest61mm_Pos"]

    positions = tools.list_valid_positions("Nest61mm_Pos")
    assert positions["ok"] is True
    assert {1, 2, 4}.issubset(set(positions["positions"]))

    labware = tools.get_labware("96_ABgene_SuperPlate_Thermo_AB2800")
    assert labware["ok"] is True
    assert labware["labware"]["python_class"] == "Plate96"
    assert labware["labware"]["pipettable"]["well_count"] == 96

    liquid = tools.lookup_liquid_class("Water Free Single")
    assert liquid["ok"] is True
    assert liquid["liquid_class"]["name"] == "Water Free Single"

    mca_liquid = tools.lookup_liquid_class("Water Free Single", device_type="Mca")
    assert mca_liquid["ok"] is True
    assert mca_liquid["liquid_class"]["name"] == "Water Free Single"
    assert any(head.startswith("Mca") for head in mca_liquid["liquid_class"]["supported_heads"])

    rules = tools.lookup_rules(protocol_type="transfer")
    assert rules["ok"] is True
    assert set(rules).issuperset({"rules", "modules", "patterns"})


def test_authoring_lookup_api_returns_supported_methods() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "api_tools")

    gripper = tools.lookup_api("wt.gripper")
    assert gripper["ok"] is True
    assert gripper["api"]["object"] == "wt.gripper"
    assert gripper["api"]["methods"][0]["signature"] == "move(labware, *, to=None, onto=None)"
    assert "wt.gripper.move(plate, onto=magnet)" in gripper["api"]["methods"][0]["examples"]

    liha = tools.lookup_api("wt.liha")
    assert liha["ok"] is True
    method_names = {method["name"] for method in liha["api"]["methods"]}
    assert {"get_tips", "aspirate", "dispense", "mix", "empty_tips", "drop_tips"}.issubset(method_names)
    assert "pick_up" not in method_names
    assert "pick_up" in liha["api"]["forbidden_common_mistakes"]

    fca = tools.lookup_api("wt.fca")
    assert fca["ok"] is True
    assert fca["api"]["object"] == "wt.fca"
    assert fca["api"]["aliased_to"] == "wt.liha"
    assert "wt.liha" in fca["api"]["note"]
    fca_alias = tools.lookup_api("fca")
    assert fca_alias["ok"] is True
    assert fca_alias["api"]["aliased_to"] == "wt.liha"

    worktable = tools.lookup_api("worktable")
    assert worktable["ok"] is True
    worktable_methods = {method["name"] for method in worktable["api"]["methods"]}
    assert {"worklist", "execute_worklist"}.issubset(worktable_methods)


def test_authoring_simulator_tools_return_structured_success_and_failure() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "sim_tools")

    sim_ok = tools.simulate_python_draft(_valid_draft(), strict=True)
    assert sim_ok["ok"] is True
    assert sim_ok["stage"] == "strict_simulation"
    assert sim_ok["state_summary"]["deck_occupancy"]["occupied_count"] == 3
    assert "wells" not in sim_ok["state_summary"]["labware_volumes"]["DestPlate"]

    compile_ok = tools.compile_and_simulate(_valid_draft())
    assert compile_ok["ok"] is True
    assert Path(compile_ok["xscr_path"]).exists()
    assert compile_ok["state_summary"]["tip_state"]["mca96"]["mounted_count"] == 0

    sim_bad = tools.simulate_python_draft(_invalid_draft(), strict=True)
    assert sim_bad["ok"] is False
    assert sim_bad["stage"] == "strict_simulation"
    assert sim_bad["category"] == "source_volume_short"
    assert sim_bad["failure"]["labware"] == "SourcePlate"
    assert sim_bad["failure"]["short_by_ul"] > 0
    assert sim_bad["state_summary"]["reagent_source_sufficiency"]["source_label"] == "SourcePlate"
    assert sim_bad["message"]

    trough_short = tools.simulate_python_draft(_trough_short_volume_draft(), strict=True)
    assert trough_short["ok"] is False
    assert trough_short["category"] == "source_volume_short"
    assert trough_short["failure"]["labware"] == "SourceTrough"
    assert trough_short["failure"]["well"] == "A1"
    assert trough_short["failure"]["short_by_ul"] == 15.0
    assert trough_short["state_summary"]["reagent_source_sufficiency"]["short_by_ul"] == 15.0


def test_authoring_simulator_state_summary_tracks_running_source_requests() -> None:
    """reagent_source_sufficiency.sources is populated on every run, not only on
    source_volume_short failures (Chunk 3 generalisation)."""
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "source_tally")

    sim_ok = tools.simulate_python_draft(_valid_draft(), strict=True)
    assert sim_ok["ok"] is True
    sources = sim_ok["state_summary"]["reagent_source_sufficiency"]["sources"]
    assert "SourcePlate" in sources
    # 96 wells * 20 uL each = 1920 uL requested.
    assert sources["SourcePlate"]["total_requested_ul"] == pytest.approx(1920.0)
    # Source had 80 uL/well * 96 = 7680 uL; after 1920 uL drawn, 5760 uL remains.
    assert sources["SourcePlate"]["remaining_volume_ul"] == pytest.approx(5760.0)
    assert sources["SourcePlate"]["well_count"] == 96
    assert sources["SourcePlate"]["max_per_well_requested_ul"] == pytest.approx(20.0)
    assert len(sources["SourcePlate"]["wells_drawn"]) <= 12
    assert sources["SourcePlate"]["sample_wells_drawn"]["A1"] == pytest.approx(20.0)
    assert sources["SourcePlate"]["wells_drawn_truncated"] is True

    # The failure-specific shape still works on source_volume_short.
    sim_bad = tools.simulate_python_draft(_invalid_draft(), strict=True)
    assert sim_bad["ok"] is False
    sufficiency = sim_bad["state_summary"]["reagent_source_sufficiency"]
    assert sufficiency["source_label"] == "SourcePlate"
    assert "SourcePlate" in sufficiency["sources"]


def test_plan_protocol_resources_sums_sources_waste_and_tip_pressure() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "resource_plan")

    result = tools.plan_protocol_resources([
        {
            "source_label": "Reagent",
            "destination_label": "Plate",
            "volume_ul": 20.0,
            "well_count": 96,
            "repetitions": 1,
            "source_fill_volume_ul": 15.0,
            "tip_capacity_ul": 100.0,
        },
        {
            "source_label": "Wash",
            "destination_label": "Plate",
            "waste_label": "Waste",
            "volume_ul": 200.0,
            "well_count": 96,
            "repetitions": 2,
            "split_volume_ul": 100.0,
            "waste_capacity_ul": 350.0,
            "tip_capacity_ul": 100.0,
        },
    ])

    assert result["ok"] is True
    sources = {source["source_label"]: source for source in result["sources"]}
    assert sources["Reagent"]["required_volume_ul"] == pytest.approx(1920.0)
    assert sources["Reagent"]["recommended_fill_volume_ul"] == pytest.approx(2112.0)
    assert sources["Wash"]["required_volume_ul"] == pytest.approx(38400.0)
    waste = {entry["waste_label"]: entry for entry in result["waste"]}
    assert waste["Waste"]["expected_waste_ul"] == pytest.approx(38400.0)
    assert result["minimum_tip_capacity_ul"] == pytest.approx(100.0)
    warning_categories = {warning["category"] for warning in result["warnings"]}
    assert "source_underfilled" in warning_categories
    assert "waste_capacity_risk" in warning_categories


def test_plan_protocol_resources_flags_tip_capacity_without_protocol_specific_fields() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "resource_plan_tip")

    result = tools.dispatch("plan_protocol_resources", {
        "phases": [
            {
                "source_label": "Source",
                "destination_label": "Dest",
                "volume_ul": 150.0,
                "well_count": 96,
                "repetitions": 1,
                "tip_capacity_ul": 100.0,
            }
        ]
    })

    assert result["ok"] is True
    assert result["minimum_tip_capacity_ul"] == pytest.approx(150.0)
    assert any(warning["category"] == "tip_capacity_risk" for warning in result["warnings"])


def test_cleanup_resource_variables_and_tip_split_are_generic() -> None:
    tools = _ground_object_draft_tools("generic_phase_variables")
    cleanup = _valid_object_labware() + [{
        "label": "LiquidWaste",
        "role": "liquid_waste",
        "python_class": "Waste",
        "catalog_name": "300ml SBS",
        "location": "Nest61mm_Pos",
        "position": 5,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": cleanup})

    tools.dispatch("plan_protocol_resources", {"phases": [{
        "phase": "wash",
        "source_label": "SourcePlate",
        "waste_label": "LiquidWaste",
        "volume_ul": 200.0,
        "well_count": 96,
        "source_fill_volume_ul": 250.0,
        "waste_capacity_ul": 300000.0,
        "tip_capacity_ul": 100.0,
        "liquid_class": "Water Free Single",
    }]})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Cleanup",
        "summary": "Multi-step cleanup with wash and liquid waste.",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": cleanup,
    })
    assert result["ok"] is False
    assert any(
        warning["category"] == "missing_phase_variable"
        for error in result["errors"]
        for warning in error.get("warnings", [])
    )

    tools = _ground_object_draft_tools("generic_phase_variables_split")
    tools.dispatch("suggest_deck_layout", {"resources": cleanup})
    plan = tools.dispatch("plan_protocol_resources", {"phases": [{
        "phase": "wash",
        "source_label": "SourcePlate",
        "waste_label": "LiquidWaste",
        "volume_ul": 200.0,
        "volume_variable": "VOLUME_WASH_UL",
        "split_volume_ul": 100.0,
        "split_volume_variable": "VOLUME_WASH_SPLIT_UL",
        "well_count": 96,
        "source_fill_volume_ul": 250.0,
        "source_fill_variable": "SOURCE_FILL_WASH_UL",
        "waste_capacity_ul": 300000.0,
        "tip_capacity_ul": 100.0,
        "liquid_class": "Water Free Single",
        "liquid_class_variable": "LIQUID_CLASS_WASH",
    }]})
    assert plan["minimum_tip_capacity_ul"] == pytest.approx(100.0)
    assert plan["max_single_operation_volume_ul"] == pytest.approx(200.0)
    assert plan["required_split_plan"][0]["split_volume_variable"] == "VOLUME_WASH_SPLIT_UL"

    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Cleanup",
        "summary": "Multi-step cleanup with wash and liquid waste.",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "variables": [
            {"name": "VOLUME_WASH_UL", "default": 200.0, "sim_value": 200.0},
            {"name": "VOLUME_WASH_SPLIT_UL", "default": 100.0, "sim_value": 100.0},
            {"name": "SOURCE_FILL_WASH_UL", "default": 250.0, "sim_value": 250.0},
        ],
        "liquid_classes": [{"name": "Water Free Single", "variable": "LIQUID_CLASS_WASH"}],
        "labware": cleanup,
    })
    assert result["ok"] is True


def _ground_object_draft_tools(path_name: str) -> AuthoringToolRegistry:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / path_name)
    tools.dispatch("lookup_workspace", {"name_or_guid": "SAT_Fluent_780_Rev3"})
    tools.dispatch("get_labware", {"name": "96_ABgene_SuperPlate_Thermo_AB2800"})
    tools.dispatch("get_labware", {"name": "MCA96, 100ul, Box"})
    tools.dispatch("get_labware", {"name": "FCA, 1000ul SBS"})
    tools.dispatch("get_labware", {"name": "24 Magnet Plate"})
    tools.dispatch("get_labware", {"name": "MCA Thru Deck Waste Chute"})
    tools.dispatch("get_labware", {"name": "300ml SBS"})
    tools.dispatch("get_labware", {"name": "100ml Trough 156mm"})
    tools.dispatch("get_labware", {"name": "LV_Alpaqua_A000350"})
    tools.dispatch("lookup_liquid_class", {"name": "Water Free Single"})
    return tools


def _valid_object_labware() -> list[dict[str, object]]:
    return [
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
    ]


def test_present_object_draft_rejects_unknown_class_and_catalog_mismatch() -> None:
    tools = _ground_object_draft_tools("object_gate_unknown_class")
    labware = _valid_object_labware()
    labware[2] = {**labware[2], "python_class": "FCATipBox"}
    tools.dispatch("suggest_deck_layout", {"resources": labware})

    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Invalid objects",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": labware,
    })

    assert result["ok"] is False
    assert result["category"] == "object_draft_invalid"
    assert any(error.get("received") == "FCATipBox" for error in result["errors"])

    mismatch = _valid_object_labware()
    mismatch[2] = {**mismatch[2], "python_class": "FCA1000Box"}
    tools.dispatch("suggest_deck_layout", {"resources": mismatch})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Invalid objects",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": mismatch,
    })
    assert result["ok"] is False
    assert any(error.get("catalog_name") == "MCA96, 100ul, Box" for error in result["errors"])


def test_present_object_draft_rejects_missing_catalog_and_accepts_grounded_examples() -> None:
    tools = _ground_object_draft_tools("object_gate_catalogs")
    labware = _valid_object_labware() + [
        {
            "label": "Magnet",
            "role": "magnet",
            "python_class": "MagnetRack",
            "catalog_name": "SPRIPlate 96R Ring Super Magnet Plate",
            "location": "Nest61mm_Pos",
            "position": 3,
        }
    ]
    tools.dispatch("suggest_deck_layout", {"resources": labware})

    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Invalid catalog",
        "summary": "Cleanup workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": labware,
    })
    assert result["ok"] is False
    assert any("not installed" in error["message"] for error in result["errors"])

    valid = _valid_object_labware() + [
        {
            "label": "Magnet",
            "role": "magnet",
            "python_class": "MagnetRack",
            # 96-well magnet: must match the 96-well source/dest plate
            # footprint. A 24-well magnet here is correctly rejected by
            # _validate_object_draft's plate↔magnet layout consistency check.
            "catalog_name": "LV_Alpaqua_A000350",
            "location": "Nest61mm_Pos",
            "position": 3,
        },
        {
            "label": "FcaTips",
            "role": "fca_tips",
            "python_class": "FCA1000Box",
            "catalog_name": "FCA, 1000ul SBS",
            "location": "Nest61mm_Pos",
            "position": 6,
        },
    ]
    tools.dispatch("suggest_deck_layout", {"resources": valid})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Valid objects",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": valid,
    })
    assert result["ok"] is True
    assert result["status"] == "needs_approval"


def test_present_object_draft_requires_exact_grounding_layout_and_resource_plan() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "object_gate_grounding")
    tools.dispatch("lookup_workspace", {"name_or_guid": "SAT_Fluent_780_Rev3"})
    tools.dispatch("lookup_liquid_class", {"name": "Water Free Single"})
    labware = _valid_object_labware()

    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Ungrounded objects",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": labware,
    })
    assert result["ok"] is False
    categories = {error["field"] for error in result["errors"]}
    assert "labware[0].catalog_name" in categories
    assert "labware" in categories

    tools = _ground_object_draft_tools("object_gate_cleanup_resources")
    cleanup = _valid_object_labware() + [
        {
            "label": "Waste",
            "role": "liquid_waste",
            "python_class": "Waste",
            "catalog_name": "300ml SBS",
            "location": "Nest61mm_Pos",
            "position": 5,
        }
    ]
    tools.dispatch("suggest_deck_layout", {"resources": cleanup})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Cleanup",
        "summary": "Multi-step cleanup with wash and bulk liquid waste.",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": cleanup,
    })
    assert result["ok"] is False
    assert any(error["field"] == "summary" for error in result["errors"])

    tools.dispatch("plan_protocol_resources", {"phases": [
            {
                "source_label": "SourcePlate",
                "waste_label": "Waste",
                "volume_ul": 100.0,
                "volume_variable": "VOLUME_WASTE_REMOVAL_UL",
                "well_count": 96,
                "repetitions": 1,
                "source_fill_volume_ul": 200.0,
                "source_fill_variable": "SOURCE_FILL_UL",
                "waste_capacity_ul": 300000.0,
                "tip_capacity_ul": 100.0,
            }
    ]})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Cleanup",
        "summary": "Multi-step cleanup with wash and bulk liquid waste.",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "variables": [
            {"name": "VOLUME_WASTE_REMOVAL_UL", "default": 100.0, "sim_value": 100.0},
            {"name": "SOURCE_FILL_UL", "default": 200.0, "sim_value": 200.0},
        ],
        "labware": cleanup,
    })
    assert result["ok"] is True


def test_catalog_semantic_overrides_reject_magnet_as_plate_and_wrong_layout() -> None:
    tools = _ground_object_draft_tools("semantic_catalogs")

    alpaqua = tools.get_labware("LV_Alpaqua_A000350")
    assert alpaqua["ok"] is True
    assert alpaqua["labware"]["category"] == "magnet_rack"
    assert alpaqua["labware"]["python_class"] == "MagnetRack"
    assert alpaqua["labware"]["layout"] == "96"

    search = tools.search_labware("Alpaqua", category="magnet_rack")
    assert any(match["name"] == "LV_Alpaqua_A000350" for match in search["matches"])

    carrier = tools.get_labware("Landscape Nest Magnet Teleshake Segment")
    assert carrier["ok"] is True
    assert carrier["labware"]["category"] == "fixed_deck"
    assert carrier["labware"]["functional_group"] == "Carrier.Deck Segment"
    assert carrier["labware"]["component_kind"] == "carrier"
    assert carrier["labware"]["component_subtype"] == "deck_segment"
    assert carrier["labware"]["python_class"] == "FixedDeck"
    magnet_search = tools.search_labware("Landscape Nest Magnet", category="magnet_rack")
    assert not any(match["name"] == "Landscape Nest Magnet Teleshake Segment" for match in magnet_search["matches"])

    bad = _valid_object_labware() + [{
        "label": "Magnet",
        "role": "magnet",
        "python_class": "Plate96",
        "catalog_name": "LV_Alpaqua_A000350",
        "layout": "96",
        "location": "Nest61mm_Pos",
        "position": 3,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": bad})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad magnet",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": bad,
    })
    assert result["ok"] is False
    assert any(error.get("suggested_python_class") == "MagnetRack" for error in result["errors"])

    carrier_as_magnet = _valid_object_labware() + [{
        "label": "CarrierMagnet",
        "role": "magnet",
        "python_class": "MagnetRack",
        "catalog_name": "Landscape Nest Magnet Teleshake Segment",
        "layout": "96",
        "location": "Nest61mm_Pos",
        "position": 3,
    }]
    tools.get_labware("Landscape Nest Magnet Teleshake Segment")
    tools.dispatch("suggest_deck_layout", {"resources": carrier_as_magnet})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Carrier magnet rejected",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": carrier_as_magnet,
    })
    assert result["ok"] is False
    assert any(error.get("component_kind") == "carrier" and error.get("component_subtype") == "deck_segment" for error in result["errors"])

    wrong_layout = _valid_object_labware() + [{
        "label": "Magnet",
        "role": "magnet",
        "python_class": "MagnetRack",
        "catalog_name": "24 Magnet Plate",
        "layout": "96",
        "location": "Nest61mm_Pos",
        "position": 3,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": wrong_layout})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad magnet layout",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": wrong_layout,
    })
    assert result["ok"] is False
    assert any(error.get("catalog_layout") == "24" for error in result["errors"])

    wrong_layout_implicit = _valid_object_labware() + [{
        "label": "Magnet",
        "role": "magnet",
        "python_class": "MagnetRack",
        "catalog_name": "24 Magnet Plate",
        "location": "Nest61mm_Pos",
        "position": 3,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": wrong_layout_implicit})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad implicit magnet layout",
        "summary": "96-well workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": wrong_layout_implicit,
    })
    assert result["ok"] is False
    assert any(error.get("expected_layout") == "96" and error.get("catalog_layout") == "24" for error in result["errors"])


def test_waste_roles_and_physical_fit_are_enforced() -> None:
    tools = _ground_object_draft_tools("waste_roles_fit")

    liquid_chute = _valid_object_labware() + [{
        "label": "LiquidWaste",
        "role": "liquid_waste",
        "python_class": "WasteChute",
        "catalog_name": "MCA Thru Deck Waste Chute",
        "location": "Nest61mm_Pos",
        "position": 5,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": liquid_chute})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad waste",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": liquid_chute,
    })
    assert result["ok"] is False
    assert any("WasteChute cannot be used" in error["message"] for error in result["errors"])

    reservoir = _valid_object_labware() + [{
        "label": "LiquidWaste",
        "role": "liquid_waste",
        "python_class": "Waste",
        "catalog_name": "300ml SBS",
        "location": "Nest61mm_Pos",
        "position": 5,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": reservoir})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Good waste",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": reservoir,
    })
    assert result["ok"] is True

    trough_on_sbs = _valid_object_labware() + [{
        "label": "Trough",
        "role": "trough",
        "python_class": "Trough100mL",
        "catalog_name": "100ml Trough 156mm",
        "location": "Nest61mm_Pos",
        "position": 5,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": trough_on_sbs})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad trough",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": trough_on_sbs,
    })
    assert result["ok"] is False
    assert any("does not physically fit" in error["message"] for error in result["errors"])

    liquid_waste_trough_on_sbs = _valid_object_labware() + [{
        "label": "LiquidWaste",
        "role": "liquid_waste",
        "python_class": "Trough100mL",
        "catalog_name": "100ml Trough Double",
        "location": "Nest61mm_Pos",
        "position": 5,
    }]
    tools.dispatch("suggest_deck_layout", {"resources": liquid_waste_trough_on_sbs})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad liquid waste trough",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": liquid_waste_trough_on_sbs,
    })
    assert result["ok"] is False
    assert any("does not physically fit" in error["message"] for error in result["errors"])


def test_present_object_draft_rejects_fca_tips_for_mca_workflow() -> None:
    tools = _ground_object_draft_tools("reject_fca_tips")
    labware = _valid_object_labware()
    labware[2] = {
        "label": "Tips",
        "role": "tips",
        "python_class": "FCA1000Box",
        "catalog_name": "FCA, 1000ul SBS",
        "location": "Nest61mm_Pos",
        "position": 4,
    }
    tools.dispatch("get_labware", {"name": "FCA, 1000ul SBS"})
    tools.dispatch("suggest_deck_layout", {"resources": labware})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Bad tips",
        "summary": "96-well MCA workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": labware,
    })
    assert result["ok"] is False
    assert any("not FCA tip boxes" in error["message"] for error in result["errors"])


def _approve_simple_object_draft(path_name: str, *, liquid_class: bool = False) -> AuthoringToolRegistry:
    tools = _ground_object_draft_tools(path_name)
    labware = _valid_object_labware()
    tools.dispatch("suggest_deck_layout", {"resources": labware})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Approved objects",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "liquid_classes": [{"name": "Water Free Single", "variable": "lc_water"}] if liquid_class else [],
        "labware": labware,
    })
    assert result["ok"] is True
    tools.approve_pending("objects")
    return tools


def test_simulate_python_draft_enforces_approved_objects() -> None:
    tools = _approve_simple_object_draft("approved_object_enforcement")

    swapped_class = _valid_draft().replace("MCA100Box(\"Tips\"", "FCA1000Box(\"Tips\"")
    result = tools.simulate_python_draft(swapped_class, strict=True)
    assert result["ok"] is False
    assert result["stage"] == "contract"
    assert "changes approved class" in result["message"]

    changed_catalog = _valid_draft().replace("MCA96, 100ul, Box", "FCA, 1000ul SBS")
    result = tools.simulate_python_draft(changed_catalog, strict=True)
    assert result["ok"] is False
    assert "changes approved catalog" in result["message"]

    omitted = _valid_draft().replace(
        '    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)\n',
        "",
    )
    result = tools.simulate_python_draft(omitted, strict=True)
    assert result["ok"] is False
    assert "omits approved labware" in result["message"]


def test_simulate_python_draft_enforces_approved_liquid_class_variables() -> None:
    tools = _approve_simple_object_draft("approved_liquid_class_enforcement", liquid_class=True)

    result = tools.simulate_python_draft(_valid_draft(), strict=True)
    assert result["ok"] is False
    # Hardcoded class string literal is still rejected; any declared
    # liquid-class variable (not only the one approved name) is accepted.
    assert "declared liquid-class variable" in result["message"]

    variable_draft = (
        _valid_draft()
        .replace('water = Reagent("Water")', 'water = Reagent("Water")\n    lc_water = "Water Free Single"')
        .replace('liquid_class="Water Free Single"', 'liquid_class=lc_water')
    )
    result = tools.simulate_python_draft(variable_draft, strict=True)
    assert result["ok"] is True


def test_simulate_python_draft_enforces_approved_phase_volume_variables() -> None:
    tools = _ground_object_draft_tools("approved_phase_volume_enforcement")
    labware = _valid_object_labware()
    tools.dispatch("suggest_deck_layout", {"resources": labware})
    tools.dispatch("plan_protocol_resources", {"phases": [{
        "phase": "transfer",
        "source_label": "SourcePlate",
        "destination_label": "DestPlate",
        "volume_ul": 20.0,
        "volume_variable": "TRANSFER_UL",
        "well_count": 96,
        "tip_capacity_ul": 100.0,
    }]})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Approved phase variables",
        "summary": "Transfer workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "variables": [{"name": "TRANSFER_UL", "default": 20.0, "sim_value": 20.0}],
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": labware,
    })
    assert result["ok"] is True
    tools.approve_pending("objects")

    result = tools.simulate_python_draft(_valid_draft(), strict=True)
    assert result["ok"] is False
    assert "must be used through variable" in result["message"]

    variable_draft = (
        _valid_draft()
        .replace("20.0", "transfer_ul")
        .replace('water = Reagent("Water")', 'water = Reagent("Water")\n    transfer_ul = 20.0')
        .replace("comment=\"transfer_ul uL transfer validator fixture\"", "comment=\"20 uL transfer validator fixture\"")
    )
    result = tools.simulate_python_draft(variable_draft, strict=True)
    assert result["ok"] is False
    assert "TRANSFER_UL" in result["message"]

    variable_draft = (
        _valid_draft()
        .replace("20.0", "TRANSFER_UL")
        .replace('water = Reagent("Water")', 'water = Reagent("Water")\n    TRANSFER_UL = 20.0')
        .replace("comment=\"TRANSFER_UL uL transfer validator fixture\"", "comment=\"20 uL transfer validator fixture\"")
    )
    result = tools.simulate_python_draft(variable_draft, strict=True)
    assert result["ok"] is True


def test_simulate_python_draft_enforces_trough_fill_volume() -> None:
    tools = _ground_object_draft_tools("trough_fill_contract")
    labware = [
        {
            "label": "SourceTrough",
            "role": "source",
            "python_class": "Trough100mL",
            "catalog_name": "100ml Trough 156mm",
            "location": "WS_100ml_1",
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
            "role": "fca_tips",
            "python_class": "FCA1000Box",
            "catalog_name": "FCA, 1000ul SBS",
            "location": "Nest61mm_Pos",
            "position": 6,
        },
    ]
    tools.dispatch("suggest_deck_layout", {"resources": labware})
    tools.dispatch("plan_protocol_resources", {"phases": [{
        "phase": "dispense",
        "source_label": "SourceTrough",
        "destination_label": "DestPlate",
        "volume_ul": 200.0,
        "volume_variable": "WASH_VOLUME_UL",
        "well_count": 96,
        "repetitions": 2,
        "tip_capacity_ul": 1000.0,
    }]})
    result = tools.dispatch("present_object_draft", {
        "protocol_name": "Trough fill contract",
        "summary": "Trough dispense workflow",
        "workspace": {"name": "SAT_Fluent_780_Rev3"},
        "variables": [{"name": "WASH_VOLUME_UL", "default": 200.0, "sim_value": 200.0}],
        "liquid_classes": [{"name": "Water Free Single"}],
        "labware": labware,
    })
    assert result["ok"] is True
    tools.approve_pending("objects")

    underfilled_draft = '''"""Trough fill validator fixture."""

from fluentvibe import Worktable, Reagent, Plate96, Trough100mL, FCA1000Box


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Trough Fill",
        comment="Trough fill contract fixture",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    wt.declare_variable("WASH_VOLUME_UL", 200.0)
    wt.set_sim_value("WASH_VOLUME_UL", 200.0)
    WASH_VOLUME_UL = 200.0
    water = Reagent("Water")
    wt.group("Labware Placement")
    source = wt.place(Trough100mL("SourceTrough", catalog="100ml Trough 156mm"), "WS_100ml_1", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
    source.fill_all(water, 1000.0)

    wt.group("Dispense")
    head = wt.liha
    head.get_tips(tips)
    head.aspirate(source, WASH_VOLUME_UL, liquid_class="Water Free Single")
    head.dispense(dest, WASH_VOLUME_UL, liquid_class="Water Free Single", well_offset=0)
    head.drop_tips()
    return wt
'''
    result = tools.simulate_python_draft(underfilled_draft, strict=True)
    assert result["ok"] is False
    assert "SourceTrough" in result["message"]
    assert "fill_all" in result["message"]

    sufficient_draft = underfilled_draft.replace(
        "source.fill_all(water, 1000.0)",
        "source.fill_all(water, 50000.0)",
    )
    result = tools.simulate_python_draft(sufficient_draft, strict=True)
    assert result["ok"] is True


def test_authoring_simulator_tools_return_structured_repair_facts() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "structured_failures")

    tip_capacity = tools.simulate_python_draft(_tip_capacity_draft(), strict=True)
    assert tip_capacity["ok"] is False
    assert tip_capacity["category"] == "tip_capacity"
    assert tip_capacity["failure"]["requested_volume_ul"] == 200.0
    assert tip_capacity["failure"]["capacity_ul"] == 100.0
    assert "use_higher_capacity_tips" in tip_capacity["repair_options"]

    overflow = tools.simulate_python_draft(_well_overflow_draft(), strict=True)
    assert overflow["ok"] is False
    assert overflow["category"] == "well_overflow"
    assert overflow["failure"]["well"] == "A1"
    assert overflow["failure"]["current_volume_ul"] == 0.0
    assert overflow["failure"]["attempted_delta_ul"] == 400.0
    assert overflow["failure"]["capacity_ul"] == 350.0
    assert overflow["state_summary"]["labware_volumes"]["DestPlate"]["failing_wells"]["A1"]["volume_ul"] == 0.0

    empty_tips = tools.simulate_python_draft(_tip_box_empty_draft(), strict=True)
    assert empty_tips["ok"] is False
    assert empty_tips["category"] == "tip_box_empty"
    assert empty_tips["failure"]["tip_box"] == "Tips"
    tip_boxes = empty_tips["state_summary"]["tip_state"]["tip_boxes"]
    assert any(box["label"] == "Tips" and box["consumed"] is True for box in tip_boxes)
    assert empty_tips["state_summary"]["tip_state"]["mca96"]["mounted_count"] == 96

    compiled_failure = tools.compile_and_simulate(_tip_capacity_draft())
    assert compiled_failure["ok"] is False
    assert compiled_failure["simulation_failure_category"] == "tip_capacity"
    assert compiled_failure["simulation_failure_details"]["details"]["capacity_ul"] == 100.0
    assert "use_higher_capacity_tips" in compiled_failure["repair_options"]
    assert compiled_failure["state_summary"]["tip_state"]["mca96"]["mounted_count"] == 96


def test_authoring_tools_reject_setup_only_transfer_draft() -> None:
    tools = AuthoringToolRegistry(
        output_dir=Path("build") / "test_prompt_authoring" / "intent_gate",
        current_prompt="Transfer 20 uL from a trough to every well of a 96 well plate.",
    )

    sim_bad = tools.simulate_python_draft(_setup_only_draft(), strict=True)
    assert sim_bad["ok"] is False
    assert sim_bad["stage"] == "contract"
    assert "does not contain both aspirate" in sim_bad["message"]

    compile_bad = tools.compile_and_simulate(_setup_only_draft())
    assert compile_bad["ok"] is False
    assert compile_bad["failure_category"] == "python_build_failure"


def test_worklist_prompt_intent_accepts_worklist_draft(tmp_path: Path) -> None:
    gwl = tmp_path / "simple.gwl"
    gwl.write_text(
        "A;Smalltrough;;;A1;;5;;;;\n"
        "D;96wellplate;;;A1;;5;;;;\n"
        "W;\n",
        encoding="utf-8",
    )
    tools = AuthoringToolRegistry(
        output_dir=Path("build") / "test_prompt_authoring" / "worklist_intent",
        current_prompt="Author a simple script that executes this GWL worklist.",
    )

    draft = f'''"""Simple worklist validator fixture."""

from fluentvibe import Worktable


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev4",
        workspace_guid="2baf8c89-406a-455a-9a91-6378fc41a0a5",
        auto_place=False,
        protocol_name="Simple Worklist",
        comment="execute a small GWL worklist",
    )
    wt.group("Worklist")
    wt.worklist(r"{gwl}", liquid_class="Water Free Single")
    return wt
'''

    result = tools.compile_and_simulate(draft)
    assert result["ok"] is True, result
    assert result["compile_ok"] is True


def test_prompt_generation_simple_worklist_script(tmp_path: Path) -> None:
    gwl = tmp_path / "simple.gwl"
    gwl.write_text(
        "A;Smalltrough;;;A1;;5;;;;\n"
        "D;96wellplate;;;A1;;5;;;;\n"
        "W;\n",
        encoding="utf-8",
    )
    generated = f'''```python
"""Generated simple worklist protocol."""

from fluentvibe import Worktable


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev4",
        workspace_guid="2baf8c89-406a-455a-9a91-6378fc41a0a5",
        auto_place=False,
        protocol_name="Simple Worklist",
        comment="execute a small GWL worklist",
    )
    wt.group("Worklist")
    wt.worklist(r"{gwl}", liquid_class="Water Free Single")
    return wt
```'''
    service = PromptAuthoringService(client=FakeMessagesListChatModel(
        responses=[AIMessage(content=generated)]
    ))

    result = service.author(
        f"Author a very simple worklist script using {gwl}.",
        output_dir=tmp_path / "authoring",
        retry_budget=1,
        lab_scope="enforce",
    )

    assert result.status == AuthoringStatus.SUCCESS, result.failure_message
    assert result.generated_code is not None
    assert ".worklist(" in result.generated_code
    assert ".aspirate(" not in result.generated_code
    assert ".dispense(" not in result.generated_code
    assert result.validation is not None
    assert result.validation.compile_ok is True


def test_simulate_python_draft_defers_intent_check_for_staged_subdraft() -> None:
    """Staged early-stage drafts must not be rejected by the prompt-intent gate.

    The staged authoring loop instructs the model to submit a Variables +
    Labware Placement-only first checkpoint. That checkpoint has no
    aspirate/dispense by design, but the prompt mentions a transfer.
    `simulate_python_draft` should pass it through; the terminal
    `compile_and_simulate` still enforces the intent gate via
    `validator.validate`.
    """
    tools = AuthoringToolRegistry(
        output_dir=Path("build") / "test_prompt_authoring" / "staged_intent_defer",
        current_prompt="Transfer 20 uL from a trough to every well of a 96 well plate.",
    )

    plan = tools.declare_protocol_workflow(
        protocol_name="Staged Transfer",
        summary="staged transfer",
        variables=[{"name": "RunId", "default": "test", "sim_value": "test"}],
        labware=[
            {"label": "SourcePlate"},
            {"label": "DestPlate"},
            {"label": "Tips"},
        ],
        groups=[
            {"name": "Variables", "objective": "declare variables"},
            {"name": "Labware Placement", "objective": "place labware"},
            {"name": "Fill Reagents", "objective": "fill sources"},
            {"name": "Transfer", "objective": "move liquid"},
        ],
    )
    assert plan["ok"] is True
    assert tools.workflow_plan is not None

    early_staged_draft = '''"""Staged early-stage draft."""

from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Staged Transfer",
        comment="early staged checkpoint",
    )
    wt.declare_variable("RunId", "test_run")
    wt.set_sim_value("RunId", "test_run")
    water = Reagent("Water")
    wt.group("Labware Placement")
    source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dest = wt.place(Plate96("DestPlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    source.fill_all(water, 80.0)
    return wt
'''

    sim_staged = tools.simulate_python_draft(early_staged_draft, strict=True)
    assert sim_staged["ok"] is True, sim_staged

    compile_terminal = tools.compile_and_simulate(early_staged_draft)
    assert compile_terminal["ok"] is False
    assert compile_terminal["failure_category"] == "python_build_failure"


def test_ask_user_returns_needs_user_sentinel() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "ask_user")

    result = tools.ask_user(question="What volume per well?", axes=["target_volume_ul"])
    assert result["ok"] is True
    assert result["status"] == "needs_user"
    assert result["question"] == "What volume per well?"
    assert result["axes"] == ["target_volume_ul"]


def test_ask_user_rejects_empty_question() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "ask_user_empty")
    result = tools.ask_user(question="   ")
    assert result["ok"] is False


def test_declare_intent_accumulates_axes() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "intent")

    tools.declare_intent(target_volume_ul=20.0, destination_label="DestPlate")
    tools.declare_intent(source_label="SourceTrough", liquid_class="Water Free Single")

    intent = tools.current_intent
    assert intent.target_volume_ul == 20.0
    assert intent.destination_label == "DestPlate"
    assert intent.source_label == "SourceTrough"
    assert intent.liquid_class == "Water Free Single"


def test_declare_protocol_workflow_requires_mandatory_prefix() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_prompt_authoring" / "workflow")

    bad = tools.declare_protocol_workflow(
        protocol_name="Bad",
        summary="bad order",
        variables=[{"name": "RunId", "default": "test", "sim_value": "test"}],
        labware=[],
        groups=[
            {"name": "Labware Placement"},
            {"name": "Variables"},
        ],
    )
    assert bad["ok"] is False
    assert bad["category"] == "workflow_plan_invalid"

    ok = tools.declare_protocol_workflow(
        protocol_name="Good",
        summary="good order",
        variables=[{"name": "RunId", "default": "test", "sim_value": "test"}],
        labware=[{"label": "Plate"}],
        groups=[
            {"name": "Variables", "objective": "declare variables"},
            {"name": "Labware Placement", "objective": "place labware"},
            {"name": "Transfer", "objective": "move liquid"},
        ],
    )
    assert ok["ok"] is True
    assert tools.workflow_plan is not None
    assert [group["name"] for group in ok["workflow"]["groups"]][:2] == ["Variables", "Labware Placement"]


def test_compile_and_simulate_passes_intent_when_satisfied() -> None:
    tools = AuthoringToolRegistry(
        output_dir=Path("build") / "test_prompt_authoring" / "intent_satisfied",
    )
    tools.declare_intent(
        target_volume_ul=20.0,
        destination_label="DestPlate",
    )
    result = tools.compile_and_simulate(_valid_draft())
    assert result["ok"] is True
    assert result["intent_check_ok"] is True


def test_compile_and_simulate_flags_intent_underfill() -> None:
    tools = AuthoringToolRegistry(
        output_dir=Path("build") / "test_prompt_authoring" / "intent_underfill",
    )
    # Declare a per-well target the protocol cannot meet (20 uL transfer can't deliver 100 uL).
    tools.declare_intent(
        target_volume_ul=100.0,
        destination_label="DestPlate",
    )
    result = tools.compile_and_simulate(_valid_draft())
    assert result["ok"] is False
    assert result["intent_check_ok"] is False
    assert result["simulation_failure_category"] == "intent_not_satisfied"


@pytest.mark.live_lm
def test_live_lm_simple_transfer_acceptance() -> None:
    assert _lm_studio_available(), f"LM Studio model {DEFAULT_LM_STUDIO_MODEL!r} is not reachable at {DEFAULT_LM_STUDIO_ENDPOINT}"

    result = PromptAuthoringService().author(
        "Author a 20 uL transfer using Water Free Single liquid class from a "
        "96-well source plate to a 96-well destination plate across all wells.",
        output_dir=Path("build") / "test_prompt_authoring" / "live_simple_transfer",
        retry_budget=8,
    )

    assert result.status.value == "success"
    assert result.generated_code is not None
    assert result.validation is not None
    assert result.validation.compile_ok is True
    assert result.validation.strict_simulation_ok is True
    assert result.compiled_xscr is not None
    assert result.compiled_xscr.exists()


@pytest.mark.live_lm
def test_live_regression_trough_to_96_well_plate() -> None:
    """Replays the exact handover prompt that previously exhausted the retry budget."""
    assert _lm_studio_available(), f"LM Studio model {DEFAULT_LM_STUDIO_MODEL!r} is not reachable at {DEFAULT_LM_STUDIO_ENDPOINT}"

    out = Path("build") / "test_regression_trough_96well"
    result = PromptAuthoringService().author(
        "I would like to transfer 20 ul of water using Water Free Single liquid "
        "class from a trough to every well of a 96 well plate",
        output_dir=out,
        retry_budget=8,
    )

    assert result.status.value == "success", (
        f"authoring failed: {result.failure_message!r}"
    )
    assert result.validation is not None
    assert result.validation.compile_ok is True
    assert result.validation.strict_simulation_ok is True
    assert result.compiled_xscr is not None and result.compiled_xscr.exists()

    src = (result.validation.python_path).read_text(encoding="utf-8")
    assert ".aspirate(" in src and ".dispense(" in src, (
        "regression: prompt requested transfer but generated source lacks pipetting calls"
    )

    tools = AuthoringToolRegistry(output_dir=out / "fc")
    fc = tools.validate_fluentcontrol_shell(xscr_path=str(result.compiled_xscr))
    assert fc.get("ok") is True, f"FluentControl shell rejected the xscr: {fc}"
    assert fc.get("error_count", 0) == 0


@pytest.mark.live_lm
def test_live_regression_complex_authoring_clarifies_or_reports_structured_failure() -> None:
    """Regression for AMPure-style multi-phase prompts that previously exhausted the budget.

    The prompt intentionally omits per-well volume and liquid class so the model
    must call ask_user before drafting. After clarification, the run must
    terminate either as success (compiled XSCR + strict simulation) or as a
    structured failure carrying a category, repair guidance, and the best draft.
    """
    assert _lm_studio_available(), f"LM Studio model {DEFAULT_LM_STUDIO_MODEL!r} is not reachable at {DEFAULT_LM_STUDIO_ENDPOINT}"

    out = Path("build") / "test_regression_complex_authoring"
    session = PromptAuthoringSession(output_dir=out, retry_budget=8)

    canned_answers = [
        "Per-well reaction volume is 50 uL. Use the 'Water Free Single' liquid class "
        "for all transfers. Source labware label: SourcePlate. Destination labware "
        "label: ElutionPlate. Apply to all 96 wells.",
        "Use 200 uL per wash, two washes total. Same liquid class. Discard wash "
        "supernatant to a waste reservoir.",
        "Use sensible defaults for any unspecified incubation or magnet timing.",
    ]

    result = session.send(
        "Author an AMPure XP bead cleanup for 96 PCR samples. Bind beads, "
        "capture on a magnet rack, wash twice with ethanol, then elute "
        "supernatant into a fresh plate."
    )

    rounds = 0
    max_rounds = len(canned_answers) + 2
    while result.status == AuthoringStatus.CLARIFICATION_REQUIRED and rounds < max_rounds:
        assert result.clarification_questions, "clarification_required without questions"
        answer = canned_answers[rounds] if rounds < len(canned_answers) else "use sensible defaults"
        rounds += 1
        result = session.send(answer)

    assert rounds >= 1, (
        "regression: model produced a draft without first calling ask_user — "
        "intent-first clarification gate did not fire on a vague multi-phase prompt"
    )
    assert result.status != AuthoringStatus.CLARIFICATION_REQUIRED, (
        "clarification loop did not converge within the canned-answer budget"
    )

    if result.status == AuthoringStatus.SUCCESS:
        assert result.validation is not None
        assert result.validation.compile_ok is True
        assert result.validation.strict_simulation_ok is True
        assert result.compiled_xscr is not None and result.compiled_xscr.exists()
        return

    assert result.status == AuthoringStatus.FAILURE, (
        f"unexpected terminal status: {result.status} ({result.failure_message!r})"
    )
    assert result.failure_category is not None, "terminal failure must carry a category"
    validation = result.validation
    assert validation is not None, "structured failure must include the last validation report"
    assert validation.simulation_failure_category or validation.failure_category, (
        "structured failure must surface a simulation_failure_category or failure_category — "
        "regression: the run must not end on opaque string-only failures"
    )
    assert validation.repair_options or validation.repair_hint, (
        "structured failure must surface repair guidance (options or hint)"
    )
    assert result.best_draft_code, "structured failure must surface the best draft so far"
    assert validation.python_path is not None and validation.python_path.exists()


@pytest.mark.live_lm
def test_live_lm_manual_protocol_uses_tools_and_simulator() -> None:
    assert _lm_studio_available(), f"LM Studio model {DEFAULT_LM_STUDIO_MODEL!r} is not reachable at {DEFAULT_LM_STUDIO_ENDPOINT}"

    result = PromptAuthoringService().author(
        """
        SOP: Place a 96-well source plate and a 96-well destination plate on the SAT Fluent deck.
        Transfer 20 uL of aqueous sample from every source well to the corresponding destination well
        using Water Free Single liquid class and MCA96 tips. Return tips after the transfer.
        """,
        output_dir=Path("build") / "test_prompt_authoring" / "live_manual_protocol",
        retry_budget=8,
    )

    names = [call["name"] for call in result.tool_calls]
    assert any(name in names for name in ("lookup_workspace", "list_valid_positions"))
    assert any(name in names for name in ("search_labware", "get_labware"))
    assert "simulate_python_draft" in names
    assert "compile_and_simulate" in names
    assert result.status.value == "success"


@pytest.mark.live_lm
def test_live_lm_simple_worklist_script_generation() -> None:
    assert _lm_studio_available(), f"LM Studio model {DEFAULT_LM_STUDIO_MODEL!r} is not reachable at {DEFAULT_LM_STUDIO_ENDPOINT}"
    gwl_path = Path(r"C:\ProgramData\Tecan\VisionX\Worklists\TEMP_Tier6.gwl")
    if not gwl_path.exists():
        pytest.skip("local FluentControl worklist example is not installed")

    result = PromptAuthoringService().author(
        "Author a very simple FluentControl worklist script for workspace SAT_Fluent_780_Rev4 "
        "with workspace GUID 2baf8c89-406a-455a-9a91-6378fc41a0a5. Use auto_place=False. "
        f"Use the existing GWL file {gwl_path} with Water Free Single liquid class and "
        "FCA, 50ul SBS DiTis. Use the fluentvibe worklist DSL: wt.group('Worklist') and "
        "wt.worklist(...). Do not write individual aspirate or dispense commands.",
        output_dir=Path("build") / "test_prompt_authoring" / "live_simple_worklist",
        retry_budget=8,
    )

    assert result.status.value == "success", result.failure_message
    assert result.generated_code is not None
    assert ".worklist(" in result.generated_code
    assert ".aspirate(" not in result.generated_code
    assert ".dispense(" not in result.generated_code
    assert result.validation is not None
    assert result.validation.compile_ok is True


# ── accept-with-gaps fallback reaches the session (webapp) path ──────────

def _mk_session_with_fake_graph(tmp_path, monkeypatch, final_state):
    """Build an off-scope session whose graph.invoke returns ``final_state``."""
    from fluentvibe.authoring import session as session_mod

    fake_client = FakeMessagesListChatModel(responses=[AIMessage(content="noop")])
    sess = PromptAuthoringSession(
        output_dir=tmp_path, retry_budget=1, lab_scope="off", client=fake_client
    )
    monkeypatch.setattr(sess, "_start_prefetch", lambda *a, **k: None)
    monkeypatch.setattr(sess, "_inject_skill_context", lambda *a, **k: None)

    class _FakeGraph:
        def invoke(self, state):
            base = {"messages": state.get("messages", []), "iterations": 1}
            base.update(final_state)
            return base

    monkeypatch.setattr(session_mod, "build_authoring_graph", lambda **kw: _FakeGraph())
    return sess


def test_session_returns_fallback_when_run_fails_after_nudge(tmp_path, monkeypatch):
    # Regression: the webapp uses PromptAuthoringSession.send, which builds the
    # graph and reads final_state itself — it must apply the same accept-with-gaps
    # fallback as run_graph, or a nudged-then-failed run discards the good draft.
    from fluentvibe.authoring.models import (
        AuthoringResult,
        AuthoringStatus,
        FailureCategory,
    )

    failure = AuthoringResult(
        status=AuthoringStatus.FAILURE, prompt="p", spec=None, generated_code=None,
        validation=None, compiled_xscr=None,
        failure_category=FailureCategory.MODEL_AUTHORING_FAILURE,
        failure_message="Model returned no Python draft.",
    )
    fallback = AuthoringResult(
        status=AuthoringStatus.SUCCESS, prompt="p", spec=None,
        generated_code="def build_worktable():\n    return wt\n",
        validation=None, compiled_xscr=None,
        coverage_gaps=({"code": "missing_pooling", "severity": "warning"},),
    )
    sess = _mk_session_with_fake_graph(
        tmp_path, monkeypatch,
        {"result": failure, "fallback_result": fallback, "best_code": fallback.generated_code},
    )

    result = sess.send("automate the library prep")
    assert result.status is AuthoringStatus.SUCCESS
    assert [g["code"] for g in result.coverage_gaps] == ["missing_pooling"]


def test_session_failure_stands_without_fallback(tmp_path, monkeypatch):
    from fluentvibe.authoring.models import (
        AuthoringResult,
        AuthoringStatus,
        FailureCategory,
    )

    failure = AuthoringResult(
        status=AuthoringStatus.FAILURE, prompt="p", spec=None, generated_code=None,
        validation=None, compiled_xscr=None,
        failure_category=FailureCategory.MODEL_AUTHORING_FAILURE,
        failure_message="Model returned no Python draft.",
    )
    sess = _mk_session_with_fake_graph(
        tmp_path, monkeypatch, {"result": failure, "fallback_result": None},
    )

    result = sess.send("automate the library prep")
    assert result.status is AuthoringStatus.FAILURE
