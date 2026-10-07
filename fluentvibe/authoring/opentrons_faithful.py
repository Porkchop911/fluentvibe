"""Opentrons trace -> fluentvibe protocol, well by well. No model.

``scripts/opentrons_trace.py`` records what the Opentrons run did to every
well (``trace.json``). This module writes a protocol for the deck profile that
does the same: the same volumes from the same source wells into the same
destination wells, in the same order, with the same tip changes, sections,
pauses and waits. Opentrons labware becomes deck labware:

* a 96-well plate whose wells stay within a 96-well plate's capacity stays a
  96-well plate (same well names); a 384-well plate likewise;
* every other well (tubes, reservoir wells, deep wells) gets its own place:
  a well of a shared "tube plate" when its content fits, else its own trough
  (25 ml, 100 ml, or a 300 ml reservoir);
* liquid poured into the Opentrons trash goes to the deck's waste.

Pipetting is done with the FCA (8 channels); an Opentrons 8-channel move keeps
its channel-to-well layout. What cannot be converted (96-channel moves,
module programs, magnet, gripper moves) is kept as an operator step or a
comment and listed in the report. :func:`volume_fidelity` compares what each
well received or gave in the Opentrons run with the fluentvibe simulation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

PLATE96_CAPACITY_UL = 350.0      # 96_ABgene_SuperPlate_Thermo_AB2800
PLATE384_CAPACITY_UL = 29.0      # 384 Well LowVol LoBase
TROUGH_SMALL_UL, TROUGH_LARGE_UL = 25000.0, 100000.0
SBS_SMALL_UL = 55000.0           # "60ml SBS MCA96", as resolver._SBS_SMALL_MAX_UL
STOCK_MARGIN = 1.1               # source wells: what is drawn, +10 % ...
STOCK_EXTRA_UL = 20.0            # ... + 20 ul, so the last draw is not short


def _ident(text: str, used: set[str]) -> str:
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", text).strip("_").lower() or "lw"
    if base[0].isdigit():
        base = "lw_" + base
    base = base[:28]
    name, i = base, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def _is_trash(info: dict) -> bool:
    name = (info.get("load_name", "") + " " + info.get("display", "")).lower()
    return "trash" in name or "waste" in name  # OT-2 trash, Flex trash bin / waste chute


@dataclass
class Container:
    label: str                      # fluentvibe labware label
    var: str                        # python variable
    kind: str                       # plate96 | plate384 | tubeplate | trough | waste
    catalog: str
    python_class: str
    site: tuple[str, int]


@dataclass
class Conversion:
    source: str
    mapping: dict[str, tuple[str, str]]        # "lwkey|well" -> (label, well)
    fills: dict[tuple[str, str], float]        # (label, well) -> initial ul
    report: dict[str, Any] = field(default_factory=dict)


class DoesNotFit(ValueError):
    """The Opentrons labware needs more deck sites than the profile has."""


def _well_budget(trace: dict) -> tuple[dict, dict, dict]:
    """Per (lwkey, well): initial stock needed, peak content, net change."""
    running: dict[tuple[str, str], float] = defaultdict(float)
    lowest: dict[tuple[str, str], float] = defaultdict(float)
    peak: dict[tuple[str, str], float] = defaultdict(float)
    for e, volume in _liquid_moves(trace["events"]):
        sign = -1.0 if e["kind"] == "aspirate" else 1.0
        for well in e["wells"]:
            key = (e["labware"], well)
            running[key] += sign * volume
            lowest[key] = min(lowest[key], running[key])
            peak[key] = max(peak[key], running[key])
    need = {k: -v for k, v in lowest.items() if v < -1e-9}
    content = {k: need.get(k, 0.0) + peak[k] for k in running}
    return need, content, dict(running)


def _liquid_moves(events):
    """(event, liquid volume per channel) for every aspirate/dispense with a well.

    An Opentrons air gap is air drawn after the liquid; the next dispense
    reports liquid + air ("aspirate 125, air gap 5, dispense 130"). The liquid
    part is the dispense minus the air the tip carries."""
    air = 0.0
    for e in events:
        kind = e["kind"]
        if kind == "air_gap":
            air += float(e.get("volume") or 0)
        elif kind in ("pick_up_tip", "drop_tip", "return_tip"):
            air = 0.0
        elif kind in ("aspirate", "dispense") and e.get("labware") and e.get("volume") is not None:
            volume = float(e["volume"])
            if kind == "dispense" and air:
                liquid = max(0.0, volume - air)
                air = max(0.0, air - volume)
                volume = liquid
            yield e, volume


def convert_trace(trace: dict, deck, *, liquid_class: str | None = None) -> Conversion:
    """Write the protocol for ``trace`` on ``deck`` (``skeleton.load_deck``)."""
    lc = liquid_class or deck.liquid_class
    need, content, net = _well_budget(trace)
    labware = trace["labware"]
    used_wells: dict[str, list[str]] = defaultdict(list)
    for (lw, well) in content:
        if well not in used_wells[lw]:
            used_wells[lw].append(well)
    for lw in used_wells:  # keep the Opentrons well order
        order = labware.get(lw, {}).get("wells", [])
        used_wells[lw].sort(key=lambda w: order.index(w) if w in order else 999)

    names: set[str] = set()
    nests = list(deck.free_nests)
    troughs = list(deck.free_trough_sites)
    large = list(deck.free_large_sites)
    containers: list[Container] = []
    mapping: dict[str, tuple[str, str]] = {}
    notes: list[str] = []

    def take(sites: list, what: str) -> tuple[str, int]:
        if not sites:
            raise DoesNotFit(f"no free deck site left for {what}")
        return sites.pop(0)

    labels: set[str] = set()

    def add(label: str, kind: str, catalog: str, python_class: str, site) -> Container:
        # FluentControl labware names must be unique in a protocol.
        base, i = label, 2
        while label in labels:
            label, i = f"{base}_{i}", i + 1
        labels.add(label)
        c = Container(label=label, var=_ident(label, names), kind=kind, catalog=catalog,
                      python_class=python_class, site=site)
        containers.append(c)
        return c

    waste = None
    if deck.waste:
        catalog, location, position = deck.waste
        waste = add("Waste", "waste", catalog, "Trough25mL", (location, position))
    tube_plate: Container | None = None
    tube_cursor = 0
    tube_wells = [f"{r}{c}" for c in range(1, 13) for r in "ABCDEFGH"]

    for lw, wells in used_wells.items():
        info = labware.get(lw, {})
        display = info.get("display") or lw
        if _is_trash(info):
            if waste is None:
                site = take(large, "the waste")
                waste = add("Waste", "waste", "300ml SBS", "Trough25mL", site)
            for w in wells:
                mapping[f"{lw}|{w}"] = (waste.label, "A1")
            continue
        peak = max(content[(lw, w)] for w in wells)
        n = len(info.get("wells", []))
        if n == 96 and info.get("rows") == 8 and peak <= PLATE96_CAPACITY_UL:
            c = add(_short(display), "plate96", deck.plate_catalog, "Plate96", take(nests, display))
            for w in wells:
                mapping[f"{lw}|{w}"] = (c.label, w)
            continue
        if n == 384 and peak <= PLATE384_CAPACITY_UL:
            c = add(_short(display), "plate384", "384 Well LowVol LoBase", "Plate384", take(nests, display))
            for w in wells:
                mapping[f"{lw}|{w}"] = (c.label, w)
            continue
        for w in wells:
            amount = content[(lw, w)]
            if amount <= PLATE96_CAPACITY_UL:
                if tube_plate is None or tube_cursor >= len(tube_wells):
                    tube_plate = add(f"TubePlate{len([c for c in containers if c.kind == 'tubeplate']) + 1}",
                                     "tubeplate", deck.plate_catalog, "Plate96", take(nests, "tube plate"))
                    tube_cursor = 0
                mapping[f"{lw}|{w}"] = (tube_plate.label, tube_wells[tube_cursor])
                tube_cursor += 1
            elif amount <= TROUGH_LARGE_UL and troughs:
                catalog, cls = ((deck.slim_small, "Trough25mL") if amount <= TROUGH_SMALL_UL * 0.9
                                else (deck.slim_large, "Trough100mL"))
                c = add(f"{_short(display)[:22]}_{w}", "trough", catalog, cls, take(troughs, f"{display} {w}"))
                mapping[f"{lw}|{w}"] = (c.label, "A1")
            elif amount <= SBS_SMALL_UL and len(nests) > 1:
                # Trough slots used up: a 60 ml SBS reservoir on a free plate nest (it
                # connects to the 61 mm nest; the skeleton builder places it there too).
                # One nest stays free for the tip box.
                c = add(f"{_short(display)[:22]}_{w}", "trough", deck.reservoir_small, "Trough100mL",
                        take(nests, f"{display} {w}"))
                mapping[f"{lw}|{w}"] = (c.label, "A1")
            else:
                c = add(f"{_short(display)[:22]}_{w}", "trough", deck.reservoir_large, "Trough25mL",
                        take(large, f"{display} {w}"))
                mapping[f"{lw}|{w}"] = (c.label, "A1")
            notes.append(f"{display} {w} -> {mapping[f'{lw}|{w}'][0]} {mapping[f'{lw}|{w}'][1]}")

    # Tips: the largest volume one channel holds before it dispenses.
    max_load = 0.0
    load = 0.0
    for e in trace["events"]:
        if e["kind"] == "aspirate" and e.get("volume"):
            load += float(e["volume"])
            max_load = max(max_load, load)
        elif e["kind"] in ("dispense", "drop_tip", "return_tip", "pick_up_tip"):
            load = 0.0 if e["kind"] != "dispense" else max(0.0, load - float(e.get("volume") or 0))
    tip_catalog, tip_class = (("FCA, 1000ul SBS", "FCA1000Box") if max_load > 200
                              else ("FCA, 200ul SBS", "FCA200Box"))
    tips = add("FcaTips", "tips", tip_catalog, tip_class, take(nests, "the FCA tips"))

    fills: dict[tuple[str, str], float] = {}
    by_label = {c.label: c for c in containers}
    for (lw, w), amount in need.items():
        target = mapping.get(f"{lw}|{w}")
        if target is None or by_label[target[0]].kind == "waste":
            continue
        cap = {"plate96": PLATE96_CAPACITY_UL, "tubeplate": PLATE96_CAPACITY_UL,
               "plate384": PLATE384_CAPACITY_UL}.get(by_label[target[0]].kind)
        fill = amount * STOCK_MARGIN + STOCK_EXTRA_UL
        if cap is not None:
            fill = min(fill, cap - max(0.0, content[(lw, w)] - amount))
        fills[target] = round(fill, 2)

    source, unconverted, kept = _write(trace, containers, mapping, fills, tips, lc, deck)
    picks = sum(1 for e in trace["events"] if e["kind"] == "pick_up_tip")
    report = {
        "labware": {lw: labware.get(lw, {}).get("display", lw) for lw in used_wells},
        "containers": [{"label": c.label, "kind": c.kind, "catalog": c.catalog, "site": list(c.site)}
                       for c in containers],
        "substitutions": notes,
        "unconverted": unconverted,
        "kept_pauses_and_waits": kept,
        "tip_pickups": picks,
        "fca_tips_used": picks * 8,
        "max_tip_load_ul": round(max_load, 1),
    }
    return Conversion(source=source, mapping=mapping, fills=fills, report=report)


def _short(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\b(opentrons|nest|corning|greiner|wellplate|tuberack|labware)\b", "", text, flags=re.I)
    words = re.findall(r"[A-Za-z0-9]+", text)
    return ("".join(w[:1].upper() + w[1:] for w in words) or "Labware")[:32]


def _section_title(text: str) -> str | None:
    title = re.sub(r"^[\s\-=*#_]+|[\s\-=*#_]+$", "", text).strip()
    if not title or len(title) > 80 or title.lower().startswith(("latching", "unlatching", "opening", "closing")):
        return None
    return title


def _write(trace, containers, mapping, fills, tips, lc, deck) -> tuple[str, list[str]]:
    by_label = {c.label: c for c in containers}
    lines: list[str] = []
    unconverted: list[str] = []
    kept: list[str] = []          # pauses and waits that are in the Fluent protocol
    groups: set[str] = set()
    have_tips = False
    tip_load = 0.0                # what the current tip holds (per channel)
    liquid = {id(ev): volume for ev, volume in _liquid_moves(trace["events"])}  # air gaps taken out

    def group(title: str) -> None:
        name = title
        i = 2
        while name in groups:
            name, i = f"{title} ({i})", i + 1
        groups.add(name)
        lines.append(f"    wt.group({name!r})")

    group("Opentrons run")
    for e in trace["events"]:
        kind = e["kind"]
        if kind == "comment":
            title = _section_title(e["text"])
            if title:
                group(title)
            continue
        if kind == "pick_up_tip":
            if have_tips:
                lines.append("    fca.drop_tips()")
            lines.append(f"    fca.get_tips({tips.var})")
            have_tips = True
            tip_load = 0.0
        elif kind in ("drop_tip", "return_tip"):
            if kind == "return_tip" and tip_load > 0.5:
                # Opentrons parks a tip with liquid in the rack and picks it up
                # again later; the FCA cannot. That liquid is not carried over.
                note = (f"parked tip: {tip_load:g} ul stayed in a tip the Opentrons protocol put back into "
                        f"the rack for later; the FCA drops it ({e['text'][:60]})")
                unconverted.append(note)
                lines.append(f"    wt.add_comment({('NOT CONVERTED: ' + note)[:200]!r})")
            if have_tips:
                lines.append("    fca.drop_tips()")
            have_tips = False
            tip_load = 0.0
        elif kind in ("aspirate", "dispense"):
            if e.get("unresolved") or not e.get("labware"):
                unconverted.append(f"{kind} with no well: {e['text'][:80]}")
                lines.append(f"    # NOT CONVERTED: {e['text'][:90]!r}")
                continue
            if e.get("channels", 1) > 8:
                unconverted.append(f"96-channel {kind}: {e['text'][:80]}")
                lines.append(f"    wt.add_comment({('TODO 96-channel: ' + e['text'][:120])!r})")
                continue
            if not have_tips:
                lines.append(f"    fca.get_tips({tips.var})  # the Opentrons pipette had a tip already")
                have_tips = True
            per_target: dict[str, list[tuple[int, str]]] = defaultdict(list)
            for channel, well in enumerate(e["wells"]):
                label, fv_well = mapping[f"{e['labware']}|{well}"]
                per_target[label].append((channel, fv_well))
            volume = liquid.get(id(e), float(e["volume"] or 0))
            if kind == "dispense" and volume > tip_load + 0.5:
                # The Opentrons simulator does not track liquid: it dispenses more than
                # the tip holds. Dispense what the tip holds and say so; the per-well
                # check (from the Opentrons volumes) shows the difference.
                unconverted.append(f"dispense of {volume:g} ul but the tip holds {tip_load:g} ul: "
                                   f"{e['text'][:70]}")
                volume = tip_load
            if volume <= 0:
                continue  # an air-gap-only (or empty-tip) dispense: nothing liquid to move
            for label, pairs in per_target.items():
                c = by_label[label]
                wells = [w for _, w in pairs]
                channels = [ch for ch, _ in pairs]
                lines.append(f"    fca.{kind}({c.var}, {volume:g}, liquid_class={lc!r}, "
                             f"wells={wells!r}, channels={channels!r})")
            change = volume
            tip_load = max(0.0, tip_load + (change if kind == "aspirate" else -change))
        elif kind == "pause":
            message = e.get("message") or e["text"]
            lines.append(f"    wt.user_prompt({message[:200]!r})")
            kept.append(f"operator prompt: {message[:120]}")
        elif kind == "delay":
            seconds = float(e.get("seconds") or 0)
            if seconds >= 1:
                lines.append(f"    wt.wait(duration_seconds={seconds:g})")
                kept.append(f"wait {seconds:g} s")
        elif kind in ("device", "magnet", "move"):
            unconverted.append(f"{kind}: {e['text'][:100]}")
            lines.append(f"    wt.user_prompt({('Opentrons ' + kind + ' step: ' + e['text'][:160])!r})")
    if have_tips:
        lines.append("    fca.drop_tips()")

    used_classes = sorted({c.python_class for c in containers})
    run_id = re.sub(r"[^0-9a-z_]+", "_", str(trace.get("name", "opentrons")).lower()).strip("_")[:40] or "opentrons"
    head = [
        "# OPENTRONS FAITHFUL CONVERSION",
        f'"""{trace.get("name", "Opentrons protocol")}',
        "",
        f"Converted well by well from the Opentrons run of {str(trace.get('protocol', '')).replace(chr(92), '/')}",
        "(scripts/opentrons_trace.py). Same volumes, wells, order and tip changes;",
        "Opentrons labware mapped to this deck (see the conversion report).",
        '"""',
        f"from fluentvibe import Reagent, Worktable, {', '.join(used_classes)}",
        "",
        "",
        "def build_worktable() -> Worktable:",
        "    wt = Worktable.from_workspace(",
        f"        {deck.workspace_name!r},",
        f"        workspace_guid={deck.workspace_guid!r},",
        "        auto_place=False,",
        f"        protocol_name={str(trace.get('name', 'Opentrons protocol'))[:60]!r},",
        "    )",
        "    fca = wt.liha",
        # The authoring gate: Variables first (at least one declared), then placement.
        f"    wt.declare_variable('RunId', {run_id!r})",
        f"    wt.set_sim_value('RunId', {run_id!r})",
        "    wt.group('Variables')",
        "    wt.group('Labware Placement')",
    ]
    for c in containers:
        head.append(f"    {c.var} = wt.place({c.python_class}({c.label!r}, catalog={c.catalog!r}), "
                    f"{c.site[0]!r}, {c.site[1]})")
    for (label, well), volume in sorted(fills.items()):
        c = by_label[label]
        if c.kind in ("trough",):
            head.append(f"    {c.var}.fill_all(Reagent({(label + ' stock')!r}), {volume:g})")
        else:
            head.append(f"    {c.var}.fill_wells([{well!r}], Reagent({(label + ' ' + well + ' stock')!r}), {volume:g})")
    return "\n".join(head + lines + ["    return wt", ""]), unconverted, kept


