"""Stamp 50 uL from PlateA columns 1-6 into PlateB columns 4-9 on the 1080 deck."""

from fluentvibe import Worktable, Plate96, MCA100Box, Reagent


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace("1080_DEV_TABLE", workspace_guid="e57462be-de02-4810-b4f7-868add6977c2", auto_place=False)
    wt.declare_variable("TARGET_VOLUME_UL", 50)
    wt.set_sim_value("TARGET_VOLUME_UL", 50)
    wt.declare_variable("LIQUID_CLASS_TRANSFER", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_TRANSFER", "Water Free Single")
    wt.group("Labware Placement")
    src = wt.place(Plate96("PlateA", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dst = wt.place(Plate96("PlateB", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
    for col in range(1, 7):
        for row in "ABCDEFGH":
            src.wells[f"{row}{col}"].add_layer(Reagent(f"Sample_{row}{col}"), 150)
    wt.group("Stamp")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips, columns=[1, 2, 3, 4, 5, 6])
    head.aspirate(src, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER", columns=[1, 2, 3, 4, 5, 6])
    head.dispense(dst, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER", columns=[4, 5, 6, 7, 8, 9])
    head.return_tips(tips, columns=[1, 2, 3, 4, 5, 6])
    head.drop_adapter()
    return wt
