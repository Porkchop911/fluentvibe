"""Verified building blocks (``fluentvibe.blocks``) on the sat_1080_test deck."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluentvibe import (
    FCA200Box,
    MagnetRack,
    MCA200Box,
    Plate96,
    Reagent,
    Trough25mL,
    Trough100mL,
    Worktable,
)
from fluentvibe.authoring.eval_rubric import score_semantic
from fluentvibe.blocks import (
    BlockError,
    add_reagent,
    offdeck_step,
    pool_columns,
    spri_cleanup,
    stamp,
)
from fluentvibe.catalog.catalog import (
    index_exists,
    resolve_workspace_by_guid,
    resolve_workspace_by_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE = REPO_ROOT / "build" / "workspaces" / "sat_1080_test" / "workspace_profile.json"
NEST = "Nest61mm_Pos"
LC = "Water Free Single"
PLATE = "96_ABgene_SuperPlate_Thermo_AB2800"
MCA_TIPS = "MCA96, 200ul, Box"


def _workspace() -> tuple[str, str]:
    if not PROFILE.exists():
        pytest.skip("sat_1080_test profile not available")
    if not index_exists():
        pytest.skip("catalog index empty")
    ws = json.loads(PROFILE.read_text(encoding="utf-8"))["workspace"]
    if resolve_workspace_by_guid(ws["guid"]) is None and resolve_workspace_by_name(ws["name"]) is None:
        pytest.skip("sat_1080_test workspace not installed")
    return ws["name"], ws["guid"]


class Deck:
    """A cleanup-ready deck: samples, magnet, troughs, three MCA tip boxes."""

    def __init__(self, *, analyte: bool = True, eluent: bool = True) -> None:
        name, guid = _workspace()
        wt = Worktable.from_workspace(name, workspace_guid=guid, auto_place=False,
                                      protocol_name="blocks", comment="")
        wt.group("Labware Placement")
        self.wt = wt
        self.samples = wt.place(Plate96("Samples", catalog=PLATE), NEST, 1)
        self.eluate = wt.place(Plate96("Eluate", catalog=PLATE), NEST, 2)
        self.barcodes = wt.place(Plate96("Barcodes", catalog=PLATE), NEST, 3)
        self.pool = wt.place(Plate96("Pool", catalog=PLATE), NEST, 4)
        self.reagent_tips = wt.place(MCA200Box("ReagentTips", catalog=MCA_TIPS), NEST, 5)
        self.sample_tips = wt.place(MCA200Box("SampleTips", catalog=MCA_TIPS), NEST, 6)
        self.eluate_tips = wt.place(MCA200Box("EluateTips", catalog=MCA_TIPS), NEST, 7)
        self.fca_tips = wt.place(FCA200Box("FCATips", catalog="FCA, 200ul SBS"), NEST, 8)
        self.magnet = wt.place(MagnetRack("Magnet", catalog="LV_Alpaqua_A000350"), NEST, 13)
        self.beads = wt.place(Trough100mL("Beads", catalog="60ml SBS MCA96"), NEST, 9)
        self.ethanol = wt.place(Trough100mL("Ethanol", catalog="60ml SBS MCA96"), NEST, 10)
        self.eb = wt.place(Trough100mL("EB", catalog="60ml SBS MCA96"), NEST, 11)
        self.waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)
        self.samples.fill_all(Reagent("Amplicon", role="analyte" if analyte else "plain"), 20.0)
        self.barcodes.fill_all(Reagent("Barcode"), 5.0)
        self.beads.fill_all(Reagent("AMPure XP", role="bead_carrier"), 15000.0)
        self.ethanol.fill_all(Reagent("80% ethanol"), 55000.0)
        self.eb.fill_all(Reagent("EB", role="eluent" if eluent else "plain"), 5000.0)

    def cleanup(self, **overrides):
        kwargs = dict(
            sample_plate=self.samples, magnet=self.magnet, bead_source=self.beads,
            wash_source=self.ethanol, elution_source=self.eb, waste=self.waste,
            eluate_plate=self.eluate, reagent_tips=self.reagent_tips,
            sample_tips=self.sample_tips, eluate_tips=self.eluate_tips,
            sample_volume_ul=20.0, bead_ratio=1.8, elution_volume_ul=15.0,
            liquid_class=LC,
        )
        kwargs.update(overrides)
        return spri_cleanup(self.wt, **kwargs)


def _final(wt, label):
    return wt.snapshots[-1].labware(label)


def test_spri_cleanup_simulates_strictly_and_passes_every_semantic_check():
    deck = Deck()
    volumes = deck.cleanup()
    assert volumes.supernatant_ul == pytest.approx(20 + 36 - 2)
    assert volumes.eluate_transfer_ul == pytest.approx(13.0)
    deck.wt.simulate(strict=True)
    report = deck.wt.simulation_report
    assert report.contamination_counts == {}
    statuses = {inv.key: inv.status for inv in score_semantic(deck.wt)}
    assert statuses == {
        "magnet_roundtrip": "pass",
        "eluate_recovered": "pass",
        "analyte_not_in_waste": "pass",
        "no_cross_contamination": "pass",
        "pooling_performed": "na",  # no source document supplied
        "spec_conformance": "na",  # no Bench Spec supplied
        "reagent_budget": "na",
    }
    eluate_a1 = _final(deck.wt, "Eluate").wells["A1"]
    assert eluate_a1.volume_ul == pytest.approx(13.0)
    assert _final(deck.wt, "Samples").slot == (NEST, 1)  # back home


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"eluate_tips": "same"}, "different tip boxes"),
        ({"bead_ratio": None}, "exactly one of bead_ratio or bead_volume_ul"),
        ({"retain_volume_ul": 20.0}, "retain_volume_ul"),
    ],
)
def test_spri_cleanup_rejects_bad_inputs(overrides, message):
    deck = Deck()
    if overrides.get("eluate_tips") == "same":
        overrides["eluate_tips"] = deck.sample_tips
    with pytest.raises(BlockError, match=message):
        deck.cleanup(**overrides)


def test_spri_cleanup_requires_tagged_roles():
    with pytest.raises(BlockError, match="role='analyte'"):
        Deck(analyte=False).cleanup()
    with pytest.raises(BlockError, match="role='eluent'"):
        Deck(eluent=False).cleanup()


def test_second_cleanup_runs_on_the_eluate_of_the_first():
    deck = Deck()
    deck.eb.fill_all(Reagent("EB", role="eluent"), 15000.0)  # two elutions (fill_all replaces)
    deck.cleanup(sample_volume_ul=20.0, elution_volume_ul=40.0, name="Cleanup 1")
    # The eluate plate was filled by the protocol, not authored: still accepted.
    deck.cleanup(sample_plate=deck.eluate, eluate_plate=deck.pool, sample_tips=deck.eluate_tips,
                 eluate_tips=deck.sample_tips,
                 sample_volume_ul=38.0, bead_ratio=1.0, elution_volume_ul=15.0, wash_count=1,
                 name="Cleanup 2")  # one 55 ml ethanol reservoir covers 3 washes
    deck.wt.simulate(strict=True)
    statuses = {inv.key: inv.status for inv in score_semantic(deck.wt)}
    assert statuses["eluate_recovered"] == "pass"
    assert statuses["analyte_not_in_waste"] == "pass"
    assert _final(deck.wt, "Pool").wells["A1"].volume_ul == pytest.approx(13.0)


def test_large_volumes_go_in_trips():
    deck = Deck()
    deck.barcodes.fill_all(Reagent("Buffer"), 300.0)
    stamp(deck.wt, source=deck.barcodes, dest=deck.pool, volume_ul=300.0,
          tips=deck.reagent_tips, liquid_class=LC, name="Big")
    deck.wt.simulate(strict=True)
    assert _final(deck.wt, "Pool").wells["A1"].volume_ul == pytest.approx(300.0)
    assert deck.wt.protocol_variables["BIG_TRIP_VOLUME_UL"] == pytest.approx(150.0)


def test_stamp_moves_each_well_to_the_same_well():
    deck = Deck()
    stamp(deck.wt, source=deck.barcodes, dest=deck.samples, volume_ul=1.0,
          tips=deck.reagent_tips, liquid_class=LC, mix_cycles=3, name="Barcoding")
    deck.wt.simulate(strict=True)
    assert _final(deck.wt, "Samples").wells["H12"].volume_ul == pytest.approx(21.0)
    assert deck.wt.simulation_report.contamination_counts == {}


def test_add_reagent_requires_separate_mix_tips():
    deck = Deck()
    with pytest.raises(BlockError, match="mix_tips"):
        add_reagent(deck.wt, reagent_source=deck.eb, plate=deck.samples, volume_ul=5.0,
                    reagent_tips=deck.reagent_tips, liquid_class=LC, mix_cycles=3)
    add_reagent(deck.wt, reagent_source=deck.eb, plate=deck.samples, volume_ul=5.0,
                reagent_tips=deck.reagent_tips, mix_tips=deck.sample_tips,
                liquid_class=LC, mix_cycles=3)
    deck.wt.simulate(strict=True)
    assert deck.wt.simulation_report.contamination_counts == {}


def test_pool_columns_builds_eight_row_pools_without_contamination():
    deck = Deck()
    pool_columns(deck.wt, source=deck.samples, dest=deck.pool, volume_ul=10.0,
                 tips=deck.fca_tips, liquid_class=LC, name="Pool")
    deck.wt.simulate(strict=True)
    pool = _final(deck.wt, "Pool")
    assert pool.wells["A1"].volume_ul == pytest.approx(120.0)
    assert pool.wells["H1"].volume_ul == pytest.approx(120.0)
    assert pool.wells["A2"].volume_ul == pytest.approx(0.0)
    assert deck.wt.simulation_report.contamination_counts == {}


def test_offdeck_step_hands_off_and_returns_the_plate():
    deck = Deck()
    offdeck_step(deck.wt, "Run TAG (30 C 2 min, 80 C 2 min) on the thermal cycler.",
                 labware=deck.samples, handoff=(NEST, 10), name="Tagmentation")
    deck.wt.simulate(strict=True)
    kinds = [type(s.step).__name__ for s in deck.wt.snapshots]
    assert "UserPromptStep" in kinds
    assert _final(deck.wt, "Samples").slot == (NEST, 1)


def test_offdeck_step_requires_an_instruction():
    deck = Deck()
    with pytest.raises(BlockError, match="instruction"):
        offdeck_step(deck.wt, "  ")


def test_intent_check_accepts_block_only_protocols():
    from fluentvibe.authoring.validator import AuthoringValidator

    source = "spri_cleanup(wt, sample_plate=samples)\nwt.place(x)"
    assert AuthoringValidator()._check_prompt_intent(source, "transfer the library") is None
    assert AuthoringValidator()._check_prompt_intent("wt.place(x)", "transfer the library")


def test_gold_ont_example_is_clean_on_every_check():
    import importlib.util

    from fluentvibe.authoring.eval_rubric import score_protocol

    _workspace()
    path = REPO_ROOT / "examples" / "ont_rbk114_blocks.py"
    spec = importlib.util.spec_from_file_location("ont_rbk114_blocks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wt = module.build_worktable()
    wt.simulate(strict=True)
    assert wt.simulation_report.contamination_counts == {}
    assert _final(wt, "RowPools").wells["A1"].volume_ul == pytest.approx(120.0)
    result = score_protocol(path.read_text(encoding="utf-8"), filename=str(path))
    assert result.failed == 0, [i for i in result.invariants if i.status == "fail"]
    assert result.get("derived_supernatant").status == "pass"


def test_pooling_invariant_needs_a_real_many_to_one_transfer():
    deck = Deck()
    doc = "Pool all the barcoded samples into a clean tube."
    stamp(deck.wt, source=deck.samples, dest=deck.pool, volume_ul=10.0,
          tips=deck.reagent_tips, liquid_class=LC)
    deck.wt.simulate(strict=True)
    inv = next(i for i in score_semantic(deck.wt, doc) if i.key == "pooling_performed")
    assert inv.status == "fail"  # a 1:1 copy is not pooling

    deck = Deck()
    pool_columns(deck.wt, source=deck.samples, dest=deck.pool, volume_ul=10.0,
                 tips=deck.fca_tips, liquid_class=LC)
    deck.wt.simulate(strict=True)
    inv = next(i for i in score_semantic(deck.wt, doc) if i.key == "pooling_performed")
    assert inv.status == "pass"
    assert inv.evidence.startswith("12 samples pooled")
    assert next(i for i in score_semantic(deck.wt) if i.key == "pooling_performed").status == "na"


def test_lookup_api_describes_blocks_from_their_signatures(tmp_path):
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    result = AuthoringToolRegistry(output_dir=tmp_path).lookup_api("fluentvibe.blocks")
    assert result["ok"] is True
    methods = {m["name"]: m for m in result["api"]["methods"]}
    assert set(methods) == {"spri_cleanup", "stamp", "add_reagent", "pool_columns",
                            "offdeck_step", "thermal_step"}
    assert "eluate_tips" in methods["spri_cleanup"]["signature"]
    assert AuthoringToolRegistry(output_dir=tmp_path).lookup_api("pool_columns")["ok"] is True


def test_spec_conformance_against_the_gold_spec():
    import importlib.util

    from fluentvibe.authoring.bench_spec import validate_bench_spec

    _workspace()
    spec, problems = validate_bench_spec(
        json.loads((REPO_ROOT / "examples" / "ont_rbk114_spec.json").read_text(encoding="utf-8"))
    )
    assert spec is not None and problems == []
    path = REPO_ROOT / "examples" / "ont_rbk114_blocks.py"
    loader = importlib.util.spec_from_file_location("ont_rbk114_blocks_conformance", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    wt = module.build_worktable()
    wt.simulate(strict=True)
    inv = next(i for i in score_semantic(wt, spec=spec) if i.key == "spec_conformance")
    assert inv.status == "pass", inv.evidence

    # Barcoding, then a thermal-cycler step written as a wait, then pooling:
    # no operator pause for the off-deck stretch.
    deck = Deck()
    stamp(deck.wt, source=deck.barcodes, dest=deck.samples, volume_ul=1.0,
          tips=deck.reagent_tips, liquid_class=LC)
    deck.wt.wait(duration_seconds=240)
    pool_columns(deck.wt, source=deck.samples, dest=deck.pool, volume_ul=10.0,
                 tips=deck.fca_tips, liquid_class=LC)
    deck.wt.simulate(strict=True)
    inv = next(i for i in score_semantic(deck.wt, spec=spec) if i.key == "spec_conformance")
    assert inv.status == "fail"
    assert "operator pause" in inv.evidence


def test_block_protocols_compile_for_fluentcontrol(tmp_path, monkeypatch):
    # Simulation alone missed that mixing needs a Mix-capable liquid class;
    # compile runs FluentControl-side checks such as that one. The check is a
    # deck rule, so activate the profile the way a real run does.
    import importlib.util

    from fluentvibe.authoring.profile import PROFILE_DIR_ENV

    _workspace()
    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE.parent))
    deck = Deck()
    deck.cleanup()
    add_reagent(deck.wt, reagent_source=deck.eb, plate=deck.eluate, volume_ul=2.0,
                reagent_tips=deck.reagent_tips, mix_tips=deck.eluate_tips,
                liquid_class=LC, mix_cycles=3)
    deck.wt.compile(tmp_path / "blocks.xscr")

    # Mixing with the transfer class is what FluentControl rejects.
    bad = Deck()
    stamp(bad.wt, source=bad.barcodes, dest=bad.samples, volume_ul=1.0, tips=bad.reagent_tips,
          liquid_class=LC, mix_cycles=3, mix_liquid_class=LC)
    with pytest.raises(Exception, match="Mix"):
        bad.wt.compile(tmp_path / "bad.xscr")

    path = REPO_ROOT / "examples" / "ont_rbk114_blocks.py"
    loader = importlib.util.spec_from_file_location("ont_rbk114_blocks_compile", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    module.build_worktable().compile(tmp_path / "gold.xscr")
    assert (tmp_path / "gold.xscr").stat().st_size > 0


def test_missing_role_error_shows_what_the_plate_holds():
    deck = Deck(analyte=False)
    with pytest.raises(BlockError) as info:
        deck.cleanup()
    message = str(info.value)
    assert "'Amplicon' (role='plain')" in message
    assert "fill_all(Reagent('<name>', role='analyte')" in message


def test_sbs_reservoir_may_sit_on_a_plate_nest_but_carrier_troughs_may_not(monkeypatch):
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.simulator.invariants import TroughPlacementError

    name, guid = _workspace()
    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE.parent))
    wt = Worktable.from_workspace(name, workspace_guid=guid, auto_place=False, protocol_name="x")
    wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)
    with pytest.raises(TroughPlacementError, match="SBS-footprint"):
        wt.place(Trough25mL("Beads", catalog="25ml_short"), "Nest7mm_Pos", 5)


def test_empty_tips_must_use_the_empty_tip_class(tmp_path, monkeypatch):
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV

    _workspace()
    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE.parent))
    good = Deck()
    good.cleanup()  # blocks empty into waste with "Empty Tip"
    good.wt.compile(tmp_path / "good.xscr")

    bad = Deck()
    head = bad.wt.mca96
    head.mount_adapter()
    head.pick_up(bad.sample_tips)
    head.aspirate(bad.samples, 5.0, liquid_class=LC)
    head.empty_tips(bad.waste, 5.0, liquid_class=LC)
    head.return_tips(bad.sample_tips)
    head.drop_adapter()
    with pytest.raises(Exception, match="Empty Tip"):
        bad.wt.compile(tmp_path / "bad.xscr")


def test_blocks_declare_fluentcontrol_variables_named_after_the_call():
    deck = Deck()
    deck.cleanup(name="PCR clean-up")
    stamp(deck.wt, source=deck.barcodes, dest=deck.eluate, volume_ul=1.0,
          tips=deck.eluate_tips,
          liquid_class=LC, name="Barcoding")
    variables = deck.wt.protocol_variables
    assert variables["PCR_CLEAN_UP_BEAD_VOLUME_UL"] == pytest.approx(36.0)
    assert variables["PCR_CLEAN_UP_SUPERNATANT_UL"] == pytest.approx(54.0)
    assert variables["PCR_CLEAN_UP_EMPTY_LIQUID_CLASS"] == "Empty Tip"
    assert variables["BARCODING_VOLUME_UL"] == pytest.approx(1.0)
    deck.wt.simulate(strict=True)
    assert _final(deck.wt, "Eluate").wells["A1"].volume_ul == pytest.approx(14.0)

    # A second cleanup with the same name would overwrite the first one's values.
    with pytest.raises(BlockError, match="different name"):
        deck.cleanup(name="PCR clean-up", bead_ratio=1.0)

    literal = Deck()
    literal.cleanup(variables=False)
    assert not any(k.startswith("BEAD_CLEANUP") for k in literal.wt.protocol_variables)


def test_thermal_step_uses_the_odtc_or_hands_off():
    from fluentvibe.blocks import thermal_step

    deck = Deck()
    thermal_step(deck.wt, deck.samples, "30 C 2 min, 80 C 2 min",
                 odtc_position=(NEST, 10), method_name="TAG", name="Tagmentation")
    deck.wt.simulate(strict=True)
    kinds = [type(s.step).__name__ for s in deck.wt.snapshots]
    assert kinds.count("LegacyDriverMacroStep") >= 4  # open, close, execute, open, close
    assert _final(deck.wt, "Samples").slot == (NEST, 1)

    manual = Deck()
    thermal_step(manual.wt, manual.samples, "30 C 2 min, 80 C 2 min",
                 handoff=(NEST, 10), name="Tagmentation")
    manual.wt.simulate(strict=True)
    assert "UserPromptStep" in [type(s.step).__name__ for s in manual.wt.snapshots]

    with pytest.raises(BlockError, match="method_name"):
        thermal_step(manual.wt, manual.samples, "x", odtc_position=(NEST, 10))


def test_user_prompt_without_auto_close_renders_a_valid_timeout():
    """FC's InfoPad rejects Timeout 0 ('Close prompt after' range 1-7200)."""
    import re

    from fluentvibe.compiler import render_protocol

    deck = Deck()
    deck.wt.user_prompt("Swap the tip racks.")
    xml = render_protocol(deck.wt.to_protocol())
    assert re.search(r"<AutoClose>False</AutoClose>\s*<Timeout>1</Timeout>", xml)


