"""Opentrons protocols as model tools: find, convert, read the draft, check it.

The deterministic converter (``opentrons_import.convert_opentrons``) writes the
protocol well by well for the active deck and reports what it could not do.
The model takes it from there (skill ``task-opentrons-conversion``): it fixes
the unconverted steps, compresses the code into loops and blocks, and checks
with ``opentrons_check_fidelity`` that every well still ends with the volume
it had in the Opentrons run.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .profile import PROFILE_DIR_ENV

OPENTRONS_TOOLS = frozenset({"opentrons_search", "opentrons_convert", "read_draft", "opentrons_check_fidelity"})
LIST_LIMIT = 40          # unconverted steps, notes, mismatches returned at most
READ_LIMIT = 200         # draft lines per read


def _entries():
    from ..protocol_index import load_index
    return load_index()


def opentrons_search(query: str, limit: int = 8) -> dict[str, Any]:
    from ..protocol_index import related_protocols, search

    entries = _entries()
    hits = search(entries, query, convertible_only=True)[: max(1, min(int(limit), 25))]
    related = related_protocols(entries)
    return {
        "ok": True,
        "count": len(hits),
        "protocols": [{
            "id": e.id, "title": e.title, "robot": e.robot, "description": e.description[:300],
            "steps": e.steps[:12], "labware": e.labware[:12], "modules": e.modules,
            "related": related.get(e.id, [])[:6],
        } for e in hits],
    }


def opentrons_convert(registry: Any, protocol: str) -> dict[str, Any]:
    from ..protocol_index import find
    from .opentrons_import import convert_opentrons

    entry = find(_entries(), protocol)
    path = Path(entry.path) if entry is not None else Path(protocol)
    if not path.exists():
        return {"ok": False, "category": "not_found",
                "message": f"No Opentrons protocol {protocol!r}: use an id from opentrons_search or a path."}
    profile = os.environ.get(PROFILE_DIR_ENV)
    if not profile:
        return {"ok": False, "category": "no_profile",
                "message": f"No deck profile active ({PROFILE_DIR_ENV} is not set)."}
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", entry.id if entry is not None else path.stem)
    output = Path(registry.output_dir or ".") / f"opentrons-{slug}"
    summary = convert_opentrons(path, Path(profile), output)
    result: dict[str, Any] = {
        "ok": summary.get("stage") == "done",
        "stage": summary.get("stage"),
        "error": summary.get("error"),
        "protocol": entry.id if entry is not None else str(path),
        "liquid_events": summary.get("liquid_events"),
    }
    loaded = _load_conversion(output)
    if loaded is None:
        return result
    trace, conversion, source = loaded
    registry.last_draft_source = source
    registry.opentrons_conversion = (trace, conversion)
    lines = source.splitlines()
    unconverted = list(summary.get("unconverted") or [])
    result.update(
        fidelity=summary.get("fidelity"),
        gate=summary.get("gate"),
        gate_error=summary.get("gate_error") or (summary.get("error") if summary.get("stage") == "gate" else None),
        draft_path=summary.get("draft"),
        draft_lines=len(lines),
        containers=summary.get("containers"),
        unconverted_count=len(unconverted),
        unconverted=unconverted[:LIST_LIMIT],
        substitutions=list(summary.get("substitutions") or [])[:LIST_LIMIT],
        # Where the work is: operator prompts and TODO comments the converter left.
        open_lines=[f"{i}: {ln.strip()[:160]}" for i, ln in enumerate(lines, 1)
                    if "TODO" in ln or "wt.user_prompt(" in ln][:LIST_LIMIT],
        next="Read the draft with read_draft, fix what is listed, then opentrons_check_fidelity.",
    )
    return result


@dataclass
class _Mapping:
    """What volume_fidelity needs of a Conversion: the well mapping and the fills."""
    mapping: dict[str, tuple[str, str]]
    fills: dict[tuple[str, str], float]


def _load_conversion(output: Path) -> tuple[dict, _Mapping, str] | None:
    try:
        trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
        conv = json.loads((output / "conversion.json").read_text(encoding="utf-8"))
        source = (output / "draft.py").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    mapping = {k: tuple(v) for k, v in conv["mapping"].items()}
    fills = {tuple(k.split("|", 1)): v for k, v in conv["fills"].items()}
    return trace, _Mapping(mapping, fills), source


def read_draft(registry: Any, start: int = 1, count: int = 120) -> dict[str, Any]:
    source = getattr(registry, "last_draft_source", None)
    if not source:
        return {"ok": False, "category": "no_draft", "message": "No draft yet."}
    lines = source.splitlines()
    start = max(1, int(start))
    count = max(1, min(int(count), READ_LIMIT))
    chunk = lines[start - 1: start - 1 + count]
    return {"ok": True, "total_lines": len(lines), "start": start,
            "text": "\n".join(f"{i}: {ln}" for i, ln in enumerate(chunk, start))}


def opentrons_check_fidelity(registry: Any, source: str | None = None) -> dict[str, Any]:
    """Simulate the draft and compare every well with the Opentrons run."""
    from .eval_rubric import build_worktable_from_source
    from .opentrons_faithful import volume_fidelity

    pair = getattr(registry, "opentrons_conversion", None)
    if pair is None:
        return {"ok": False, "category": "no_conversion", "message": "Run opentrons_convert first."}
    trace, conversion = pair
    source = source or getattr(registry, "last_draft_source", None)
    if not source:
        return {"ok": False, "category": "no_draft", "message": "No draft to check."}
    registry.last_draft_source = source
    try:
        wt = build_worktable_from_source(source, "opentrons_draft.py")
        wt.simulate(strict=True)
    except Exception as exc:  # noqa: BLE001 - the model needs the message, whatever it is
        return {"ok": False, "category": "simulation", "message": f"{type(exc).__name__}: {str(exc)[:600]}"}
    fidelity = volume_fidelity(trace, conversion, wt.simulation_report.final_labware)
    matching = fidelity["wells_matching"] == fidelity["wells_checked"]
    return {"ok": matching, "wells_checked": fidelity["wells_checked"],
            "wells_matching": fidelity["wells_matching"], "mismatches": fidelity["mismatches"][:LIST_LIMIT],
            "note": None if matching else
            "Containers keep the converter's labels and wells: the check maps Opentrons wells by them."}
