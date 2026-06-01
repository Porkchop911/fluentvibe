"""Tests for the compatibility-index extension.

Covers:
1. Schema-version guard rejects old DBs with a CLI-pointing message.
2. build_index() populates component_sites / workspace_components /
   liquid_class_heads tables.
3. Pinned compatibility queries against the live FluentControl install.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fluentvibe.catalog import (
    CatalogSchemaOutOfDate,
    INDEX_SCHEMA_VERSION,
    find_grip_modes,
    find_labware_for_site,
    find_legal_stacks,
    find_sites_for,
    find_workspaces_using,
    liquid_classes_for_head,
    index_exists,
    open_index,
    resolve_by_name,
)
from fluentvibe.catalog.catalog import _check_schema
from fluentvibe.catalog.indexer import _canonical_catalog_name, build_index
from fluentvibe.catalog.xcmp import (
    XcmpArrangement,
    XcmpComponent,
    XcmpPipettable,
    XwspWorkspace,
    WorkspaceOccupant,
)


# ── 1. Schema guard ────────────────────────────────────────────────


def test_schema_check_raises_on_old_index(tmp_path: Path) -> None:
    """A pre-v2 install row must trip CatalogSchemaOutOfDate with CLI hint."""
    db_path = tmp_path / "old.db"
    # Seed an install row at schema_version=0 (the legacy state).
    with open_index(db_path) as conn:
        conn.execute(
            "INSERT INTO install (install_path, fingerprint, built_at, schema_version) "
            "VALUES (?, ?, ?, ?)",
            ("/legacy", "deadbeef", "2026-01-01T00:00:00+00:00", 0),
        )
        conn.commit()

    with open_index(db_path) as conn:
        with pytest.raises(CatalogSchemaOutOfDate) as exc:
            _check_schema(conn)

    assert "fluentvibe catalog refresh" in str(exc.value)
    assert f"v{INDEX_SCHEMA_VERSION}" in str(exc.value)


def test_schema_check_passes_on_current_index(tmp_path: Path) -> None:
    db_path = tmp_path / "current.db"
    with open_index(db_path) as conn:
        conn.execute(
            "INSERT INTO install (install_path, fingerprint, built_at, schema_version) "
            "VALUES (?, ?, ?, ?)",
            ("/cur", "abc", "2026-05-01T00:00:00+00:00", INDEX_SCHEMA_VERSION),
        )
        conn.commit()
        # Should not raise.
        _check_schema(conn)


def test_schema_check_quiet_when_install_table_empty(tmp_path: Path) -> None:
    """First-time build path: no install row yet means no version check."""
    db_path = tmp_path / "empty.db"
    with open_index(db_path) as conn:
        _check_schema(conn)  # must not raise


# ── 2. build_index populates compat tables ─────────────────────────


def _arr(sites: int = 1) -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=sites,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
        allowed_grip_modes={0: ("RoMa",)} if sites else {},
    )


def test_build_index_populates_compat_tables(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)

    # Two components: a carrier and a labware that fits its footprint.
    nest_path = components / "nest.xcmp"
    plate_path = components / "plate.xcmp"
    nest_path.write_text("<x/>")
    plate_path.write_text("<x/>")

    def fake_load_xcmp(path: Path) -> XcmpComponent:
        if path.name == "nest.xcmp":
            return XcmpComponent(
                guid="nest-guid",
                name="MyNest",
                file_path=path,
                dim_mm=(127.0, 86.0, 10.0),
                functional_group="Carrier.Nest",
                footprint="Microplate",
                arrangement=_arr(sites=1),
            )
        return XcmpComponent(
            guid="plate-guid",
            name="MyPlate",
            file_path=path,
            dim_mm=(127.0, 86.0, 15.0),
            functional_group="Labware.Microplate",
            footprint="Microplate",
            pipettable=XcmpPipettable(
                x_wells=12,
                y_wells=8,
                x_spacing_mm=9.0,
                y_spacing_mm=9.0,
                first_well_mm=(10.0, 10.0, 0.0),
            ),
        )

    ws_path = workspaces / "MyWorkspace.xwsp"
    ws_path.write_text("<x/>")

    def fake_load_xwsp(path: Path) -> XwspWorkspace:
        return XwspWorkspace(
            guid="ws-guid",
            name="MyWorkspace",
            file_path=path,
            base_worktable_guid=None,
            base_worktable_name=None,
            occupants=(
                WorkspaceOccupant(
                    site_path=(3,),
                    site_index=3,
                    catalog_name="MyNest[001]",  # FC's positional suffix
                    base_location_identifier="Nest",
                    base_location_connector_identifier=None,
                ),
            ),
            available_sites=((((3,), "Nest"),)),
            location_names=("Nest",),
            referenced_labware_names=("MyNest",),
        )

    monkeypatch.setattr("fluentvibe.catalog.indexer.load_xcmp", fake_load_xcmp)
    monkeypatch.setattr("fluentvibe.catalog.indexer.load_xwsp", fake_load_xwsp)

    db_path = tmp_path / "index.db"
    counts = build_index(install_path=install, db_path=db_path)

    assert counts["component_sites"] == 1  # nest's 1×1 site; plate has no arrangement
    assert counts["workspace_components"] == 1

    # The positional suffix was stripped → JOIN finds the canonical name.
    sites_for_plate = find_sites_for("MyPlate", db_path=db_path)
    assert len(sites_for_plate) == 1
    s = sites_for_plate[0]
    assert s.workspace_name == "MyWorkspace"
    assert s.component_name == "MyNest"
    assert s.site_index == 1   # 1-based, FC convention
    assert s.footprint == "Microplate"
    assert "RoMa" in s.grip_modes

    # The compat helper for grippers reports the nest's grip mode.
    assert find_grip_modes("MyNest", db_path=db_path) == ["RoMa"]

    # find_labware_for_site returns labware (non-lid) sharing the footprint.
    candidates = find_labware_for_site("MyNest", 1, db_path=db_path)
    assert any(r.name == "MyPlate" for r in candidates)


def test_canonical_catalog_name_strips_positional_suffix() -> None:
    assert _canonical_catalog_name("MyNest[001]") == "MyNest"
    assert _canonical_catalog_name("96 Well Flat[042]") == "96 Well Flat"
    assert _canonical_catalog_name("Plain Name") == "Plain Name"
    assert _canonical_catalog_name("  Trim Me  [007]  ") == "Trim Me"


# ── 3. Live-install pinned cases ───────────────────────────────────


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_live_find_sites_for_microplate() -> None:
    """96 Well Flat has 'Microplate' footprint → must find carrier sites."""
    plate = resolve_by_name("96 Well Flat")
    assert plate is not None
    assert plate.footprint == "Microplate"

    sites = find_sites_for("96 Well Flat")
    assert sites, "Expected non-empty site list for a Microplate-footprint plate"
    # All returned sites must actually accept the Microplate footprint.
    assert all(s.footprint == "Microplate" for s in sites)


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_live_find_legal_stacks_for_plate() -> None:
    stacks = find_legal_stacks("96 Well Flat")
    above_names = [r.name for r in stacks["above"]]
    below_names = [r.name for r in stacks["below"]]
    # Above: at least one Microplate-footprint lid candidate.
    assert any("Lid" in name for name in above_names), above_names
    # Below: at least one adapter or magnet_rack with matching footprint.
    assert below_names, "Expected at least one stack-below candidate"
    # All above entries must be lids.
    for r in stacks["above"]:
        assert r.is_lid


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_live_liquid_classes_for_head_mca96() -> None:
    lcs = liquid_classes_for_head("Mca96")
    assert lcs, "Expected at least one LC supporting head 'Mca96'"
    # Every entry must declare Mca96 in supported_heads.
    for lc in lcs:
        assert "Mca96" in lc.supported_heads


@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_live_find_workspaces_using_common_carrier() -> None:
    """Carriers that ARE pre-placed (nests, frames) should reverse-lookup."""
    # '61mm Nest' is among the most-placed components on the live install.
    matches = find_workspaces_using("61mm Nest")
    assert matches, "Expected workspaces using '61mm Nest'"
