"""v1 acceptance: snapshot introspection works on a re-authored protocol.

Verifies that after `wt.simulate()`, every step's snapshot reflects the
correct twin state — well layers, tip flow, and is_magnetized stacking.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tecanlab import (  # noqa: E402
    MagnetRack, Plate96, Reagent, Worktable,
)
from tecanlab.catalog.catalog import index_exists  # noqa: E402


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_simple_transfer_layers_flow() -> None:
    """The 20µL Input gDNA layer flows source → tips → dest."""
    from examples.simple_transfer import build_worktable

    wt = build_worktable()
    wt.simulate()

    assert wt.snapshots, "simulate() should populate snapshots"

    # First snapshot is the AddLabware for SourcePlate. Source is full of input_dna.
    src_first = wt.snapshots[0].labware("SourcePlate")
    assert src_first.well("A1").volume_ul == 50.0
    assert len(src_first.well("A1").layers) == 1
    assert src_first.well("A1").layers[0].reagent.name == "Input gDNA"

    # Find the snapshot right after Aspirate (head holds 20µL per tip; source loses 20µL).
    aspirate_snap = next(
        s for s in wt.snapshots if type(s.step).__name__ == "AspirateStep"
    )
    src_after_asp = aspirate_snap.labware("SourcePlate")
    dst_after_asp = aspirate_snap.labware("DestPlate")
    assert src_after_asp.well("A1").volume_ul == 30.0
    assert dst_after_asp.well("A1").volume_ul == 0.0
    assert len(aspirate_snap.mca_tips) == 96
    assert aspirate_snap.mca_tips[0].volume_ul == 20.0
    assert aspirate_snap.mca_tips[0].layers[0].reagent.name == "Input gDNA"

    # Snapshot after Dispense: tips empty, dest holds 20µL.
    dispense_snap = next(
        s for s in wt.snapshots if type(s.step).__name__ == "DispenseStep"
    )
    src_after_disp = dispense_snap.labware("SourcePlate")
    dst_after_disp = dispense_snap.labware("DestPlate")
    assert src_after_disp.well("A1").volume_ul == 30.0
    assert dst_after_disp.well("A1").volume_ul == 20.0
    assert dispense_snap.mca_tips[0].volume_ul == 0.0


def test_magnetized_state_toggles_with_gripper_stack() -> None:
    """is_magnetized flips True when stacked onto a MagnetRack, False after move-off."""
    wt = Worktable(name="Magnet stack test")
    wt.group("Setup")
    plate = wt.place(Plate96("Plate", catalog="96 Well Flat"), "Nest", 1)
    rack = wt.place(MagnetRack("Mag", catalog="24 Magnet Plate"), "Nest", 7)

    assert plate.is_magnetized is False

    wt.group("Engage magnet")
    wt.gripper.move(plate, onto=rack)

    wt.group("Disengage")
    wt.gripper.move(plate, to=("Nest", 1))

    wt.simulate()

    # Walk snapshots: find the move-onto-rack and move-off transitions.
    transfer_snaps = [
        s for s in wt.snapshots if type(s.step).__name__ == "RgaTransferLabwareStep"
    ]
    assert len(transfer_snaps) == 2

    # After the first transfer (onto rack), plate should be magnetized.
    plate_on_rack = transfer_snaps[0].labware("Plate")
    assert plate_on_rack.is_magnetized is True
    # The plate's slot should match the rack's.
    assert plate_on_rack.slot == ("Nest", 7)

    # After the second transfer (back to Nest 1), no longer magnetized.
    plate_off_rack = transfer_snaps[1].labware("Plate")
    assert plate_off_rack.is_magnetized is False
    assert plate_off_rack.slot == ("Nest", 1)
