"""FluentControl shell validation for generated XSCR files.

This module patches a generated script payload into a persistent FluentControl
UserSpecific script named ``shell``, opens that script in FluentControl, and
scrapes InfoPad error lines. UI automation dependencies are imported lazily so
normal authoring imports do not require FluentControl or pywinauto.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_SHELL_XSCR = Path(
    os.environ.get(
        "TECANLAB_SHELL_XSCR",
        r"C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific\shell.xscr",
    )
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
        }


@dataclass(frozen=True)
class DialogScanResult:
    texts: list[str]
    checksum_dialog_seen: bool
    load_failure_texts: list[str]


_REGION_RE = re.compile(r"<Comment>.*?</Payload>", re.DOTALL)
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
    load_failure = bool(_LOAD_FAILURE_RE.search(text)) and not checksum
    return checksum, load_failure


def patch_shell_xscr_from_generated(
    generated_xscr: Path,
    *,
    shell_xscr: Path = DEFAULT_SHELL_XSCR,
    backup: bool = False,
) -> None:
    if not generated_xscr.exists():
        raise FluentControlShellError(f"Generated XSCR not found: {generated_xscr}")
    if not shell_xscr.exists():
        raise FluentControlShellError(f"Shell XSCR not found: {shell_xscr}")

    new_region = extract_comment_to_payload_region(read_xscr_text(generated_xscr))
    shell_text = read_xscr_text(shell_xscr)
    patched = replace_comment_to_payload_region(shell_text, new_region)

    if backup:
        backup_root = Path(os.getenv("TECAN_SHELL_BACKUP_DIR") or Path(tempfile.gettempdir()) / "tecan_shell_backups")
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / f"{shell_xscr.name}.bak_{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            backup_path.write_text(shell_text, encoding="utf-8")
        except Exception:
            pass

    write_xscr_text(shell_xscr, patched)


def validate_generated_xscr_via_shell(
    generated_xscr: Path,
    *,
    shell_xscr: Path = DEFAULT_SHELL_XSCR,
    process_id: Optional[int] = None,
    restore_shell: bool = False,
    backup: bool = False,
) -> UiResult:
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

    original = read_xscr_text(shell_xscr) if restore_shell else None
    try:
        patch_shell_xscr_from_generated(generated_xscr, shell_xscr=shell_xscr, backup=backup)
        try:
            return open_shell_and_read_infopad(
                process_id=process_id,
                close_before_open=True,
                close_after_read=True,
            )
        except Exception:
            return open_xscr_and_read_infopad(shell_xscr, process_id=process_id, close_after_read=True)
    finally:
        if restore_shell:
            try:
                write_xscr_text(shell_xscr, original or "")
            except Exception:
                pass


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
        import win32con
        import win32gui

        hwnd = window.handle
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        time.sleep(0.1)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
    except Exception:
        try:
            window.set_focus()
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


def _pick_leftmost_shell_element(fc_win):
    matches = []
    for el in fc_win.descendants(title="shell"):
        try:
            rect = el.rectangle()
            matches.append((rect.left, rect.top, el))
        except Exception:
            continue
    matches.sort(key=lambda item: (item[0], item[1]))
    if matches:
        return matches[0][2]
    return _navigate_tree_to_shell(fc_win)


def _navigate_tree_to_shell(fc_win):
    try:
        for el in fc_win.descendants(title="Scripts"):
            try:
                el.expand()
            except Exception:
                _safe_double_click(el)
            time.sleep(0.3)
            break
        else:
            return None

        for el in fc_win.descendants(title="Under_development"):
            try:
                el.expand()
            except Exception:
                _safe_double_click(el)
            time.sleep(0.3)
            break
        else:
            return None

        for el in fc_win.descendants(title="shell"):
            try:
                el.rectangle()
                return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def _dismiss_modal_dialogs(fc_win, timeout_s: float = 3.0) -> DialogScanResult:
    from pywinauto import Desktop

    deadline = time.monotonic() + timeout_s
    save_re = re.compile(r"save.*changes|do you want to save|save the changes", re.IGNORECASE)
    seen_texts: list[str] = []
    checksum_seen = False
    load_failures: list[str] = []

    def _dialog_text(dlg) -> str:
        parts = []
        try:
            for desc in dlg.descendants():
                try:
                    text = (desc.window_text() or "").strip()
                except Exception:
                    continue
                if text:
                    parts.append(text)
        except Exception:
            pass
        return " ".join(parts)

    def _iter_dialog_windows():
        windows = []
        try:
            windows.extend(Desktop(backend="uia").windows())
        except Exception:
            pass
        try:
            windows.extend(Desktop(backend="win32").windows(class_name="#32770"))
        except Exception:
            pass
        return windows

    while time.monotonic() < deadline:
        dismissed_any = False
        for dlg in _iter_dialog_windows():
            try:
                if not dlg.is_visible():
                    continue
            except Exception:
                continue
            text = _dialog_text(dlg)
            if text and text not in seen_texts:
                seen_texts.append(text)
                checksum_hit, load_failure_hit = classify_dialog_text(text)
                checksum_seen = checksum_seen or checksum_hit
                if load_failure_hit and text not in load_failures:
                    load_failures.append(text)

            if save_re.search(text):
                for btn_title in ("No", "Don't Save", "Dont Save", "Cancel"):
                    try:
                        btn = dlg.child_window(title=btn_title, control_type="Button")
                        if btn.exists(timeout=0.2):
                            _safe_click(btn)
                            dismissed_any = True
                            break
                    except Exception:
                        continue
                if dismissed_any:
                    break

            if classify_dialog_text(text)[0]:
                for btn_title in ("Yes", "OK"):
                    try:
                        btn = dlg.child_window(title=btn_title, control_type="Button")
                        if btn.exists(timeout=0.2):
                            _safe_click(btn)
                            dismissed_any = True
                            break
                    except Exception:
                        continue
                if dismissed_any:
                    break

        if not dismissed_any:
            try:
                main_text = _dialog_text(fc_win)
            except Exception:
                main_text = ""
            if main_text and main_text not in seen_texts:
                seen_texts.append(main_text)
                checksum_hit, load_failure_hit = classify_dialog_text(main_text)
                checksum_seen = checksum_seen or checksum_hit
                if load_failure_hit and main_text not in load_failures:
                    load_failures.append(main_text)

            if save_re.search(main_text):
                for btn_title in ("No", "Don't Save", "Dont Save", "Cancel"):
                    try:
                        btn = fc_win.child_window(title=btn_title, control_type="Button")
                        if btn.exists(timeout=0.2):
                            _safe_click(btn)
                            dismissed_any = True
                            break
                    except Exception:
                        continue

            if not dismissed_any and classify_dialog_text(main_text)[0]:
                for btn_title in ("Yes", "OK"):
                    try:
                        btn = fc_win.child_window(title=btn_title, control_type="Button")
                        if btn.exists(timeout=0.2):
                            _safe_click(btn)
                            dismissed_any = True
                            break
                    except Exception:
                        continue

        if not dismissed_any:
            try:
                btn = fc_win.child_window(title="OK", control_type="Button")
                if btn.exists(timeout=0.2):
                    _safe_click(btn.wrapper_object())
                    dismissed_any = True
            except Exception:
                pass

        if not dismissed_any:
            break
        time.sleep(0.2)

    return DialogScanResult(
        texts=seen_texts,
        checksum_dialog_seen=checksum_seen,
        load_failure_texts=load_failures,
    )


def _close_shell_tab_if_open(fc_win, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            for tab in fc_win.descendants(control_type="TabItem"):
                if (tab.window_text() or "").strip() != "shell":
                    continue
                _safe_click(tab)
                _bring_to_foreground(fc_win)
                fc_win.type_keys("^{F4}")
                time.sleep(0.2)
                _dismiss_modal_dialogs(fc_win, timeout_s=1.0)
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _click_infopad_tab(fc_win) -> None:
    try:
        tab = fc_win.child_window(title_re=r"^Infopad$", control_type="TabItem")
        if tab.exists(timeout=0.5):
            _safe_click(tab)
            time.sleep(0.2)
            return
    except Exception:
        pass
    try:
        for desc in fc_win.descendants():
            try:
                if (desc.window_text() or "").strip() == "Infopad":
                    _safe_click(desc)
                    time.sleep(0.2)
                    return
            except Exception:
                continue
    except Exception:
        pass


def _scan_error_lines_anywhere(fc_win, limit: int = 200) -> list[str]:
    found: dict[int, str] = {}
    leftovers: list[str] = []
    try:
        for desc in fc_win.descendants():
            try:
                text = (desc.window_text() or "").strip()
            except Exception:
                continue
            if not text or not _ERR_LINE_RE.match(text):
                continue
            try:
                number = int(text.split(":", 1)[0])
                found.setdefault(number, text)
            except Exception:
                leftovers.append(text)
            if len(found) + len(leftovers) >= limit:
                break
    except Exception:
        return []
    ordered = [found[key] for key in sorted(found)]
    for line in leftovers:
        if line not in ordered:
            ordered.append(line)
    return ordered[:limit]


def _infopad_context_lines(fc_win, limit: int) -> list[str]:
    found_infopad = False
    lines: list[str] = []
    try:
        for desc in fc_win.descendants():
            try:
                text = desc.window_text() or ""
            except Exception:
                continue
            if text == "Infopad":
                found_infopad = True
                continue
            if not found_infopad:
                continue
            control_type = getattr(getattr(desc, "element_info", None), "control_type", "")
            if control_type in ("Text", "Edit", "Document"):
                stripped = text.strip()
                if stripped:
                    lines.append(stripped)
            if len(lines) >= limit:
                break
    except Exception as exc:
        raise FluentControlShellError(f"Failed reading InfoPad: {exc}") from exc
    return lines


def open_shell_and_read_infopad(
    *,
    process_id: Optional[int] = None,
    settle_seconds: float = 1.0,
    infopad_line_limit: int = 200,
    close_before_open: bool = True,
    close_after_read: bool = False,
) -> UiResult:
    fc_win = _connect_fluent_window(process_id)
    if not fc_win.exists(timeout=2):
        raise FluentControlShellError("FluentControl window not found.")

    _bring_to_foreground(fc_win)
    if close_before_open:
        _close_shell_tab_if_open(fc_win, timeout_s=2.0)

    shell_el = _pick_leftmost_shell_element(fc_win)
    if shell_el is None:
        raise FluentControlShellError("Could not find a UI element titled 'shell' in FluentControl.")

    try:
        _safe_double_click(shell_el)
    except Exception:
        _safe_click(shell_el)
        fc_win.type_keys("{ENTER}")

    time.sleep(settle_seconds)
    dialogs = _dismiss_modal_dialogs(fc_win, timeout_s=3.0)
    if dialogs.load_failure_texts:
        return UiResult(
            opened=False,
            infopad_lines=[],
            error_lines=dialogs.load_failure_texts,
            load_failed=True,
            load_error_text="\n\n".join(dialogs.load_failure_texts),
            checksum_dialog_seen=dialogs.checksum_dialog_seen,
            modal_dialogs=dialogs.texts,
        )

    _click_infopad_tab(fc_win)
    error_lines: list[str] = []
    lines: list[str] = []
    for _ in range(3):
        error_lines = _scan_error_lines_anywhere(fc_win, limit=infopad_line_limit)
        lines = list(error_lines)
        if error_lines:
            break
        time.sleep(0.4)
    if not error_lines:
        lines = _infopad_context_lines(fc_win, infopad_line_limit)

    if close_after_read:
        _close_shell_tab_if_open(fc_win, timeout_s=2.0)

    return UiResult(
        opened=True,
        infopad_lines=lines,
        error_lines=error_lines,
        checksum_dialog_seen=dialogs.checksum_dialog_seen,
        modal_dialogs=dialogs.texts,
    )


def _try_open_xscr_via_file_dialog(fc_win, xscr_path: Path, timeout_s: float = 12.0) -> None:
    from pywinauto import Desktop

    xscr_path = xscr_path.resolve()
    _bring_to_foreground(fc_win)
    fc_win.type_keys("^o")

    deadline = time.monotonic() + timeout_s
    dialog = None
    while time.monotonic() < deadline and dialog is None:
        try:
            dialogs = Desktop(backend="win32").windows(class_name="#32770")
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
        dialog.type_keys(str(xscr_path), with_spaces=True)
        dialog.type_keys("{ENTER}")
        return
    except Exception:
        pass

    try:
        dialog.child_window(class_name="Edit").set_edit_text(str(xscr_path))
        dialog.child_window(title_re=r"^(Open|OK)$", class_name="Button").click()
    except Exception as exc:
        raise FluentControlShellError(f"Failed to drive Open file dialog: {exc}") from exc


def open_xscr_and_read_infopad(
    xscr_path: Path,
    *,
    process_id: Optional[int] = None,
    settle_seconds: float = 1.5,
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
    _try_open_xscr_via_file_dialog(fc_win, xscr_path)
    time.sleep(settle_seconds)
    dialogs = _dismiss_modal_dialogs(fc_win, timeout_s=3.0)
    if dialogs.load_failure_texts:
        return UiResult(
            opened=False,
            infopad_lines=[],
            error_lines=dialogs.load_failure_texts,
            load_failed=True,
            load_error_text="\n\n".join(dialogs.load_failure_texts),
            checksum_dialog_seen=dialogs.checksum_dialog_seen,
            modal_dialogs=dialogs.texts,
        )

    _click_infopad_tab(fc_win)
    error_lines: list[str] = []
    lines: list[str] = []
    for _ in range(3):
        error_lines = _scan_error_lines_anywhere(fc_win, limit=infopad_line_limit)
        lines = list(error_lines)
        if error_lines:
            break
        time.sleep(0.4)
    if not error_lines:
        lines = _infopad_context_lines(fc_win, infopad_line_limit)

    if close_after_read:
        try:
            if xscr_path.resolve() == DEFAULT_SHELL_XSCR.resolve():
                _close_shell_tab_if_open(fc_win, timeout_s=2.0)
        except Exception:
            pass

    return UiResult(
        opened=True,
        infopad_lines=lines,
        error_lines=error_lines,
        checksum_dialog_seen=dialogs.checksum_dialog_seen,
        modal_dialogs=dialogs.texts,
    )
