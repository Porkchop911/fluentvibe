"""AMPure cleanup on the `sat_1080_test` workspace profile.

Companion to ``test_examples.test_ampure_cleanup_magnet_roundtrip`` (which
runs on the small 780 deck). This binds the same bead-model cleanup to the
1080 Base Unit workspace captured by the ``build/workspaces/sat_1080_test``
profile and asserts:

- the protocol binds to the profile's workspace and every placement lands on
  a slot the profile says is valid for this deck, and
- the bead model behaves correctly end to end — the magnet flips, the
  supernatant draw retains beads + bound DNA, and elution recovers the DNA.

Skips cleanly when the catalog index is empty or the 1080 workspace is not
installed locally, so it is a no-op on machines without this FluentControl
deck.
"""

from __future__ import annotations

import json
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
from fluentvibe.authoring.grounding import load_current_worktable_snapshot  # noqa: E402
from fluentvibe.catalog.catalog import (  # noqa: E402
    index_exists,
    resolve_workspace_by_guid,
    resolve_workspace_by_name,
)

PROFILE_DIR = REPO_ROOT / "build" / "workspaces" / "sat_1080_test"
# All AMPure labware sits on the 1080 deck's primary nest, one role per site.
NEST = "Nest61mm_Pos"
PLACEMENT = {
    "magnet": 7,
    "sample": 1,
    "waste": 2,
    "beads": 3,
    "eb": 4,
    "tips": 5,
    "eluate": 6,
}
LIQUID_CLASS = "Water Free Single"


def _profile() -> dict:
    path = PROFILE_DIR / "workspace_profile.json"
    if not path.exists():
        pytest.skip(f"sat_1080_test profile not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_workspace(profile: dict) -> tuple[str, str]:
    if not index_exists():
        pytest.skip("catalog index empty")
    name = profile["workspace"]["name"]
    guid = profile["workspace"]["guid"]
    if resolve_workspace_by_guid(guid) is None and resolve_workspace_by_name(name) is None:
        pytest.skip(f"workspace {name!r} ({guid}) not installed in this catalog")
    return name, guid


def _build_ampure_1080(profile: dict) -> Worktable:
    name, guid = profile["workspace"]["name"], profile["workspace"]["guid"]
    wt = Worktable.from_workspace(
        name,
        workspace_guid=guid,
        auto_place=False,
        protocol_name="AMPure cleanup (1080)",
        comment="Bead-attribute model on the 1080 Base Unit deck",
    )

    sample_buffer = Reagent("Sample buffer")
    dna = Reagent("Sample DNA", role="analyte")
    ampure = Reagent("AMPure beads", role="bead_carrier")
    elution = Reagent("Elution buffer", role="eluent")

    wt.group("Setup")
    magnet = wt.place(MagnetRack("Magnet", catalog="24 Magnet Plate"), NEST, PLACEMENT["magnet"])
    plate = wt.place(Plate96("Sample", catalog="96 Well Flat"), NEST, PLACEMENT["sample"])
    waste = wt.place(Plate96("Waste", catalog="96 Well Flat"), NEST, PLACEMENT["waste"])
    bead_src = wt.place(Plate96("Beads", catalog="96 Well Flat"), NEST, PLACEMENT["beads"])
    eb_src = wt.place(Plate96("EB", catalog="96 Well Flat"), NEST, PLACEMENT["eb"])
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), NEST, PLACEMENT["tips"])
    final = wt.place(Plate96("Eluate", catalog="96 Well Flat"), NEST, PLACEMENT["eluate"])

    plate.fill_all(sample_buffer, 30.0)
    for w in plate.wells.values():      # small analyte marker on top of the buffer
        w.layers.append(Layer(reagent=dna, volume_ul=2.0))
    bead_src.fill_all(ampure, 60.0)
    eb_src.fill_all(elution, 40.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)

    wt.group("Add beads and bind")
    head.aspirate(bead_src, 60.0, liquid_class=LIQUID_CLASS)
    head.dispense(plate, 60.0, liquid_class=LIQUID_CLASS)   # sets bead phase
    head.mix(plate, 40.0, liquid_class=LIQUID_CLASS)         # DNA binds beads

    wt.group("Magnetise, remove supernatant")
    wt.gripper.move(plate, onto=magnet)
    head.aspirate(plate, 90.0, liquid_class=LIQUID_CLASS)    # buffer + beads = 90 µL free liquid
    head.dispense(waste, 90.0, liquid_class=LIQUID_CLASS)
    wt.gripper.move(plate, to=(NEST, PLACEMENT["sample"]))

    wt.group("Elute")
    head.aspirate(eb_src, 40.0, liquid_class=LIQUID_CLASS)
    head.dispense(plate, 40.0, liquid_class=LIQUID_CLASS)
    head.mix(plate, 30.0, liquid_class=LIQUID_CLASS)         # DNA released

    wt.group("Magnetise, recover eluate")
    wt.gripper.move(plate, onto=magnet)
    head.aspirate(plate, 42.0, liquid_class=LIQUID_CLASS)    # EB 40 + DNA 2
    head.dispense(final, 42.0, liquid_class=LIQUID_CLASS)
    wt.gripper.move(plate, to=(NEST, PLACEMENT["sample"]))

    head.return_tips(tips)
    head.drop_adapter()
    return wt


