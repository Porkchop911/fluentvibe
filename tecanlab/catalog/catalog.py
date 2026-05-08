"""SQL queries over the catalog index.

The index lives at `tecanlab/catalog/install_index.db` (inside the package).
It's built by `indexer.build_index()` and queried by everything else in
tecanlab that needs to resolve a catalog name to a file path.
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
    install_path  TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    built_at      TEXT NOT NULL
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
    component_subtype TEXT
);

CREATE INDEX IF NOT EXISTS components_by_name     ON components(name);
CREATE INDEX IF NOT EXISTS components_by_category ON components(category);

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
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
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


# ── Queries ────────────────────────────────────────────────────────


def resolve_by_name(name: str, *, db_path: Path | str | None = None) -> Optional[CatalogEntry]:
    """Return the unique component with this exact name, or None."""
    with open_index(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM components WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    return _entry_from_row(row) if row else None


def find_components(pattern: str, *, db_path: Path | str | None = None) -> list[CatalogEntry]:
    """Substring search on component name. Case-insensitive."""
    like = f"%{pattern}%"
    with open_index(db_path) as conn:
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
        rows = conn.execute(sql, params).fetchall()
    return [_entry_from_row(r) for r in rows]


def list_by_category(category: str, *, db_path: Path | str | None = None) -> list[CatalogEntry]:
    with open_index(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM components WHERE category = ? ORDER BY name", (category,)
        ).fetchall()
    return [_entry_from_row(r) for r in rows]


def category_counts(*, db_path: Path | str | None = None) -> dict[str, int]:
    with open_index(db_path) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM components GROUP BY category ORDER BY n DESC"
        ).fetchall()
    return {r["category"]: r["n"] for r in rows}


def resolve_workspace_by_name(name: str, *, db_path: Path | str | None = None) -> Optional[WorkspaceEntry]:
    with open_index(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM workspaces WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    if not row:
        return None
    return WorkspaceEntry(guid=row["guid"], name=row["name"], file_path=Path(row["file_path"]))


def resolve_workspace_by_guid(guid: str, *, db_path: Path | str | None = None) -> Optional[WorkspaceEntry]:
    with open_index(db_path) as conn:
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


def install_info(*, db_path: Path | str | None = None) -> Optional[dict[str, str]]:
    """Return the install path / fingerprint / built_at row, or None."""
    with open_index(db_path) as conn:
        row = conn.execute("SELECT * FROM install LIMIT 1").fetchone()
    if not row:
        return None
    return dict(row)


# ── Helpers ────────────────────────────────────────────────────────


def _entry_from_row(row: sqlite3.Row) -> CatalogEntry:
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
        functional_group=row["functional_group"] if "functional_group" in row.keys() else None,
        component_kind=row["component_kind"] if "component_kind" in row.keys() and row["component_kind"] else "unknown",
        component_subtype=row["component_subtype"] if "component_subtype" in row.keys() else None,
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    component_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(components)").fetchall()
    }
    if "functional_group" not in component_columns:
        conn.execute("ALTER TABLE components ADD COLUMN functional_group TEXT")
    if "component_kind" not in component_columns:
        conn.execute("ALTER TABLE components ADD COLUMN component_kind TEXT NOT NULL DEFAULT 'unknown'")
    if "component_subtype" not in component_columns:
        conn.execute("ALTER TABLE components ADD COLUMN component_subtype TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS components_by_kind ON components(component_kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS components_by_subtype ON components(component_subtype)")

    liquid_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(liquid_classes)").fetchall()
    }
    if "supported_heads" not in liquid_columns:
        conn.execute("ALTER TABLE liquid_classes ADD COLUMN supported_heads TEXT")


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
