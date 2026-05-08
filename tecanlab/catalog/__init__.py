"""FluentControl catalog: SQLite-backed labware/location/liquid-class lookup.

The v1.1 catalog index (`install_index.db`) is built by walking the FC
install directory and parsing each XCMP/XWSP. Lookup helpers in `catalog.py`
resolve catalog names to file paths; `xcmp.py` parses the XML on demand.

The legacy `database.py` (vendored `tecan.db`) is kept for backward
compatibility but is not used by v1.1's catalog-driven labware construction.
"""

from .catalog import (
    CatalogEntry, WorkspaceEntry, LiquidClassEntry,
    DEFAULT_INDEX_PATH,
    open_index, index_exists,
    resolve_by_name, find_components, find_components_by_metadata, list_by_category,
    category_counts, resolve_workspace_by_name, resolve_liquid_class_by_name,
    install_info,
)
from .database import TecanDatabase, get_database
from .dsl_recipes import (
    FakeRecipeEmbedder,
    HashingRecipeEmbedder,
    retrieve_dsl_recipes,
    seed_curated_dsl_recipes,
)
from .fc_install import default_install_bundle, rewrite_checksum_in_place
from .indexer import build_index, install_path_default, fingerprint_matches
from .inference import CATEGORIES, infer_category
from .xcmp import (
    XcmpComponent, XcmpArrangement, XcmpPipettable, XcmpCavity,
    XsitSite,
    XwspWorkspace, WorkspaceOccupant,
    load_xcmp, load_xsit, load_xwsp,
)
from .xlqc import XlqcLiquidClass, load_xlqc

__all__ = [
    # v1.1 catalog API
    "CatalogEntry", "WorkspaceEntry", "LiquidClassEntry",
    "DEFAULT_INDEX_PATH",
    "open_index", "index_exists",
    "resolve_by_name", "find_components", "find_components_by_metadata", "list_by_category",
    "category_counts", "resolve_workspace_by_name", "resolve_liquid_class_by_name",
    "install_info",
    "build_index", "install_path_default", "fingerprint_matches",
    "CATEGORIES", "infer_category",
    "XcmpComponent", "XcmpArrangement", "XcmpPipettable", "XcmpCavity",
    "XsitSite",
    "XwspWorkspace", "WorkspaceOccupant",
    "load_xcmp", "load_xsit", "load_xwsp",
    "XlqcLiquidClass", "load_xlqc",
    # Legacy
    "TecanDatabase", "get_database",
    "FakeRecipeEmbedder", "HashingRecipeEmbedder",
    "retrieve_dsl_recipes", "seed_curated_dsl_recipes",
    "default_install_bundle", "rewrite_checksum_in_place",
]


def ensure_index() -> None:
    """Build (or rebuild) the catalog index as needed.

    Called at the top of `tecanlab/__init__.py` on first import. Behaviour:

    1. If the index file is missing, build it from the default install.
    2. If the index exists but its fingerprint no longer matches the
       on-disk install (FC update / new components added), rebuild — unless
       the env var `TECANLAB_NO_AUTO_REBUILD` is set, in which case warn
       once and leave the stale index in place.
    3. If no FC install is reachable, return silently and leave whatever
       state is on disk; the offline fallback in labware classes handles
       the rest.

    Indexing must never break imports — exceptions are caught and dropped.
    """
    import os
    import warnings

    install = install_path_default()
    components_dir = install / "SystemSpecific" / "Worktable" / "Components"
    if not components_dir.exists():
        return  # offline-fallback territory; caller deals with empty index

    if not index_exists():
        try:
            build_index(install_path=install)
        except Exception:
            pass
        return

    # Index exists — check for drift.
    try:
        if fingerprint_matches(install):
            return
    except Exception:
        return

    if os.environ.get("TECANLAB_NO_AUTO_REBUILD"):
        warnings.warn(
            "Catalog index fingerprint does not match the on-disk install. "
            "Run `tecanlab catalog refresh` to rebuild "
            "(or unset TECANLAB_NO_AUTO_REBUILD).",
            stacklevel=2,
        )
        return

    try:
        build_index(install_path=install)
    except Exception:
        pass
