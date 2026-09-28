"""FluentControl as a checker the authoring model can use on its own drafts.

A draft that compiles and simulates can still be rejected by FluentControl's
context check (the InfoPad): arms that cannot reach a position, labware that
does not fit a site, a prompt timeout of 0, duplicate labware names. This
module opens a compiled draft in FluentControl through the shell validator
(``fluentcontrol_shell``), reads the InfoPad, and turns each error line into a
finding the model can act on:

* the InfoPad line number is mapped back to the IR step and from there to the
  **Python line** (``Step.source_pos``) and group that emitted it;
* the message is classified and given a **fix hint**;
* follow-on errors (the "available liquid volume is 0" after an unreachable
  aspirate) and repeats are folded into one finding with a count.

FluentControl is one desktop application, so checks are serialised with a lock
file, and the UserSpecific shell script is restored after every check. The UI
automation runs in a child process (``fc_worker``) with a timeout, so it never
stalls the web server that asked for it.

``FLUENTVIBE_FC_CHECK=1`` makes the authoring graph run this check itself
before accepting a draft (see ``graph.py``); the ``check_in_fluentcontrol``
tool lets the model run it whenever it wants.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

FC_CHECK_ENV = "FLUENTVIBE_FC_CHECK"
FC_CHECK_TIMEOUT_ENV = "FLUENTVIBE_FC_CHECK_TIMEOUT_S"  # the worker is killed after this
FC_CHECK_BUDGET = 2  # repair turns the graph gives for InfoPad findings
_LOCK = Path(tempfile.gettempdir()) / "fluentvibe_fluentcontrol.lock"
_LINE = re.compile(r"^\s*(\d+)\s*:\s*(.+)$")


def fc_check_enabled() -> bool:
    return os.environ.get(FC_CHECK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


# (class, pattern, hint). First match wins; hints speak to the Python author.
_CLASSES: list[tuple[str, re.Pattern[str], str]] = [
    ("out_of_reach", re.compile(r"(?P<labware>.+?) (?:out of range|outside of range)\. Arm cannot move", re.I),
     "The arm cannot reach this labware where it is placed. An MCA96 cannot pipette in a slim trough "
     "(use an SBS reservoir such as '60ml SBS MCA96' on a 61 mm nest); otherwise move the labware to a "
     "position the arm reaches (the deck profile's reach.json lists them)."),
    ("no_liquid_in_tip", re.compile(r"volume to pipette is higher than the available liquid volume in the selected tip", re.I),
     "Follows from an aspirate that failed (see the finding before it); fix that one."),
    ("prompt_timeout", re.compile(r"'Close prompt after' exceeds", re.I),
     "A user prompt has an invalid auto-close time; use wt.user_prompt(text) without timeout or 1-7200 s."),
    ("duplicate_labware_name", re.compile(r"Labware name already exists", re.I),
     "Two labware share a name (also counts after the first was removed). Give each wt.place(...) its own name."),
    ("no_connector", re.compile(r"No connector for this rotation at this site", re.I),
     "This labware type does not fit that deck site. Place it on another location type (e.g. SBS reservoirs: "
     "'60ml SBS MCA96' on Nest61mm_Pos, '300ml SBS' on Nest7mm_Pos) or pick a catalog that fits the site."),
    ("invalid_labware", re.compile(r"Select a valid labware|Not possible to use for this command", re.I),
     "The command's labware is not usable here, usually because its placement failed (see an earlier "
     "'No connector' finding) or the labware type does not suit this command."),
    ("destination_occupied", re.compile(r"Destination location .* is occupied", re.I),
     "A gripper move or hand-off targets an occupied position. Use a free position for the hand-off / move."),
    ("select_wells", re.compile(r"^Select wells\.?$", re.I),
     "The pipetting command selects no wells for this labware; give well_offset / use a plate or trough "
     "the head can address."),
    ("tips_missing", re.compile(r"not found on the workspace|DiTi.*not found|No DiTi", re.I),
     "The tip type the command needs is not on the deck; place a matching tip box (FCA tips for the LiHa, "
     "an MCA96 box for the MCA)."),
    ("not_mounted", re.compile(r"not mounted|adapter", re.I),
     "The head's tips/adapter are not mounted at this point; pick up tips (and mount the MCA adapter) first."),
    ("liquid_class", re.compile(r"liquid class|Mix|Empty ?Tip", re.I),
     "The liquid class is missing a section this command needs (Mix for mixing, Empty Tip for emptying)."),
    ("z_range", re.compile(r"Z-?Max|Z-?Range|missing in tip|below the|above the", re.I),
     "The tips cannot reach the liquid height/depth here (labware too tall or volume too low)."),
]


@dataclass
class Finding:
    kind: str
    message: str
    hint: str
    fc_lines: list[int] = field(default_factory=list)
    python_lines: list[int] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    labware: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "count": len(self.fc_lines),
            "python_lines": sorted(set(self.python_lines))[:12],
            "groups": sorted(set(self.groups))[:6],
            "labware": self.labware,
            "hint": self.hint,
        }


def _classify(message: str) -> tuple[str, str, str | None]:
    for kind, pattern, hint in _CLASSES:
        match = pattern.search(message)
        if match:
            return kind, hint, match.groupdict().get("labware")
    return "other", "Read the message; it names the command and what FluentControl expects.", None


def _step_index(wt) -> dict[int, tuple[int | None, str]]:
    """FluentControl tree line -> (Python line, group name) for every step."""
    index: dict[int, tuple[int | None, str]] = {}
    try:
        protocol = wt.to_protocol()
    except Exception:
        return index

    def walk(steps, group: str) -> None:
        for step in steps:
            number = getattr(step, "line_number", None)
            pos = getattr(step, "source_pos", None)
            if number is not None:
                index[int(number)] = (getattr(pos, "line", None), group)
            walk(getattr(step, "steps", None) or (), group)

    for group in protocol.groups:
        walk(group.steps, group.name)
    return index


def _ascii(text: str) -> str:
    return re.sub(r"[^!-~]", "", text or "")


def explain_infopad(error_lines: list[str], wt=None) -> list[Finding]:
    """Group InfoPad lines into findings with Python lines and fix hints."""
    index = _step_index(wt) if wt is not None else {}
    # The scan also sees the script tree: a group named "1: Add beads" looks
    # like an InfoPad line. Compared without non-ASCII (FC shows µ garbled).
    groups = {_ascii(name) for _, name in index.values() if name}
    findings: dict[tuple[str, str], Finding] = {}
    previous_kind = None
    for raw in error_lines:
        if _ascii(raw) in groups:
            continue
        match = _LINE.match(raw)
        number, message = (int(match.group(1)), match.group(2).strip()) if match else (None, raw.strip())
        kind, hint, labware = _classify(message)
        if kind == "no_liquid_in_tip" and previous_kind in {"out_of_reach", "invalid_labware", "select_wells"}:
            previous_kind = kind
            continue  # consequence of the finding before it
        previous_kind = kind
        key = (kind, labware or message)
        finding = findings.setdefault(key, Finding(kind=kind, message=message, hint=hint, labware=labware))
        if number is not None:
            finding.fc_lines.append(number)
            py_line, group = index.get(number, (None, ""))
            if py_line is not None:
                finding.python_lines.append(py_line)
            if group:
                finding.groups.append(group)
    return list(findings.values())


def fluentcontrol_available() -> tuple[bool, str]:
    """FluentControl running and the shell script present?"""
    from .fluentcontrol_shell import DEFAULT_SHELL_XSCR

    if os.name != "nt":
        return False, "FluentControl checks need Windows"
    if not DEFAULT_SHELL_XSCR.exists():
        return False, f"shell script not found: {DEFAULT_SHELL_XSCR}"
    # tasklist can come back empty under memory pressure; look twice before
    # concluding FluentControl is gone (a false "not running" skipped a check).
    reason = "FluentControl (SystemSW.exe) is not running"
    for attempt in range(3):
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SystemSW.exe", "/NH"],
                                 capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            reason = f"could not list processes: {exc}"
            out = ""
        if "SystemSW.exe" in out:
            return True, ""
        time.sleep(2.0 * (attempt + 1))
    return False, reason


def _lock_owner() -> int | None:
    try:
        return int(_LOCK.read_text(encoding="utf-8").strip() or 0) or None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    """True when ``pid`` still runs (unknown counts as alive: never break a live lock).
    Not ``os.kill(pid, 0)``: on Windows signal 0 is CTRL_C_EVENT."""
    if not pid:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.GetLastError() == 5                # access denied: it exists
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259                         # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


@contextmanager
def _fluentcontrol_lock(timeout_s: float = 900.0) -> Iterator[None]:
    """One FluentControl check at a time (it drives a single desktop window)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(str(_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:  # a lock of a process that is gone (Cancel kills the tree), or an old one, is stale
                if not _pid_alive(_lock_owner()) or time.time() - _LOCK.stat().st_mtime > timeout_s:
                    _LOCK.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("another FluentControl check is still running")
            time.sleep(2.0)
    try:
        yield
    finally:
        _LOCK.unlink(missing_ok=True)


def _fc_check_timeout_s() -> float:
    try:
        return float(os.environ.get(FC_CHECK_TIMEOUT_ENV, "") or 300.0)
    except ValueError:
        return 300.0


def _run_fc_worker(xscr_path: Path) -> SimpleNamespace:
    """Run the UI check in ``fc_worker`` (a child process) and read its result.

    UI Automation in the web server's job thread stalled the job polling, so
    the COM work gets its own process. Called under ``_fluentcontrol_lock``;
    a timeout kills the child, and the shell script it was patching is put
    back here because the child's own restore never ran.
    """
    from .fc_worker import RESULT_PREFIX
    from .fluentcontrol_shell import DEFAULT_SHELL_XSCR, read_xscr_text, write_xscr_text

    try:
        shell_text = read_xscr_text(DEFAULT_SHELL_XSCR)
    except Exception:
        shell_text = None
    timeout_s = _fc_check_timeout_s()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "fluentvibe.authoring.fc_worker", str(Path(xscr_path).resolve())],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
            cwd=str(Path(__file__).resolve().parents[2]),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:  # run() has killed the child
        _restore_shell(shell_text, write_xscr_text, DEFAULT_SHELL_XSCR)
        raise RuntimeError(f"FluentControl did not finish within {timeout_s:g} s (worker killed)") from None
    lines = [line for line in (proc.stdout or "").splitlines() if line.startswith(RESULT_PREFIX)]
    if proc.returncode != 0 or not lines:
        _restore_shell(shell_text, write_xscr_text, DEFAULT_SHELL_XSCR)
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["no result"]
        raise RuntimeError(f"worker exited with code {proc.returncode}: {detail[0][:300]}")
    try:
        data = json.loads(lines[-1][len(RESULT_PREFIX):])
    except ValueError as exc:
        raise RuntimeError(f"worker printed an unreadable result: {exc}") from None
    return SimpleNamespace(
        opened=bool(data.get("opened", False)),
        load_failed=bool(data.get("load_failed", False)),
        load_error_text=str(data.get("load_error_text") or ""),
        error_lines=[str(line) for line in data.get("error_lines") or []],
    )


def _restore_shell(text, write, path) -> None:
    if text is None:
        return
    try:
        write(path, text)
    except Exception:
        pass


def check_in_fluentcontrol(xscr_path: Path, *, source: str | None = None, source_file: str | None = None,
                           validator=None) -> dict[str, Any]:
    """Open ``xscr_path`` in FluentControl, read the InfoPad, explain the findings.

    ``source`` / ``source_file`` (the draft the .xscr was compiled from) let
    findings point at Python lines. ``validator`` replaces the UI automation
    in tests.
    """
    if validator is None:
        available, reason = fluentcontrol_available()
        if not available:
            return {"ok": None, "available": False, "message": f"FluentControl check skipped: {reason}"}
        validator = _run_fc_worker

    try:
        with _fluentcontrol_lock():
            ui = validator(Path(xscr_path))
    except Exception as exc:  # the UI automation failed, not the protocol
        return {"ok": None, "available": False, "message": f"FluentControl check could not run: {exc}"}

    if getattr(ui, "load_failed", False) or not getattr(ui, "opened", True):
        return {
            "ok": False, "available": True,
            "message": "FluentControl could not load the script: " + (getattr(ui, "load_error_text", "") or "")[:400],
            "findings": [],
        }
    wt = None
    if source:
        try:
            from .eval_rubric import build_worktable_from_source

            wt = build_worktable_from_source(source, source_file or "<draft>")
        except Exception:
            wt = None
    errors = list(getattr(ui, "error_lines", None) or [])
    findings = explain_infopad(errors, wt)
    return {
        "ok": not findings,
        "available": True,
        "error_count": len(errors),
        "findings": [f.to_dict() for f in findings],
        "message": ("FluentControl opened the script with no InfoPad errors."
                    if not findings else
                    f"FluentControl's InfoPad reports {len(errors)} error line(s) in {len(findings)} finding(s)."),
    }


def fc_findings_message(check: dict[str, Any]) -> str:
    """The repair request the graph sends when FluentControl rejects a draft."""
    lines = [
        "FluentControl opened the compiled draft and its InfoPad (the context check that runs before a "
        "script can be executed) reports errors. The draft passed fluentvibe's own simulation, so these "
        "are about the real deck: reach, labware fit, names, prompts. Fix each finding in the Python "
        "source (edit_draft for small changes) and run simulate_python_draft again:",
    ]
    for finding in check.get("findings", [])[:8]:
        where = ", ".join(f"line {n}" for n in finding.get("python_lines", [])[:4]) or "location unknown"
        group = f" in {finding['groups'][0]!r}" if finding.get("groups") else ""
        lines.append(f"- [{finding['kind']}] {finding['message']} (x{finding['count']}; {where}{group}). "
                     f"Fix: {finding['hint']}")
    return "\n".join(lines)
