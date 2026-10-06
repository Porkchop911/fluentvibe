"""FluentControl shell inspection for generated XSCR files.

A generated script's payload is patched into a persistent FluentControl
UserSpecific script named ``shell``, which is opened in FluentControl and its
InfoPad read. The script stays open afterwards and the shell keeps the last
inspected protocol (it is never restored): what was checked can be looked at.

Two callers, one sequence: ``by="human"`` (every button: web app, VS Code,
``fc-open``) leaves FluentControl in front with the InfoPad showing;
``by="agent"`` (the authoring loop) hands the focus back to the window that
had it. Waits poll for the next UI state instead of sleeping fixed times.
UI automation dependencies are imported lazily so normal authoring imports do
not require FluentControl or pywinauto.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

DEFAULT_SHELL_XSCR = Path(
    r"C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\b010c60d-813d-40cf-848a-584d0432f789.xscr"
)


class FluentControlShellError(RuntimeError):
    pass


@dataclass(frozen=True)
class UiResult:
    opened: bool
    infopad_lines: list[str]
    error_lines: list[str]
    load_failed: bool = False
    load_error_text: str = ""
    checksum_dialog_seen: bool = False
    modal_dialogs: list[str] | None = None
    diagnostics: list[str] | None = None
    backup_path: str | None = None
    timings: dict | None = None

    @property
    def ok(self) -> bool:
        return self.opened and not self.load_failed and not self.error_lines

    def to_dict(self, *, xscr_path: Path | None = None, shell_xscr: Path | None = None) -> dict:
        return {
            "ok": self.ok,
            "opened": self.opened,
            "load_failed": self.load_failed,
            "load_error_text": self.load_error_text,
            "checksum_dialog_seen": self.checksum_dialog_seen,
            "error_count": len(self.error_lines),
            "errors": self.error_lines,
            "infopad_sample": self.infopad_lines[:50],
            "modal_dialogs": (self.modal_dialogs or [])[:20],
            "xscr_path": str(xscr_path) if xscr_path else None,
            "shell_xscr": str(shell_xscr) if shell_xscr else None,
            "diagnostics": self.diagnostics or [],
            "backup_path": self.backup_path,
            "timings": self.timings or {},
        }


@dataclass(frozen=True)
class DialogScanResult:
    texts: list[str]
    checksum_dialog_seen: bool
    load_failure_texts: list[str]


_REGION_RE = re.compile(r"<Comment>.*?</Payload>", re.DOTALL)
_SHELL_LOCK = threading.RLock()
_ERR_LINE_RE = re.compile(r"^\d{1,4}:\s+")
_LOAD_FAILURE_RE = re.compile(
    r"(load operation.*failed|failed with exception|does not match the end tag|"
    r"array index should be empty and not null|please contact the application administrator|"
    r"invalid checksum)",
    re.IGNORECASE,
)


def read_xscr_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def write_xscr_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def extract_comment_to_payload_region(xml_text: str) -> str:
    match = _REGION_RE.search(xml_text)
    if not match:
        raise FluentControlShellError("Could not find <Comment>..</Payload> region in XSCR.")
    return match.group(0)


def replace_comment_to_payload_region(target_xml: str, new_region: str) -> str:
    if not _REGION_RE.search(target_xml):
        raise FluentControlShellError("Could not find <Comment>..</Payload> region in shell XSCR.")
    return _REGION_RE.sub(lambda _match: new_region, target_xml, count=1)


def precheck_xscr_text(xml_text: str) -> list[str]:
    issues: list[str] = []
    for open_tag, close_tag in (("<Objects>", "</Objects>"), ("<Statements>", "</Statements>")):
        open_count = xml_text.count(open_tag)
        close_count = xml_text.count(close_tag)
        if open_count != close_count:
            issues.append(f"XML tag mismatch: {open_tag}={open_count} but {close_tag}={close_count}.")
    try:
        ET.fromstring(xml_text)
    except ET.ParseError as exc:
        issues.append(f"XML parse error: {exc}")
    return issues


def precheck_xscr_file(path: Path) -> list[str]:
    if not path.exists():
        return [f"XSCR file not found: {path}"]
    return precheck_xscr_text(read_xscr_text(path))


def classify_dialog_text(text: str) -> tuple[bool, bool]:
    checksum = bool(re.search(r"(invalid checksum|checksum|VX_ESHRD_002_002)", text, re.IGNORECASE))
    load_failure = bool(_LOAD_FAILURE_RE.search(text)) and bool(re.search(r"load operation|failed with exception|end tag|array index|application administrator", text, re.I))
    return checksum, load_failure


def patch_shell_xscr_from_generated(
    generated_xscr: Path,
    *,
    shell_xscr: Path = DEFAULT_SHELL_XSCR,
    backup: bool = True,
) -> Path | None:
    if not generated_xscr.exists():
        raise FluentControlShellError(f"Generated XSCR not found: {generated_xscr}")
    if not shell_xscr.exists():
        raise FluentControlShellError(f"Shell XSCR not found: {shell_xscr}")
    if generated_xscr.resolve() == shell_xscr.resolve():
        raise FluentControlShellError("Generated XSCR and shell must be different files")

    new_region = extract_comment_to_payload_region(read_xscr_text(generated_xscr))
    shell_text = read_xscr_text(shell_xscr)
    patched = replace_comment_to_payload_region(shell_text, new_region)
    issues = precheck_xscr_text(patched)
    if issues:
        raise FluentControlShellError("Invalid patched shell: " + "; ".join(issues))

    backup_path = None
    if backup:
        backup_root = Path(os.getenv("TECAN_SHELL_BACKUP_DIR") or Path(tempfile.gettempdir()) / "tecan_shell_backups")
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / f"{shell_xscr.name}.bak_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        backup_path.write_bytes(shell_xscr.read_bytes())

    from ..deployer import _checksum_rewrite_and_verify

    with tempfile.NamedTemporaryFile(dir=shell_xscr.parent, suffix=".xscr", delete=False) as staging:
        staging_path = Path(staging.name)
    try:
        write_xscr_text(staging_path, patched)
        checksum = _checksum_rewrite_and_verify(staging_path)
        if not checksum.get("is_valid"):
            raise FluentControlShellError("Patched shell checksum verification failed")
        os.replace(staging_path, shell_xscr)
    finally:
        staging_path.unlink(missing_ok=True)
    return backup_path


def validate_generated_xscr_via_shell(
    generated_xscr: Path,
    *,
    by: str = "agent",
    shell_xscr: Path = DEFAULT_SHELL_XSCR,
    process_id: Optional[int] = None,
    backup: bool = True,
) -> UiResult:
    """Inspect ``generated_xscr`` in FluentControl through the shell script.

    Close the shell's tab if open, patch the payload in, open it, read the
    InfoPad, leave it open. ``by="human"`` ends with FluentControl in front;
    ``by="agent"`` gives the focus back to the window that had it."""
    if by not in ("human", "agent"):
        raise ValueError("by must be 'human' or 'agent'")
    shell_xscr = Path(shell_xscr)
    generated_xscr = Path(generated_xscr)
    precheck_errors = precheck_xscr_file(generated_xscr)
    if precheck_errors:
        return UiResult(
            opened=False,
            infopad_lines=[],
            error_lines=precheck_errors,
            load_failed=True,
            load_error_text="\n".join(precheck_errors),
            modal_dialogs=[],
        )
    if not shell_xscr.exists():
        return UiResult(
            opened=False,
            infopad_lines=[],
            error_lines=[f"Shell XSCR not found: {shell_xscr}"],
            load_failed=True,
            load_error_text=f"Shell XSCR not found: {shell_xscr}",
            modal_dialogs=[],
        )

    timings: dict[str, float] = {}
    started = time.monotonic()
    previous_window = _foreground_window() if by == "agent" else None
    with _SHELL_LOCK:
        shell_root = ET.fromstring(shell_xscr.read_bytes().decode("utf-8-sig"))
        script_name = shell_root.findtext("./Payload/ObjectName") or "shell"
        # Close the editor BEFORE changing its backing file. Otherwise closing
        # a dirty tab can overwrite the new payload or reload cached content.
        fc_win = _connect_fluent_window(process_id)
        mark = time.monotonic()
        _close_shell_tab_if_open(fc_win, script_name=script_name)
        timings["close_previous_s"] = round(time.monotonic() - mark, 2)
        mark = time.monotonic()
        backup_path = patch_shell_xscr_from_generated(generated_xscr, shell_xscr=shell_xscr, backup=backup)
        timings["patch_s"] = round(time.monotonic() - mark, 2)
        try:
            try:
                result = open_shell_and_read_infopad(
                    process_id=process_id, script_name=script_name,
                    close_before_open=False, close_after_read=False,
                )
            except FluentControlShellError as exc:
                result = open_xscr_and_read_infopad(shell_xscr, process_id=process_id, close_after_read=False)
                result = replace(result, diagnostics=[f"Tree open failed; used exact file path: {exc}"])
        finally:
            if by == "human":
                _bring_to_foreground(fc_win)
            else:
                _restore_foreground(previous_window)
    timings.update(result.timings or {})
    timings["total_s"] = round(time.monotonic() - started, 2)
    return replace(result, backup_path=str(backup_path) if backup_path else None, timings=timings)


def _foreground_window() -> int | None:
    try:
        import win32gui

        return win32gui.GetForegroundWindow() or None
    except Exception:
        return None


def _restore_foreground(hwnd: int | None) -> None:
    if not hwnd:
        return
    try:
        import win32gui

        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _until(predicate, timeout_s: float, interval_s: float = 0.05):
    """Poll ``predicate`` until it returns something truthy or time runs out;
    return its last value. Exceptions count as "not yet" (the UI tree can be
    busy while FluentControl loads)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            value = predicate()
        except Exception:
            value = None
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval_s)


