from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_prompt_authoring import _valid_draft
from fluentvibe.authoring.fluentcontrol_shell import (
    DEFAULT_SHELL_XSCR,
    classify_dialog_text,
    extract_comment_to_payload_region,
    precheck_xscr_text,
    replace_comment_to_payload_region,
)
from fluentvibe.authoring.tools import AuthoringToolRegistry
from fluentvibe.authoring import fluentcontrol_shell as shell_tools
from fluentvibe import deployer


def test_shell_region_replace_preserves_backslashes() -> None:
    generated = r"<Root><Comment>C:\temp\generated</Comment><Payload><Objects /></Payload></Root>"
    shell = r"<Root><Comment>old</Comment><Payload><Objects /></Payload></Root>"

    region = extract_comment_to_payload_region(generated)
    patched = replace_comment_to_payload_region(shell, region)

    assert r"C:\temp\generated" in patched
    assert "<Comment>old</Comment>" not in patched


def test_shell_precheck_detects_malformed_xscr() -> None:
    xml = '<?xml version="1.0" encoding="utf-8"?><Root><Statements><Objects></Objects></Root>'

    issues = precheck_xscr_text(xml)

    assert any("XML tag mismatch" in issue for issue in issues)
    assert any("XML parse error" in issue for issue in issues)


def test_shell_precheck_accepts_balanced_xml() -> None:
    xml = '<?xml version="1.0" encoding="utf-8"?><Root><Statements><Objects></Objects></Statements></Root>'

    assert precheck_xscr_text(xml) == []


def test_shell_dialog_classifier() -> None:
    checksum, load_failure = classify_dialog_text(
        "The Script 'shell' has an invalid checksum. Do you want the system to fix the checksum?"
    )
    assert checksum is True
    assert load_failure is False

    checksum, load_failure = classify_dialog_text(
        "The load operation for Script 'shell' failed with Exception: end tag mismatch"
    )
    assert checksum is False
    assert load_failure is True


def _patch_files(tmp_path):
    generated = tmp_path / "generated.xscr"
    shell = tmp_path / "shell.xscr"
    generated.write_text('<Root><Payload><ObjectName>new</ObjectName><Comment>new</Comment><Objects /></Payload><Checksum>new</Checksum></Root>', encoding="utf-8")
    shell.write_bytes(b'\xef\xbb\xbf<Root><Payload><ObjectName>shell</ObjectName><Comment>old</Comment><Objects /></Payload><Checksum>old</Checksum></Root>\r\n')
    return generated, shell


def test_patch_preserves_identity_and_backs_up_exact_original(tmp_path, monkeypatch):
    generated, shell = _patch_files(tmp_path)
    original = shell.read_bytes()
    monkeypatch.setenv("TECAN_SHELL_BACKUP_DIR", str(tmp_path / "backups"))
    def checksum(path):
        assert path != shell
        assert '<ObjectName>shell</ObjectName>' in path.read_text(encoding="utf-8")
        return {"is_valid": True}
    monkeypatch.setattr(deployer, "_checksum_rewrite_and_verify", checksum)
    backup = shell_tools.patch_shell_xscr_from_generated(generated, shell_xscr=shell)
    assert backup.read_bytes() == original
    assert '<Comment>new</Comment>' in shell.read_text(encoding="utf-8")


def test_checksum_failure_does_not_overwrite_shell(tmp_path, monkeypatch):
    generated, shell = _patch_files(tmp_path)
    original = shell.read_bytes()
    monkeypatch.setattr(deployer, "_checksum_rewrite_and_verify", lambda path: {"is_valid": False})
    with pytest.raises(shell_tools.FluentControlShellError, match="checksum verification"):
        shell_tools.patch_shell_xscr_from_generated(generated, shell_xscr=shell, backup=False)
    assert shell.read_bytes() == original


def test_malformed_patch_does_not_overwrite_shell(tmp_path):
    generated, shell = _patch_files(tmp_path)
    original = shell.read_bytes()
    generated.write_text('<Comment>bad</Comment><Payload><Objects></Payload>', encoding="utf-8")
    with pytest.raises(shell_tools.FluentControlShellError, match="Invalid patched shell"):
        shell_tools.patch_shell_xscr_from_generated(generated, shell_xscr=shell, backup=False)
    assert shell.read_bytes() == original


