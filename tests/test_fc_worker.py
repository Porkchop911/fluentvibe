"""The FluentControl check runs its UI automation in a child process (fc_worker).

No test here starts FluentControl: subprocess.run and the shell validator are
replaced.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from fluentvibe.authoring import fc_feedback, fc_worker, fluentcontrol_shell
from fluentvibe.authoring.fc_feedback import check_in_fluentcontrol


def _setup(tmp_path, monkeypatch, run):
    lock = tmp_path / "fc.lock"
    shell = tmp_path / "shell.xscr"
    shell.write_text("ORIGINAL", encoding="utf-8")
    monkeypatch.setattr(fc_feedback, "_LOCK", lock)
    monkeypatch.setattr(fc_feedback, "fluentcontrol_available", lambda: (True, ""))
    monkeypatch.setattr(fluentcontrol_shell, "DEFAULT_SHELL_XSCR", shell)
    monkeypatch.setattr(fluentcontrol_shell, "read_xscr_text", lambda p: p.read_text(encoding="utf-8"))
    monkeypatch.setattr(fluentcontrol_shell, "write_xscr_text", lambda p, t: p.write_text(t, encoding="utf-8"))
    monkeypatch.setattr(fc_feedback.subprocess, "run", run)
    return lock, shell


def _done(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_parent_parses_the_worker_result_and_holds_the_lock(tmp_path, monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"], seen["timeout"] = cmd, kwargs["timeout"]
        seen["locked"] = lock.exists()
        result = {"opened": True, "load_failed": False, "load_error_text": "",
                  "error_lines": ["012: Labware name already exists"]}
        return _done("noise from pywinauto\n" + fc_worker.RESULT_PREFIX + json.dumps(result) + "\n")

    lock, _ = _setup(tmp_path, monkeypatch, run)
    monkeypatch.setenv(fc_feedback.FC_CHECK_TIMEOUT_ENV, "42")
    out = check_in_fluentcontrol(tmp_path / "x.xscr")
    assert seen["locked"] and not lock.exists()
    assert seen["cmd"][1:3] == ["-m", "fluentvibe.authoring.fc_worker"] and seen["timeout"] == 42.0
    assert out["available"] is True and out["ok"] is False and out["error_count"] == 1
    assert out["findings"][0]["kind"] == "duplicate_labware_name"


def test_clean_worker_result_is_ok(tmp_path, monkeypatch):
    result = {"opened": True, "load_failed": False, "load_error_text": "", "error_lines": []}
    _setup(tmp_path, monkeypatch, lambda cmd, **kw: _done(fc_worker.RESULT_PREFIX + json.dumps(result)))
    out = check_in_fluentcontrol(tmp_path / "x.xscr")
    assert out["ok"] is True and out["available"] is True


def test_timeout_kills_the_worker_and_restores_the_shell(tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        shell.write_text("PATCHED BY A KILLED WORKER", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    lock, shell = _setup(tmp_path, monkeypatch, run)
    out = check_in_fluentcontrol(tmp_path / "x.xscr")
    assert out["ok"] is None and out["available"] is False
    assert out["message"].startswith("FluentControl check could not run:") and "killed" in out["message"]
    assert shell.read_text(encoding="utf-8") == "ORIGINAL" and not lock.exists()


def test_real_timeout_kills_a_hanging_child(tmp_path, monkeypatch):
    real_run = subprocess.run

    def run(cmd, **kwargs):  # a child that hangs instead of the worker
        return real_run([cmd[0], "-c", "import time; time.sleep(60)"], **kwargs)

    _setup(tmp_path, monkeypatch, run)
    monkeypatch.setenv(fc_feedback.FC_CHECK_TIMEOUT_ENV, "1")
    out = check_in_fluentcontrol(tmp_path / "x.xscr")
    assert out["available"] is False and "within 1 s" in out["message"]


def test_crash_or_bad_output_is_not_a_protocol_failure(tmp_path, monkeypatch):
    cases = [
        _done("", returncode=3, stderr="Traceback...\nCOM initialisation failed: boom\n"),
        _done("no result line at all\n"),
        _done(fc_worker.RESULT_PREFIX + "{not json\n"),
    ]
    messages = []
    for case in cases:
        _, shell = _setup(tmp_path, monkeypatch, lambda cmd, _c=case, **kw: _c)
        out = check_in_fluentcontrol(tmp_path / "x.xscr")
        assert out["ok"] is None and out["available"] is False, case
        assert shell.read_text(encoding="utf-8") == "ORIGINAL"
        messages.append(out["message"])
    assert "code 3" in messages[0] and "COM initialisation failed: boom" in messages[0]


def test_worker_prints_one_result_line(monkeypatch, capsys, tmp_path):
    ui = fluentcontrol_shell.UiResult(opened=True, infopad_lines=["x"], error_lines=["5: Select wells."])

    def validate(path, **kwargs):
        print("stray print")  # goes to stderr, not into the result
        assert kwargs == {"restore_shell": True}
        return ui

    monkeypatch.setattr(fc_worker, "_init_com", lambda: None)
    monkeypatch.setattr(fluentcontrol_shell, "validate_generated_xscr_via_shell", validate)
    assert fc_worker.main([str(tmp_path / "x.xscr")]) == 0
    out = capsys.readouterr()
    assert "stray print" in out.err
    (line,) = out.out.splitlines()
    data = json.loads(line[len(fc_worker.RESULT_PREFIX):])
    assert data == {"opened": True, "load_failed": False, "load_error_text": "", "error_lines": ["5: Select wells."]}


def test_worker_exits_nonzero_when_com_fails(monkeypatch, capsys):
    def broken():
        raise OSError("no apartment")

    monkeypatch.setattr(fc_worker, "_init_com", broken)
    assert fc_worker.main(["x.xscr"]) == 3
    assert "no apartment" in capsys.readouterr().err


def test_injected_validator_stays_in_process(tmp_path, monkeypatch):
    def run(*a, **kw):
        raise AssertionError("no child process with an injected validator")

    monkeypatch.setattr(fc_feedback.subprocess, "run", run)
    monkeypatch.setattr(fc_feedback, "_LOCK", tmp_path / "fc.lock")
    out = check_in_fluentcontrol(tmp_path / "x.xscr",
                                 validator=lambda p: SimpleNamespace(opened=True, load_failed=False, error_lines=[]))
    assert out["ok"] is True


def test_a_lock_left_by_a_killed_check_is_taken_over_at_once(tmp_path, monkeypatch):
    """Cancel in the editor kills the CLI and its worker: their lock must not
    block the next check for 15 minutes."""
    import subprocess
    import sys
    import time

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    lock = tmp_path / "fc.lock"
    lock.write_text(str(dead.pid), encoding="utf-8")
    monkeypatch.setattr(fc_feedback, "_LOCK", lock)
    started = time.monotonic()
    with fc_feedback._fluentcontrol_lock(timeout_s=30):
        assert lock.read_text(encoding="utf-8") == str(__import__("os").getpid())
    assert time.monotonic() - started < 5