def validate_xscr_direct(
    xscr_path: Path,
    *,
    process_id: Optional[int] = None,
) -> UiResult:
    precheck_errors = precheck_xscr_file(xscr_path)
    if precheck_errors:
        return UiResult(
            opened=False,
            infopad_lines=[],
            error_lines=precheck_errors,
            load_failed=True,
            load_error_text="\n".join(precheck_errors),
            modal_dialogs=[],
        )
    return open_xscr_and_read_infopad(xscr_path, process_id=process_id, close_after_read=True)


def keep_display_awake() -> None:
    try:
        import ctypes

        es_continuous = 0x80000000
        es_system_required = 0x00000001
        es_display_required = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(
            es_continuous | es_system_required | es_display_required
        )
    except Exception:
        pass


def _postmessage_double_click(el) -> bool:
    try:
        import win32api
        import win32con
        import win32gui

        hwnd = el.handle
        rect = el.rectangle()
        client_pt = win32gui.ScreenToClient(
            hwnd,
            (rect.left + (rect.right - rect.left) // 2, rect.top + (rect.bottom - rect.top) // 2),
        )
        lparam = win32api.MAKELONG(client_pt[0], client_pt[1])
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        return True
    except Exception:
        return False


def _bring_to_foreground(window) -> None:
    try:
        window.set_focus()
    except Exception:
        try:
            import win32gui

            win32gui.SetForegroundWindow(window.handle)
        except Exception:
            pass


def _safe_click(el) -> None:
    try:
        el.invoke()
        return
    except Exception:
        pass
    try:
        el.select()
        return
    except Exception:
        pass
    el.click_input()


def _safe_double_click(el) -> None:
    try:
        el.invoke()
        return
    except Exception:
        pass
    try:
        el.select()
        from pywinauto.keyboard import send_keys

        send_keys("{ENTER}")
        return
    except Exception:
        pass
    if _postmessage_double_click(el):
        return
    el.double_click_input()


def _connect_fluent_window(process_id: Optional[int] = None):
    from pywinauto import timings
    from pywinauto.application import Application

    timings.Timings.window_find_timeout = 8
    if process_id is not None:
        app = Application(backend="uia").connect(process=process_id)
    else:
        app = Application(backend="uia").connect(title="FluentControl")
    return app.window(title="FluentControl")


def _pick_leftmost_shell_element(fc_win, script_name="shell"):
    matches = []
    # FluentControl's WPF control bar exposes script labels as Text inside a
    # ListItem on some versions, rather than as standard TreeItems.
    for el in fc_win.descendants(title=script_name):
        try:
            if not el.is_visible():
                continue
            target = el
            for _ in range(4):
                kind = getattr(target.element_info, "control_type", "")
                if kind in ("TreeItem", "ListItem"):
                    break
                if kind in ("TabItem", "Window"):
                    target = None
                    break
                target = target.parent()
            else:
                target = None
            if target is None:
                continue
            rect = target.rectangle()
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue
            matches.append((rect.left, rect.top, target))
        except Exception:
            continue
    matches.sort(key=lambda item: (item[0], item[1]))
    if matches:
        return matches[0][2]
    return _navigate_tree_to_shell(fc_win, script_name)


def _navigate_tree_to_shell(fc_win, script_name="shell"):
    """Expand Scripts > Under_development until the script's item shows."""
    for folder, child in (("Scripts", "Under_development"), ("Under_development", script_name)):
        found = list(fc_win.descendants(title=folder))
        if not found:
            return None
        try:
            found[0].expand()
        except Exception:
            _safe_double_click(found[0])
        if not _until(lambda name=child: fc_win.descendants(title=name), 3.0):
            return None
    for el in fc_win.descendants(title=script_name, control_type="TreeItem"):
        try:
            el.rectangle()
            return el
        except Exception:
            continue
    return None


def _dismiss_modal_dialogs(fc_win, timeout_s: float = 3.0) -> DialogScanResult:
    from pywinauto import Desktop

    deadline = time.monotonic() + timeout_s
    seen = []
    failures = []
    checksum_seen = False
    process_id = fc_win.process_id()
    while time.monotonic() < deadline:
        dismissed = False
        # Only this FluentControl process; never dismiss another app's dialog.
        for dlg in Desktop(backend="uia").windows(process=process_id):
            if not dlg.is_visible() or dlg.handle == fc_win.handle:
                continue
            text = " ".join((el.window_text() or "").strip() for el in dlg.descendants())
            if text and text not in seen:
                seen.append(text)
            checksum, failed = classify_dialog_text(text)
            checksum_seen |= checksum
            if failed and text not in failures:
                failures.append(text)
            if re.search(r"save.*changes|do you want to save", text, re.I):
                # Do not discard unrelated unsaved user edits.
                raise FluentControlShellError("FluentControl has unsaved changes; save or close that script before validating")
            titles = ("Yes", "OK") if checksum else (("OK",) if failed else ())
            for title in titles:
                button = dlg.child_window(title=title, control_type="Button")
                if button.exists(timeout=0.1):
                    _safe_click(button)
                    dismissed = True
                    break
        if not dismissed:
            break
        time.sleep(0.1)
    return DialogScanResult(seen, checksum_seen, failures)


def _script_tab(fc_win, script_name: str):
    """The open script tab for ``script_name`` (a trailing " *" = unsaved), or None."""
    for tab in fc_win.descendants(control_type="TabItem"):
        if (tab.window_text() or "").strip().rstrip(" *") == script_name:
            return tab
    return None


def _fc_dialog_open(fc_win) -> bool:
    """A FluentControl window other than the main one is showing (a dialog)."""
    from pywinauto import Desktop

    process_id = fc_win.process_id()
    return any(dlg.is_visible() and dlg.handle != fc_win.handle
               for dlg in Desktop(backend="uia").windows(process=process_id))


def _click_tab_close_button(tab) -> bool:
    """Close a document tab with its own close button. Ctrl+F4 closes whichever
    tab is active, which is not necessarily this one."""
    for button in tab.descendants(control_type="Button"):
        name = (button.window_text() or "").strip().lower()
        if not name or re.search(r"close|schlie|^x$|×", name):
            _safe_click(button)
            return True
    return False


def _close_shell_tab_if_open(fc_win, timeout_s: float = 3.0, script_name: str = "shell") -> bool:
    tab = _script_tab(fc_win, script_name)
    if tab is None:
        return False
    _safe_click(tab)
    if not _click_tab_close_button(tab):
        # No close button found: Ctrl+F4, but only with this tab active.
        selected = False
        try:
            selected = bool(tab.is_selected())
        except Exception:
            pass
        if not selected:
            raise FluentControlShellError(f"Could not close the {script_name!r} tab safely; close it in FluentControl")
        _bring_to_foreground(fc_win)
        fc_win.type_keys("^{F4}")
    # A dirty tab asks to save: _dismiss_modal_dialogs refuses rather than discard edits.
    _until(lambda: _script_tab(fc_win, script_name) is None or _fc_dialog_open(fc_win), timeout_s)
    _dismiss_modal_dialogs(fc_win, timeout_s=1.0)
    if _until(lambda: _script_tab(fc_win, script_name) is None, 1.0) is not True:
        raise FluentControlShellError(f"Script tab did not close: {script_name}")
    return True


def _wait_for_script_tab(fc_win, script_name: str, timeout_s: float = 5.0) -> bool:
    return _until(lambda: _script_tab(fc_win, script_name) is not None, timeout_s) is True


def _click_infopad_tab(fc_win) -> bool:
    try:
        tab = fc_win.child_window(title_re=r"^Infopad$", control_type="TabItem")
        if tab.exists(timeout=0.5):
            _safe_click(tab)
            return True
    except Exception:
        pass
    try:
        for desc in fc_win.descendants():
            try:
                if (desc.window_text() or "").strip() == "Infopad":
                    _safe_click(desc)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _read_infopad_pass(fc_win, limit: int) -> tuple[bool, list[str], list[str]]:
    """One walk of the window: (InfoPad found, its lines, every ``n: ...`` line).

    The context lines are the Text/Edit/Document elements after the "Infopad"
    label; error lines are taken from anywhere, because some FluentControl
    versions put the InfoPad rows before that label in the tree."""
    found_infopad = False
    lines: list[str] = []
    errors: list[str] = []
    try:
        for desc in fc_win.descendants():
            try:
                text = desc.window_text() or ""
            except Exception:
                continue
            for line in text.splitlines():
                line = line.strip()
                if _ERR_LINE_RE.match(line) and line not in errors and len(errors) < limit:
                    errors.append(line)
            if text == "Infopad":
                found_infopad = True
                continue
            if found_infopad and len(lines) < limit:
                control_type = getattr(getattr(desc, "element_info", None), "control_type", "")
                if control_type in ("Text", "Edit", "Document") and text.strip():
                    lines.append(text.strip())
    except Exception as exc:
        raise FluentControlShellError(f"Failed reading InfoPad: {exc}") from exc
    return found_infopad, lines, errors


def _read_infopad_validation(fc_win, limit: int, timeout_s: float = 1.5) -> tuple[list[str], list[str]]:
    """InfoPad lines and error lines. The InfoPad fills in shortly after the
    tab opens: read until errors show or ``timeout_s`` passes; the last read
    is the verdict. No InfoPad and no error line anywhere is never a pass."""
    deadline = time.monotonic() + timeout_s
    while True:
        found, lines, errors = _read_infopad_pass(fc_win, limit)
        if errors:
            return (lines if found else errors), errors
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    if not found:
        raise FluentControlShellError("InfoPad content could not be located")
    return lines, errors


def _await_script_open(fc_win, script_name: str, timeout_s: float) -> tuple[bool, DialogScanResult]:
    """Wait for the script tab, handling FluentControl's dialogs as they come.

    Returns (tab open, dialogs seen). A load-failure dialog ends the wait."""
    texts: list[str] = []
    failures: list[str] = []
    checksum = False
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        _until(lambda: _script_tab(fc_win, script_name) is not None or _fc_dialog_open(fc_win), remaining)
        if _fc_dialog_open(fc_win):
            seen = _dismiss_modal_dialogs(fc_win, timeout_s=3.0)
            texts += [t for t in seen.texts if t not in texts]
            failures += [t for t in seen.load_failure_texts if t not in failures]
            checksum = checksum or seen.checksum_dialog_seen
            if failures:
                return False, DialogScanResult(texts, checksum, failures)
            if time.monotonic() < deadline:
                continue
        opened = _script_tab(fc_win, script_name) is not None
        return opened, DialogScanResult(texts, checksum, failures)


def _infopad_result(fc_win, dialogs: DialogScanResult, limit: int, timings: dict) -> UiResult:
    mark = time.monotonic()
    if not _click_infopad_tab(fc_win):
        return UiResult(opened=True, infopad_lines=[], error_lines=["InfoPad could not be read"],
                        modal_dialogs=dialogs.texts, timings=timings)
    lines, error_lines = _read_infopad_validation(fc_win, limit)
    timings["read_infopad_s"] = round(time.monotonic() - mark, 2)
    return UiResult(
        opened=True,
        infopad_lines=lines,
        error_lines=error_lines,
        checksum_dialog_seen=dialogs.checksum_dialog_seen,
        modal_dialogs=dialogs.texts,
        timings=timings,
    )


def _not_opened(dialogs: DialogScanResult, script_name: str, timings: dict) -> UiResult:
    errors = dialogs.load_failure_texts or [f"Script tab did not open: {script_name}"]
    return UiResult(opened=False, infopad_lines=[], error_lines=errors, load_failed=True,
                    load_error_text="\n\n".join(errors), checksum_dialog_seen=dialogs.checksum_dialog_seen,
                    modal_dialogs=dialogs.texts, timings=timings)


def open_shell_and_read_infopad(
    *,
    process_id: Optional[int] = None,
    load_timeout_s: float = 90.0,
    infopad_line_limit: int = 200,
    close_before_open: bool = True,
    close_after_read: bool = False,
    script_name: str = "shell",
) -> UiResult:
    fc_win = _connect_fluent_window(process_id)
    if not fc_win.exists(timeout=2):
        raise FluentControlShellError("FluentControl window not found.")

    _bring_to_foreground(fc_win)
    if close_before_open:
        _close_shell_tab_if_open(fc_win, script_name=script_name)

    timings: dict[str, float] = {}
    mark = time.monotonic()
    shell_el = _pick_leftmost_shell_element(fc_win, script_name)
    if shell_el is None:
        raise FluentControlShellError("Could not find a UI element titled 'shell' in FluentControl.")
    timings["find_shell_s"] = round(time.monotonic() - mark, 2)

    mark = time.monotonic()
    try:
        _safe_double_click(shell_el)
    except Exception:
        _safe_click(shell_el)
        fc_win.type_keys("{ENTER}")
    # Invoke/select does not open the script on every WPF control. If nothing
    # at all happened after a few seconds (no tab, no dialog), double-click
    # for real once; then wait for the load itself.
    started = _until(lambda: _script_tab(fc_win, script_name) is not None or _fc_dialog_open(fc_win), 4.0)
    if not started:
        shell_el.double_click_input()
    opened, dialogs = _await_script_open(fc_win, script_name, load_timeout_s)
    timings["open_s"] = round(time.monotonic() - mark, 2)
    if not opened:
        return _not_opened(dialogs, script_name, timings)
    result = _infopad_result(fc_win, dialogs, infopad_line_limit, timings)
    if close_after_read:
        _close_shell_tab_if_open(fc_win, script_name=script_name)
    return result


def _try_open_xscr_via_file_dialog(fc_win, xscr_path: Path, timeout_s: float = 12.0) -> None:
    from pywinauto import Desktop

    xscr_path = xscr_path.resolve()
    _bring_to_foreground(fc_win)
    fc_win.type_keys("^o")

    deadline = time.monotonic() + timeout_s
    dialog = None
    while time.monotonic() < deadline and dialog is None:
        try:
            dialogs = Desktop(backend="win32").windows(class_name="#32770", process=fc_win.process_id())
        except Exception:
            dialogs = []
        for candidate in dialogs:
            try:
                if not candidate.is_visible():
                    continue
                button = candidate.child_window(title_re=r"^(Open|OK)$", class_name="Button")
                edit = candidate.child_window(class_name="Edit")
                if button.exists(timeout=0.1) and edit.exists(timeout=0.1):
                    dialog = candidate
                    break
            except Exception:
                continue
        time.sleep(0.1)

    if dialog is None:
        raise FluentControlShellError("Open file dialog not found after Ctrl+O.")

    try:
        dialog.set_focus()
    except Exception:
        pass

    try:
        dialog.child_window(class_name="Edit").set_edit_text(str(xscr_path))
        _safe_click(dialog.child_window(title_re=r"^(Open|OK)$", class_name="Button"))
    except Exception as exc:
        raise FluentControlShellError(f"Failed to drive Open file dialog: {exc}") from exc


def open_xscr_and_read_infopad(
    xscr_path: Path,
    *,
    process_id: Optional[int] = None,
    load_timeout_s: float = 90.0,
    infopad_line_limit: int = 200,
    close_after_read: bool = True,
) -> UiResult:
    xscr_path = Path(xscr_path)
    if not xscr_path.exists():
        raise FluentControlShellError(f"XSCR not found: {xscr_path}")

    fc_win = _connect_fluent_window(process_id)
    if not fc_win.exists(timeout=2):
        raise FluentControlShellError("FluentControl window not found.")

    _bring_to_foreground(fc_win)
    root = ET.fromstring(read_xscr_text(xscr_path))
    script_name = root.findtext("./Payload/ObjectName") or xscr_path.stem
    timings: dict[str, float] = {}
    mark = time.monotonic()
    _try_open_xscr_via_file_dialog(fc_win, xscr_path)
    opened, dialogs = _await_script_open(fc_win, script_name, load_timeout_s)
    timings["open_s"] = round(time.monotonic() - mark, 2)
    if not opened:
        return _not_opened(dialogs, script_name, timings)
    result = _infopad_result(fc_win, dialogs, infopad_line_limit, timings)
    if close_after_read:
        _close_shell_tab_if_open(fc_win, script_name=script_name)
    return result
