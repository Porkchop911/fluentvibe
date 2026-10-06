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
MULTI_CHANNEL = re.compile(r"_multi|8channel|8_channel|96channel|96_channel|multi_flex", re.IGNORECASE)
REAGENT_WORDS = re.compile(
    r"wash|ethanol|etoh|buffer|bead|ampure|mix|reagent|reservoir|enzyme|primer|adapter|water|elut",
    re.IGNORECASE,
)
SAMPLE_WORDS = re.compile(r"sample|librar|amplicon|pcr product|index", re.IGNORECASE)
# Set per protocol (one protocol per process). Multi-channel run logs name
# only row A, so column-wise additions into one sample column look like
# pooling into one well; labware that received liquid earlier in the run
# (a real pool's source) is tracked across sections.
_MULTI_CHANNEL = False
_RECEIVED: set[str] = set()
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
    # A value the protocol already assigned (its in-file default) wins.
    import inspect
    local = inspect.currentframe().f_back.f_locals
    return [local[n] if n in local else _FV_DEFAULTS[n] if n in _FV_DEFAULTS else _fv_guess(n) for n in names]
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


# OT-2 protocols on older API levels simulate against a virtual robot; without
# attached modules every load_module() fails. Attach one of each generation.
_OT2_MODULES = {
    "magdeck": ["magneticModuleV1", "magneticModuleV2"],
    "tempdeck": ["temperatureModuleV1", "temperatureModuleV2", "temperatureModuleV2"],
    "thermocycler": ["thermocyclerModuleV1", "thermocyclerModuleV2"],
    "heatershaker": ["heaterShakerModuleV1"],
}


def _ot2_hardware_file(out_dir: Path) -> Path:
    path = out_dir / "_ot2_simulator_setup.json"
    if not path.exists():
        setup = {
            "machine": "OT-2 Standard",
            "strict_attached_instruments": False,
            "attached_modules": {
                kind: [{"serial_number": f"fv-{model}-{i}", "model": model, "calls": []}
                       for i, model in enumerate(models)]
                for kind, models in _OT2_MODULES.items()
            },
        }
        path.write_text(json.dumps(setup), encoding="utf-8")
    return path


def _fields_defaults(folder: Path) -> dict:
    """The protocol's own defaults from ``fields.json`` (the Opentrons Protocols
    repo ships one per customisable protocol): ``default``, or a dropdown's
    first option. Guessed values made protocols divide by zero, misread a CSV
    or put two pipettes on one mount."""
    path = folder / "fields.json"
    if not path.exists():
        return {}
    try:
        fields = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    values = {}
    for item in fields if isinstance(fields, list) else []:
        name = item.get("name")
        if not name:
            continue
        if "default" in item:
            values[name] = item["default"]
        elif item.get("options"):
            values[name] = item["options"][0].get("value")
    return values


