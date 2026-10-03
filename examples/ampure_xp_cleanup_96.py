"""AMPure XP bead cleanup of a full 96-well PCR plate (20 uL per well).

Head assignment follows the lab scope:

* **FCA / LiHa** (`wt.liha`) does every trough-to-plate addition — AMPure XP
  beads, 70 % ethanol washes, and the elution buffer. A fixed-channel arm is
  the right tool for dispensing a reagent out of a reservoir column by column.
* **MCA96** (`wt.mca96`) does everything else: the resuspension mixes, the
  supernatant removal, the wash aspirates, and the eluate transfer to the
  elution plate.

Volumes are derived from the primitives in Python *before* they are declared as
FluentControl variables, so changing `PCR_SAMPLE_UL` re-derives the bead volume,
the supernatant draw, and the trough fills. Every pipetting call passes the
variable **name** (a string), not the Python value, so the values stay editable
in FluentControl.

Liquid classes are per-role string variables. All of them default to
`"Water Free Single"` as requested; the one exception is `LIQUID_CLASS_MIX`,
which must default to a Mix-capable class (`"Water Mix"`) because FluentControl
rejects a Mix step on `"Water Free Single"` with
`Liquid subclass section "Mix" is missing`.

Bead model: dispensing a `role="bead_carrier"` reagent establishes the well's
solid-phase bead phase; mixing off the magnet binds the `role="analyte"` DNA to
the beads, and mixing off the magnet with an `role="eluent"` buffer releases it.
Moving the plate onto the magnet rack *is* the magnetisation step — there is no
separate engage command.
"""

from __future__ import annotations

from fluentvibe import (
    Worktable, Reagent, Layer,
    Plate96, MagnetRack, Trough,
    MCA200Box, FCA200Box,
)

# ---------------------------------------------------------------------------
# Deck / labware
# ---------------------------------------------------------------------------
WORKSPACE = "SAT_Fluent_780_Rev3"
WORKSPACE_GUID = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
PLATE = "96_ABgene_SuperPlate_Thermo_AB2800"   # sample plate and elution plate
MAGNET = "LV_Alpaqua_A000350"                  # 96-well Alpaqua magnet, SBS format
WASTE_CATALOG = "300ml SBS"
BEAD_TROUGH = "25ml_short"                     # ~4 mL of beads, ~4 mL of elution buffer
ETHANOL_TROUGH = "100ml"                       # ~42 mL of 70 % ethanol
MCA_TIPS = "MCA96, 200ul, Box"                # 200 uL aspirates (the ethanol wash)
FCA_TIPS = "FCA, 200ul SBS"

WELLS = 96
COLUMNS = 12

# ---------------------------------------------------------------------------
# Protocol parameters (FluentControl variables)
# ---------------------------------------------------------------------------
PCR_SAMPLE_UL = 20.0                 # PCR reaction volume per well
BEAD_RATIO = 1.8                    # AMPure XP size-selection ratio (x sample volume)
BEAD_VOLUME_UL = round(BEAD_RATIO * PCR_SAMPLE_UL, 1)          # 36.0
RESIDUAL_SUPERNATANT_UL = 5.0       # leave 5 uL so the bead pellet is not drawn out
SUPERNATANT_ASPIRATE_UL = PCR_SAMPLE_UL + BEAD_VOLUME_UL - RESIDUAL_SUPERNATANT_UL  # 51.0
ETHANOL_WASH_VOLUME_UL = 200.0      # sample + beads = 56 uL < 200 uL, so 200 uL is enough
ETHANOL_WASH_COUNT = 2
ELUTION_VOLUME_UL = 40.0            # 40 uL elution buffer per well
RETAIN_ELUTATE_UL = 2.0             # leave a little behind to limit bead carryover
TRANSFER_VOLUME_UL = ELUTION_VOLUME_UL - RETAIN_ELUTATE_UL     # 38.0

BIND_SECONDS = 300                  # 5 min binding incubation
SETTLE_SECONDS = 120                # 2 min on the magnet
WASH_CONTACT_SECONDS = 30           # 30 s ethanol contact
ELUTION_SECONDS = 120               # 2 min elution incubation
DRY_SECONDS = 0                     # optional air-dry; keep at 0 for <=10 kb fragments

