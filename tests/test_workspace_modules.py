from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import MagnetRack, MCA100Box, Plate96, Reagent, Worktable  # noqa: E402
from fluentvibe.authoring.workspace_modules import SPRI_MODULE_SOURCE  # noqa: E402


def test_spri_cleanup_handles_analyte_only_seeded_sample() -> None:
    namespace: dict[str, object] = {}
    exec(SPRI_MODULE_SOURCE, namespace)  # noqa: S102 - trusted module source fixture
    spri_cleanup = namespace["spri_cleanup"]

    wt = Worktable.from_workspace("780_Empty", auto_place=False)
    sample_plate = wt.place(Plate96("Sample", catalog="96 Well Flat"), "Site", 1)
    eluate_plate = wt.place(Plate96("Eluate", catalog="96 Well Flat"), "Site", 2)
    magnet = wt.place(MagnetRack("Magnet", catalog="24 Magnet Plate"), "Site", 3)
    waste = wt.place(Plate96("Waste", catalog="96 Well Flat"), "Site", 4)
    cleanup_tips = wt.place(MCA100Box("CleanupTips", catalog="MCA96, 100ul, Box"), "Site", 5)
    eluate_tips = wt.place(MCA100Box("EluateTips", catalog="MCA96, 100ul, Box"), "Site", 6)
    bead_source = wt.place(Plate96("Beads", catalog="96 Well Flat"), "Site", 7)
    elution_source = wt.place(Plate96("EB", catalog="96 Well Flat"), "Site", 8)

    dna = Reagent("Sample DNA", role="analyte")
    beads = Reagent("AMPure beads", role="bead_carrier")
    eluent = Reagent("Elution buffer", role="eluent")
    # This is the natural model output: it seeds the full sample as analyte.
    # The helper normalizes that into bulk sample liquid plus a small marker.
    sample_plate.fill_all(dna, 50.0)
    bead_source.fill_all(beads, 60.0)
    elution_source.fill_all(eluent, 40.0)

    spri_cleanup(
        wt,
        sample_plate=sample_plate,
        magnet=magnet,
        bead_source=bead_source,
        elution_source=elution_source,
        waste_labware=waste,
        eluate_plate=eluate_plate,
        cleanup_tips=cleanup_tips,
        eluate_tips=eluate_tips,
        analyte=dna,
        beads=beads,
        eluent=eluent,
        liquid_class="Water Free Single",
        sample_volume_ul=50.0,
        bead_volume_ul=50.0,
        elution_volume_ul=30.0,
    )

    wt.simulate()

    final = wt.snapshots[-1]
    eluate = {layer.reagent.name for layer in final.labware("Eluate").well("A1").layers}
    assert {"Sample DNA", "Elution buffer"} <= eluate
    assert "Sample DNA" not in {
        layer.reagent.name
        for well in final.labware("Waste").wells.values()
        for layer in well.layers
    }
    sample_a1 = final.labware("Sample").well("A1")
    assert sample_a1.bead_phase is not None and sample_a1.bead_phase.present