def _usage_defaults(source: str) -> dict:
    """Guess each get_values() parameter from how the protocol uses it.

    Looks, in order of confidence, for comparisons with a literal
    (``if mode == "x"``), dict-literal lookups (``table[mode]``), and the call
    it is passed to (``load_labware`` / ``load_module`` / ``load_instrument``),
    then string methods called on it.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    names: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "get_values"):
            target = node.targets[0]
            elts = target.elts if isinstance(target, (ast.List, ast.Tuple)) else [target]
            names += [e.id for e in elts if isinstance(e, ast.Name)]
    dict_literals = {
        t.id: node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name) and isinstance(node.value, ast.Dict)
    }
    guesses: dict[str, tuple[int, object]] = {}

    def offer(name: str, rank: int, value) -> None:
        if name in names and (name not in guesses or rank < guesses[name][0]):
            guesses[name] = (rank, value)

    def const(node):
        return node.value if isinstance(node, ast.Constant) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
            for comp in node.comparators:
                if const(comp) is not None:
                    offer(node.left.id, 0, const(comp))
                elif isinstance(comp, (ast.List, ast.Tuple, ast.Set)) and comp.elts and const(comp.elts[0]) is not None:
                    offer(node.left.id, 0, const(comp.elts[0]))
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name) and isinstance(node.value, ast.Name):
            table = dict_literals.get(node.value.id)
            if table is not None and table.keys and const(table.keys[0]) is not None:
                offer(node.slice.id, 0, const(table.keys[0]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            first = node.args[:1] + [kw.value for kw in node.keywords
                                     if kw.arg in ("load_name", "module_name", "instrument_name")]
            if method == "load_instrument":
                mount_args = node.args[1:2] + [kw.value for kw in node.keywords if kw.arg == "mount"]
                for arg in mount_args:
                    if isinstance(arg, ast.Name):
                        offer(arg.id, 1, "left" if "20" in arg.id or "left" in arg.id.lower() else "right")
            for arg in first:
                if not isinstance(arg, ast.Name):
                    continue
                low = arg.id.lower()
                if method == "load_labware":
                    value = ("opentrons_96_tiprack_300ul" if "tip" in low else
                             "nest_12_reservoir_15ml" if "res" in low or "trough" in low else
                             "opentrons_24_tuberack_nest_1.5ml_snapcap" if "tube" in low or "rack" in low else
                             "nest_96_wellplate_100ul_pcr_full_skirt")
                    offer(arg.id, 1, value)
                elif method == "load_module":
                    offer(arg.id, 1, "magnetic module gen2" if "mag" in low else
                          "thermocycler" if "tc" in low or "thermo" in low else "temperature module gen2")
                elif method == "load_instrument":
                    offer(arg.id, 1, "p20_single_gen2" if "20" in low else
                          "p300_multi_gen2" if "multi" in low or "m300" in low or "8" in low else "p300_single_gen2")
            if isinstance(node.func.value, ast.Name) and method in ("split", "lower", "upper", "strip", "startswith", "endswith"):
                offer(node.func.value.id, 2, "A1")
    return {name: value for name, (rank, value) in guesses.items()}


def _simulate(protocol: Path, hardware_dir: Path | None = None):
    from opentrons import simulate

    source = protocol.read_text(encoding="utf-8")
    global _MULTI_CHANNEL
    _MULTI_CHANNEL = bool(MULTI_CHANNEL.search(source))
    _RECEIVED.clear()
    if "get_values(" in source and "def get_values" not in source:
        stub = _GET_VALUES_STUB.format(defaults={**_usage_defaults(source), **_commented_defaults(source),
                                                 **_fields_defaults(protocol.parent)})
        # Keep `from __future__` imports first.
        lines = source.splitlines(keepends=True)
        head = [line for line in lines if line.startswith("from __future__")]
        rest = [line for line in lines if not line.startswith("from __future__")]
        source = "".join(head) + stub + "".join(rest)
    labware_dirs = [str(protocol.parent)] + [str(d) for d in protocol.parent.rglob("*") if d.is_dir()]
    stub_dir = (hardware_dir / "_stub_labware") if hardware_dir is not None else None
    if stub_dir is not None and stub_dir.exists():
        labware_dirs.append(str(stub_dir))
    flex = bool(re.search(r"robotType['\"]?\s*[:=]\s*['\"](Flex|OT-3)", source))
    hardware = None if flex or hardware_dir is None else str(_ot2_hardware_file(hardware_dir))
    # Missing custom labware: write a stand-in (a standard definition of the
    # same kind, renamed) and retry. Geometry is approximate; volumes and wells
    # are what the spec needs.
    for _attempt in range(6):
        try:
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                runlog, _bundle = simulate.simulate(
                    io.StringIO(source), file_name=protocol.name, custom_labware_paths=labware_dirs,
                    hardware_simulator_file_path=hardware,
                )
            return runlog, captured.getvalue()
        except Exception as exc:  # noqa: BLE001
            missing = (re.search(r'labware\s+definition for\s+"([^"]+)"', str(exc))
                       or re.search(r'Labware \\?"([^"\\]+)\\?" not found', str(exc)))
            if missing is None or stub_dir is None:
                raise
            _write_stub_labware(missing.group(1), stub_dir)
            if str(stub_dir) not in labware_dirs:
                labware_dirs.append(str(stub_dir))
    raise RuntimeError("too many missing labware definitions")


_STUB_TEMPLATES = (
    (r"tip", "opentrons_96_tiprack_300ul"),
    (r"reservoir|trough", "nest_12_reservoir_15ml"),
    (r"tube|rack|vial|falcon|eppendorf_1", "opentrons_24_tuberack_nest_1.5ml_snapcap"),
    (r"384", "corning_384_wellplate_112ul_flat"),
    (r"deep|dwp|2ml|2000", "nest_96_wellplate_2ml_deep"),
)


def _write_stub_labware(load_name: str, stub_dir: Path) -> None:
    from opentrons.protocols.labware import get_labware_definition

    template = next((t for pattern, t in _STUB_TEMPLATES if re.search(pattern, load_name, re.I)),
                    "nest_96_wellplate_100ul_pcr_full_skirt")
    definition = json.loads(json.dumps(get_labware_definition(template)))
    definition["parameters"]["loadName"] = load_name
    definition["namespace"] = "custom_beta"
    definition["version"] = 1
    definition["metadata"]["displayName"] = f"{load_name} (stand-in: {template})"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / f"{load_name}.json").write_text(json.dumps(definition), encoding="utf-8")


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


_ADD_BEADS = re.compile(r"bead|ampure|axp|spri|mag|bind", re.I)
_ADD_WASH = re.compile(r"wash|ethanol|etoh|alcohol", re.I)
_ADD_ELUTE = re.compile(r"elut|\beb\b|\bte\b|water|nfw|h2o", re.I)
_ADD_OTHER = re.compile(r"lysis|proteinase|\bpk\b|sample|master ?mix|enzyme|primer", re.I)


def _cleanup_parameters(liquid) -> dict:
    """Bead volume, washes, wash and elution volume from a clean-up section.

    Follows one well of the plate that receives the most liquid (the clean-up
    plate). Each reagent addition is named by its source labware (beads,
    wash / ethanol, elution buffer; lysis and sample additions are ignored);
    unnamed additions fall back to position: first = beads, repeated middle
    source = wash, last = elution. Repeated trips from one source with no
    removal in between are one addition (665 ul moved as 3 x 221.7 ul).
    """
    pairs, last = [], None
    for e in liquid:
        if e["kind"] == "aspirate":
            last = (e.get("labware"), e.get("well"))
        elif e["kind"] == "dispense" and last and last[0] and e.get("labware") and last[0] != e["labware"]:
            pairs.append((last[0], last[1], e["labware"], e.get("well"), e.get("volume")))
    if not pairs:
        return {}
    # The clean-up plate: the most-dispensed destination that is not a
    # reservoir or waste (supernatant often goes back into a reservoir well).
    candidates = [p[2] for p in pairs if not re.search(r"reservoir|trough|waste|trash", p[2], re.I)]
    plate = Counter(candidates or [p[2] for p in pairs]).most_common(1)[0][0]
    additions = [p for p in pairs if p[2] == plate and p[4]]
    if not additions:
        return {}
    well = Counter(p[3] for p in additions).most_common(1)[0][0]
    events: list[list] = []  # [source labware, source well, volume]
    removed = False
    for src, src_well, dest, dest_well, volume in pairs:
        if src == plate and (src_well == well or not src_well):
            removed = True
            continue
        if dest != plate or dest_well != well or not volume:
            continue
        if events and not removed and events[-1][:2] == [src, src_well]:
            events[-1][2] += volume
        else:
            events.append([src, src_well, volume])
        removed = False
    if not events:
        return {}

    def kind(event):
        name = event[0]
        for label, pattern in (("wash", _ADD_WASH), ("elute", _ADD_ELUTE), ("beads", _ADD_BEADS), ("other", _ADD_OTHER)):
            if pattern.search(name):
                return label
        return None

    kinds = [kind(e) for e in events]
    beads = [e for e, k in zip(events, kinds) if k == "beads"]
    washes = [e for e, k in zip(events, kinds) if k == "wash"]
    elutes = [e for e, k in zip(events, kinds) if k == "elute"]
    unnamed = [e for e, k in zip(events, kinds) if k is None]
    if not beads and unnamed:
        beads = [unnamed.pop(0)]
    if not elutes and len(unnamed) > 1 and unnamed[-1] is events[-1]:
        elutes = [unnamed.pop()]
    if not washes and unnamed:
        source = Counter((e[0], e[1]) for e in unnamed).most_common(1)[0][0]
        washes = [e for e in unnamed if (e[0], e[1]) == source]
    out: dict = {}
    if beads:
        out["volume_ul"] = round(beads[0][2], 2)
    if washes:
        out["wash_ul"] = round(statistics.median(e[2] for e in washes), 1)
        out["washes"] = len(washes)
    if elutes:
        out["elute_ul"] = round(elutes[-1][2], 2)
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
    earlier = set(_RECEIVED)
    _RECEIVED.update(p[2] for p in moves)
    reagent_like = bool(REAGENT_WORDS.search(src) or REAGENT_WORDS.search(title) or RESERVOIR_WORDS.search(src))
    sample_source = bool(SAMPLE_WORDS.search(src)) or src in earlier
    if (len(dst_wells) == 1 and len(src_wells) > 1 and not reagent_like
            and (sample_source or not _MULTI_CHANNEL)):
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
        route = step.pop("_route", None)
        if step["op"] == "add" and route:
            step["_source"] = route[0]
    return merged


def convert(protocol_dir: Path, hardware_dir: Path | None = None) -> dict:
    # A single protocol file is accepted too (its folder may hold others).
    if protocol_dir.is_file():
        protocol, protocol_dir = protocol_dir, protocol_dir.parent
    else:
        protocol = next((p for p in sorted(protocol_dir.glob("*.py"))), None)
    if protocol is None:
        raise FileNotFoundError(f"no .py protocol in {protocol_dir}")
    runlog, printed = _simulate(protocol, hardware_dir)
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
    # Samples: the most wells any labware has touched. Aspirates count too: a
    # pooling protocol draws from N sample wells into one tube, and counting
    # only dispenses gave it a single sample.
    wells = defaultdict(set)
    for e in events:
        if e.get("labware") and e["kind"] in ("dispense", "aspirate"):
            wells[e["labware"]].add(e["well"])
    sample_count = max((len(w) for w in wells.values()), default=1)
    # Reagents: one per source labware an add step draws from, named after the
    # step's own heading ("Adding Acetone (...)" -> "Acetone") when it has one.
    reagents, by_source = [], {}
    for step in steps:
        source = step.pop("_source", None)
        if step["op"] != "add" or not source:
            continue
        if source not in by_source:
            heading = re.sub(r"\s*\(.*$", "", step["text"]).strip()
            heading = re.sub(r"^(adding|add|dispensing|dispense|transferring|transfer)\s+", "", heading, flags=re.I)
            name = heading if heading and len(heading) <= 60 and not heading.lower().startswith("step") else source
            rid = f"R{len(reagents) + 1}"
            kind = "ethanol" if re.search(r"ethanol|etoh", name + source, re.I) else                 "water" if re.search(r"water|h2o", name + source, re.I) else None
            reagents.append({"id": rid, "name": f"{name} ({source})", "role": "reagent", "liquid_type": kind})
            by_source[source] = rid
        step["reagent"] = by_source[source]
    return {
        "title": title,
        "sample_count": max(sample_count, 1),
        "sample_volume_ul": None,
        # Opentrons protocols work on samples already in the plate.
        "starts_empty": False,
        "reagents": reagents,
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
        spec = convert(directory, out)
        (out / f"{directory.stem if directory.is_file() else directory.name}.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        row.update(status="ok", steps=len(spec["steps"]),
                   ops=dict(Counter(s["op"] for s in spec["steps"])))
    except Exception as exc:  # noqa: BLE001 - record and continue
        row.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300],
                   trace=traceback.format_exc(limit=2)[-600:])
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocols", nargs="+", type=Path,
                    help="protocol directories or .py files (or a corpus root with --all)")
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
