"""v1.1 acceptance: `Worktable.from_workspace` loads .xwsp data correctly."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import FixedDeck, Plate96, Worktable, InvalidSlotError  # noqa: E402
from fluentvibe.catalog.catalog import (  # noqa: E402
    WorkspaceEntry,
    index_exists,
    resolve_workspace_by_guid,
    resolve_workspace_by_name,
)
from fluentvibe.catalog.indexer import build_index, install_path_default  # noqa: E402
from fluentvibe.catalog.xcmp import (  # noqa: E402
    load_component_site_location_names,
    load_xsit,
    load_xwsp,
)


def _install_present() -> bool:
    return (install_path_default() / "SystemSpecific" / "Worktable" / "Components").exists()


@pytest.fixture(scope="module", autouse=True)
def _refresh_index_for_real_install() -> None:
    if _install_present():
        build_index()


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_unknown_workspace_raises() -> None:
    with pytest.raises(ValueError, match="not found in catalog index"):
        Worktable.from_workspace("This Workspace Does Not Exist")


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_workspace_registers_valid_slots() -> None:
    """Pick any workspace in the index that has at least 4 sites and exercise it."""
    rich_ws = _find_workspace_with_min_sites(4)
    if rich_ws is None:
        pytest.skip("no workspace in this install has 4+ available sites")

    wt = Worktable.from_workspace(rich_ws, auto_place=False)
    assert wt.workspace_name == rich_ws
    assert wt.valid_slots is not None
    assert len(wt.valid_slots) >= 4


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_workspace_resolves_by_name_when_guid_is_missing_locally() -> None:
    entry = resolve_workspace_by_name("780_Empty")
    assert entry is not None
    wt = Worktable.from_workspace(
        "780_Empty",
        workspace_guid="00000000-0000-0000-0000-000000000000",
        auto_place=False,
    )
    assert wt.workspace_name == "780_Empty"
    assert wt.workspace_guid == entry.guid == entry.file_path.stem


_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_load_xwsp_uses_workspace_file_guid_for_sat_workspace() -> None:
    # Assert install-independent invariants: load_xwsp must derive the
    # workspace guid from the file name and read a well-formed base-worktable
    # guid + non-empty name out of the file. Concrete base-unit values are
    # install-specific (e.g. a 780 vs 1080 base unit) and deliberately not
    # pinned here — see REVIEW_NOTES.md on install-coupled tests.
    path = _workspace_path("291ba293-6361-4f8f-aa8d-7c2643d3f096")
    ws = load_xwsp(path)

    assert ws.guid == path.stem
    assert ws.name == "SAT_Fluent_780_Rev3"
    assert _GUID_RE.match(ws.base_worktable_guid or ""), ws.base_worktable_guid
    assert (ws.base_worktable_name or "").strip()


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_load_xsit_exposes_location_group_name_for_nest_site() -> None:
    site_path = (
        install_path_default()
        / "SystemSpecific"
        / "Worktable"
        / "Sites"
        / "f1111111-1234-aaaa-ffff-000000000049.xsit"
    )
    site = load_xsit(site_path)

    assert site.guid == site_path.stem
    assert site.location_group_name == "Nest61mm_Pos"


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_sat_workspace_available_sites_use_logical_location_names() -> None:
    path = _workspace_path("291ba293-6361-4f8f-aa8d-7c2643d3f096")
    ws = load_xwsp(path)

    logical_names = {name for _, name in ws.available_sites if name}
    assert "Nest61mm_Pos" in logical_names
    assert any(name.startswith("Nest61mm_Pos") for name in logical_names)


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_workspace_resolves_by_name_and_guid_to_same_file() -> None:
    expected_guid = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
    by_name = resolve_workspace_by_name("SAT_Fluent_780_Rev3")
    by_guid = resolve_workspace_by_guid(expected_guid)

    assert by_name is not None
    assert by_guid is not None
    assert by_name.guid == expected_guid
    assert by_guid.guid == expected_guid
    assert by_name.file_path == by_guid.file_path


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_sat_workspace_valid_slots_include_nest61mm_position_6() -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
    )

    assert wt.valid_slots is not None
    assert ("Nest61mm_Pos", 6) in wt.valid_slots


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_trough_carrier_component_exposes_ordered_trough_site_names() -> None:
    component_path = (
        install_path_default()
        / "SystemSpecific"
        / "Worktable"
        / "Components"
        / "a82e129c-c3d4-46fc-8985-7c78d63ac0cb.xcmp"
    )

    assert load_component_site_location_names(component_path) == (
        "TroughMP_1",
        "TroughMP_2",
        "TroughMP_3",
        "TroughMP_4",
        "TroughMP_5",
        "TroughMP_6",
    )


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_placing_trough_carrier_registers_child_trough_slots() -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev4",
        workspace_guid="2baf8c89-406a-455a-9a91-6378fc41a0a5",
        auto_place=False,
    )

    assert wt.valid_slots is not None
    wt.place(
        FixedDeck("Trough_Carrier", catalog="6_Trough_Carrier_Reservoir"),
        "Nest7mm_Pos",
        6,
        allow_occupied=True,
    )

    assert ("TroughMP_1", 1) in wt.valid_slots
    assert ("TroughMP_4", 1) in wt.valid_slots
    assert ("TroughMP_6", 1) in wt.valid_slots


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_invalid_slot_raises_on_place() -> None:
    rich_ws = _find_workspace_with_min_sites(1)
    if rich_ws is None:
        pytest.skip("no workspace available")
    wt = Worktable.from_workspace(rich_ws, auto_place=False)
    with pytest.raises(InvalidSlotError, match="not on workspace"):
        wt.place(Plate96("Bogus", catalog="96 Well Flat"), "DefinitelyNotALocation", 999)


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_valid_slot_accepted() -> None:
    rich_ws = _find_workspace_with_min_sites(1)
    if rich_ws is None:
        pytest.skip("no workspace available")
    wt = Worktable.from_workspace(rich_ws, auto_place=False)
    loc, pos = sorted(wt.valid_slots)[0]
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), loc, pos)
    assert src.slot == (loc, pos)


def test_compile_requires_bound_workspace(tmp_path: Path) -> None:
    wt = Worktable(name="Unbound protocol")
    wt.group("Setup")
    wt.place(Plate96("Plate", catalog="96 Well Flat"), "Site", 1)

    with pytest.raises(ValueError, match="not bound to a specific FluentControl workspace"):
        wt.compile(tmp_path / "unbound.xscr")


def test_workspace_auto_place_fails_loudly_on_unresolved_occupant(monkeypatch) -> None:
    from fluentvibe.catalog import catalog as catalog_mod
    from fluentvibe.catalog import xcmp as xcmp_mod

    fake_workspace = SimpleNamespace(
        guid="fake-workspace-guid",
        name="FakeWorkspace",
        occupants=(
            SimpleNamespace(
                catalog_name="Missing Deck Item",
                site_index=0,
                base_location_identifier="Site",
            ),
        ),
        available_sites=(((0,), "Site"),),
        location_names=("Site",),
    )

    monkeypatch.setattr(catalog_mod, "index_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        catalog_mod,
        "resolve_workspace_by_name",
        lambda *args, **kwargs: WorkspaceEntry(
            guid="11111111-1111-1111-1111-111111111111",
            name="FakeWorkspace",
            file_path=Path("fake.xwsp"),
        ),
    )
    monkeypatch.setattr(catalog_mod, "resolve_workspace_by_guid", lambda *args, **kwargs: None)
    monkeypatch.setattr(catalog_mod, "resolve_by_name", lambda *args, **kwargs: None)
    monkeypatch.setattr(xcmp_mod, "load_xwsp", lambda *args, **kwargs: fake_workspace)

    with pytest.raises(ValueError, match="requires occupant 'Missing Deck Item'"):
        Worktable.from_workspace("FakeWorkspace")


def test_workspace_reference_is_not_ambiguous_when_name_and_guid_hit_same_file(monkeypatch) -> None:
    from fluentvibe.catalog import catalog as catalog_mod
    from fluentvibe.catalog import xcmp as xcmp_mod

    same_file = Path("shared.xwsp")
    fake_workspace = SimpleNamespace(
        guid="doc-guid",
        name="Shared Workspace",
        occupants=(),
        available_sites=(((0,), "Site"),),
        location_names=("Site",),
    )

    monkeypatch.setattr(catalog_mod, "index_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        catalog_mod,
        "resolve_workspace_by_guid",
        lambda *args, **kwargs: WorkspaceEntry(
            guid="guid-row",
            name="Shared Workspace",
            file_path=same_file,
        ),
    )
    monkeypatch.setattr(
        catalog_mod,
        "resolve_workspace_by_name",
        lambda *args, **kwargs: WorkspaceEntry(
            guid="name-row",
            name="Shared Workspace",
            file_path=same_file,
        ),
    )
    monkeypatch.setattr(xcmp_mod, "load_xwsp", lambda *args, **kwargs: fake_workspace)

    wt = Worktable.from_workspace("Shared Workspace", workspace_guid="doc-guid", auto_place=False)
    assert wt.workspace_name == "Shared Workspace"
    assert wt.workspace_guid == "doc-guid"


def test_workspace_reference_is_ambiguous_when_name_and_guid_hit_different_files(monkeypatch) -> None:
    from fluentvibe.catalog import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "index_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        catalog_mod,
        "resolve_workspace_by_guid",
        lambda *args, **kwargs: WorkspaceEntry(
            guid="guid-row",
            name="Workspace From Guid",
            file_path=Path("guid.xwsp"),
        ),
    )
    monkeypatch.setattr(
        catalog_mod,
        "resolve_workspace_by_name",
        lambda *args, **kwargs: WorkspaceEntry(
            guid="name-row",
            name="Workspace From Name",
            file_path=Path("name.xwsp"),
        ),
    )

    with pytest.raises(ValueError, match="Workspace reference is ambiguous"):
        Worktable.from_workspace("Workspace From Name", workspace_guid="guid-row", auto_place=False)


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_protocol_carries_workspace_guid_and_name() -> None:
    rich_ws = _find_workspace_with_min_sites(1)
    if rich_ws is None:
        pytest.skip("no workspace available")
    wt = Worktable.from_workspace(rich_ws, auto_place=False, protocol_name="Bound")
    proto = wt.to_protocol()
    assert proto.worktable_name == wt.workspace_name
    assert proto.worktable_guid == wt.workspace_guid


def _find_workspace_with_min_sites(min_sites: int) -> str | None:
    import sqlite3
    from fluentvibe.catalog.catalog import DEFAULT_INDEX_PATH

    conn = sqlite3.connect(str(DEFAULT_INDEX_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT name, file_path FROM workspaces").fetchall()
    conn.close()

    for r in rows:
        try:
            ws = load_xwsp(r["file_path"])
        except Exception:
            continue
        if len(ws.available_sites) >= min_sites:
            return r["name"]
    return None


def _workspace_path(stem: str) -> Path:
    return install_path_default() / "SystemSpecific" / "Worktable" / "Workspaces" / f"{stem}.xwsp"
