"""Phase C.1 — fingerprint-based auto-rebuild.

When the catalog index's stored fingerprint no longer matches the
on-disk FC install, ``ensure_index()`` must rebuild silently —
unless ``FLUENTVIBE_NO_AUTO_REBUILD`` is set.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.catalog import (  # noqa: E402
    DEFAULT_INDEX_PATH,
    ensure_index,
    fingerprint_matches,
    index_exists,
    install_path_default,
)


def _read_install_row(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT install_path, fingerprint, built_at FROM install LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _set_fingerprint(db_path: Path, fingerprint: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE install SET fingerprint = ?", (fingerprint,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def isolated_index(tmp_path: Path) -> Path:
    """Copy the install index so fingerprint tests never mutate shared state."""
    if not index_exists():
        pytest.skip("catalog index empty")
    target = tmp_path / "install_index.db"
    shutil.copy2(DEFAULT_INDEX_PATH, target)
    return target


def test_fingerprint_mismatch_triggers_rebuild(
    isolated_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing a stale fingerprint causes ensure_index() to rebuild."""
    before = _read_install_row(isolated_index)
    assert before, "expected an install row in the index"
    real_fingerprint = before["fingerprint"]
    real_built_at = before["built_at"]

    # Poison the stored fingerprint so it no longer matches the on-disk install.
    _set_fingerprint(isolated_index, "stale-fingerprint-test-marker")
    assert not fingerprint_matches(install_path_default(), db_path=isolated_index)

    # ensure_index() should detect the drift and rebuild.
    monkeypatch.delenv("FLUENTVIBE_NO_AUTO_REBUILD", raising=False)
    ensure_index(db_path=isolated_index)

    after = _read_install_row(isolated_index)
    assert after["fingerprint"] == real_fingerprint, (
        "fingerprint should be restored to the on-disk install's hash"
    )
    assert after["built_at"] != real_built_at or after["fingerprint"] != "stale-fingerprint-test-marker"
    assert fingerprint_matches(install_path_default(), db_path=isolated_index)


def test_no_auto_rebuild_env_var_keeps_stale_index(
    isolated_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With FLUENTVIBE_NO_AUTO_REBUILD=1, drift is warned but not rebuilt."""
    before = _read_install_row(isolated_index)
    assert before
    # Snapshot real fingerprint, then poison and set the env var.
    _set_fingerprint(isolated_index, "another-stale-marker")

    monkeypatch.setenv("FLUENTVIBE_NO_AUTO_REBUILD", "1")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ensure_index(db_path=isolated_index)
    warning_msgs = [str(w.message) for w in caught]
    assert any("fingerprint" in m.lower() for m in warning_msgs), (
        f"expected a fingerprint-drift warning; got {warning_msgs!r}"
    )
    # Index was NOT rebuilt — fingerprint is still the marker.
    intermediate = _read_install_row(isolated_index)
    assert intermediate["fingerprint"] == "another-stale-marker"
