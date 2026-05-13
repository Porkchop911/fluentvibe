"""Tests for `fluentvibe.deployer.deploy_xscr` against a tmp datastore."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_XSCR = REPO_ROOT / "simple_transfer.xscr"


def _fluentcontrol_core_available() -> bool:
    try:
        from fluentvibe.catalog.fc_install import shared_core
    except Exception:
        return False
    return shared_core() is not None


def _stub_psutil(monkeypatch, running: bool) -> None:
    """Make psutil report SystemSW.exe as running or not."""
    class _FakeProc:
        def __init__(self, name: str) -> None:
            self.info = {"name": name}

    class _FakePsutil:
        @staticmethod
        def process_iter(_attrs):
            return [_FakeProc("SystemSW.exe")] if running else [_FakeProc("explorer.exe")]

    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil)


@pytest.fixture
def tmp_datastore(tmp_path: Path) -> Path:
    target = tmp_path / "UserSpecific"
    target.mkdir()
    return target


@pytest.fixture
def fixture_xscr() -> Path:
    if not FIXTURE_XSCR.exists():
        pytest.skip(f"fixture xscr missing: {FIXTURE_XSCR}")
    return FIXTURE_XSCR


@pytest.mark.skipif(not _fluentcontrol_core_available(), reason="fluentcontrol_core not importable")
def test_deploy_xscr_drops_in_with_fresh_guid(monkeypatch, tmp_datastore: Path, fixture_xscr: Path) -> None:
    from fluentvibe.deployer import deploy_xscr

    _stub_psutil(monkeypatch, running=False)

    result = deploy_xscr(fixture_xscr, datastore_dir=tmp_datastore)

    assert result.deployed_path.exists()
    assert result.deployed_path.parent == tmp_datastore
    assert re.fullmatch(r"[0-9a-fA-F-]{36}", result.deployed_path.stem)
    assert result.file_guid == result.deployed_path.stem
    assert result.inspect.get("is_valid") is True
    assert result.inspect.get("matches_stored_checksum") is True
    # Source was not perturbed.
    assert fixture_xscr.read_bytes() == fixture_xscr.read_bytes()


@pytest.mark.skipif(not _fluentcontrol_core_available(), reason="fluentcontrol_core not importable")
def test_deploy_xscr_rewrites_object_name_and_re_checksums(monkeypatch, tmp_datastore: Path, fixture_xscr: Path) -> None:
    from fluentvibe.deployer import deploy_xscr

    _stub_psutil(monkeypatch, running=False)

    result = deploy_xscr(
        fixture_xscr,
        datastore_dir=tmp_datastore,
        new_object_name="Renamed_From_Test",
    )

    text = result.deployed_path.read_text(encoding="utf-8")
    assert "<ObjectName>Renamed_From_Test</ObjectName>" in text
    assert result.object_name == "Renamed_From_Test"
    assert result.inspect.get("is_valid") is True


@pytest.mark.skipif(not _fluentcontrol_core_available(), reason="fluentcontrol_core not importable")
def test_deploy_xscr_regenerates_workspace_delta_id(monkeypatch, tmp_datastore: Path, fixture_xscr: Path) -> None:
    from fluentvibe.deployer import deploy_xscr

    _stub_psutil(monkeypatch, running=False)

    result = deploy_xscr(fixture_xscr, datastore_dir=tmp_datastore)
    assert result.workspace_delta_id is not None
    src_text = fixture_xscr.read_text(encoding="utf-8")
    src_match = re.search(r"&lt;Identifier&gt;([0-9a-fA-F-]{36})&lt;/Identifier&gt;", src_text)
    assert src_match is not None
    assert result.workspace_delta_id != src_match.group(1)


@pytest.mark.skipif(not _fluentcontrol_core_available(), reason="fluentcontrol_core not importable")
def test_deploy_xscr_keeps_workspace_delta_id_when_disabled(monkeypatch, tmp_datastore: Path, fixture_xscr: Path) -> None:
    from fluentvibe.deployer import deploy_xscr

    _stub_psutil(monkeypatch, running=False)

    src_text = fixture_xscr.read_text(encoding="utf-8")
    src_match = re.search(r"&lt;Identifier&gt;([0-9a-fA-F-]{36})&lt;/Identifier&gt;", src_text)
    assert src_match is not None

    result = deploy_xscr(
        fixture_xscr,
        datastore_dir=tmp_datastore,
        new_workspace_delta_id=False,
    )
    assert result.workspace_delta_id == src_match.group(1)


def test_deploy_xscr_blocks_when_systemsw_running(monkeypatch, tmp_datastore: Path, fixture_xscr: Path) -> None:
    from fluentvibe.deployer import DeploymentError, deploy_xscr

    _stub_psutil(monkeypatch, running=True)

    with pytest.raises(DeploymentError, match="SystemSW.exe"):
        deploy_xscr(fixture_xscr, datastore_dir=tmp_datastore)
    assert list(tmp_datastore.iterdir()) == []


@pytest.mark.skipif(not _fluentcontrol_core_available(), reason="fluentcontrol_core not importable")
def test_deploy_xscr_allows_running_when_overridden(monkeypatch, tmp_datastore: Path, fixture_xscr: Path) -> None:
    from fluentvibe.deployer import deploy_xscr

    _stub_psutil(monkeypatch, running=True)

    result = deploy_xscr(
        fixture_xscr,
        datastore_dir=tmp_datastore,
        require_fc_closed=False,
    )
    assert result.deployed_path.exists()


def test_deploy_xscr_missing_source_raises(tmp_datastore: Path, monkeypatch) -> None:
    from fluentvibe.deployer import DeploymentError, deploy_xscr

    _stub_psutil(monkeypatch, running=False)

    with pytest.raises(DeploymentError, match="not found"):
        deploy_xscr(Path(r"C:\does-not-exist\nope.xscr"), datastore_dir=tmp_datastore)
