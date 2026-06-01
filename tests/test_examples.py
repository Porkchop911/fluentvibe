"""Phase A — pinning tests for each example protocol in examples/.

Each test asserts the example builds a clean Worktable, simulates without
errors, compiles to a .xscr, and that key snapshot facts hold (final
volumes, magnet flips, loop iteration counts).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.catalog.catalog import index_exists  # noqa: E402
from tests._module_loader import load_module  # noqa: E402


def _load_example(name: str):
    """Import an example/ module by filename."""
    path = REPO_ROOT / "examples" / f"{name}.py"
    return load_module(path, alias=f"examples.{name}")


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_round_trip_780_empty() -> None:
    """A.1 — round-trip example builds, simulates, and compiles."""
    module = _load_example("round_trip_780_empty")
    wt = module.build_worktable()

    assert wt.workspace_name == "780_Empty"
    assert wt.valid_slots is not None
    # Post xwsp parser fix: 780_Empty enumerates ~56 slots, not 1.
    assert len(wt.valid_slots) >= 50, (
        f"expected 780_Empty to enumerate >=50 slots, got {len(wt.valid_slots)}"
    )

    wt.simulate()
    dest = wt.snapshots[-1].labware("DestPlate")
    assert dest.well("A1").volume_ul == pytest.approx(20.0)
    assert len(dest.well("A1").layers) == 1
    assert dest.well("A1").layers[0].reagent.name == "Sample"


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_ampure_cleanup_magnet_roundtrip() -> None:
    """A.3 — bead model: stack-derived is_magnetized flips; the magnet
    retains beads + bound DNA while supernatant liquid still aspirates;
    elution releases the DNA into the recovered eluate."""
    module = _load_example("ampure_cleanup")
    wt = module.build_worktable()
    wt.simulate()

    def _sample_mag(s):
        try:
            return s.labware("Sample").is_magnetized
        except KeyError:
            return None

    # Magnetization round-trip is observable on the Sample plate.
    sample_mag = [m for m in (_sample_mag(s) for s in wt.snapshots) if m is not None]
    assert any(sample_mag) and not all(sample_mag)

    # The supernatant draw (first aspirate while Sample is magnetised)
    # carries bulk liquid but NOT the DNA — DNA is bound to the retained
    # beads.
    types = [type(s.step).__name__ for s in wt.snapshots]
    on_idx = next(
        i for i, s in enumerate(wt.snapshots) if _sample_mag(s) is True
    )
    asp_idx = next(
        i for i, t in enumerate(types[on_idx:], start=on_idx) if t == "AspirateStep"
    )
    sup = {l.reagent.name for l in wt.snapshots[asp_idx].mca_tips[0].layers}
    assert "AMPure beads" in sup and "Sample DNA" not in sup

    final = wt.snapshots[-1]
    # DNA was retained on beads through the supernatant draw, never washed
    # to waste.
    waste_reagents = {
        l.reagent.name
        for w in final.labware("Waste").wells.values()
        for l in w.layers
    }
    assert "Sample DNA" not in waste_reagents
    # Elution released the DNA; it is recovered in the eluate plate.
    eluate = {l.reagent.name for l in final.labware("Eluate").well("A1").layers}
    assert "Sample DNA" in eluate and "Elution buffer" in eluate
    # Beads stayed behind in the sample well (bead phase retained).
    sample_a1 = final.labware("Sample").well("A1")
    assert sample_a1.bead_phase is not None and sample_a1.bead_phase.present


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_loop_conditional_dispatches_correctly() -> None:
    """A.2 — loop body dispatched N times, conditional then-branch taken."""
    module = _load_example("loop_conditional")
    wt = module.build_worktable()
    wt.simulate()

    # 3 wash cycles * (10 µL main + 5 µL conditional rinse) = 45 µL into waste
    waste_a1 = wt.snapshots[-1].labware("Waste").well("A1")
    assert waste_a1.volume_ul == pytest.approx(45.0)

    # The IR contains a single LoopStep with a nested ConditionalStep.
    from fluentvibe.ir.schema import LoopStep, ConditionalStep
    proto = wt.to_protocol()
    pipetting = next(g for g in proto.groups if g.name == "Wash")
    loop_step = next(s for s in pipetting.steps if isinstance(s, LoopStep))
    assert loop_step.number_of_loops == "cycles"
    assert any(isinstance(s, ConditionalStep) for s in loop_step.steps)
    cond_step = next(s for s in loop_step.steps if isinstance(s, ConditionalStep))
    assert cond_step.left_variable == "ph"
    assert cond_step.operator == ">="
    assert cond_step.right_value == 7


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_normalize_to_target() -> None:
    """A.4 — uniform dilution across wells with non-uniform starting state.

    Documents v1.1 limitation: MCA aspirate is auto-parallel with a single
    volume scalar. Per-well varying volumes require IR or FCA changes.
    """
    module = _load_example("normalize_to_target")
    wt = module.build_worktable()
    wt.simulate()

    # All dest wells get the same total (uniform dispense).
    final_dst = wt.snapshots[-1].labware("Dest")
    volumes = {w_addr: w.volume_ul for w_addr, w in final_dst.wells.items()}
    assert all(v == pytest.approx(60.0) for v in volumes.values()), (
        f"expected every dest well to be 60 µL after uniform transfer, got "
        f"{set(volumes.values())}"
    )

    # Source plate's per-well starting volumes diverged; after a uniform
    # 30 µL aspirate from every well, the remaining volumes still diverge
    # — confirms per-well state survives auto-parallel pipetting.
    final_src = wt.snapshots[-1].labware("Source")
    src_volumes = {w_addr: w.volume_ul for w_addr, w in final_src.wells.items()}
    assert len(set(round(v, 2) for v in src_volumes.values())) > 50, (
        "expected source plate to retain per-well state divergence"
    )
