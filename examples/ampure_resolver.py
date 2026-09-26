"""AMPure XP clean-up written with wt.add (resolved) plus low-level blocks.

The request this implements (the user's AMPure prompt): 20 ul PCR samples, a
whole 96-well plate, Abgene SuperPlates for samples and elution, liquid
classes as string variables defaulting to "Water Free Single", beads, elution
buffer AND ethanol dispensed by the FCA from troughs, everything else on the
MCA. Its requirements ledger is ``ampure_resolver.requirements.json``.

``wt.add`` resolves each reagent's trough, the head, the tips and the fill
from the deck profile (FLUENTVIBE_PROFILE_DIR); ``head="fca"`` on the ethanol
overrides the default (ethanol is a bulk liquid -> MCA) and is checked.
"""

from fluentvibe import MCA200Box, MagnetRack, Plate96, Reagent, Trough25mL, Worktable
from fluentvibe.blocks import mix_wells, release, remove_liquid, separate, stamp

PLATE = "96_ABgene_SuperPlate_Thermo_AB2800"


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "Worktable_Global_Dev_1_nikop-Copy 1",
        workspace_guid="e57462be-de02-4810-b4f7-868add6977c2",
        auto_place=False,
        protocol_name="AMPure XP PCR clean-up (resolver)",
    )
    for var in ("LC_BEADS", "LC_ETHANOL", "LC_ELUTION", "LC_SAMPLE"):
        wt.declare_variable(var, "Water Free Single")
        wt.set_sim_value(var, "Water Free Single")
    # Mixing needs a liquid class with a Mix section (FluentControl rejects
    # "Water Free Single" for mixing): its own string variable.
    wt.declare_variable("LC_MIX", "Water Mix")
    wt.set_sim_value("LC_MIX", "Water Mix")

    wt.group("Labware Placement")
    samples = wt.place(Plate96("Samples", catalog=PLATE), "Nest61mm_Pos", 1)
    eluate = wt.place(Plate96("Eluate", catalog=PLATE), "Nest61mm_Pos", 2)
    sample_tips = wt.place(MCA200Box("SampleTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 3)
    eluate_tips = wt.place(MCA200Box("EluateTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 5)
    magnet = wt.place(MagnetRack("Magnet", catalog="LV_Alpaqua_A000350"), "Nest61mm_Pos", 13)
    waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)

    pcr = Reagent("PCR product", role="analyte")
    samples.fill_all(Reagent("PCR matrix"), 18)
    samples.layer_all(pcr, 2)
    beads = Reagent("AMPure XP beads", role="bead_carrier")
    ethanol = Reagent("70% ethanol")
    eb = Reagent("Elution buffer", role="eluent")

    wt.group("Bind")
    wt.add(beads, to=samples, volume_ul=36, liquid_class_var="LC_BEADS", name="Add beads")
    mix_wells(wt, plate=samples, tips=sample_tips, volume_ul=45, cycles=10, liquid_class="LC_MIX", name="Mix beads")
    wt.wait(duration_seconds=300)

    wt.group("Separate")
    separate(wt, plate=samples, magnet=magnet, settle_seconds=120, name="Magnet")
    remove_liquid(wt, plate=samples, waste=waste, volume_ul=49, tips=sample_tips,
                  liquid_class="LC_SAMPLE", name="Discard supernatant")

    for n in (1, 2):
        wt.group(f"Ethanol wash {n}")
        wt.add(ethanol, to=samples, volume_ul=200, head="fca", liquid_class_var="LC_ETHANOL",
               name=f"Ethanol {n}")
        wt.wait(duration_seconds=30)
        remove_liquid(wt, plate=samples, waste=waste, volume_ul=200 if n == 1 else 205, tips=sample_tips,
                      liquid_class="LC_ETHANOL", name=f"Discard ethanol {n}")

    wt.group("Elute")
    release(wt, plate=samples, to=("Nest61mm_Pos", 1), name="Off magnet")
    wt.add(eb, to=samples, volume_ul=40, liquid_class_var="LC_ELUTION", name="Add elution buffer")
    mix_wells(wt, plate=samples, tips=sample_tips, volume_ul=32, cycles=10, liquid_class="LC_MIX", name="Mix eluate")
    wt.wait(duration_seconds=120)
    separate(wt, plate=samples, magnet=magnet, settle_seconds=60, name="Magnet for eluate")
    stamp(wt, source=samples, dest=eluate, volume_ul=38, tips=eluate_tips, liquid_class="LC_SAMPLE",
          name="Eluate to new plate")
    release(wt, plate=samples, to=("Nest61mm_Pos", 1), name="Spent plate off magnet")
    return wt
