"""Child process for one FluentControl check (``fc_feedback.check_in_fluentcontrol``).

The UI automation (pywinauto / UI Automation over COM) runs here, not in the
web server: in the server's job thread it stalled the job polling for seconds.
Usage: ``python -m fluentvibe.authoring.fc_worker <draft.xscr> [human|agent]``
(default agent). Prints one JSON line (prefixed with ``RESULT_PREFIX``) on
stdout; exits non-zero with a message on stderr when the check itself fails.
The parent holds the lock and enforces the timeout. The shell keeps the
inspected script (it is never restored; the patch itself is atomic).
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

RESULT_PREFIX = "FC_WORKER_RESULT "


def _init_com() -> None:
    # pywinauto's "uia" backend talks COM; initialise this thread explicitly
    # (single-threaded apartment, as comtypes would on import).
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except ImportError:
        import comtypes

        comtypes.CoInitialize()


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2) or (len(argv) == 2 and argv[1] not in ("human", "agent")):
        print("usage: python -m fluentvibe.authoring.fc_worker <draft.xscr> [human|agent]", file=sys.stderr)
        return 2
    try:
        _init_com()
    except Exception as exc:
        print(f"COM initialisation failed: {exc}", file=sys.stderr)
        return 3
    try:
        from .fluentcontrol_shell import validate_generated_xscr_via_shell

        # Stray prints from the automation must not corrupt the result line.
        with contextlib.redirect_stdout(sys.stderr):
            ui = validate_generated_xscr_via_shell(Path(argv[0]), by=argv[1] if len(argv) == 2 else "agent")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    result = {
        "opened": bool(ui.opened),
        "load_failed": bool(ui.load_failed),
        "load_error_text": ui.load_error_text or "",
        "error_lines": list(ui.error_lines or []),
    }
    print(RESULT_PREFIX + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