MIX_CYCLES = 10
# Mix volume is the whole free liquid in the well (resuspension, not overlay).
BIND_MIX_UL = PCR_SAMPLE_UL + BEAD_VOLUME_UL          # 56.0
ELUTION_MIX_UL = ELUTION_VOLUME_UL                    # 40.0

# Trough fills: wells x per-well volume x repeats, plus 10 % tip/dead volume.
def _trough_fill(per_well_ul: float, repeats: int = 1) -> float:
    return round(WELLS * per_well_ul * repeats * 1.1, 1)

BEAD_TROUGH_FILL_UL = _trough_fill(BEAD_VOLUME_UL)               # 3,801.6 uL
ELUTION_TROUGH_FILL_UL = _trough_fill(ELUTION_VOLUME_UL)         # 4,224.0 uL
ETHANOL_TROUGH_FILL_UL = _trough_fill(ETHANOL_WASH_VOLUME_UL, ETHANOL_WASH_COUNT)  # 42,240 uL
WASTE_TOTAL_UL = WELLS * (SUPERNATANT_ASPIRATE_UL
                         + ETHANOL_WASH_VOLUME_UL * ETHANOL_WASH_COUNT)  # 43,296 uL

# Small analyte marker inside the 20 uL PCR reaction (bulk buffer + DNA = 20 uL).
DNA_MARKER_UL = 2.0

LIQUID_CLASS_DEFAULT = "Water Free Single"
MIX_LIQUID_CLASS = "Water Mix"      # "Water Free Single" has no Mix section

LIQUID_CLASSES = {
    "LIQUID_CLASS_BEADS": LIQUID_CLASS_DEFAULT,
    "LIQUID_CLASS_SUPERNATANT": LIQUID_CLASS_DEFAULT,
    "LIQUID_CLASS_ETHANOL": LIQUID_CLASS_DEFAULT,
    "LIQUID_CLASS_ELUTION_BUFFER": LIQUID_CLASS_DEFAULT,
    "LIQUID_CLASS_ELUATE": LIQUID_CLASS_DEFAULT,
    "LIQUID_CLASS_MIX": MIX_LIQUID_CLASS,
}

VOLUME_VARIABLES = {
    "PCR_SAMPLE_UL": PCR_SAMPLE_UL,
    "BEAD_RATIO": BEAD_RATIO,
    "BEAD_VOLUME_UL": BEAD_VOLUME_UL,
    "RESIDUAL_SUPERNATANT_UL": RESIDUAL_SUPERNATANT_UL,
    "SUPERNATANT_ASPIRATE_UL": SUPERNATANT_ASPIRATE_UL,
    "ETHANOL_WASH_VOLUME_UL": ETHANOL_WASH_VOLUME_UL,
    "ETHANOL_WASH_COUNT": ETHANOL_WASH_COUNT,
    "ELUTION_VOLUME_UL": ELUTION_VOLUME_UL,
    "RETAIN_ELUTATE_UL": RETAIN_ELUTATE_UL,
    "TRANSFER_VOLUME_UL": TRANSFER_VOLUME_UL,
    "BIND_MIX_UL": BIND_MIX_UL,
    "ELUTION_MIX_UL": ELUTION_MIX_UL,
    "MIX_CYCLES": MIX_CYCLES,
    "BIND_SECONDS": BIND_SECONDS,
    "SETTLE_SECONDS": SETTLE_SECONDS,
    "WASH_CONTACT_SECONDS": WASH_CONTACT_SECONDS,
    "ELUTION_SECONDS": ELUTION_SECONDS,
    "DRY_SECONDS": DRY_SECONDS,
    "BEAD_TROUGH_FILL_UL": BEAD_TROUGH_FILL_UL,
    "ELUTION_TROUGH_FILL_UL": ELUTION_TROUGH_FILL_UL,
    "ETHANOL_TROUGH_FILL_UL": ETHANOL_TROUGH_FILL_UL,
}


