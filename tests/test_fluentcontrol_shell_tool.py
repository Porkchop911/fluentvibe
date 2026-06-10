from __future__ import annotations

from pathlib import Path

import pytest

from fluentvibe.authoring.fluentcontrol_shell import (
    DEFAULT_SHELL_XSCR,
    classify_dialog_text,
    extract_comment_to_payload_region,
    precheck_xscr_text,
    replace_comment_to_payload_region,
)
from fluentvibe.authoring.tools import AuthoringToolRegistry
from tests.test_prompt_authoring import _valid_draft


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
