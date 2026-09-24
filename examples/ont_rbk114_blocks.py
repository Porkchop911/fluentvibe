"""ONT SQK-RBK114.96 amplicon barcoding on the sat_1080_test deck, built from blocks.

Gold reference for document-driven authoring (see docs/authoring-strategy.md):
every stage is a ``fluentvibe.blocks`` call, and steps the deck cannot do are
explicit operator hand-offs instead of waits or comments.

Document → deck mapping (RAA_9198_v114_revM):

* PCR clean-up (recommended before barcoding; the document leaves the method
  open — 1.8× AMPure XP, 2 ethanol washes, 15 µl elution are assumptions):
  ``spri_cleanup`` on the amplicon plate, with lab-stock beads and buffer (the
  kit's AXP and EB are kept for the pooled clean-up).
* 9 µl of clean amplicon per sample into the barcoding plate: ``stamp``.
  (The document normalises input to 50 ng in 9 µl first; this protocol assumes
  the amplicons are already at that concentration.)
* 1 µl Rapid Barcode per sample from the RB01-96 plate, mix: ``stamp``.
* 30 °C 2 min → 80 °C 2 min, cool, spin down (thermal cycler, off the deck):
  ``offdeck_step``.
* Pool 10 µl of every sample: ``pool_columns`` gives 8 row pools of 120 µl.
* Combining into one tube, the pooled 1:1 AXP clean-up with 1.5 ml ethanol
  washes, Qubit, and Rapid Adapter attachment are tube-scale steps that need a
  tube magnet this deck does not have: one ``offdeck_step`` with the exact
  document instructions.
"""

from fluentvibe import (
    FCA200Box,
    MagnetRack,
    MCA200Box,
    Plate96,
    Reagent,
    Trough25mL,
    Trough100mL,
    Worktable,
)
from fluentvibe.blocks import offdeck_step, pool_columns, spri_cleanup, stamp

LC = "Water Free Single"
NEST = "Nest61mm_Pos"
PLATE = "96_ABgene_SuperPlate_Thermo_AB2800"
MCA_TIPS = "MCA96, 200ul, Box"
HANDOFF = (NEST, 10)


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "Ribbon_Worktable_Global_Dev_1_nikop-Copy 1",
        workspace_guid="e57462be-de02-4810-b4f7-868add6977c2",
        auto_place=False,
        protocol_name="ONT SQK-RBK114.96 amplicon barcoding",
        comment="Gold reference built from fluentvibe.blocks",
    )
    wt.declare_variable("RunId", "rbk114_blocks")
    wt.set_sim_value("RunId", "rbk114_blocks")

    wt.group("Labware Placement")
    amplicons = wt.place(Plate96("Amplicons", catalog=PLATE), NEST, 1)
    clean = wt.place(Plate96("CleanAmplicons", catalog=PLATE), NEST, 2)
    barcoding = wt.place(Plate96("Barcoding", catalog=PLATE), NEST, 3)
    barcodes = wt.place(Plate96("RapidBarcodes", catalog=PLATE), NEST, 4)
    reagent_tips = wt.place(MCA200Box("ReagentTips", catalog=MCA_TIPS), NEST, 5)
    sample_tips = wt.place(MCA200Box("SampleTips", catalog=MCA_TIPS), NEST, 6)
    eluate_tips = wt.place(MCA200Box("EluateTips", catalog=MCA_TIPS), NEST, 7)
    barcode_tips = wt.place(MCA200Box("BarcodeTips", catalog=MCA_TIPS), NEST, 8)
    pool_plate = wt.place(Plate96("RowPools", catalog=PLATE), NEST, 11)
    fca_tips = wt.place(FCA200Box("FCATips", catalog="FCA, 200ul SBS"), NEST, 12)
    magnet = wt.place(MagnetRack("Magnet", catalog="LV_Alpaqua_A000350"), NEST, 13)
    axp = wt.place(Trough25mL("AXP", catalog="25ml_short"), "WS_100ml_1", 1)
    ethanol = wt.place(Trough100mL("Ethanol80", catalog="100ml"), "WS_100ml_1", 2)
    eb = wt.place(Trough25mL("EB", catalog="25ml_short"), "WS_100ml_1", 3)
    waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)

    amplicons.fill_all(Reagent("Amplicon DNA", role="analyte"), 20.0)
    barcodes.fill_all(Reagent("Rapid Barcode"), 5.0)
    axp.fill_all(Reagent("AMPure XP beads, lab stock", role="bead_carrier"), 5000.0)
    ethanol.fill_all(Reagent("80% ethanol"), 50000.0)
    eb.fill_all(Reagent("Elution buffer, lab stock (10 mM Tris)", role="eluent"), 3000.0)

    spri_cleanup(
        wt,
        sample_plate=amplicons, magnet=magnet, bead_source=axp, wash_source=ethanol,
        elution_source=eb, waste=waste, eluate_plate=clean,
        reagent_tips=reagent_tips, sample_tips=sample_tips, eluate_tips=eluate_tips,
        sample_volume_ul=20.0, bead_ratio=1.8, elution_volume_ul=15.0,
        wash_volume_ul=150.0, wash_count=2, liquid_class=LC,
        name="PCR clean-up",
    )
    # Same samples, same well positions: the eluate tips may be reused.
    stamp(wt, source=clean, dest=barcoding, volume_ul=9.0, tips=eluate_tips,
          liquid_class=LC, name="Amplicon input (9 ul)")
    stamp(wt, source=barcodes, dest=barcoding, volume_ul=1.0, tips=barcode_tips,
          liquid_class=LC, mix_cycles=5, mix_volume_ul=8.0, name="Rapid barcoding")
    offdeck_step(
        wt,
        "Seal the Barcoding plate, incubate 30 C for 2 min then 80 C for 2 min on the "
        "thermal cycler, cool briefly on ice, spin down, unseal and return it.",
        labware=barcoding, handoff=HANDOFF, name="Barcoding incubation",
    )
    pool_columns(wt, source=barcoding, dest=pool_plate, volume_ul=10.0, tips=fca_tips,
                 liquid_class=LC, dest_column=1, name="Pool barcoded samples")
    offdeck_step(
        wt,
        "Combine the 8 row pools in RowPools column 1 (8 x 120 ul = 960 ul) in a 2 ml "
        "DNA LoBind tube. Add 960 ul AXP, mix, Hula 10 min. Pellet on a magnet, remove "
        "supernatant, wash twice with 1.5 ml 80% ethanol, dry 30 s, resuspend in 15 ul "
        "EB for 10 min, pellet and keep 15 ul eluate. Quantify 1 ul on a Qubit. Take "
        "11 ul (max 800 ng), add 1 ul diluted RA (1.5 ul RA + 3.5 ul ADB), incubate "
        "5 min at room temperature, keep on ice for flow-cell loading.",
        name="Pooled clean-up and adapter attachment (tube scale)",
    )
    return wt