def build_worktable() -> Worktable:
    ampure_beads = Reagent("AMPure XP beads", role="bead_carrier")
    pcr_dna = Reagent("PCR product DNA", role="analyte")
    pcr_buffer = Reagent("PCR reaction buffer")          # bulk of the 20 uL reaction
    ethanol = Reagent("70% ethanol")   # plain liquid; the magnet keeps the beads
    elution_buffer = Reagent("Elution buffer", role="eluent")

    wt = Worktable.from_workspace(
        WORKSPACE,
        workspace_guid=WORKSPACE_GUID,
        auto_place=False,
        protocol_name="AMPure XP PCR cleanup (96-well, 20 uL)",
        comment="20 uL PCR products; beads/ethanol/elution from troughs by FCA, "
                "all other steps on the MCA96.",
    )

    # Variables first: every volume, time and liquid class is a runtime variable.
    for name, value in LIQUID_CLASSES.items():
        wt.declare_variable(name, value)
        wt.set_sim_value(name, value)
    for name, value in VOLUME_VARIABLES.items():
        wt.declare_variable(name, value)
        wt.set_sim_value(name, value)

    wt.group("Labware placement")
    sample = wt.place(Plate96("SamplePlate", catalog=PLATE), "Nest61mm_Pos", 1)
    eluate = wt.place(Plate96("ElutionPlate", catalog=PLATE), "Nest61mm_Pos", 2)
    magnet = wt.place(MagnetRack("MagnetPlate", catalog=MAGNET), "Nest61mm_Pos", 3)
    mca_tips = wt.place(MCA200Box("MCATipsProcess", catalog=MCA_TIPS), "Nest61mm_Pos", 4)
    fca_tips = wt.place(FCA200Box("FCATips", catalog=FCA_TIPS), "Nest61mm_Pos", 6)
    eluate_tips = wt.place(MCA200Box("MCATipsEluate", catalog=MCA_TIPS), "Nest61mm_Pos", 7)
    # Troughs only reach the WS_100ml_ reservoir row on this deck.
    waste = wt.place(Trough("Waste", catalog=WASTE_CATALOG), "WS_100ml_1", 1)
    bead_trough = wt.place(Trough("BeadTrough", catalog=BEAD_TROUGH), "WS_100ml_1", 2)
    ethanol_trough = wt.place(Trough("EthanolTrough", catalog=ETHANOL_TROUGH), "WS_100ml_1", 3)
    elution_trough = wt.place(Trough("ElutionTrough", catalog=BEAD_TROUGH), "WS_100ml_1", 4)

    # Seed the plate: 18 uL buffer + 2 uL DNA marker = the 20 uL PCR reaction.
    sample.fill_all(pcr_buffer, PCR_SAMPLE_UL - DNA_MARKER_UL)
    for well in sample.wells.values():
        well.layers.append(Layer(reagent=pcr_dna, volume_ul=DNA_MARKER_UL))
    bead_trough.fill_all(ampure_beads, BEAD_TROUGH_FILL_UL)
    ethanol_trough.fill_all(ethanol, ETHANOL_TROUGH_FILL_UL)
    elution_trough.fill_all(elution_buffer, ELUTION_TROUGH_FILL_UL)

    fca = wt.liha
    mca = wt.mca96

    wt.group("Add AMPure XP beads (FCA from trough)")
    fca.get_tips(fca_tips)
    with wt.loop(times=COLUMNS, name="Dispense beads column by column", loop_variable="col"):
        fca.aspirate(bead_trough, "BEAD_VOLUME_UL", liquid_class="LIQUID_CLASS_BEADS")
        fca.dispense(sample, "BEAD_VOLUME_UL",
                     liquid_class="LIQUID_CLASS_BEADS", well_offset="(col-1)*8")
    fca.drop_tips()

    wt.group("Bind (mix off magnet, then incubate)")
    mca.mount_adapter()
    mca.pick_up(mca_tips)
    mca.mix(sample, "BIND_MIX_UL", cycles="MIX_CYCLES", liquid_class="LIQUID_CLASS_MIX")
    mca.return_tips(mca_tips)
    wt.wait(duration_seconds="BIND_SECONDS")

    wt.group("Separate beads and remove supernatant (MCA)")
    wt.gripper.move(sample, onto=magnet)          # this IS the magnetisation
    wt.wait(duration_seconds="SETTLE_SECONDS")
    mca.pick_up(mca_tips)
    mca.aspirate(sample, "SUPERNATANT_ASPIRATE_UL", liquid_class="LIQUID_CLASS_SUPERNATANT")
    mca.empty_tips(waste, "SUPERNATANT_ASPIRATE_UL", liquid_class="LIQUID_CLASS_SUPERNATANT")
    mca.return_tips(mca_tips)

    wt.group("Ethanol washes (FCA dispense, MCA aspirate)")
    with wt.loop(times="ETHANOL_WASH_COUNT", name="Wash", loop_variable="wash"):
        fca.get_tips(fca_tips)
        with wt.loop(times=COLUMNS, name="Dispense ethanol column by column", loop_variable="col"):
            fca.aspirate(ethanol_trough, "ETHANOL_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_ETHANOL")
            fca.dispense(sample, "ETHANOL_WASH_VOLUME_UL",
                         liquid_class="LIQUID_CLASS_ETHANOL", well_offset="(col-1)*8")
        fca.drop_tips()
        wt.wait(duration_seconds="WASH_CONTACT_SECONDS")
        mca.pick_up(mca_tips)
        mca.aspirate(sample, "ETHANOL_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_ETHANOL")
        mca.empty_tips(waste, "ETHANOL_WASH_VOLUME_UL", liquid_class="LIQUID_CLASS_ETHANOL")
        mca.return_tips(mca_tips)
    if DRY_SECONDS:
        wt.wait(duration_seconds="DRY_SECONDS")   # optional; skip for fragments >=10 kb

    wt.group("Elute (off magnet: add elution buffer, mix, incubate)")
    wt.gripper.move(sample, to=("Nest61mm_Pos", 1))
    fca.get_tips(fca_tips)
    with wt.loop(times=COLUMNS, name="Dispense elution buffer column by column", loop_variable="col"):
        fca.aspirate(elution_trough, "ELUTION_VOLUME_UL", liquid_class="LIQUID_CLASS_ELUTION_BUFFER")
        fca.dispense(sample, "ELUTION_VOLUME_UL",
                     liquid_class="LIQUID_CLASS_ELUTION_BUFFER", well_offset="(col-1)*8")
    fca.drop_tips()
    mca.pick_up(mca_tips)
    mca.mix(sample, "ELUTION_MIX_UL", cycles="MIX_CYCLES", liquid_class="LIQUID_CLASS_MIX")
    mca.return_tips(mca_tips)
    wt.wait(duration_seconds="ELUTION_SECONDS")

    wt.group("Separate beads and transfer eluate (MCA, fresh tips)")
    wt.gripper.move(sample, onto=magnet)
    wt.wait(duration_seconds="SETTLE_SECONDS")
    mca.pick_up(eluate_tips)
    mca.aspirate(sample, "TRANSFER_VOLUME_UL", liquid_class="LIQUID_CLASS_ELUATE")
    mca.dispense(eluate, "TRANSFER_VOLUME_UL", liquid_class="LIQUID_CLASS_ELUATE")
    mca.return_tips(eluate_tips)
    mca.drop_adapter()
    wt.gripper.move(sample, to=("Nest61mm_Pos", 1))

    return wt


if __name__ == "__main__":
    wt = build_worktable()
    wt.simulate()
    out = wt.compile("ampure_xp_cleanup_96.xscr")

    final = wt.snapshots[-1]
    a1 = final.labware("SamplePlate").well("A1")
    eluate_a1 = final.labware("ElutionPlate").well("A1")
    waste_a1 = final.labware("Waste").well("A1")

    print(f"Wrote {out}")
    print(f"Sample A1  : {a1.volume_ul:.1f} uL free liquid, "
          f"beads present={a1.bead_phase.present if a1.bead_phase else False}")
    print(f"Eluate A1  : {eluate_a1.volume_ul:.1f} uL "
          f"({sorted({layer.reagent.name for layer in eluate_a1.layers})})")
    print(f"Waste      : {waste_a1.volume_ul:.1f} uL tracked in the pool "
          f"(the run discards {WASTE_TOTAL_UL:.1f} uL; v1.1 tracks one tip's "
          f"worth per trough pool)")
    print(f"DNA in eluate: {any(layer.reagent.name == 'PCR product DNA' for layer in eluate_a1.layers)}")
    print(f"DNA in waste : {any(layer.reagent.name == 'PCR product DNA' for well in final.labware('Waste').wells.values() for layer in well.layers)}")
