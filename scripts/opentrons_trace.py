"""Trace an Opentrons protocol well by well (run in the Opentrons environment).

    .venv-opentrons/Scripts/python.exe scripts/opentrons_trace.py <protocol.py or folder> --out trace.json

The protocol runs in the Opentrons simulator with its default parameters
(the same set-up as ``opentrons_to_spec.py``). Every executed command becomes
one event, in order: aspirate / dispense with the volume *per channel* and
the wells each channel touched (an 8-channel move at A1 is A1..H1; on a
one-row reservoir all eight channels draw from the same well), tip pick-up /
drop, comments, pauses, delays, module and labware-move commands. The
labware table names each labware (load name, display name, slot, wells in
order, rows, columns, capacity per well).

This is the ground truth for ``fluentvibe opentrons --faithful`` and for the
volume-fidelity check: what each well received and gave, from the Opentrons
run itself. Imports nothing from fluentvibe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opentrons_to_spec as ots  # noqa: E402  (shares the simulator set-up)

WASTE_KEY = "waste:trash"


def _labware_key(labware) -> str:
    slot = str(getattr(labware, "parent", "") or "")
    try:
        slot = labware.parent if isinstance(labware.parent, str) else str(labware.parent)
    except Exception:  # noqa: BLE001
        pass
    load_name = getattr(labware, "load_name", None) or getattr(getattr(labware, "_core", None), "load_name", "") or ""
    return f"{slot}:{load_name or labware}"


def _labware_info(labware) -> dict:
    wells = labware.wells()
    rows = labware.rows()
    try:
        capacity = float(wells[0].max_volume) if wells else 0.0
    except Exception:  # noqa: BLE001
        capacity = 0.0
    return {
        "load_name": getattr(labware, "load_name", "") or "",
        "display": str(getattr(labware, "name", "") or labware),
        "slot": str(getattr(labware, "parent", "")),
        "wells": [w.well_name for w in wells],
        "rows": len(rows),
        "columns": len(labware.columns()),
        "capacity_ul": capacity,
        "is_tiprack": bool(getattr(labware, "is_tiprack", False)),
    }


def _channels(instrument) -> int:
    for attr in ("active_channels", "channels"):
        try:
            value = int(getattr(instrument, attr))
            if value > 0:
                return value
        except Exception:  # noqa: BLE001  (APIVersionError below the attribute's API level)
            continue
    name = str(getattr(instrument, "name", "")).lower()
    return 96 if "96" in name else 8 if "multi" in name else 1


def _touched(well, channels: int) -> list[str]:
    """The wells the pipette's channels touch when its first channel is at ``well``.

    Returns well names; a name repeats when several channels share one well
    (a reservoir trough)."""
    if channels <= 1:
        return [well.well_name]
    labware = well.parent
    rows = labware.rows()
    if channels >= 96:
        if len(labware.wells()) >= 96:
            return [w.well_name for w in labware.wells()][:96]
        return [w.well_name for w in labware.wells() for _ in range(max(1, 96 // len(labware.wells())))][:96]
    # 8 channels down one column.
    if len(rows) == 1:
        return [well.well_name] * channels
    column = next((c for c in labware.columns() if any(w.well_name == well.well_name for w in c)), [well])
    start = next(i for i, w in enumerate(column) if w.well_name == well.well_name)
    step = 2 if len(column) >= 16 else 1  # 384 plate: every other row
    picked = column[start::step][:channels]
    return [w.well_name for w in picked] or [well.well_name]


def _walk(entries, parent_text: str = ""):
    """(entry, its parent's text) in run order. An air gap is logged as
    "Air gap of 5 uL" with an "Aspirating 5.0 uL" inside it: that aspirate is air."""
    for entry in entries:
        yield entry, parent_text
        text = " ".join(str((entry.get("payload") or {}).get("text", "")).split())
        yield from _walk(entry.get("subsequent", []) or [], text)


def trace(protocol: Path, hardware_dir: Path) -> dict:
    runlog, _captured = ots._simulate(protocol, hardware_dir)
    labware: dict[str, dict] = {}
    events: list[dict] = []
    for entry, parent_text in _walk(runlog):
        payload = entry.get("payload", {}) or {}
        text = " ".join(str(payload.get("text", "")).split())
        base = ots._event(entry)
        if parent_text.lower().startswith("air gap") and text.startswith("Aspirating"):
            continue  # the air gap's own aspirate: air, already counted by the "Air gap" entry
        kind = base.get("kind", "other")
        if text.startswith("Picking up tip"):
            kind = "pick_up_tip"
        elif text.startswith("Returning tip"):
            kind = "return_tip"  # back into the rack; a "parked" tip may still hold liquid
        elif text.startswith("Dropping tip"):
            kind = "drop_tip"
        elif text.lower().startswith("air gap"):
            kind = "air_gap"  # air drawn into the tip; the next dispense includes it
        event: dict = {"kind": kind, "text": text[:300]}
        if kind in ("pick_up_tip", "drop_tip", "return_tip") and payload.get("instrument") is not None:
            event["channels"] = _channels(payload["instrument"])  # which pipette: 96-channel -> MCA
        if kind in ("aspirate", "dispense"):
            location = payload.get("location")
            well = getattr(location, "labware", None)
            well = getattr(well, "as_well", lambda: well)() if well is not None and not hasattr(well, "well_name") else well
            instrument = payload.get("instrument")
            channels = _channels(instrument) if instrument is not None else 1
            volume = payload.get("volume", base.get("volume"))
            if well is None or not hasattr(well, "well_name"):
                if kind == "dispense" and re.search(r"waste chute|trash", text, re.I):
                    # Flex waste chute / trash bin: no well, but a place: the waste.
                    labware.setdefault(WASTE_KEY, {"load_name": "waste", "display": "Waste", "slot": "",
                                                   "wells": ["A1"], "rows": 1, "columns": 1,
                                                   "capacity_ul": 1e9, "is_tiprack": False})
                    event.update(labware=WASTE_KEY, wells=["A1"] * channels)
                else:
                    event["unresolved"] = True
            else:
                key = _labware_key(well.parent)
                labware.setdefault(key, _labware_info(well.parent))
                event.update(labware=key, wells=_touched(well, channels))
            event.update(volume=float(volume) if volume is not None else None, channels=channels,
                         pipette=str(getattr(instrument, "name", "")))
        elif kind == "air_gap":
            m = ots.VOL_RE.search(text) or ots.re.search(r"(?P<vol>\d+(?:\.\d+)?)\s*u[lL]", text)
            event["volume"] = float(m.group("vol")) if m else 0.0
        elif kind == "delay":
            event["seconds"] = base.get("seconds", 0.0)
        elif kind == "pause":
            event["message"] = base.get("message", "")
        elif kind == "device":
            event["temps"] = base.get("temps", [])
        if kind == "other" and not text:
            continue
        events.append(event)
    source = protocol.read_text(encoding="utf-8", errors="replace")
    facts = {k: v for k, v in (("protocolName", ots.re.search(r"""['"]protocolName['"]\s*:\s*['"]([^'"]+)""", source)),)
             if v}
    return {
        "protocol": str(protocol),
        "name": facts["protocolName"].group(1) if "protocolName" in facts else protocol.stem,
        "labware": labware,
        "events": events,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", type=Path, help="Opentrons protocol .py or its folder")
    ap.add_argument("--out", type=Path, required=True, help="trace JSON to write")
    args = ap.parse_args()
    protocol = args.protocol
    if protocol.is_dir():
        protocol = next(iter(sorted(protocol.glob("*.py"))), None)
        if protocol is None:
            print(json.dumps({"status": "error", "error": f"no .py protocol in {args.protocol}"}))
            return 1
    try:
        data = trace(protocol, args.out.parent)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500],
                          "traceback": traceback.format_exc()[-1500:]}))
        return 1
    args.out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    moves = sum(1 for e in data["events"] if e["kind"] in ("aspirate", "dispense"))
    print(json.dumps({"status": "ok", "events": len(data["events"]), "liquid_events": moves,
                      "labware": len(data["labware"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
