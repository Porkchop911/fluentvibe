from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path


def _import_core():
    try:
        import fluentcontrol_core  # type: ignore

        return fluentcontrol_core
    except ImportError:
        root = Path(__file__).resolve()
        candidates = [
            root.parents[2] / "fluentcontrol_core",
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            value = str(candidate)
            if value not in sys.path:
                sys.path.insert(0, value)
            try:
                import fluentcontrol_core  # type: ignore

                return fluentcontrol_core
            except ImportError:
                continue
    return None


@lru_cache(maxsize=1)
def shared_core():
    return _import_core()


def default_install_bundle():
    core = shared_core()
    if core is None:
        return None
    return core.default_install_bundle()


def rewrite_checksum_in_place(path) -> bool:
    core = shared_core()
    if core is None:
        return False
    try:
        payload = core.rewrite_checksum(path, in_place=True)
    except Exception:
        return False
    return bool(payload.get("is_valid"))


def normalize_finding_payload(finding):
    core = shared_core()
    if core is None:
        return dict(finding) if isinstance(finding, dict) else None
    try:
        return core.normalize_finding(finding)
    except Exception:
        return dict(finding) if isinstance(finding, dict) else None


def ingest_default_install_bundle(db=None):
    core = shared_core()
    if core is None:
        return None
    bundle = core.default_install_bundle()
    if bundle is None:
        return None
    if db is None:
        from .database import get_database

        db = get_database()
    return db.ingest_install_bundle(bundle)
