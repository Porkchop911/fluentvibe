"""Opentrons protocol -> fluentvibe protocol for a deck profile. No model.

The Opentrons protocol runs in the Opentrons simulator (a separate Python
with the ``opentrons`` package, ``.venv-opentrons`` by default). Two ways on:

* **well by well** (default): ``scripts/opentrons_trace.py`` records every
  aspirate and dispense with its wells and volume; ``opentrons_faithful``
  writes a protocol that moves the same volumes between the same wells in
  the same order (labware mapped to the deck), and the result is checked
  well by well against the Opentrons run (volume fidelity);
* **via a Bench Spec** (``faithful=False``): the run is summarised into spec
  steps and the skeleton builder writes block-based Python. Shorter code,
  but it loses which liquid goes into which wells (a buffer added to one
  tube became sample moved into a plate).

Either way the protocol is compiled and simulated, and optionally inspected
in FluentControl. Used by ``fluentvibe opentrons``, VS Code and the web app.
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
# Larger runs are refused: one step per liquid event plus a simulated deck
# per step; the machine shares its memory with the model server.
MAX_LIQUID_EVENTS = 6000


def default_opentrons_python() -> Path:
    """FLUENTVIBE_OPENTRONS_PYTHON, else ``.venv-opentrons`` in this checkout or
    an enclosing one (a worktree under ``.worktrees`` shares the main folder's)."""
    configured = os.environ.get(OPENTRONS_PYTHON_ENV, "").strip()
    if configured:
        return Path(configured)
    candidates = [folder / ".venv-opentrons" / "Scripts" / "python.exe" for folder in (REPO, *REPO.parents)]
    return next((c for c in candidates if c.exists()), candidates[0])


def _run_opentrons(python: Path, args: list[str], timeout_s: float = 900) -> tuple[dict, str]:
    """Run a script in the Opentrons Python; its last JSON line and stderr.
    Stop (the job's cancel token) kills it."""
    from .. import cancel

    proc = subprocess.Popen(
        [str(python), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    token = cancel.current()
    with (token.closing(proc.kill) if token is not None else nullcontext()):
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {"status": "error", "error": f"the Opentrons simulator did not finish within {timeout_s:g} s"}, ""
    if token is not None:
        token.check()
    rows = [line for line in stdout.splitlines() if line.startswith("{")]
    row = json.loads(rows[-1]) if rows else {"status": "error", "error": stderr.strip()[-300:]}
    return row, stderr


def convert_opentrons(
    protocol: Path,
    profile: Path,
    output: Path,
    *,
    faithful: bool = True,
    review: bool = False,
    review_client: Any = None,
    fc_check: bool = False,
    fc_by: str = "human",
    opentrons_python: Optional[Path] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Convert ``protocol`` (an Opentrons ``.py`` or its folder) for ``profile``.

    Writes draft.py, draft.xscr and result.json into ``output`` (plus
    trace.json and conversion.json well by well, or spec.json / spec.md via a
    spec). Returns the summary: ``stage`` ("done" or where it stopped),
    ``error``, ``gate`` (compiled and simulated), ``fidelity`` (well by well:
    wells whose volume change matches the Opentrons run), ``unconverted``
    (what stayed an operator step or comment), the FluentControl verdict when
    checked, and the paths of what was written."""
    from .profile import PROFILE_DIR_ENV

    def note(message: str) -> None:
        if progress is not None:
            progress(message)

    protocol, profile, output = Path(protocol), Path(profile), Path(output)
    python = Path(opentrons_python or default_opentrons_python())
    started = time.monotonic()
    summary: dict[str, Any] = {"stage": "convert", "error": None, "protocol": str(protocol),
                               "output_dir": str(output), "mode": "well by well" if faithful else "spec"}

    def finish() -> dict[str, Any]:
        summary.pop("_trace", None)
        summary.pop("_conversion", None)
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

    note("running the protocol in the Opentrons simulator")
    if faithful:
        source = _well_by_well(python, protocol, profile, output, summary, note)
    else:
        source = _via_spec(python, protocol, profile, output, summary, note)
    if source is None:
        return _finish_with_review(protocol, summary, review, review_client, note, finish)
    draft = output / "draft.py"
    draft.write_text(source, encoding="utf-8")
    summary["draft"] = str(draft)

    from .lab_scope import load_lab_scope
    from .tools import AuthoringToolRegistry

    note("compiling and simulating")
    registry = AuthoringToolRegistry(output_dir=output)
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    summary["gate"] = bool(gate.get("success"))
    if not summary["gate"]:
        summary.update(stage="gate", error=" ".join(str(gate.get("failure_message", "")).split())[:600])
        return _finish_with_review(protocol, summary, review, review_client, note, finish)

    from .eval_rubric import build_worktable_from_source

    wt = build_worktable_from_source(source, str(draft))
    if faithful:
        from .opentrons_faithful import volume_fidelity

        wt.simulate(strict=True)
        fidelity = volume_fidelity(summary.pop("_trace"), summary.pop("_conversion"),
                                   wt.simulation_report.final_labware)
        summary["fidelity"] = fidelity
        note(f"well by well: {fidelity['wells_matching']}/{fidelity['wells_checked']} wells match the Opentrons run")
    xscr = output / "draft.xscr"
    wt.compile(xscr)
    summary["xscr"] = str(xscr)
    if fc_check:
        from .fc_feedback import check_in_fluentcontrol

        note("checking in FluentControl (InfoPad)")
        fc = check_in_fluentcontrol(xscr, source=source, source_file=str(draft), by=fc_by)
        summary["fc_ok"] = fc.get("ok")
        summary["fc_message"] = fc.get("message")
        summary["fc_findings"] = [f"{f['kind']}: {f['message'][:100]}" for f in fc.get("findings", [])]
    summary["stage"] = "done"
    return _finish_with_review(protocol, summary, review, review_client, note, finish)


def _finish_with_review(protocol, summary, review, client, note, finish):
    """Strata's second opinion on a finished (or stopped) conversion."""
    if review:
        from .opentrons_review import review_conversion

        note("Strata is reviewing the conversion against the Opentrons protocol")
        summary["review"] = review_conversion(protocol, summary, client=client)
    return finish()


def _well_by_well(python, protocol, profile, output, summary, note) -> Optional[str]:
    from .opentrons_faithful import DoesNotFit, convert_trace
    from .skeleton import load_deck

    trace_path = output / "trace.json"
    row, _ = _run_opentrons(python, [str(REPO / "scripts" / "opentrons_trace.py"), str(protocol),
                                     "--out", str(trace_path)])
    if row.get("status") != "ok":
        summary["error"] = row.get("error") or "the Opentrons simulator reported no result"
        return None
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    summary["name"] = trace.get("name")
    summary["liquid_events"] = row.get("liquid_events")
    if (row.get("liquid_events") or 0) > MAX_LIQUID_EVENTS:
        summary.update(stage="trace", error=f"{row.get('liquid_events')} aspirate/dispense events: more than "
                                            f"{MAX_LIQUID_EVENTS}, too large to convert step by step here")
        return None
    if not row.get("liquid_events"):
        summary.update(stage="trace", error="the Opentrons run moved no liquid with its default settings "
                                            "(it probably needs an input file or other settings); nothing to convert")
        return None
    note(f"{row.get('liquid_events')} aspirate/dispense events; writing the protocol for this deck")
    try:
        conversion = convert_trace(trace, load_deck(profile))
    except DoesNotFit as exc:
        summary.update(stage="deck", error=f"does not fit the deck: {exc}")
        return None
    (output / "conversion.json").write_text(json.dumps(
        {"report": conversion.report, "mapping": conversion.mapping,
         "fills": {f"{k[0]}|{k[1]}": v for k, v in conversion.fills.items()}}, indent=1), encoding="utf-8")
    summary.update(unconverted=conversion.report["unconverted"],
                   kept_pauses_and_waits=conversion.report["kept_pauses_and_waits"],
                   substitutions=conversion.report["substitutions"],
                   containers=conversion.report["containers"],
                   fca_tips_used=conversion.report["fca_tips_used"],
                   _trace=trace, _conversion=conversion)
    return conversion.source


def _via_spec(python, protocol, profile, output, summary, note) -> Optional[str]:
    from .bench_spec import spec_to_markdown, validate_bench_spec
    from .skeleton import DeckMismatch, OpenValues, build_skeleton, load_deck

    row, _ = _run_opentrons(python, [str(REPO / "scripts" / "opentrons_to_spec.py"), str(protocol),
                                     "--out", str(output), "--single"])
    if row.get("status") != "ok":
        summary["error"] = row.get("error") or "the Opentrons simulator reported no result"
        return None
    name = protocol.stem if protocol.is_file() else protocol.name
    raw = json.loads((output / f"{name}.json").read_text(encoding="utf-8"))
    raw.pop("_source", None)
    (output / "spec.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    spec, problems = validate_bench_spec(raw)
    if spec is None:
        summary.update(stage="spec", error="; ".join(p.message for p in problems))
        return None
    spec_md = output / "spec.md"
    spec_md.write_text(spec_to_markdown(spec, problems), encoding="utf-8")
    summary["spec_md"] = str(spec_md)
    summary["steps"] = [st.op for st in spec.steps]
    note(f"spec: {len(spec.steps)} steps; building the protocol for this deck")
    try:
        source = build_skeleton(spec, load_deck(profile))
    except (OpenValues, DeckMismatch, ValueError) as exc:
        summary.update(stage="skeleton", error=str(exc))
        return None
    summary["todo_steps"] = source.count('wt.add_comment("TODO')
    return source
