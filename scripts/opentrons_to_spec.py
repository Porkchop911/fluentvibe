"""Convert Opentrons protocols into draft Bench Specs via the Opentrons simulator.

Run with the isolated environment (it needs the ``opentrons`` package, which is
kept out of fluentvibe's own environment):

    .venv-opentrons/Scripts/python.exe scripts/opentrons_to_spec.py D:/Opentron_protocols/ptcl-1111 \
        --out build/eval/opentrons-specs

Each protocol is simulated with its default parameters; the executed-command
log is split into sections at the protocol's own comments and pauses, and each
section becomes one spec step:

* pauses → ``manual`` (off-deck operator step);
* thermocycler / heater-shaker / temperature-module programs → ``incubate``
  ``off_deck`` with the temperatures and times;
* magnet engage / plate moves onto a magnetic block together with bead or
  ethanol handling → ``bead_cleanup``;
* liquid handling → ``add`` (reservoir/tube → plate), ``transfer`` (plate →
  plate), or ``pool`` (many wells → one well), with the per-well volume.

The result is a *draft* in the Bench Spec JSON shape (``fluentvibe
spec``-compatible); validate it with ``fluentvibe.authoring.bench_spec`` and
review it before use. This script deliberately imports nothing from
fluentvibe so it can run in the Opentrons environment.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import statistics
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

LOC_RE = re.compile(r"(?P<well>[A-P]\d{1,2}) of (?P<labware>.+?) on (?:slot |)(?P<slot>[A-D]?\d+|[^ ]+)")
VOL_RE = re.compile(r"(?P<vol>\d+(?:\.\d+)?) uL")
TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?) ?°?\s?C")
SECONDS_RE = re.compile(r"(\d+) minutes? and (\d+(?:\.\d+)?) seconds")
BEAD_WORDS = re.compile(r"bead|ampure|axp|spri|magnet", re.IGNORECASE)
ETHANOL_WORDS = re.compile(r"ethanol|etoh", re.IGNORECASE)
RESERVOIR_WORDS = re.compile(r"reservoir|trough|tube|rack|block|reagent", re.IGNORECASE)
# Module commands (not liquid handling that merely happens on a module).
DEVICE_START = re.compile(
    r"(Setting (Temperature Module|Thermocycler|Heater-Shaker|block|lid)|Thermocycler|Heater-Shaker|"
    r"Opening|Closing|Shaking|Deactivating|Executing profile|Setting the (block|lid)|Heating|Cooling)",
    re.IGNORECASE,
)
# A comment is a section heading when it reads like words, not a debug print.
HEADING = re.compile(r"^[~\-=*#\s]*(step|steps|add|addition|wash|elut|bind|remov|pool|incubat|transfer|"
                     r"clean|end.?prep|ligat|barcod|repair|prim|load|dilut|prepar|normal|beads|protocol)",
                     re.IGNORECASE)


_GET_VALUES_STUB = """
import ast as _ast
_FV_DEFAULTS = {defaults!r}
_FV_MOUNTS = iter(["left", "right"] * 4)


def _fv_guess(name):
    low = name.lower()
    if "mount" in low:
        return next(_FV_MOUNTS)
    if "tip" in low and ("well" in low or "start" in low):
        return "A1"
    if low.startswith(("num", "n_", "number")) or "count" in low or "samp" in low and "vol" not in low:
        return 8
    if "vol" in low or low.endswith("_ul"):
        return 20
    if "time" in low or "min" in low or "sec" in low:
        return 1
    if low.startswith(("do_", "use_", "is_", "include", "skip", "dry_run")):
        return False
    if "csv" in low or "file" in low:
        return ""
    return 1


def get_values(*names):
    return [_FV_DEFAULTS[n] if n in _FV_DEFAULTS else _fv_guess(n) for n in names]
