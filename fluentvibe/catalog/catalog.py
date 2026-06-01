"""SQL queries over the catalog index.

The index lives at `fluentvibe/catalog/install_index.db` (inside the package).
It's built by `indexer.build_index()` and queried by everything else in
fluentvibe that needs to resolve a catalog name to a file path.
"""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional


DEFAULT_INDEX_PATH = Path(__file__).resolve().parent / "install_index.db"

# Bumped whenever the catalog schema gains compat-relevant columns or
# tables. Old indexes are detected on first read and rejected with a
# clear "run `fluentvibe catalog refresh`" message — we never auto-rebuild
# (the user's CLI-only choice).
INDEX_SCHEMA_VERSION = 2


class CatalogSchemaOutOfDate(RuntimeError):
    """Raised when an on-disk index predates the running fluentvibe build."""


# ── Public types ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogEntry:
    """One row from the `components` table."""

    guid: str
    name: str
    category: str
    file_path: Path
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    dim_x_mm: Optional[float] = None
    dim_y_mm: Optional[float] = None
    dim_z_mm: Optional[float] = None
    site_count: Optional[int] = None
    functional_group: Optional[str] = None
    component_kind: str = "unknown"
    component_subtype: Optional[str] = None
    footprint: Optional[str] = None
    is_lid: bool = False
    renderer: Optional[str] = None


@dataclass(frozen=True)
class CompatibleSite:
    """A site that accepts a given component's footprint."""

    workspace_guid: str
    workspace_name: str
    component_guid: str   # the carrier/labware that exposes this site
    component_name: str
    site_index: int       # 1-based, FC convention
    footprint: Optional[str]
    grip_modes: tuple[str, ...] = ()
    base_location: Optional[str] = None


@dataclass(frozen=True)
class WorkspaceEntry:
    """One row from the `workspaces` table."""

    guid: str
    name: str
    file_path: Path


@dataclass(frozen=True)
class LiquidClassEntry:
    """One row from the `liquid_classes` table."""

    guid: str
    name: str
    head: Optional[str]
    file_path: Path
    supported_heads: tuple[str, ...] = ()


