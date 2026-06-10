"""Canonical worklist example: CSV-driven cherrypick with per-row volumes.

A worklist runs on the LiHa/FCA and its Load Worklist step does its OWN tip
pickup using ``diti_type`` (default ``FCA, 1000ul SBS``). FluentControl rejects
the script at open ("Get DiTis: '…' not found on the workspace") unless a DiTi
box of that exact type is on the deck — so, exactly like every other protocol
places the tip box for the head it uses, a worklist must place a matching FCA
DiTi box. You do NOT call get_tips/pick_up for a worklist; the worklist owns its
tips.

``liquid_class`` may be a literal class name or a declared ``LIQUID_CLASS_*``
variable (resolved to its value at author time — the worklist renders a literal
class name, not an FC variable reference).
"""

from fluentvibe import Plate96, TipBox, Worktable


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="Worklist cherrypick",
        comment="CSV pick list with per-row volumes (LiHa worklist)",
    )

    wt.declare_variable("LIQUID_CLASS_TRANSFER", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_TRANSFER", "Water Free Single")

    wt.group("Labware")
    # CSV column A/C labware labels must match these placed plates.
    wt.place(Plate96("Source", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    wt.place(Plate96("Dest", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    # REQUIRED: FCA DiTi box matching the worklist's diti_type (default FCA, 1000ul SBS).
    wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)

    wt.group("Cherrypick from pick list")
    wt.worklist(
        r"C:\ProgramData\Tecan\VisionX\Worklists\picklist.csv",
        liquid_class="LIQUID_CLASS_TRANSFER",
        well_positions="alphanumeric",
    )
    return wt


if __name__ == "__main__":
    build_worktable()
