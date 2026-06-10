"""Bead-attribute model: beads are a retained solid-phase attribute, not a
pinned liquid layer. A magnet never withholds liquid; it immobilises the
beads and whatever analyte is bound to them. Pure simulator — no FC index.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import (  # noqa: E402
    Layer,
    MagnetRack,
    MCA100Box,
    Plate96,
    Reagent,
    Worktable,
)
from fluentvibe.reagent import ROLES  # noqa: E402


def _rig():
    wt = Worktable()
    wt.group("Setup")
    src = wt.place(Plate96("Src", catalog="96 Well Flat"), "Nest", 1)
    plate = wt.place(Plate96("Plate", catalog="96 Well Flat"), "Nest", 2)
    eb = wt.place(Plate96("EB", catalog="96 Well Flat"), "Nest", 3)
    final = wt.place(Plate96("Final", catalog="96 Well Flat"), "Nest", 6)
    rack = wt.place(MagnetRack("Mag", catalog="24 Magnet Plate"), "Nest", 7)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    return wt, src, plate, eb, final, rack, tips


def test_reagent_role_and_back_compat_alias():
    assert set(ROLES) == {"plain", "bead_carrier", "analyte", "eluent"}
    assert Reagent("x").role == "plain"
    b = Reagent("AMPure", role="bead_carrier")
    assert b.carries_beads and b.pinned_when_magnetized  # read-only alias
    assert not Reagent("buf").pinned_when_magnetized
    with pytest.raises(ValueError):
        Reagent("bad", role="nonsense")


def test_full_bind_wash_elute_cycle():
    wt, src, plate, eb, final, rack, tips = _rig()
    buf = Reagent("Sample buffer")
    dna = Reagent("DNA", role="analyte")
    beads = Reagent("AMPure", role="bead_carrier")
    elution = Reagent("EB", role="eluent")

    plate.fill_all(buf, 30.0)
    for w in plate.wells.values():
        w.layers.append(Layer(reagent=dna, volume_ul=2.0))
    src.fill_all(beads, 60.0)
    eb.fill_all(elution, 40.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)

    wt.group("bind")
    head.aspirate(src, 60.0, liquid_class="Water Free Single")
    head.dispense(plate, 60.0, liquid_class="Water Free Single")  # sets bead phase
    head.mix(plate, 40.0, liquid_class="Water Free Single")        # DNA binds

    wt.group("supernatant")
    wt.gripper.move(plate, onto=rack)
    head.aspirate(plate, 90.0, liquid_class="Water Free Single")   # all free liquid
    head.dispense(src, 90.0, liquid_class="Water Free Single")
    wt.gripper.move(plate, to=("Nest", 2))

    wt.group("elute")
    head.aspirate(eb, 40.0, liquid_class="Water Free Single")
    head.dispense(plate, 40.0, liquid_class="Water Free Single")
    head.mix(plate, 30.0, liquid_class="Water Free Single")        # DNA released
    wt.gripper.move(plate, onto=rack)
    head.aspirate(plate, 42.0, liquid_class="Water Free Single")   # eluate
    head.dispense(final, 42.0, liquid_class="Water Free Single")

    wt.simulate()  # must not raise (all liquid aspirable, magnet or not)

    snaps = wt.snapshots
    # After the bind mix, DNA is bound to the beads (off the free liquid).
    mix_idx = next(
        i for i, s in enumerate(snaps)
        if type(s.step).__name__ == "Mca384MixStep"
    )
    a1_after_bind = snaps[mix_idx].labware("Plate").well("A1")
    assert {l.reagent.name for l in a1_after_bind.layers} == {"Sample buffer", "AMPure"}
    assert a1_after_bind.bead_phase.present
    assert [l.reagent.name for l in a1_after_bind.bead_phase.bound] == ["DNA"]

    # Supernatant draw to Src must not carry DNA (it is bound + retained).
    final_snap = snaps[-1]
    src_reagents = {
        l.reagent.name for w in final_snap.labware("Src").wells.values()
        for l in w.layers
    }
    assert "DNA" not in src_reagents
    # Elution released the DNA; recovered in the Final plate.
    fin = {l.reagent.name for l in final_snap.labware("Final").well("A1").layers}
    assert "DNA" in fin and "EB" in fin
    # Beads retained in the sample well.
    plate_a1 = final_snap.labware("Plate").well("A1")
    assert plate_a1.bead_phase.present and not plate_a1.bead_phase.bound


def test_off_magnet_aspirate_entrains_beads_silently():
    wt, src, plate, eb, final, rack, tips = _rig()
    dna = Reagent("DNA", role="analyte")
    beads = Reagent("AMPure", role="bead_carrier")
    src.fill_all(beads, 50.0)
    plate.fill_all(Reagent("buf"), 20.0)
    for w in plate.wells.values():
        w.layers.append(Layer(reagent=dna, volume_ul=2.0))

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(src, 50.0, liquid_class="Water Free Single")
    head.dispense(plate, 50.0, liquid_class="Water Free Single")
    head.mix(plate, 30.0, liquid_class="Water Free Single")  # off magnet → DNA binds
    # Aspirate while NOT magnetised: beads + bound DNA entrained into the tip.
    head.aspirate(plate, 70.0, liquid_class="Water Free Single")

    wt.simulate()  # silent — no raise

    snaps = wt.snapshots
    asp_idx = max(
        i for i, s in enumerate(snaps)
        if type(s.step).__name__ == "AspirateStep"
    )
    asp = snaps[asp_idx]
    plate_a1 = asp.labware("Plate").well("A1")
    # Liquid drawn and beads entrained with it (off magnet).
    assert plate_a1.volume_ul == pytest.approx(0.0)
    assert plate_a1.bead_phase is None or not plate_a1.bead_phase.present
    # The bound analyte was carried into the tip along with the liquid.
    tip_reagents = {l.reagent.name for l in asp.mca_tips[0].layers}
    assert "DNA" in tip_reagents
