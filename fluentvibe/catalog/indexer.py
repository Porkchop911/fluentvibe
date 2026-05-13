"""Catalog indexer — scans a FluentControl install and writes the SQL index.

One public entry point: `build_index(install_path, db_path)`. Walks the
`SystemSpecific/Worktable/Components/`, `Workspaces/`, and `Sites/`
sub-directories of the install, parses each XCMP/XWSP/XSIT, infers the
component category, and inserts rows into `install_index.db`.

Cheap to run end-to-end (~5–15 s for a 629-component install). Idempotent —
the index is dropped and rebuilt each run.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .catalog import DEFAULT_INDEX_PATH, open_index
from .inference import component_taxonomy, infer_category
from .xcmp import load_xcmp, load_xwsp


DEFAULT_INSTALL_PATH = Path(r"C:\ProgramData\Tecan\VisionX\Database")


def install_path_default() -> Path:
    """Resolve the default FluentControl install path.

    Priority: `FLUENTVIBE_FC_INSTALL` env var > hard-coded default.
    """
    env = os.environ.get("FLUENTVIBE_FC_INSTALL")
    return Path(env) if env else DEFAULT_INSTALL_PATH


def build_index(
    install_path: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
) -> dict[str, int]:
    """Walk the install, parse, infer, write rows. Returns row counts.

    Counts dict keys: `components`, `workspaces`, `sites`, plus per-category.
    """
    install = Path(install_path) if install_path else install_path_default()
    db = Path(db_path) if db_path else DEFAULT_INDEX_PATH

    components_dir = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces_dir = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites_dir = install / "SystemSpecific" / "Worktable" / "Sites"
    liquid_classes_dir = install / "SystemSpecific" / "LiquidClasses"

    if not components_dir.exists():
        raise FileNotFoundError(
            f"Components directory not found at {components_dir!s}. "
            f"Set FLUENTVIBE_FC_INSTALL or pass install_path explicitly."
        )

    counts: dict[str, int] = {
        "components": 0, "workspaces": 0, "sites": 0, "liquid_classes": 0,
    }
    per_category: dict[str, int] = {}

    fingerprint = _install_fingerprint(install)

    with open_index(db) as conn:
        # Drop and rebuild — idempotent, simpler than upserts.
        conn.executescript(
            "DELETE FROM components; DELETE FROM workspaces; DELETE FROM sites; "
            "DELETE FROM liquid_classes; DELETE FROM install;"
        )

        # ── Components ────────────────────────────────────────────
        component_rows: list[tuple] = []
        for path in components_dir.glob("*.xcmp"):
            try:
                comp = load_xcmp(path)
            except Exception:
                continue
            category = infer_category(comp)
            per_category[category] = per_category.get(category, 0) + 1

            grid_x, grid_y = _component_grid(comp)
            dim = comp.dim_mm or (None, None, None)
            site_count = comp.arrangement.site_count if comp.arrangement else None
            functional_group, component_kind, component_subtype = component_taxonomy(comp.functional_group)

            component_rows.append((
                comp.guid,
                comp.name,
                category,
                str(comp.file_path),
                grid_x, grid_y,
                dim[0], dim[1], dim[2],
                site_count,
                functional_group,
                component_kind,
                component_subtype,
            ))

        if component_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO components
                   (guid, name, category, file_path, grid_x, grid_y,
                    dim_x_mm, dim_y_mm, dim_z_mm, site_count,
                    functional_group, component_kind, component_subtype)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                component_rows,
            )
            counts["components"] = len(component_rows)

        # ── Workspaces ────────────────────────────────────────────
        if workspaces_dir.exists():
            workspace_rows: list[tuple] = []
            for path in sorted(workspaces_dir.glob("*.xwsp")):
                try:
                    ws = load_xwsp(path)
                except Exception:
                    continue
                workspace_rows.append((ws.guid, ws.name, str(ws.file_path)))
            if workspace_rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO workspaces (guid, name, file_path) VALUES (?, ?, ?)",
                    workspace_rows,
                )
                workspace_count = conn.execute(
                    "SELECT COUNT(*) AS n FROM workspaces"
                ).fetchone()["n"]
                if workspace_count != len(workspace_rows):
                    raise RuntimeError(
                        "Workspace index build collapsed parsed `.xwsp` files into fewer rows. "
                        "Check workspace GUID extraction for collisions."
                    )
                counts["workspaces"] = workspace_count

        # ── Sites (lightweight: just guid + path) ─────────────────
        if sites_dir.exists():
            site_rows: list[tuple] = []
            for path in sites_dir.glob("*.xsit"):
                site_rows.append((path.stem, str(path)))
            if site_rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO sites (guid, file_path) VALUES (?, ?)",
                    site_rows,
                )
                counts["sites"] = len(site_rows)

        # ── Liquid classes ───────────────────────────────────────
        if liquid_classes_dir.exists():
            from .xlqc import load_xlqc

            liquid_class_rows: list[tuple] = []
            for path in liquid_classes_dir.glob("*.xlqc"):
                try:
                    lc = load_xlqc(path)
                except Exception:
                    continue
                liquid_class_rows.append((
                    lc.guid,
                    lc.name,
                    lc.head,
                    json.dumps(list(lc.supported_heads)),
                    str(lc.file_path),
                ))
            if liquid_class_rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO liquid_classes "
                    "(guid, name, head, supported_heads, file_path) VALUES (?, ?, ?, ?, ?)",
                    liquid_class_rows,
                )
                counts["liquid_classes"] = len(liquid_class_rows)

        # ── Install fingerprint ──────────────────────────────────
        conn.execute(
            "INSERT INTO install (install_path, fingerprint, built_at) VALUES (?, ?, ?)",
            (str(install), fingerprint, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        conn.commit()

    counts.update(per_category)
    return counts


def _component_grid(comp) -> tuple[Optional[int], Optional[int]]:
    """Pick the most useful (rows, cols) grid for a component.

    For pipettable labware (plates, tip boxes), use the well grid.
    For carriers without a well grid, use the arrangement site grid.
    """
    if comp.pipettable is not None:
        return comp.pipettable.x_wells, comp.pipettable.y_wells
    if comp.arrangement is not None:
        return comp.arrangement.sites_in_x, comp.arrangement.sites_in_y
    return None, None


def _install_fingerprint(install: Path) -> str:
    """SHA-256 of sorted (relative_path, mtime) tuples under the install root.

    Cheap proxy for "has the install changed since we indexed it."
    """
    h = hashlib.sha256()
    for sub in ("Components", "Workspaces", "Sites"):
        d = install / "SystemSpecific" / "Worktable" / sub
        if not d.exists():
            continue
        entries = sorted(
            (str(p.relative_to(install)), int(p.stat().st_mtime))
            for p in d.iterdir()
            if p.is_file()
        )
        for rel, mtime in entries:
            h.update(rel.encode("utf-8"))
            h.update(str(mtime).encode("ascii"))
    return h.hexdigest()


def fingerprint_matches(install_path: Optional[Path | str] = None,
                        db_path: Optional[Path | str] = None) -> bool:
    """True iff the on-disk install fingerprint matches the indexed one."""
    from .catalog import install_info  # local import to avoid cycles
    install = Path(install_path) if install_path else install_path_default()
    info = install_info(db_path=db_path)
    if not info:
        return False
    if info["install_path"] != str(install):
        return False
    try:
        return info["fingerprint"] == _install_fingerprint(install)
    except FileNotFoundError:
        return False