def test_mca_in_a_slim_trough_is_refused_at_compile(tmp_path, monkeypatch):
    """FC reports MCA96 + slim trough as 'out of range'; refuse it before FC does."""
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.simulator.invariants import TroughPlacementError

    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE.parent))
    deck = Deck()
    slim = deck.wt.place(Trough25mL("Slim", catalog="25ml_short"), "WS_100ml_1", 2)
    slim.fill_all(Reagent("Buffer"), 20000.0)
    stamp(deck.wt, source=slim, dest=deck.pool, volume_ul=10.0, tips=deck.reagent_tips, liquid_class=LC)
    with pytest.raises(TroughPlacementError, match="slim trough 'Slim'"):
        deck.wt.compile(tmp_path / "x.xscr")


def test_positions_fluentcontrol_flagged_as_unreachable_are_refused(tmp_path, monkeypatch):
    """reach.json (scripts/probe_deck_reach.py) records FC's 'out of range' verdicts."""
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.simulator.invariants import InvalidSlotError

    reach = PROFILE.parent / "reach.json"
    if not reach.exists():
        pytest.skip("no measured reach for the sat_1080_test profile")
    unreachable = json.loads(reach.read_text(encoding="utf-8"))["unreachable"]["mca96"].get("Nest7mm_Pos")
    if not unreachable:
        pytest.skip("no unreachable 7 mm nest measured")
    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE.parent))
    deck = Deck()
    far = deck.wt.place(Trough25mL("Far", catalog="300ml SBS"), "Nest7mm_Pos", unreachable[0])
    far.fill_all(Reagent("Buffer"), 50000.0)
    stamp(deck.wt, source=far, dest=deck.pool, volume_ul=10.0, tips=deck.reagent_tips, liquid_class=LC)
    with pytest.raises(InvalidSlotError, match="cannot reach"):
        deck.wt.compile(tmp_path / "x.xscr")
