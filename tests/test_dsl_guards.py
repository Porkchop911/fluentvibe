"""DSL guard rails — deterministic rules the LM regresses on intermittently.

Three rules, encoded in the authoring layer so the build fails fast (and the
LM repair loop sees a Python traceback) instead of passing compile+simulate
and exploding only inside FluentControl's InfoPad.

1. Troughs must be placed on `WS_100ml_*` (Class 3 — out of arm range).
2. The `100ml` trough catalog needs an ethanol/wash marker (Class 4 — Z-Max
   unreachable for standard tips).
3. A LiHa/worklist protocol must place an FCA tip box (Class 1 — `No
   DiTi-Labware … found`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import (  # noqa: E402
    LihaTipMismatchError,
    LiquidClassSectionError,
    MCA200Box,
    MissingFCATipBoxError,
    Plate96,
    TipBox,
    Trough,
    TroughPlacementError,
    Worktable,
)
from fluentvibe.catalog import index_exists  # noqa: E402

requires_index = pytest.mark.skipif(
    not index_exists(), reason="needs the FluentControl catalog index"
)


# ── Rule 1 — trough slot guard ─────────────────────────────────────────


def _deck780(name: str) -> Worktable:
    """Trough/LiHa guard rails are scoped to the SAT_Fluent_780 deck."""
    wt = Worktable(name=name)
    wt.workspace_name = "SAT_Fluent_780_Rev3"
    wt.workspace_guid = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    wt.group("Setup")
    return wt


def test_trough_on_ws_100ml_slot_passes() -> None:
    wt = _deck780("Trough OK")
    wt.place(Trough("Buffer", catalog="25ml_short"), "WS_100ml_1", 1)


def test_trough_on_nest_slot_raises() -> None:
    wt = _deck780("Trough bad slot")
    with pytest.raises(TroughPlacementError, match="WS_100ml_"):
        wt.place(Trough("Buffer", catalog="25ml_short"), "Nest61mm_Pos", 1)


def test_trough_on_arbitrary_location_raises() -> None:
    wt = _deck780("Trough bad slot 2")
    with pytest.raises(TroughPlacementError):
        wt.place(Trough("Buffer", catalog="25ml_short"), "Site", 1)


def test_trough_off_780_deck_is_not_guarded() -> None:
    """Vanilla Worktable (no deck binding) skips the trough rule — the rule is
    data-driven per deck and shouldn't fire on test fixtures or workspaces with
    no configured deck_rules."""
    wt = Worktable(name="Generic")
    wt.group("Setup")
    wt.place(Trough("Buffer", catalog="25ml_short"), "Nest", 1)


def test_unknown_bound_deck_has_no_deck_rules() -> None:
    """A workspace with no entry in config deck_rules and no active profile is
    left permissive — the guards are keyed to data, not a hardcoded name."""
    wt = Worktable(name="Other")
    wt.workspace_name = "Some_Other_Deck_Rev9"
    assert wt._deck_rules() == {}
    wt.group("Setup")
    wt.place(Trough("Buffer", catalog="25ml_short"), "Nest61mm_Pos", 1)  # no raise


def test_profile_deck_rules_drive_trough_guard(tmp_path: Path, monkeypatch) -> None:
    """An active workspace-app profile supplies deck_rules; the trough guard
    then enforces that deck's trough family (proves the rules are data-driven,
    not hardcoded to 780)."""
    import json

    import yaml as _yaml

    from fluentvibe.authoring.profile import PROFILE_DIR_ENV

    name = "Profile_Deck_Q"
    root = tmp_path / "prof"
    root.mkdir()
    (root / "workspace_profile.json").write_text(
        json.dumps({"workspace": {"name": name, "guid": "11112222-3333-4444-5555-666677778888"}}),
        encoding="utf-8",
    )
    (root / "generation.profile.yaml").write_text(
        _yaml.safe_dump({"deck_rules": {name: {"trough_locations": ["WS_50ml_"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(PROFILE_DIR_ENV, str(root))

    wt = Worktable(name="profile-bound")
    wt.workspace_name = name
    wt.group("Setup")
    wt.place(Trough("Buffer", catalog="25ml_short"), "WS_50ml_1", 1)  # reachable: ok
    with pytest.raises(TroughPlacementError, match="WS_50ml_"):
        wt.place(Trough("Buffer2", catalog="25ml_short"), "Nest61mm_Pos", 1)


# ── Rule 2 — 100ml trough purpose check ────────────────────────────────


def test_100ml_trough_with_wash_marker_passes() -> None:
    wt = _deck780("100ml OK")
    wt.place(Trough("EthanolReservoir", catalog="100ml"), "WS_100ml_1", 1)


@pytest.mark.parametrize("label", ["Ethanol80", "WashBuffer", "EtOH", "AlcoholDump"])
def test_100ml_trough_label_variants_pass(label: str) -> None:
    wt = _deck780(f"100ml OK {label}")
    wt.place(Trough(label, catalog="100ml"), "WS_100ml_1", 1)


def test_100ml_trough_without_marker_raises() -> None:
    wt = _deck780("100ml bad")
    with pytest.raises(TroughPlacementError, match="25ml_short"):
        wt.place(Trough("Buffer", catalog="100ml"), "WS_100ml_1", 1)


def test_25ml_trough_no_marker_required() -> None:
    wt = _deck780("25ml OK")
    wt.place(Trough("Buffer", catalog="25ml_short"), "WS_100ml_1", 1)


# ── Rule 3 — LiHa requires FCA tip box ────────────────────────────────


def _liha_only_worktable(tmp_path: Path, *, place_fca: bool) -> Worktable:
    """Build a minimal LiHa-using protocol; optionally place an FCA box."""
    wt = Worktable(name="Liha probe")
    wt.workspace_name = "SAT_Fluent_780_Rev3"
    wt.workspace_guid = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    wt.group("Setup")
    plate = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest61mm_Pos", 1)
    if place_fca:
        wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
    wt.group("Run")
    wt.liha.get_tips()
    wt.liha.aspirate(plate, 50.0, liquid_class="Water Free Single")
    wt.liha.dispense(plate, 50.0, liquid_class="Water Free Single")
    wt.liha.drop_tips()
    return wt


def test_liha_without_fca_box_raises(tmp_path: Path) -> None:
    wt = _liha_only_worktable(tmp_path, place_fca=False)
    with pytest.raises(MissingFCATipBoxError, match="FCA"):
        wt.compile(tmp_path / "out.xscr")


def test_liha_with_fca_box_compiles(tmp_path: Path) -> None:
    wt = _liha_only_worktable(tmp_path, place_fca=True)
    out = wt.compile(tmp_path / "out.xscr")
    assert out.exists()


def test_mca_only_protocol_does_not_require_fca_box(tmp_path: Path) -> None:
    wt = Worktable(name="MCA only")
    wt.workspace_name = "SAT_Fluent_780_Rev3"
    wt.workspace_guid = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    wt.group("Setup")
    plate = wt.place(Plate96("P", catalog="96 Well Flat"), "Nest61mm_Pos", 1)
    wt.place(MCA200Box("MCA_Tips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4)
    wt.group("Run")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up("MCA_Tips")
    wt.mca96.aspirate(plate, 50.0, liquid_class="Water Free Single")
    wt.mca96.dispense(plate, 50.0, liquid_class="Water Free Single")
    wt.mca96.return_tips()
    out = wt.compile(tmp_path / "_test_mca_only.xscr")
    assert out.exists()


# ── Rule 4 — LiHa must pick up an FCA tip box (not an MCA box) ─────────


def _liha_picks_box(tmp_path: Path, box: TipBox) -> Worktable:
    """LiHa protocol that places *and* picks up the given tip box. An FCA box
    is also present so the presence check (Rule 3) passes — this isolates the
    pickup-type guard."""
    wt = Worktable(name="Liha pickup probe")
    wt.workspace_name = "SAT_Fluent_780_Rev3"
    wt.workspace_guid = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    wt.group("Setup")
    plate = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest61mm_Pos", 1)
    wt.place(TipBox("FCA_Present", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
    wt.place(box, "Nest61mm_Pos", 4)
    wt.group("Run")
    wt.liha.get_tips(box)
    wt.liha.aspirate(plate, 50.0, liquid_class="Water Free Single")
    wt.liha.drop_tips()
    return wt


def test_liha_picking_up_mca_box_raises(tmp_path: Path) -> None:
    """Presence check passes (an FCA box is on deck) but the LiHa picks up an
    MCA box — FC would raise `No DiTi-Labware MCA96 … found`."""
    wt = _liha_picks_box(tmp_path, MCA200Box("LiHa_Tips", catalog="MCA96, 200ul, Box"))
    with pytest.raises(LihaTipMismatchError, match="FCA"):
        wt.compile(tmp_path / "out.xscr")


def test_liha_picking_up_fca_box_compiles(tmp_path: Path) -> None:
    wt = _liha_picks_box(tmp_path, TipBox("FCA_Pick", catalog="FCA, 200ul SBS"))
    out = wt.compile(tmp_path / "out.xscr")
    assert out.exists()


# ── Rule 5 — Mix steps require a Mix-capable liquid class ──────────────


def _mca_mix(tmp_path: Path, liquid_class: str, *, as_variable: bool) -> Worktable:
    wt = Worktable(name="Mix probe")
    wt.workspace_name = "SAT_Fluent_780_Rev3"
    wt.workspace_guid = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    wt.group("Setup")
    plate = wt.place(Plate96("P", catalog="96 Well Flat"), "Nest61mm_Pos", 1)
    wt.place(MCA200Box("MCA_Tips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4)
    wt.group("Run")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up("MCA_Tips")
    if as_variable:
        wt.declare_variable("LC_MIX", liquid_class)
        wt.mca96.mix(plate, 50.0, cycles=5, liquid_class="LC_MIX")
    else:
        wt.mca96.mix(plate, 50.0, cycles=5, liquid_class=liquid_class)
    wt.mca96.return_tips()
    return wt


@requires_index
def test_mix_with_non_mix_class_raises(tmp_path: Path) -> None:
    wt = _mca_mix(tmp_path, "Water Free Single", as_variable=False)
    with pytest.raises(LiquidClassSectionError, match="Mix"):
        wt.compile(tmp_path / "out.xscr")


@requires_index
def test_mix_with_non_mix_class_via_variable_raises(tmp_path: Path) -> None:
    """The liquid class is usually a declared variable — resolve it before
    checking (this is the ip-dynabeads regression shape)."""
    wt = _mca_mix(tmp_path, "Water Free Single", as_variable=True)
    with pytest.raises(LiquidClassSectionError):
        wt.compile(tmp_path / "out.xscr")


@requires_index
def test_mix_with_water_mix_compiles(tmp_path: Path) -> None:
    wt = _mca_mix(tmp_path, "Water Mix", as_variable=False)
    out = wt.compile(tmp_path / "out.xscr")
    assert out.exists()


@requires_index
def test_mix_with_unknown_class_is_not_blocked(tmp_path: Path) -> None:
    """An unresolvable class name leaves the guard silent (don't block on
    missing catalog data)."""
    wt = _mca_mix(tmp_path, "Totally Made Up Class", as_variable=False)
    out = wt.compile(tmp_path / "out.xscr")
    assert out.exists()
