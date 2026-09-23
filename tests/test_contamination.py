"""Cross-contamination findings from the simulator's sample-lineage tracking."""

from __future__ import annotations

from fluentvibe import FCA1000Box, MCA100Box, Plate96, Reagent, Trough100mL, Worktable
from fluentvibe.authoring.eval_rubric import score_semantic
from fluentvibe.authoring.tools import _tip_hygiene_findings
from fluentvibe.simulator.contamination import CARRYOVER_INTO_SOURCE, CROSS_SAMPLE


def _liha_deck():
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Contamination",
        comment="",
    )
    wt.group("Labware Placement")
    buffer = wt.place(Trough100mL("Buffer", catalog="100ml Trough 156mm"), "WS_100ml_1", 1)
    samples = wt.place(
        Plate96("Samples", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1
    )
    dest = wt.place(
        Plate96("Dest", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2
    )
    tips = wt.place(FCA1000Box("FCATips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 5)
    buffer.fill_all(Reagent("buffer"), 5000.0)
    samples.fill_all(Reagent("DNA", role="analyte"), 50.0)
    return wt, buffer, samples, dest, tips


def _counts(wt):
    wt.simulate()
    return dict(wt.simulation_report.contamination_counts)


def test_same_tips_across_sample_columns_is_flagged():
    wt, _, samples, dest, tips = _liha_deck()
    head = wt.liha
    head.get_tips(tips)
    for col in range(3):
        head.aspirate(samples, 10.0, liquid_class="Water Free Single", well_offset=col * 8)
        head.dispense(dest, 10.0, liquid_class="Water Free Single", well_offset=col * 8)
    head.drop_tips()
    counts = _counts(wt)
    assert counts.get(CROSS_SAMPLE) == 16  # columns 2 and 3, 8 channels each
    first = wt.simulation_report.contamination_events[0]
    assert (first["labware"], first["well"]) == ("Samples", "A2")
    assert first["tip_carries"] == ["Samples:A1"]


def test_fresh_tips_per_sample_column_is_clean():
    wt, _, samples, dest, tips = _liha_deck()
    head = wt.liha
    for col in range(3):
        head.get_tips(tips)
        head.aspirate(samples, 10.0, liquid_class="Water Free Single", well_offset=col * 8)
        head.dispense(dest, 10.0, liquid_class="Water Free Single", well_offset=col * 8)
        head.drop_tips()
    assert _counts(wt) == {}


def test_reagent_dispensed_into_samples_from_above_may_reuse_tips():
    wt, buffer, samples, _, tips = _liha_deck()
    head = wt.liha
    head.get_tips(tips)
    for col in range(12):
        head.aspirate(buffer, 10.0, liquid_class="Water Free Single")
        head.dispense(samples, 10.0, liquid_class="Water Free Single", well_offset=col * 8)
    head.drop_tips()
    assert _counts(wt) == {}


def test_sample_tip_entering_a_reagent_source_is_flagged():
    wt, buffer, samples, _, tips = _liha_deck()
    head = wt.liha
    head.get_tips(tips)
    head.mix(samples, 20.0, cycles=3, liquid_class="Water Free Single")
    head.aspirate(buffer, 10.0, liquid_class="Water Free Single")
    head.drop_tips()
    assert _counts(wt) == {CARRYOVER_INTO_SOURCE: 8}


def test_sample_lineage_follows_the_liquid():
    # Moving sample A1 to Dest:A1 with fresh tips, then touching Dest:A1 with
    # tips that touched Samples:A1 is the same sample, so it is not flagged.
    wt, _, samples, dest, tips = _liha_deck()
    head = wt.liha
    head.get_tips(tips)
    head.aspirate(samples, 10.0, liquid_class="Water Free Single")
    head.dispense(dest, 10.0, liquid_class="Water Free Single")
    head.mix(dest, 5.0, cycles=2, liquid_class="Water Free Single")
    head.drop_tips()
    assert _counts(wt) == {}


def test_returned_mca_tips_remember_what_they_touched():
    wt = Worktable.from_workspace("780_Empty", auto_place=False, protocol_name="MCA reuse")
    wt.group("Setup")
    a = wt.place(Plate96("PlateA", catalog="96 Well Flat"), "Site", 1)
    b = wt.place(Plate96("PlateB", catalog="96 Well Flat"), "Site", 2)
    dest = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Site", 3)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 4)
    a.fill_all(Reagent("DNA-A", role="analyte"), 50.0)
    b.fill_all(Reagent("DNA-B", role="analyte"), 50.0)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(a, 10.0, liquid_class="Water Free Single")
    head.dispense(dest, 10.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.pick_up(tips)
    head.aspirate(b, 10.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    assert _counts(wt) == {CROSS_SAMPLE: 96}


def test_rubric_and_tool_surface_the_findings():
    wt, _, samples, dest, tips = _liha_deck()
    head = wt.liha
    head.get_tips(tips)
    for col in range(2):
        head.mix(samples, 20.0, cycles=3, liquid_class="Water Free Single", well_offset=col * 8)
    head.drop_tips()
    wt.simulate()
    inv = next(i for i in score_semantic(wt) if i.key == "no_cross_contamination")
    assert inv.status == "fail"
    assert "cross_sample_tip_reuse=8" in inv.evidence
    hygiene = _tip_hygiene_findings(wt.simulation_report)
    assert hygiene is not None
    assert hygiene["counts"] == {CROSS_SAMPLE: 8}
    assert hygiene["examples"][0]["well"] == "A2"
    assert "fresh tips" in hygiene["hint"]


def test_no_analyte_means_no_findings_and_na():
    wt, buffer, _, dest, tips = _liha_deck()
    head = wt.liha
    head.get_tips(tips)
    head.aspirate(buffer, 10.0, liquid_class="Water Free Single")
    head.dispense(dest, 10.0, liquid_class="Water Free Single")
    head.drop_tips()
    wt.simulate()
    assert _tip_hygiene_findings(wt.simulation_report) is None
