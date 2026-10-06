"""Opentrons protocol -> fluentvibe protocol for a deck profile. No model.

The Opentrons protocol runs in the Opentrons simulator (a separate Python
with the ``opentrons`` package, ``.venv-opentrons`` by default); its steps
become a Bench Spec, the spec becomes Python for the deck (the skeleton
builder), which is compiled and simulated, and optionally inspected in
FluentControl. Used by ``fluentvibe opentrons`` and the web app.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
OPENTRONS_PYTHON_ENV = "FLUENTVIBE_OPENTRONS_PYTHON"


def default_opentrons_python() -> Path:
    """FLUENTVIBE_OPENTRONS_PYTHON, else ``.venv-opentrons`` in this checkout or
    an enclosing one (a worktree under ``.worktrees`` shares the main folder's)."""
    configured = os.environ.get(OPENTRONS_PYTHON_ENV, "").strip()
    if configured:
        return Path(configured)
    candidates = [folder / ".venv-opentrons" / "Scripts" / "python.exe" for folder in (REPO, *REPO.parents)]
    return next((c for c in candidates if c.exists()), candidates[0])


def convert_opentrons(
    protocol: Path,
    profile: Path,
    output: Path,
    *,
    fc_check: bool = False,
    fc_by: str = "human",
    opentrons_python: Optional[Path] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Convert ``protocol`` (an Opentrons ``.py`` or its folder) for ``profile``.

    Writes spec.json, spec.md, draft.py, draft.xscr and result.json into
    ``output``. Returns the summary: ``stage`` ("done" or where it stopped),
    ``error``, the spec's ``steps``, ``gate`` (compiled and simulated),
    ``todo_steps`` (steps left as TODO comments), the FluentControl verdict
    when checked, and the paths of what was written."""
    from .. import cancel
    from .bench_spec import spec_to_markdown, validate_bench_spec
    from .profile import PROFILE_DIR_ENV
    from .skeleton import DeckMismatch, OpenValues, build_skeleton, load_deck

    def note(message: str) -> None:
        if progress is not None:
            progress(message)

    protocol, profile, output = Path(protocol), Path(profile), Path(output)
    python = Path(opentrons_python or default_opentrons_python())
    started = time.monotonic()
    summary: dict[str, Any] = {"stage": "convert", "error": None, "protocol": str(protocol),
                               "output_dir": str(output)}

    def finish() -> dict[str, Any]:
        summary["total_s"] = round(time.monotonic() - started, 1)
        (output / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    output.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        summary["error"] = f"no Python with the opentrons package at {python} (see scripts/opentrons_to_spec.py)"
        return finish()
    if not protocol.exists():
        summary["error"] = f"Opentrons protocol not found: {protocol}"
        return finish()
    os.environ[PROFILE_DIR_ENV] = str(profile)

    note("running the protocol in the Opentrons simulator and reading its steps")
    proc = subprocess.Popen(
        [str(python), str(REPO / "scripts" / "opentrons_to_spec.py"), str(protocol), "--out", str(output), "--single"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    token = cancel.current()
    with (token.closing(proc.kill) if token is not None else nullcontext()):
        try:
            stdout, stderr = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            summary["error"] = "the Opentrons simulator did not finish within 600 s"
            return finish()
    if token is not None:
        token.check()  # Stop pressed: the simulator was killed
    rows = [line for line in stdout.splitlines() if line.startswith("{")]
    row = json.loads(rows[-1]) if rows else {"status": "error", "error": stderr.strip()[-300:]}
    if row.get("status") != "ok":
        summary["error"] = row.get("error") or "the Opentrons simulator reported no result"
        return finish()

    name = protocol.stem if protocol.is_file() else protocol.name
    raw = json.loads((output / f"{name}.json").read_text(encoding="utf-8"))
    raw.pop("_source", None)
    (output / "spec.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    spec, problems = validate_bench_spec(raw)
    if spec is None:
        summary.update(stage="spec", error="; ".join(p.message for p in problems))
        return finish()
    spec_md = output / "spec.md"
    spec_md.write_text(spec_to_markdown(spec, problems), encoding="utf-8")
    summary["spec_md"] = str(spec_md)
    summary["steps"] = [st.op for st in spec.steps]

    note(f"spec: {len(spec.steps)} steps; building the protocol for this deck")
    try:
        source = build_skeleton(spec, load_deck(profile))
    except (OpenValues, DeckMismatch, ValueError) as exc:
        summary.update(stage="skeleton", error=str(exc))
        return finish()
    draft = output / "draft.py"
    draft.write_text(source, encoding="utf-8")
    summary["draft"] = str(draft)
    summary["todo_steps"] = source.count('wt.add_comment("TODO')

    from .lab_scope import load_lab_scope
    from .tools import AuthoringToolRegistry

    note("compiling and simulating")
    registry = AuthoringToolRegistry(output_dir=output)
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    summary["gate"] = bool(gate.get("success"))
    if not summary["gate"]:
        summary.update(stage="gate", error=" ".join(str(gate.get("failure_message", "")).split())[:600])
        return finish()

    from .eval_rubric import build_worktable_from_source

    xscr = output / "draft.xscr"
    build_worktable_from_source(source, str(draft)).compile(xscr)
    summary["xscr"] = str(xscr)
    if fc_check:
        from .fc_feedback import check_in_fluentcontrol

        note("checking in FluentControl (InfoPad)")
        fc = check_in_fluentcontrol(xscr, source=source, source_file=str(draft), by=fc_by)
        summary["fc_ok"] = fc.get("ok")
        summary["fc_message"] = fc.get("message")
        summary["fc_findings"] = [f"{f['kind']}: {f['message'][:100]}" for f in fc.get("findings", [])]
    summary["stage"] = "done"
    return finish()