def test_validation_closes_before_patch_and_restores_bytes(tmp_path, monkeypatch):
    generated, shell = _patch_files(tmp_path)
    original = shell.read_bytes()
    events = []
    monkeypatch.setattr(shell_tools, "_connect_fluent_window", lambda pid: object())
    def close(window, **kwargs):
        events.append("close")
        if len(events) == 1:
            assert shell.read_bytes() == original
    monkeypatch.setattr(shell_tools, "_close_shell_tab_if_open", close)
    monkeypatch.setattr(deployer, "_checksum_rewrite_and_verify", lambda path: {"is_valid": True})
    def opened(**kwargs):
        events.append("open")
        assert kwargs["close_before_open"] is False
        assert kwargs["script_name"] == "shell"
        assert '<Comment>new</Comment>' in shell.read_text(encoding="utf-8")
        return shell_tools.UiResult(True, [], [])
    monkeypatch.setattr(shell_tools, "open_shell_and_read_infopad", opened)
    result = shell_tools.validate_generated_xscr_via_shell(generated, shell_xscr=shell, restore_shell=True)
    assert result.ok
    assert events == ["close", "open", "close"]
    assert shell.read_bytes() == original


def test_combined_checksum_and_load_failure_is_not_accepted():
    checksum, failed = classify_dialog_text("Invalid checksum. The load operation failed with exception.")
    assert checksum and failed


def test_shell_not_opened_is_reported_as_failure(monkeypatch):
    class Window:
        def exists(self, **kwargs):
            return True
    monkeypatch.setattr(shell_tools, "_connect_fluent_window", lambda pid: Window())
    monkeypatch.setattr(shell_tools, "_bring_to_foreground", lambda win: None)
    monkeypatch.setattr(shell_tools, "_pick_leftmost_shell_element", lambda win, name: object())
    monkeypatch.setattr(shell_tools, "_safe_double_click", lambda el: None)
    monkeypatch.setattr(shell_tools, "_dismiss_modal_dialogs", lambda *a, **k: shell_tools.DialogScanResult([], False, []))
    monkeypatch.setattr(shell_tools, "_wait_for_script_tab", lambda *a: False)
    result = shell_tools.open_shell_and_read_infopad(close_before_open=False, settle_seconds=0)
    assert not result.ok
    assert not result.opened
    assert "did not open" in result.load_error_text


def test_validate_fluentcontrol_shell_requires_input() -> None:
    tools = AuthoringToolRegistry(output_dir=Path("build") / "test_fluentcontrol_shell_tool" / "missing_input")

    result = tools.validate_fluentcontrol_shell()

    assert result["ok"] is False
    assert result["category"] == "fluentcontrol_shell_failure"
    assert "requires either source or xscr_path" in result["message"]


def test_validate_fluentcontrol_shell_missing_shell_is_structured(tmp_path: Path) -> None:
    xscr = tmp_path / "generated.xscr"
    shell = tmp_path / "missing_shell.xscr"
    xscr.write_text(
        '<?xml version="1.0" encoding="utf-8"?><Root><Comment>ok</Comment><Payload><Statements><Objects></Objects></Statements></Payload></Root>',
        encoding="utf-8",
    )
    tools = AuthoringToolRegistry(output_dir=tmp_path / "out")

    result = tools.validate_fluentcontrol_shell(xscr_path=str(xscr), shell_xscr=str(shell))

    assert result["ok"] is False
    assert result["opened"] is False
    assert result["load_failed"] is True
    assert result["error_count"] == 1
    assert "Shell XSCR not found" in result["load_error_text"]


def _fluentcontrol_shell_available() -> bool:
    if not DEFAULT_SHELL_XSCR.exists():
        return False
    try:
        import pywinauto  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.fluentcontrol_shell
def test_live_fluentcontrol_shell_accepts_simple_transfer(tmp_path: Path) -> None:
    assert _fluentcontrol_shell_available(), (
        f"FluentControl shell prerequisites are not reachable; expected shell at {DEFAULT_SHELL_XSCR}"
    )

    tools = AuthoringToolRegistry(output_dir=tmp_path / "out")

    result = tools.validate_fluentcontrol_shell(source=_valid_draft())

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["load_failed"] is False
    assert result["error_count"] == 0
