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
file, and the UserSpecific shell script is restored after every check.

``FLUENTVIBE_FC_CHECK=1`` makes the authoring graph run this check itself
before accepting a draft (see ``graph.py``); the ``check_in_fluentcontrol``
tool lets the model run it whenever it wants.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

FC_CHECK_ENV = "FLUENTVIBE_FC_CHECK"
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
            try:  # a lock older than the timeout belongs to a dead check
                if time.time() - _LOCK.stat().st_mtime > timeout_s:
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
        from .fluentcontrol_shell import validate_generated_xscr_via_shell

        def validator(path):
            return validate_generated_xscr_via_shell(path, restore_shell=True)

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