"""


def _commented_defaults(source: str) -> dict:
    """``# name = <literal>`` lines (Protocol Library protocols keep their
    default parameters as commented assignments next to ``get_values``)."""
    import ast

    defaults = {}
    for match in re.finditer(r"^\s*#\s*([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", source, re.M):
        try:
            defaults.setdefault(match.group(1), ast.literal_eval(match.group(2)))
        except (ValueError, SyntaxError):
            continue
    return defaults


def _simulate(protocol: Path):
    from opentrons import simulate

    source = protocol.read_text(encoding="utf-8")
    if "get_values(" in source and "def get_values" not in source:
        stub = _GET_VALUES_STUB.format(defaults=_commented_defaults(source))
        # Keep `from __future__` imports first.
        lines = source.splitlines(keepends=True)
        head = [line for line in lines if line.startswith("from __future__")]
        rest = [line for line in lines if not line.startswith("from __future__")]
        source = "".join(head) + stub + "".join(rest)
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        runlog, _bundle = simulate.simulate(
            io.StringIO(source), file_name=protocol.name, custom_labware_paths=[str(protocol.parent)],
        )
    return runlog, captured.getvalue()


def _flatten(entries, out=None):
    out = [] if out is None else out
    for entry in entries:
        out.append(entry)
        _flatten(entry.get("subsequent", []), out)
    return out


def _event(entry) -> dict:
    payload = entry.get("payload", {})
    text = " ".join(str(payload.get("text", "")).split())
    event = {"text": text}
    if text.startswith("Aspirating") or text.startswith("Dispensing"):
        event["kind"] = "aspirate" if text.startswith("Aspirating") else "dispense"
        vol = VOL_RE.search(text)
        loc = LOC_RE.search(text)
        event["volume"] = float(vol.group("vol")) if vol else None
        if loc:
            event["well"], event["labware"] = loc.group("well"), loc.group("labware")
    elif text.startswith("Mixing"):
        event["kind"] = "mix"
    elif text.startswith("Pausing"):
        event["kind"] = "pause"
        event["message"] = text.split(":", 1)[-1].strip()
    elif text.startswith("Delaying"):
        event["kind"] = "delay"
        m = SECONDS_RE.search(text)
        event["seconds"] = (int(m.group(1)) * 60 + float(m.group(2))) if m else 0.0
    elif re.match(r"(Engaging|Disengaging) Magnetic", text) or ("magnetic" in text.lower() and text.startswith("Moving")):
        event["kind"] = "magnet"
    elif DEVICE_START.match(text):
        event["kind"] = "device"
        event["temps"] = [float(t) for t in TEMP_RE.findall(text)]
    elif text.startswith("Moving labware") or text.startswith("Moving ") and " to " in text and " of " not in text:
        event["kind"] = "move"
    elif text and not re.match(r"(Picking up|Dropping|Returning|Moving to|Configure|Touching|Blowing|Air gap|Homing)", text):
        event["kind"] = "comment"
    else:
        event["kind"] = "other"
    return event


def _sections(events):
    """Split at comments (section titles) and pauses."""
    sections, current = [], {"title": "Start", "events": []}
    for event in events:
        if event["kind"] == "comment" and HEADING.match(event["text"]) and " of " not in event["text"]:
            title = event["text"].strip("-~*# \n")
            if current["events"]:
                sections.append(current)
                current = {"title": title, "events": []}
            else:
                current["title"] = title
            continue
        if event["kind"] == "pause":
            if current["events"]:
                sections.append(current)
            sections.append({"title": event.get("message") or "Pause", "events": [event]})
            current = {"title": "After pause", "events": []}
            continue
        current["events"].append(event)
    if current["events"]:
        sections.append(current)
    return sections


def _cleanup_parameters(liquid) -> dict:
    """Bead volume, washes, wash and elution volume from a clean-up section.

    Reads the moves into the plate that receives the most liquid (the clean-up
    plate): the first reservoir addition is the beads, a volume added again and
    again is the wash, the last addition is the elution buffer.
    """
    pairs, last = [], None
    for e in liquid:
        if e["kind"] == "aspirate":
            last = e.get("labware")
        elif e["kind"] == "dispense" and last and e.get("labware") and last != e["labware"]:
            pairs.append((last, e["labware"], e.get("well"), e.get("volume")))
    if not pairs:
        return {}
    # The clean-up plate: the most-dispensed destination that is not a
    # reservoir or waste (supernatant often goes back into a reservoir well).
    candidates = [p[1] for p in pairs if not re.search(r"reservoir|trough|waste|trash", p[1], re.I)]
    plate = Counter(candidates or [p[1] for p in pairs]).most_common(1)[0][0]
    additions = [p for p in pairs if p[1] == plate and p[3]]
    if not additions:
        return {}
    first_well = additions[0][2]
    per_well = [p[3] for p in additions if p[2] == first_well]
    if not per_well:
        return {}
    out: dict = {"volume_ul": round(per_well[0], 2)}
    counts = Counter(round(v, 1) for v in per_well[1:-1])
    if counts:
        wash, times = counts.most_common(1)[0]
        if times >= 1 and wash > 0:
            out["wash_ul"] = wash
            out["washes"] = times
    if len(per_well) > 1:
        out["elute_ul"] = round(per_well[-1], 2)
    return out


def _classify(section, step_id: str) -> dict | None:
    events = section["events"]
    kinds = Counter(e["kind"] for e in events)
    title = section["title"][:120]
    if kinds.get("pause") and len(events) == 1:
        return {"id": step_id, "op": "manual", "text": title, "location": "manual"}
    device = [e for e in events if e["kind"] == "device"]
    liquid = [e for e in events if e["kind"] in ("aspirate", "dispense")]
    if device and not liquid:
        temps = sorted({t for e in device for t in e.get("temps", [])})
        return {"id": step_id, "op": "incubate", "text": f"{title} ({'; '.join(e['text'] for e in device)[:160]})",
                "location": "off_deck", "temp_c": temps}
    if not liquid:
        delays = [e["seconds"] for e in events if e["kind"] == "delay" and e.get("seconds", 0) >= 60]
        if delays:
            return {"id": step_id, "op": "incubate", "text": title, "location": "deck",
                    "minutes": [round(sum(delays) / 60, 1)]}
        return None
    text_blob = " ".join([title] + [e.get("labware", "") for e in liquid])
    if kinds.get("magnet") or (BEAD_WORDS.search(text_blob) and ETHANOL_WORDS.search(text_blob)):
        return {"id": step_id, "op": "bead_cleanup", "text": title, "location": "deck",
                **_cleanup_parameters(liquid)}
    # Pair each dispense with the aspirate before it; a pair within one labware
    # is mixing (helper functions mix by aspirate/dispense), not a transfer.
    pairs, last_src = [], None
    for e in liquid:
        if e["kind"] == "aspirate":
            last_src = (e.get("labware"), e.get("well"))
        elif e["kind"] == "dispense" and last_src and last_src[0] and e.get("labware"):
            pairs.append((last_src[0], last_src[1], e["labware"], e.get("well"), e.get("volume")))
    moves = [p for p in pairs if p[0] != p[2]]
    if not moves:
        return None  # mixing only
    src = Counter(p[0] for p in moves).most_common(1)[0][0]
    dst = Counter(p[2] for p in moves).most_common(1)[0][0]
    route = [p for p in moves if p[0] == src and p[2] == dst]
    dst_wells = {p[3] for p in route}
    src_wells = {p[1] for p in route}
    volumes = [p[4] for p in route if p[4]]
    per_well = round(statistics.median(volumes), 2) if volumes else None
    if len(dst_wells) == 1 and len(src_wells) > 1:
        op = "pool"
    elif RESERVOIR_WORDS.search(src) and not RESERVOIR_WORDS.search(dst):
        op = "add"
    else:
        op = "transfer"
    return {"id": step_id, "op": op, "text": f"{title} ({src} -> {dst})", "location": "deck",
            "volume_ul": per_well, "_route": (src, dst)}


def _merge(steps: list[dict]) -> list[dict]:
    """Merge adjacent steps that are the same operation on the same labware
    (helper functions print many sub-headings), fold short deck delays into
    the step before, and renumber."""
    merged: list[dict] = []
    for step in steps:
        prev = merged[-1] if merged else None
        key = (step["op"], step["location"], step.get("_route"))
        if prev is not None and (prev["op"], prev["location"], prev.get("_route")) == key and step["op"] != "manual":
            if step.get("temp_c"):
                prev["temp_c"] = sorted(set(prev.get("temp_c", [])) | set(step["temp_c"]))
            continue
        if step["op"] == "incubate" and step["location"] == "deck" and prev is not None                 and sum(step.get("minutes", [0])) < 2:
            continue
        merged.append(step)
    for i, step in enumerate(merged, 1):
        step["id"] = f"s{i}"
        step.pop("_route", None)
    return merged


def convert(protocol_dir: Path) -> dict:
    protocol = next((p for p in sorted(protocol_dir.glob("*.py"))), None)
    if protocol is None:
        raise FileNotFoundError(f"no .py protocol in {protocol_dir}")
    runlog, printed = _simulate(protocol)
    events = [_event(e) for e in _flatten(runlog)]
    # Classify sections, then re-classify runs of adjacent sections that are the
    # same kind of step (e.g. a clean-up split over beads / wash / elution
    # headings) as one section, so their parameters are read together.
    classified = []
    for section in _sections(events):
        step = _classify(section, "s")
        if step is None:
            continue
        key = (step["op"], step["location"], step.get("_route"))
        if classified and classified[-1][0] == key and step["op"] not in ("manual", "incubate"):
            classified[-1][1]["events"].extend(section["events"])
        else:
            classified.append((key, {"title": section["title"], "events": list(section["events"])}))
    steps = [s for s in (_classify(sec, "s") for _, sec in classified) if s is not None]
    steps = _merge(steps)
    readme = protocol_dir / "README.md"
    title = protocol.stem
    if readme.exists():
        first = next((line.strip("# ").strip() for line in readme.read_text(encoding="utf-8", errors="replace").splitlines()
                      if line.startswith("#")), None)
        title = first or title
    wells = defaultdict(set)
    for e in events:
        if e.get("labware") and e["kind"] == "dispense":
            wells[e["labware"]].add(e["well"])
    sample_count = max((len(w) for w in wells.values()), default=1)
    return {
        "title": title,
        "sample_count": max(sample_count, 1),
        "sample_volume_ul": None,
        "reagents": [],
        "steps": steps,
        "notes": [
            f"Draft converted from Opentrons protocol {protocol_dir.name}/{protocol.name} via the Opentrons "
            "simulator (default parameters). Reagents are not identified; review ops, volumes and locations.",
        ],
        "_source": {"protocol": str(protocol), "log_events": len(events), "printed": printed[:2000]},
    }


def _convert_one(directory: Path, out: Path) -> dict:
    row = {"protocol": directory.name}
    try:
        spec = convert(directory)
        (out / f"{directory.name}.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        row.update(status="ok", steps=len(spec["steps"]),
                   ops=dict(Counter(s["op"] for s in spec["steps"])))
    except Exception as exc:  # noqa: BLE001 - record and continue
        row.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300],
                   trace=traceback.format_exc(limit=2)[-600:])
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocols", nargs="+", type=Path, help="protocol directories (or a corpus root with --all)")
    ap.add_argument("--all", action="store_true", help="treat each argument as a corpus root")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per protocol (default 120)")
    ap.add_argument("--single", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    dirs = [d for root in args.protocols for d in (sorted(p for p in root.iterdir() if p.is_dir()) if args.all else [root])]
    summary = []
    for directory in dirs:
        if args.single:
            row = _convert_one(directory, args.out)
        else:
            # One process per protocol: some protocols hang in the simulator.
            import subprocess

            try:
                done = subprocess.run(
                    [sys.executable, __file__, str(directory), "--out", str(args.out), "--single"],
                    capture_output=True, text=True, timeout=args.timeout,
                )
                lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
                row = json.loads(lines[-1]) if lines else {
                    "protocol": directory.name, "status": "error",
                    "error": (done.stderr.strip().splitlines() or ["no output"])[-1][:300],
                }
            except subprocess.TimeoutExpired:
                row = {"protocol": directory.name, "status": "error",
                       "error": f"Timeout: simulation took longer than {args.timeout}s"}
        summary.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "trace"}), flush=True)
    if not args.single:
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ok = sum(1 for r in summary if r["status"] == "ok")
    print(f"converted {ok}/{len(summary)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