def volume_fidelity(trace: dict, conversion: Conversion, final_labware: dict) -> dict[str, Any]:
    """Per Opentrons well: the net volume change in the Opentrons run vs in the
    fluentvibe simulation (final - initial fill) of the well it maps to."""
    _, _, net = _well_budget(trace)
    fv_net: dict[tuple[str, str], float] = defaultdict(float)
    expected: dict[tuple[str, str], float] = defaultdict(float)
    for (lw, w), change in net.items():
        target = conversion.mapping.get(f"{lw}|{w}")
        if target is not None:
            expected[target] += change
    checked = mismatched = 0
    worst: list[str] = []
    for target, change in expected.items():
        label, well = target
        if label == "Waste":
            continue
        wells = (final_labware.get(label) or {}).get("wells") or {}
        final = float((wells.get(well) or {}).get("volume_ul", 0.0))
        fv_net[target] = final - conversion.fills.get(target, 0.0)
        checked += 1
        if abs(fv_net[target] - change) > 0.5:
            mismatched += 1
            if len(worst) < 10:
                worst.append(f"{label} {well}: Opentrons {change:+.1f} ul, fluentvibe {fv_net[target]:+.1f} ul")
    return {"wells_checked": checked, "wells_matching": checked - mismatched, "mismatches": worst}
