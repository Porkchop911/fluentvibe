"""Simulation must not mutate the authored protocol.

Before the fix, the simulator used the author-side labware objects as its twin,
so every ``simulate()`` call drained the source plate further (50 → 30 → 10 µL
in ``examples/simple_transfer.py``) and repeated scoring of one draft gave
different answers.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_example(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.build_worktable()


def _first_well_volumes(labware_iter) -> dict[str, float]:
    out: dict[str, float] = {}
    for lw in labware_iter:
        wells = getattr(lw, "wells", None)
        if wells:
            first = next(iter(wells.values()))
            out[lw.label] = round(sum(layer.volume_ul for layer in first.layers), 3)
    return out


def _authored(wt) -> dict[str, float]:
    return _first_well_volumes(lw for stack in wt.slot_map.values() for lw in stack)


def _final(wt) -> dict[str, float]:
    return _first_well_volumes(
        lw for stack in wt.snapshots[-1].slot_map.values() for lw in stack
    )


def test_simulate_leaves_authored_labware_untouched():
    wt = _load_example("simple_transfer")
    before = _authored(wt)
    wt.simulate()
    assert _authored(wt) == before
    # The simulated result is still visible in the snapshots.
    assert _final(wt) != before


def test_repeated_simulation_is_deterministic():
    wt = _load_example("simple_transfer")
    wt.simulate()
    first = _final(wt)
    wt.simulate()
    assert _final(wt) == first


def test_set_variable_effects_are_in_snapshots_not_the_worktable():
    from fluentvibe import Worktable

    wt = Worktable(name="vars")
    wt.declare_variable("cycles", 1)
    wt.set_sim_value("cycles", 1)
    wt.set_variable("cycles", 2)
    wt.simulate()
    assert wt.snapshots[-1].variables["cycles"] == 2
    assert wt.sim_values["cycles"] == 1
    wt.simulate()
    assert wt.snapshots[-1].variables["cycles"] == 2


@pytest.mark.parametrize(
    "example", ["simple_transfer", "loop_conditional", "ampure_cleanup", "normalize_to_target"]
)
def test_examples_simulate_twice_with_identical_reports(example):
    wt = _load_example(example)
    wt.simulate()
    first = (_final(wt), [(s.step_index, len(s.warnings)) for s in wt.snapshots])
    wt.simulate()
    assert (_final(wt), [(s.step_index, len(s.warnings)) for s in wt.snapshots]) == first
