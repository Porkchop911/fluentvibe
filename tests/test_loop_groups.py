"""Blocks inside a native loop: their groups nest in the loop."""

from fluentvibe import MCA200Box, Plate96, Reagent, Trough25mL, Worktable
from fluentvibe.blocks import add_reagent, remove_liquid
from fluentvibe.ir.schema import LoopStep, ScriptGroupStep


def test_blocks_in_a_loop_nest_their_groups_and_repeat():
    wt = Worktable(name="loop")
    wt.group("Labware Placement")
    plate = wt.place(Plate96("P", catalog="96 Well Flat"), "Nest", 1)
    buffer = wt.place(Trough25mL("Buffer", catalog="300ml SBS"), "Nest", 2)
    waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest", 3)
    tips = wt.place(MCA200Box("Tips", catalog="MCA96, 200ul, Box"), "Nest", 4)
    tips2 = wt.place(MCA200Box("Tips2", catalog="MCA96, 200ul, Box"), "Nest", 5)
    buffer.fill_all(Reagent("Wash"), 50000)
    plate.fill_all(Reagent("Sample"), 20)
    wt.group("Washes")
    with wt.loop(times=3, name="Wash 3 times"):
        add_reagent(wt, reagent_source=buffer, plate=plate, volume_ul=100, reagent_tips=tips,
                    liquid_class="Water Free Single", name="Add wash")
        remove_liquid(wt, plate=plate, waste=waste, volume_ul=100, tips=tips2,
                      liquid_class="Water Free Single", name="Remove wash")
    wt.group("After")
    wt.wait(1)
    groups = wt.to_protocol().groups
    assert [g.name for g in groups] == ["Labware Placement", "Washes", "After"]
    [loop] = [s for s in groups[1].steps if isinstance(s, LoopStep)]
    assert [s.name for s in loop.steps if isinstance(s, ScriptGroupStep)] == ["Add wash", "Remove wash"]
    wt.simulate()
    well = wt.snapshots[-1].labware("P").well("A1")
    assert abs(sum(layer.volume_ul for layer in well.layers) - 20) < 1e-6
    assert abs(sum(l.volume_ul for l in wt.snapshots[-1].labware("Waste").well("A1").layers) - 3 * 100) < 1e-6  # ran 3 times
