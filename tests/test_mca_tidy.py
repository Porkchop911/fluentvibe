"""The MCA keeps adapter and tips between consecutive MCA stages; FCA reagent
additions run as native column loops."""
from __future__ import annotations

from fluentvibe import FCA200Box, MagnetRack, MCA200Box, Plate96, Reagent, Trough25mL, Trough100mL, Worktable
from fluentvibe.blocks import add_reagent, distribute_reagent, mix_wells, offdeck_step, release, remove_liquid, separate
from fluentvibe.ir.schema import (
    DropHeadAdapterStep,
    GetHeadAdapterStep,
    LihaDispenseStep,
    LoopStep,
    PickUpTipsStep,
    ScriptGroupStep,
    SetTipsBackStep,
    UserPromptStep,
)

LC = "Water Free Single"


def _walk(steps):
    for step in steps:
        yield step
        yield from _walk(getattr(step, "steps", None) or [])


def _count(groups, kind) -> int:
    return sum(isinstance(s, kind) for g in groups for s in _walk(g.steps))


def _wash_protocol(*, operator_between: bool = False) -> Worktable:
    wt = Worktable.from_workspace("SAT_Fluent_780_Rev3", workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
                                  auto_place=False, protocol_name="Wash")
    work = wt.place(Plate96("Work", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    magnet = wt.place(MagnetRack("Magnet", catalog="LV_Alpaqua_A000350"), "Nest61mm_Pos", 3)
    work_tips = wt.place(MCA200Box("WorkTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4)
    wash_tips = wt.place(MCA200Box("WashTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 5)
    wash = wt.place(Trough100mL("Wash", catalog="60ml SBS MCA96"), "Nest61mm_Pos", 6)
    waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)
    work.fill_all(Reagent("Beads", role="bead_carrier"), 40)
    wash.fill_all(Reagent("Wash buffer"), 50000)
    mix_wells(wt, plate=work, tips=work_tips, volume_ul=30, name="Resuspend beads")
    if operator_between:
        offdeck_step(wt, "Check the plate.", name="Operator")
    separate(wt, plate=work, magnet=magnet, name="First magnet")
    remove_liquid(wt, plate=work, waste=waste, volume_ul=38, tips=work_tips, liquid_class=LC, name="Discard binding")
    with wt.loop(times=3, name="Washes"):
        release(wt, plate=work, to=("Nest61mm_Pos", 1), name="Off magnet")
        add_reagent(wt, reagent_source=wash, plate=work, volume_ul=100, reagent_tips=wash_tips,
                    liquid_class=LC, name="Add wash")
        mix_wells(wt, plate=work, tips=work_tips, volume_ul=80, name="Mix")
        separate(wt, plate=work, magnet=magnet, name="Magnet")
        remove_liquid(wt, plate=work, waste=waste, volume_ul=100, tips=work_tips, liquid_class=LC, name="Discard")
    return wt


def test_consecutive_mca_blocks_keep_the_adapter_and_simulate():
    wt = _wash_protocol()
    authored, sent = wt._groups, wt.to_protocol().groups
    assert _count(authored, GetHeadAdapterStep) == 5
    assert _count(sent, GetHeadAdapterStep) == _count(sent, DropHeadAdapterStep) == 1
    loop = next(s for g in sent for s in _walk(g.steps) if isinstance(s, LoopStep))
    assert not any(isinstance(s, (GetHeadAdapterStep, DropHeadAdapterStep)) for s in _walk(loop.steps))
    # The tips still change where the box changes (wash tips vs the plate's own tips).
    assert _count(sent, PickUpTipsStep) == _count(sent, SetTipsBackStep) < _count(authored, PickUpTipsStep)
    wt.simulate(strict=True)


def test_the_head_is_left_clean_for_an_operator_step():
    sent = _wash_protocol(operator_between=True).to_protocol().groups
    order = [s for g in sent for s in _walk(g.steps)
             if isinstance(s, (UserPromptStep, DropHeadAdapterStep, GetHeadAdapterStep, SetTipsBackStep))]
    i = next(k for k, s in enumerate(order) if isinstance(s, UserPromptStep))
    assert isinstance(order[i - 1], DropHeadAdapterStep) and isinstance(order[i - 2], SetTipsBackStep)
    assert isinstance(order[i + 1], GetHeadAdapterStep)


def test_round_trips_can_be_kept():
    wt = _wash_protocol()
    wt.keep_mca_round_trips = True
    assert _count(wt.to_protocol().groups, GetHeadAdapterStep) == 5


def test_authored_steps_are_not_changed():
    wt = _wash_protocol()
    wt.to_protocol()
    assert _count(wt._groups, GetHeadAdapterStep) == 5


def test_fca_distribute_is_one_loop_per_run_of_columns():
    wt = Worktable.from_workspace("SAT_Fluent_780_Rev3", workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
                                  auto_place=False, protocol_name="Distribute")
    plate = wt.place(Plate96("P", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    tips = wt.place(FCA200Box("T", catalog="FCA, 200ul SBS"), "Nest61mm_Pos", 4)
    trough = wt.place(Trough25mL("R", catalog="25ml_short"), "WS_100ml_1", 2)
    trough.fill_all(Reagent("Buffer"), 20000)
    distribute_reagent(wt, source=trough, plate=plate, volume_ul=20, tips=tips, liquid_class=LC,
                       columns=[1, 2, 3, 4, 7, 9, 10, 11, 12], name="Add buffer")
    steps = [s for g in wt._groups for s in g.steps if not isinstance(s, ScriptGroupStep)]
    loops = [s for s in steps if isinstance(s, LoopStep)]
    assert [loop.number_of_loops for loop in loops] == [4, 4]
    assert _count(wt._groups, LihaDispenseStep) == 3        # two loop bodies + column 7
    wt.simulate(strict=True)
    well = wt.snapshots[-1].labware("P").well
    assert [round(well(f"H{c}").volume_ul) for c in range(1, 13)] == [20, 20, 20, 20, 0, 0, 20, 0, 20, 20, 20, 20]
