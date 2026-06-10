"""AMPure-style cleanup — Phase A.3.

Demonstrates the bead model. Magnetic beads are a *solid-phase attribute*
of a well, not a pinned liquid layer — a magnet never withholds liquid from
a tip; it only immobilises the beads and whatever analyte is bound to them.

Reagent roles drive everything (no fake chemistry):
- ``bead_carrier``  AMPure bead suspension. Its µL is normal liquid;
                     dispensing it establishes the well's bead phase.
- ``analyte``        the captured species (DNA). A small marker volume,
                     distinct from the bulk buffer.
- ``eluent``         elution buffer that releases bound analyte.
- ``plain``          the bulk sample buffer / wash (default).

Flow: add beads → mix off-magnet (DNA binds) → magnetise → aspirate the
*entire* supernatant to waste (DNA stays bound, beads retained) → off
magnet, add elution buffer, mix (DNA released) → magnetise → aspirate the
eluate to the final plate (beads stay behind).

Caveat: the v1.1 simulator's auto-parallel aspirate pairs the 96 MCA
channels with ``labware.wells.values()`` 1:1. This example uses 96-well
plates as the bead/eluent sources and waste so every channel has a target.
Tracked in the gap log.
"""

from fluentvibe import (
    Layer,
    MagnetRack,
    MCA100Box,
    Plate96,
    Reagent,
    Worktable,
)


def build_worktable() -> Worktable:
    sample_buffer = Reagent("Sample buffer")            # bulk liquid (plain)
    dna = Reagent("Sample DNA", role="analyte")         # captured species
    ampure = Reagent("AMPure beads", role="bead_carrier")
    elution = Reagent("Elution buffer", role="eluent")

    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="AMPure cleanup",
        comment="Bead-attribute model: bind, wash supernatant, elute",
    )

    wt.group("Setup")
    magnet = wt.place(MagnetRack("Magnet", catalog="24 Magnet Plate"), "Site", 7)
    plate = wt.place(Plate96("Sample", catalog="96 Well Flat"), "Site", 1)
    waste = wt.place(Plate96("Waste", catalog="96 Well Flat"), "Site", 2)
    bead_src = wt.place(Plate96("Beads", catalog="96 Well Flat"), "Site", 3)
    eb_src = wt.place(Plate96("EB", catalog="96 Well Flat"), "Site", 4)
    final = wt.place(Plate96("Eluate", catalog="96 Well Flat"), "Site", 6)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 5)

    plate.fill_all(sample_buffer, 30.0)
    for w in plate.wells.values():      # small analyte marker on top of the buffer
        w.layers.append(Layer(reagent=dna, volume_ul=2.0))
    bead_src.fill_all(ampure, 60.0)
    eb_src.fill_all(elution, 40.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)

    wt.group("Add beads and bind")
    head.aspirate(bead_src, 60.0, liquid_class="Water Free Single")
    head.dispense(plate, 60.0, liquid_class="Water Free Single")  # sets bead phase
    head.mix(plate, 40.0, liquid_class="Water Free Single")        # DNA binds beads

    wt.group("Magnetise, remove supernatant")
    wt.gripper.move(plate, onto=magnet)
    # All free liquid (sample buffer + bead suspension = 90 µL) aspirates;
    # the magnet holds the beads + bound DNA in the well.
    head.aspirate(plate, 90.0, liquid_class="Water Free Single")
    head.dispense(waste, 90.0, liquid_class="Water Free Single")
    wt.gripper.move(plate, to=("Site", 1))

    wt.group("Elute")
    head.aspirate(eb_src, 40.0, liquid_class="Water Free Single")
    head.dispense(plate, 40.0, liquid_class="Water Free Single")
    head.mix(plate, 30.0, liquid_class="Water Free Single")        # DNA released

    wt.group("Magnetise, recover eluate")
    wt.gripper.move(plate, onto=magnet)
    head.aspirate(plate, 42.0, liquid_class="Water Free Single")   # EB 40 + DNA 2
    head.dispense(final, 42.0, liquid_class="Water Free Single")
    wt.gripper.move(plate, to=("Site", 1))

    head.return_tips(tips)
    head.drop_adapter()

    return wt


if __name__ == "__main__":
    wt = build_worktable()
    wt.simulate()
    out = wt.compile("ampure_cleanup.xscr")
    print(f"Wrote {out}")

    final_snap = wt.snapshots[-1]
    sample_a1 = final_snap.labware("Sample").well("A1")
    eluate_a1 = final_snap.labware("Eluate").well("A1")

    bp = sample_a1.bead_phase
    print(f"Sample A1 beads retained:  present={bp.present if bp else None}, "
          f"free liquid={sample_a1.volume_ul:.1f} uL")
    print(f"Eluate A1 reagents:        "
          f"{sorted({l.reagent.name for l in eluate_a1.layers})}")
    print(f"DNA recovered in eluate:   "
          f"{any(l.reagent.name == 'Sample DNA' for l in eluate_a1.layers)}")
