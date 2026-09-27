"""wt.volume: volumes as FluentControl variables with run-time calculations."""

import pytest

from fluentvibe import MCA200Box, Plate96, Reagent, Trough25mL, Worktable
from fluentvibe.ir.schema import AspirateStep, SetVariableStep
from fluentvibe.variables import Volume, per_trip


def test_arithmetic_keeps_the_expression():
    a, b = Volume(20, "SAMPLE_UL"), Volume(36, "BEADS_UL")
    assert (a + b - 5).expr == "SAMPLE_UL + BEADS_UL - 5" and float(a + b - 5) == 51
    assert ((a + b) * 0.8).expr == "(SAMPLE_UL + BEADS_UL) * 0.8"
    assert (100 - (a + b)).expr == "100 - (SAMPLE_UL + BEADS_UL)"
    assert (a + 0).expr == "SAMPLE_UL" and (a * 1).expr == "SAMPLE_UL"
    assert per_trip(a, 1) is a and per_trip(a, 2).expr == "SAMPLE_UL / 2"
    assert per_trip(20.0, 3) == 6.67


def test_steps_reference_the_variable():
    step = AspirateStep(labware_name="P", volume=Volume(20, "SAMPLE_UL") - 2)
    assert step.volume == "SAMPLE_UL - 2"


def test_derived_volume_is_a_set_variable_the_simulator_evaluates():
    wt = Worktable(name="v")
    wt.group("Variables")
    sample = wt.volume("SAMPLE_UL", 20)
    remove = wt.volume("REMOVE_UL", sample + 30 - 5)
    assert isinstance(remove, Volume) and remove.expr == "REMOVE_UL" and float(remove) == 45
    [set_step] = [s for s in wt.to_protocol().groups[0].steps if isinstance(s, SetVariableStep)]
    assert set_step.expression and set_step.value == "SAMPLE_UL + 30 - 5"
    with pytest.raises(ValueError):
        wt.volume("SAMPLE_UL", 25)
    with pytest.raises(ValueError):
        wt.volume("bad name", 1)


def test_fluentcontrol_edit_propagates_in_simulation(tmp_path):
    """An edited base default reaches the dependent volume at run time."""
    wt = Worktable(name="v")
    wt.group("Variables")
    sample = wt.volume("SAMPLE_UL", 20)
    remove = wt.volume("REMOVE_UL", sample - 5)
    wt.group("Labware Placement")
    plate = wt.place(Plate96("P", catalog="96 Well Flat"), "Nest", 1)
    waste = wt.place(Trough25mL("W", catalog="25ml_short"), "Nest", 2)
    tips = wt.place(MCA200Box("T", catalog="MCA96, 200ul, Box"), "Nest", 3)
    plate.fill_all(Reagent("S"), 20)
    wt.group("Remove")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(plate, remove, liquid_class="Water Free Single")
    head.dispense(waste, remove, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    # "Edited in FluentControl": SAMPLE_UL is now 10, so REMOVE_UL is 5.
    wt.protocol_variables["SAMPLE_UL"] = 10.0
    wt.simulate()
    well = wt.snapshots[-1].labware("P").well("A1")
    assert sum(layer.volume_ul for layer in well.layers) == pytest.approx(15.0)