# ── Schema ─────────────────────────────────────────────────────────


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS install (
    install_path   TEXT PRIMARY KEY,
    fingerprint    TEXT NOT NULL,
    built_at       TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS components (
    guid          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    grid_x        INTEGER,
    grid_y        INTEGER,
    dim_x_mm      REAL,
    dim_y_mm      REAL,
    dim_z_mm      REAL,
    site_count    INTEGER,
    functional_group TEXT,
    component_kind TEXT NOT NULL DEFAULT 'unknown',
    component_subtype TEXT,
    footprint     TEXT,
    is_lid        INTEGER NOT NULL DEFAULT 0,
    renderer      TEXT,
    allowed_grip_modes TEXT
);

CREATE INDEX IF NOT EXISTS components_by_name      ON components(name);
CREATE INDEX IF NOT EXISTS components_by_category  ON components(category);
CREATE INDEX IF NOT EXISTS components_by_footprint ON components(footprint);

CREATE TABLE IF NOT EXISTS workspaces (
    guid          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    file_path     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS workspaces_by_name ON workspaces(name);

CREATE TABLE IF NOT EXISTS sites (
    guid          TEXT PRIMARY KEY,
    file_path     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS liquid_classes (
    guid          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    head          TEXT,
    supported_heads TEXT,
    file_path     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS liquid_classes_by_name ON liquid_classes(name);

CREATE TABLE IF NOT EXISTS component_sites (
    component_guid TEXT NOT NULL,
    site_index     INTEGER NOT NULL,    -- 1-based, FC convention
    footprint      TEXT,                -- footprint accepted at this site
    grip_modes     TEXT,                -- JSON list of CGA names
    PRIMARY KEY (component_guid, site_index)
);

CREATE INDEX IF NOT EXISTS component_sites_by_footprint ON component_sites(footprint);

CREATE TABLE IF NOT EXISTS workspace_components (
    workspace_guid TEXT NOT NULL,
    component_name TEXT NOT NULL,
    site_path      TEXT NOT NULL,       -- '0/2/1' style
    base_location  TEXT,
    PRIMARY KEY (workspace_guid, component_name, site_path)
);

CREATE INDEX IF NOT EXISTS workspace_components_by_name ON workspace_components(component_name);

CREATE TABLE IF NOT EXISTS liquid_class_heads (
    liquid_class_guid TEXT NOT NULL,
    head              TEXT NOT NULL,
    PRIMARY KEY (liquid_class_guid, head)
);

CREATE INDEX IF NOT EXISTS liquid_class_heads_by_head ON liquid_class_heads(head);
"""


# ── Connection management ──────────────────────────────────────────


@contextmanager
def open_index(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager returning a connection to the catalog index."""
    path = Path(db_path) if db_path else DEFAULT_INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Order matters: migrations add columns to legacy tables first so
        # SCHEMA_SQL's CREATE INDEX statements that reference those columns
        # succeed on upgraded databases. On a fresh DB the migrate pass is
        # a cheap no-op (PRAGMA on a missing table returns nothing).
        _migrate_schema(conn)
        conn.executescript(SCHEMA_SQL)
        yield conn
    finally:
        conn.close()


def index_exists(db_path: Path | str | None = None) -> bool:
    """True iff the index DB has at least one component row."""
    path = Path(db_path) if db_path else DEFAULT_INDEX_PATH
    if not path.exists():
        return False
    try:
        with open_index(path) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM components").fetchone()
            return bool(row and row["n"] > 0)
    except sqlite3.DatabaseError:
        return False


def _check_schema(conn: sqlite3.Connection) -> None:
    """Raise CatalogSchemaOutOfDate if the install row is older than us.

    Called from public query helpers so an upgrade-without-rebuild surfaces
    a clear, CLI-pointing error rather than silently returning empty rows
    for the new compatibility tables.
    """
    row = conn.execute("SELECT schema_version FROM install LIMIT 1").fetchone()
    if row is None:
        # Empty install table — nothing has been indexed yet. Let the
        # query return whatever it returns; ensure_index/build_index
        # owns first-time population.
        return
    version = row["schema_version"] if "schema_version" in row.keys() else 0
    if version is None or version < INDEX_SCHEMA_VERSION:
        raise CatalogSchemaOutOfDate(
            f"Catalog index schema is v{version or 0}, this fluentvibe build "
            f"requires v{INDEX_SCHEMA_VERSION}. "
            f"Run `fluentvibe catalog refresh` to rebuild."
        )


# ── Queries ────────────────────────────────────────────────────────


def resolve_by_name(name: str, *, db_path: Path | str | None = None) -> Optional[CatalogEntry]:
    """Return the unique component with this exact name, or None."""
    with open_index(db_path) as conn:
        _check_schema(conn)
        row = conn.execute(
            "SELECT * FROM components WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    return _entry_from_row(row) if row else None


def find_components(pattern: str, *, db_path: Path | str | None = None) -> list[CatalogEntry]:
    """Substring search on component name. Case-insensitive."""
    like = f"%{pattern}%"
    with open_index(db_path) as conn:
        _check_schema(conn)
        rows = conn.execute(
            "SELECT * FROM components WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
            (like,),
        ).fetchall()
    return [_entry_from_row(r) for r in rows]


def find_components_by_metadata(
    pattern: str,
    *,
    component_kind: str | None = None,
    component_subtype: str | None = None,
    db_path: Path | str | None = None,
) -> list[CatalogEntry]:
    """Substring search on component name with optional component taxonomy filters."""
    like = f"%{pattern}%"
    clauses = ["name LIKE ? COLLATE NOCASE"]
    params: list[str] = [like]
    if component_kind:
        clauses.append("component_kind = ?")
        params.append(component_kind)
    if component_subtype:
        clauses.append("component_subtype = ?")
        params.append(component_subtype)
    sql = "SELECT * FROM components WHERE " + " AND ".join(clauses) + " ORDER BY name"
    with open_index(db_path) as conn:
        _check_schema(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_entry_from_row(r) for r in rows]


def list_by_category(category: str, *, db_path: Path | str | None = None) -> list[CatalogEntry]:
    with open_index(db_path) as conn:
        _check_schema(conn)
        rows = conn.execute(
            "SELECT * FROM components WHERE category = ? ORDER BY name", (category,)
        ).fetchall()
    return [_entry_from_row(r) for r in rows]


def category_counts(*, db_path: Path | str | None = None) -> dict[str, int]:
    with open_index(db_path) as conn:
        _check_schema(conn)
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM components GROUP BY category ORDER BY n DESC"
        ).fetchall()
    return {r["category"]: r["n"] for r in rows}


def resolve_workspace_by_name(name: str, *, db_path: Path | str | None = None) -> Optional[WorkspaceEntry]:
    with open_index(db_path) as conn:
        _check_schema(conn)
        row = conn.execute(
            "SELECT * FROM workspaces WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    if not row:
        return None
    return WorkspaceEntry(guid=row["guid"], name=row["name"], file_path=Path(row["file_path"]))


def resolve_workspace_by_guid(guid: str, *, db_path: Path | str | None = None) -> Optional[WorkspaceEntry]:
    with open_index(db_path) as conn:
        _check_schema(conn)
        row = conn.execute(
            "SELECT * FROM workspaces WHERE guid = ? LIMIT 1", (guid,)
        ).fetchone()
    if not row:
        return None
    return WorkspaceEntry(guid=row["guid"], name=row["name"], file_path=Path(row["file_path"]))


def resolve_liquid_class_by_name(
    name: str, *, db_path: Path | str | None = None
) -> Optional[LiquidClassEntry]:
    """Return the liquid_classes row matching ``name`` exactly, or None."""
    with open_index(db_path) as conn:
        _check_schema(conn)
        row = conn.execute(
            "SELECT * FROM liquid_classes WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    if not row:
        return None
    supported_heads = _json_tuple(row["supported_heads"]) if "supported_heads" in row.keys() else ()
    if not supported_heads:
        try:
            from .xlqc import load_xlqc

            supported_heads = load_xlqc(Path(row["file_path"])).supported_heads
        except Exception:
            supported_heads = ()
    return LiquidClassEntry(
        guid=row["guid"],
        name=row["name"],
        head=row["head"],
        file_path=Path(row["file_path"]),
        supported_heads=supported_heads,
    )


def liquid_class_supports_section(
    name: str, section: str, *, db_path: Path | str | None = None
) -> Optional[bool]:
    """Whether liquid class ``name`` carries the micro-script ``section``.

    Returns ``True``/``False`` when the class is found in the index, or
    ``None`` when it can't be resolved (unknown class / no index / parse
    error) so callers can treat "unknown" as "don't block".
    """
    entry = resolve_liquid_class_by_name(name, db_path=db_path)
    if entry is None:
        return None
    try:
        from .xlqc import section_names

        names = {s.lower() for s in section_names(entry.file_path)}
    except Exception:
        return None
    return section.lower() in names


def install_info(*, db_path: Path | str | None = None) -> Optional[dict[str, str]]:
    """Return the install path / fingerprint / built_at row, or None."""
    with open_index(db_path) as conn:
        row = conn.execute("SELECT * FROM install LIMIT 1").fetchone()
    if not row:
        return None
    return dict(row)


# ── Compatibility queries ──────────────────────────────────────────


def find_sites_for(name: str, *, db_path: Path | str | None = None) -> list[CompatibleSite]:
    """Sites whose footprint matches the labware's footprint.

    Returns one row per (workspace, carrier-site) where the carrier site
    accepts the named labware's footprint. Walks `workspace_components`
    (carriers actually placed in a workspace) joined to `component_sites`
    (their accepted footprints).
    """
    with open_index(db_path) as conn:
        _check_schema(conn)
        target = conn.execute(
            "SELECT footprint FROM components WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        if not target or not target["footprint"]:
            return []
        footprint = target["footprint"]
        # GROUP BY collapses multi-instance carriers (e.g. "384 Well" placed
        # at four deck positions in one workspace) into one row per
        # (workspace, carrier, site_index). MIN() picks a stable
        # representative for the per-deck-position fields we still want
        # to surface (base_location). grip_modes is site-intrinsic so it's
        # consistent across placements.
        rows = conn.execute(
            """
            SELECT
                w.guid    AS ws_guid,
                w.name    AS ws_name,
                c.guid    AS comp_guid,
                c.name    AS comp_name,
                cs.site_index AS site_index,
                cs.footprint  AS site_footprint,
                cs.grip_modes AS grip_modes,
                MIN(wc.base_location) AS base_location
            FROM component_sites cs
            JOIN components c          ON c.guid = cs.component_guid
            JOIN workspace_components wc ON wc.component_name = c.name
            JOIN workspaces w          ON w.guid = wc.workspace_guid
            WHERE cs.footprint = ?
            GROUP BY w.guid, c.guid, cs.site_index
            ORDER BY w.name, c.name, cs.site_index
            """,
            (footprint,),
        ).fetchall()
    return [
        CompatibleSite(
            workspace_guid=r["ws_guid"],
            workspace_name=r["ws_name"],
            component_guid=r["comp_guid"],
            component_name=r["comp_name"],
            site_index=int(r["site_index"]),
            footprint=r["site_footprint"],
            grip_modes=_json_tuple(r["grip_modes"]),
            base_location=r["base_location"],
        )
        for r in rows
    ]


def find_labware_for_site(
    carrier_name: str,
    site_index: int,
    *,
    db_path: Path | str | None = None,
) -> list[CatalogEntry]:
    """Labware the catalog accepts on a specific carrier site.

    Match is by footprint string equality on `components.footprint`.
    """
    with open_index(db_path) as conn:
        _check_schema(conn)
        site_row = conn.execute(
            """
            SELECT cs.footprint
            FROM component_sites cs
            JOIN components c ON c.guid = cs.component_guid
            WHERE c.name = ? AND cs.site_index = ?
            LIMIT 1
            """,
            (carrier_name, site_index),
        ).fetchone()
        if not site_row or not site_row["footprint"]:
            return []
        rows = conn.execute(
            "SELECT * FROM components WHERE footprint = ? AND is_lid = 0 ORDER BY name",
            (site_row["footprint"],),
        ).fetchall()
    return [_entry_from_row(r) for r in rows]


def find_grip_modes(
    name: str,
    site_index: int | None = None,
    *,
    db_path: Path | str | None = None,
) -> list[str]:
    """CGA grip-mode names allowed for this component (optionally per site)."""
    with open_index(db_path) as conn:
        _check_schema(conn)
        comp = conn.execute(
            "SELECT guid FROM components WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        if not comp:
            return []
        if site_index is None:
            rows = conn.execute(
                "SELECT grip_modes FROM component_sites WHERE component_guid = ?",
                (comp["guid"],),
            ).fetchall()
            seen: list[str] = []
            for r in rows:
                for mode in _json_tuple(r["grip_modes"]):
                    if mode not in seen:
                        seen.append(mode)
            return seen
        row = conn.execute(
            "SELECT grip_modes FROM component_sites WHERE component_guid = ? AND site_index = ?",
            (comp["guid"], site_index),
        ).fetchone()
        if not row:
            return []
        return list(_json_tuple(row["grip_modes"]))


def find_legal_stacks(
    name: str,
    *,
    db_path: Path | str | None = None,
) -> dict[str, list[CatalogEntry]]:
    """Legal stack candidates above and below this component.

    Derived (not stored) — uses footprint equality + dim_x/y proximity +
    the lid/category heuristics. Cheap because the table is ~600 rows
    and joins are on indexed columns.
    """
    above: list[CatalogEntry] = []
    below: list[CatalogEntry] = []
    with open_index(db_path) as conn:
        _check_schema(conn)
        target = conn.execute(
            "SELECT * FROM components WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        if not target or not target["footprint"]:
            return {"above": [], "below": []}
        footprint = target["footprint"]
        target_is_lid = bool(target["is_lid"])
        target_category = target["category"]
        # If the target is itself a lid/adapter/magnet_rack, the question
        # of "what stacks above/below" is less interesting — return empty
        # so we don't recommend nonsense like "a lid above this lid".
        if target_is_lid:
            return {"above": [], "below": []}

        # ABOVE: lids matching the footprint.
        above_rows = conn.execute(
            "SELECT * FROM components WHERE footprint = ? AND is_lid = 1 ORDER BY name",
            (footprint,),
        ).fetchall()
        above = [_entry_from_row(r) for r in above_rows]

        # BELOW: adapters/magnet_racks matching the footprint, excluding the
        # target itself.
        below_rows = conn.execute(
            """
            SELECT * FROM components
            WHERE footprint = ?
              AND category IN ('adapter', 'magnet_rack')
              AND name <> ?
            ORDER BY category, name
            """,
            (footprint, name),
        ).fetchall()
        below = [_entry_from_row(r) for r in below_rows]

    return {"above": above, "below": below}


def find_workspaces_using(
    name: str,
    *,
    db_path: Path | str | None = None,
) -> list[WorkspaceEntry]:
    """Workspaces whose `.xwsp` lists this component as an occupant."""
    with open_index(db_path) as conn:
        _check_schema(conn)
        rows = conn.execute(
            """
            SELECT DISTINCT w.guid AS guid, w.name AS name, w.file_path AS file_path
            FROM workspace_components wc
            JOIN workspaces w ON w.guid = wc.workspace_guid
            WHERE wc.component_name = ?
            ORDER BY w.name
            """,
            (name,),
        ).fetchall()
    return [
        WorkspaceEntry(guid=r["guid"], name=r["name"], file_path=Path(r["file_path"]))
        for r in rows
    ]


def liquid_classes_for_head(
    head: str,
    *,
    db_path: Path | str | None = None,
) -> list[LiquidClassEntry]:
    """Liquid classes that support the given head (e.g. 'Mca', 'Fca', 'LiHa')."""
    with open_index(db_path) as conn:
        _check_schema(conn)
        rows = conn.execute(
            """
            SELECT lc.*
            FROM liquid_class_heads lch
            JOIN liquid_classes lc ON lc.guid = lch.liquid_class_guid
            WHERE lch.head = ?
            ORDER BY lc.name
            """,
            (head,),
        ).fetchall()
    out: list[LiquidClassEntry] = []
    for row in rows:
        supported = _json_tuple(row["supported_heads"]) if "supported_heads" in row.keys() else ()
        out.append(
            LiquidClassEntry(
                guid=row["guid"],
                name=row["name"],
                head=row["head"],
                file_path=Path(row["file_path"]),
                supported_heads=supported,
            )
        )
    return out


# ── Helpers ────────────────────────────────────────────────────────


def _entry_from_row(row: sqlite3.Row) -> CatalogEntry:
    keys = row.keys()
    return CatalogEntry(
        guid=row["guid"],
        name=row["name"],
        category=row["category"],
        file_path=Path(row["file_path"]),
        grid_x=row["grid_x"],
        grid_y=row["grid_y"],
        dim_x_mm=row["dim_x_mm"],
        dim_y_mm=row["dim_y_mm"],
        dim_z_mm=row["dim_z_mm"],
        site_count=row["site_count"],
        functional_group=row["functional_group"] if "functional_group" in keys else None,
        component_kind=row["component_kind"] if "component_kind" in keys and row["component_kind"] else "unknown",
        component_subtype=row["component_subtype"] if "component_subtype" in keys else None,
        footprint=row["footprint"] if "footprint" in keys else None,
        is_lid=bool(row["is_lid"]) if "is_lid" in keys and row["is_lid"] is not None else False,
        renderer=row["renderer"] if "renderer" in keys else None,
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns missing on legacy installs.

    Only ALTERs tables that already exist; on a fresh DB this is a
    no-op and SCHEMA_SQL creates everything afterward with the current
    column set.
    """
    if _table_exists(conn, "components"):
        component_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(components)").fetchall()
        }
        if "functional_group" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN functional_group TEXT")
        if "component_kind" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN component_kind TEXT NOT NULL DEFAULT 'unknown'")
        if "component_subtype" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN component_subtype TEXT")
        if "footprint" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN footprint TEXT")
        if "is_lid" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN is_lid INTEGER NOT NULL DEFAULT 0")
        if "renderer" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN renderer TEXT")
        if "allowed_grip_modes" not in component_columns:
            conn.execute("ALTER TABLE components ADD COLUMN allowed_grip_modes TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS components_by_kind ON components(component_kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS components_by_subtype ON components(component_subtype)")

    if _table_exists(conn, "liquid_classes"):
        liquid_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(liquid_classes)").fetchall()
        }
        if "supported_heads" not in liquid_columns:
            conn.execute("ALTER TABLE liquid_classes ADD COLUMN supported_heads TEXT")

    if _table_exists(conn, "install"):
        install_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(install)").fetchall()
        }
        if "schema_version" not in install_columns:
            conn.execute("ALTER TABLE install ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 0")


def _json_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if item)
