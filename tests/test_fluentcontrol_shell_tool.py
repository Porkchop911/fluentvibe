from __future__ import annotations

from pathlib import Path

import pytest

from fluentvibe import deployer
from fluentvibe.authoring import fluentcontrol_shell as shell_tools
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


@pytest.mark.parametrize("by", ["human", "agent"])
def test_inspection_closes_before_patch_and_leaves_the_script_open(tmp_path, monkeypatch, by):
    generated, shell = _patch_files(tmp_path)
    original = shell.read_bytes()
    events = []
    monkeypatch.setattr(shell_tools, "_connect_fluent_window", lambda pid: object())
    def close(window, **kwargs):
        events.append("close")
        assert shell.read_bytes() == original
    monkeypatch.setattr(shell_tools, "_close_shell_tab_if_open", close)
    monkeypatch.setattr(deployer, "_checksum_rewrite_and_verify", lambda path: {"is_valid": True})
    def opened(**kwargs):
        events.append("open")
        assert kwargs["close_before_open"] is False and kwargs["close_after_read"] is False
        assert kwargs["script_name"] == "shell"
        assert '<Comment>new</Comment>' in shell.read_text(encoding="utf-8")
        return shell_tools.UiResult(True, [], [], timings={"open_s": 1.0})
    monkeypatch.setattr(shell_tools, "open_shell_and_read_infopad", opened)
    monkeypatch.setattr(shell_tools, "_foreground_window", lambda: 4242)
    monkeypatch.setattr(shell_tools, "_bring_to_foreground", lambda win: events.append("front"))
    monkeypatch.setattr(shell_tools, "_restore_foreground", lambda hwnd: events.append(f"back:{hwnd}"))
    result = shell_tools.validate_generated_xscr_via_shell(generated, shell_xscr=shell, by=by, backup=False)
    assert result.ok
    # Never restored, never closed: the inspected script stays open.
    assert events == ["close", "open", "front" if by == "human" else "back:4242"]
    assert '<Comment>new</Comment>' in shell.read_text(encoding="utf-8")
    assert result.timings["open_s"] == 1.0 and "total_s" in result.timings


def test_inspection_rejects_an_unknown_caller(tmp_path):
    generated, shell = _patch_files(tmp_path)
    with pytest.raises(ValueError, match="human"):
        shell_tools.validate_generated_xscr_via_shell(generated, shell_xscr=shell, by="robot")


def test_combined_checksum_and_load_failure_is_not_accepted():
    checksum, failed = classify_dialog_text("Invalid checksum. The load operation failed with exception.")
    assert checksum and failed


def _quick_ui(monkeypatch, tab_states):
    """Fake FluentControl: ``tab_states`` says, call by call, whether the
    script tab is there; no dialog ever shows; waits check once."""
    class Window:
        def exists(self, **kwargs):
            return True
    states = iter(tab_states)
    last = [False]
    def tab(win, name):
        last[0] = next(states, last[0])
        return "tab" if last[0] else None
    monkeypatch.setattr(shell_tools, "_connect_fluent_window", lambda pid: Window())
    monkeypatch.setattr(shell_tools, "_bring_to_foreground", lambda win: None)
    monkeypatch.setattr(shell_tools, "_safe_double_click", lambda el: None)
    monkeypatch.setattr(shell_tools, "_script_tab", tab)
    monkeypatch.setattr(shell_tools, "_fc_dialog_open", lambda win: False)
    monkeypatch.setattr(shell_tools, "_until", lambda predicate, timeout_s, interval_s=0.05: predicate())


def test_shell_not_opened_is_reported_as_failure(monkeypatch):
    class ScriptElement:
        def double_click_input(self):
            pass
    _quick_ui(monkeypatch, [False])
    monkeypatch.setattr(shell_tools, "_pick_leftmost_shell_element", lambda win, name: ScriptElement())
    result = shell_tools.open_shell_and_read_infopad(close_before_open=False)
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


def test_shell_picker_accepts_wpf_text_inside_list_item(monkeypatch):
    from types import SimpleNamespace
    class Element:
        def __init__(self, kind, parent=None, visible=True):
            self.element_info = SimpleNamespace(control_type=kind)
            self._parent = parent
            self._visible = visible
        def parent(self):
            return self._parent
        def is_visible(self):
            return self._visible
        def rectangle(self):
            return SimpleNamespace(left=10, top=20, right=100, bottom=40)
    item = Element("ListItem")
    label = Element("Text", item)
    tab = Element("TabItem")
    hidden = Element("TreeItem", visible=False)
    class Window:
        def descendants(self, **kwargs):
            assert kwargs == {"title": "shell"}
            return [tab, hidden, label]
    assert shell_tools._pick_leftmost_shell_element(Window()) is item


def test_infopad_catches_errors_before_context_marker(monkeypatch):
    from types import SimpleNamespace
    class Element:
        element_info = SimpleNamespace(control_type="Text")
        def __init__(self, text):
            self.text = text
        def window_text(self):
            return self.text
    class Window:
        def descendants(self):
            return [Element("035: Liquid subclass missing.\n036: Invalid location."),
                    Element("Infopad"), Element("shell")]
    lines, errors = shell_tools._read_infopad_validation(Window(), 200)
    assert errors == ["035: Liquid subclass missing.", "036: Invalid location."]


def test_infopad_missing_content_never_passes():
    class Window:
        def descendants(self):
            return []
    with pytest.raises(shell_tools.FluentControlShellError, match="could not be located"):
        shell_tools._read_infopad_validation(Window(), 200)


def test_infopad_is_read_until_errors_show(monkeypatch):
    error = "052: Liquid subclass missing."
    reads = iter([(True, [], []), (True, [], []), (True, [error], [error])])
    monkeypatch.setattr(shell_tools, "_read_infopad_pass", lambda *args: next(reads))
    monkeypatch.setattr(shell_tools.time, "sleep", lambda _: None)
    lines, errors = shell_tools._read_infopad_validation(object(), 200)
    assert errors == [error]


def test_shell_open_retries_double_click_when_invoke_only_selects(monkeypatch):
    clicked = []
    class Element:
        def double_click_input(self):
            clicked.append(True)
    # Nothing happens after invoke; after the real double-click the tab is there.
    _quick_ui(monkeypatch, [False, False, True])
    monkeypatch.setattr(shell_tools, "_pick_leftmost_shell_element", lambda *args: Element())
    monkeypatch.setattr(shell_tools, "_click_infopad_tab", lambda win: True)
    monkeypatch.setattr(shell_tools, "_read_infopad_validation", lambda *args: ([], []))
    result = shell_tools.open_shell_and_read_infopad(close_before_open=False)
    assert result.ok and clicked == [True]


def test_shell_open_does_not_double_click_a_loading_script_again(monkeypatch):
    # The tab came up after invoke: no second open while FluentControl loads.
    clicked = []
    class Element:
        def double_click_input(self):
            clicked.append(True)
    _quick_ui(monkeypatch, [True])
    monkeypatch.setattr(shell_tools, "_pick_leftmost_shell_element", lambda *args: Element())
    monkeypatch.setattr(shell_tools, "_click_infopad_tab", lambda win: True)
    monkeypatch.setattr(shell_tools, "_read_infopad_validation", lambda *args: ([], []))
    result = shell_tools.open_shell_and_read_infopad(close_before_open=False)
    assert result.ok and clicked == []