def test_sat_1080_profile_describes_the_1080_deck() -> None:
    """The profile names the 1080 Base Unit and every AMPure slot we use is
    valid on that deck per the saved snapshot."""
    profile = _profile()
    assert profile["workspace"]["base_worktable_name"] == "1080 Base Unit"

    snapshot = load_current_worktable_snapshot(PROFILE_DIR / "current_worktable.py")
    assert snapshot is not None
    assert snapshot["workspace"]["guid"] == profile["workspace"]["guid"]

    valid = set(snapshot["valid_slots"])
    for role, position in PLACEMENT.items():
        assert (NEST, position) in valid, f"{role} slot {(NEST, position)} not valid on the 1080 deck"


def test_ampure_cleanup_magnet_roundtrip_on_1080() -> None:
    """Bead model on the 1080 deck: the magnet flips, the supernatant draw
    retains beads + bound DNA, and elution recovers the DNA into the eluate."""
    profile = _profile()
    name, _guid = _require_workspace(profile)

    wt = _build_ampure_1080(profile)
    assert wt.workspace_name == name
    wt.simulate()

    def _sample_mag(s):
        try:
            return s.labware("Sample").is_magnetized
        except KeyError:
            return None

    # Magnetization round-trip is observable on the Sample plate.
    sample_mag = [m for m in (_sample_mag(s) for s in wt.snapshots) if m is not None]
    assert any(sample_mag) and not all(sample_mag)

    # The supernatant draw (first aspirate while Sample is magnetised) carries
    # bulk liquid + beads but NOT the DNA — DNA stays bound to retained beads.
    types = [type(s.step).__name__ for s in wt.snapshots]
    on_idx = next(i for i, s in enumerate(wt.snapshots) if _sample_mag(s) is True)
    asp_idx = next(
        i for i, t in enumerate(types[on_idx:], start=on_idx) if t == "AspirateStep"
    )
    sup = {l.reagent.name for l in wt.snapshots[asp_idx].mca_tips[0].layers}
    assert "AMPure beads" in sup and "Sample DNA" not in sup

    final = wt.snapshots[-1]
    # DNA was retained on beads through the supernatant draw, never washed away.
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


def test_ampure_1080_compiles_to_bound_xscr(tmp_path: Path) -> None:
    """The 1080-bound protocol compiles to a workspace-bound .xscr."""
    profile = _profile()
    _require_workspace(profile)

    wt = _build_ampure_1080(profile)
    wt.simulate()
    out = wt.compile(tmp_path / "ampure_1080.xscr")
    assert Path(out).exists()
