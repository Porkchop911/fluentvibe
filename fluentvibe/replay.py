"""Protocol replay: what is on the deck and in every well after each protocol step.

``replay_frames(wt)`` simulates the protocol and records one frame per
protocol step (``wt.group``): every labware on the deck with its position and,
per well, the volume and the main reagent. ``replay_html(frames)`` renders a
standalone page (no server, no external scripts) with the deck, a colour per
reagent, and a slider / play button over the steps.
"""

from __future__ import annotations

import html
import json
from typing import Any

_ROWS = "ABCDEFGHIJKLMNOP"


def _group_of_steps(wt) -> dict[int, str]:
    names: dict[int, str] = {}

    def walk(steps, group: str) -> None:
        for step in steps:
            names[id(step)] = group
            for attr in ("steps", "then_steps", "else_steps"):
                walk(getattr(step, attr, None) or (), group)

    for group in getattr(wt, "_groups", []):
        walk(group.steps, group.name)
    return names


def _labware_state(labware, reagents: dict[str, int]) -> dict[str, Any]:
    wells = getattr(labware, "wells", {}) or {}
    rows, cols = labware._effective_grid() if hasattr(labware, "_effective_grid") else (1, 1)
    rows, cols = max(1, rows or 1), max(1, cols or 1)
    cells = []
    for c in range(1, cols + 1):
        for r in range(rows):
            well = wells.get(f"{_ROWS[r]}{c}")
            if well is None:
                cells.append(None)
                continue
            layers = [(layer.reagent, layer.volume_ul) for layer in well.layers if layer.volume_ul > 1e-6]
            bead = getattr(well, "bead_phase", None)
            volume = sum(v for _, v in layers)
            main = max(layers, key=lambda item: item[1])[0].name if layers else None
            if main is not None and main not in reagents:
                reagents[main] = len(reagents)
            cells.append([round(volume, 1), reagents.get(main, -1),
                          1 if bead is not None and getattr(bead, "present", False) else 0])
    return {"label": labware.label, "category": getattr(labware, "category", ""),
            "catalog": getattr(labware, "catalog_name", ""), "rows": rows, "cols": cols,
            "capacity": max((getattr(w, "max_volume_ul", 0) or 0) for w in wells.values()) if wells else 0,
            "magnetized": bool(getattr(labware, "is_magnetized", False)), "cells": cells}


def replay_frames(wt, *, title: str | None = None) -> dict[str, Any]:
    """Simulate ``wt`` and record the deck after each protocol step."""
    if not getattr(wt, "snapshots", None):
        wt.simulate()
    group_of = _group_of_steps(wt)
    reagents: dict[str, int] = {}
    frames = []
    snapshots = list(wt.snapshots)
    for i, snap in enumerate(snapshots):
        group = group_of.get(id(snap.step), "")
        nxt = group_of.get(id(snapshots[i + 1].step), None) if i + 1 < len(snapshots) else None
        if nxt == group:
            continue  # record the state at the end of each group
        labware = []
        seen: set[int] = set()
        for (location, position), stack in snap.slot_map.items():
            for depth, lw in enumerate(stack):
                if id(lw) in seen:
                    continue
                seen.add(id(lw))
                state = _labware_state(lw, reagents)
                state.update(location=location, position=position, depth=depth)
                labware.append(state)
        labware.sort(key=lambda s: (s["location"], s["position"], s["depth"]))
        frames.append({"step": group or type(snap.step).__name__, "index": snap.step_index, "labware": labware})
    # Scale each labware to the most it holds during the run, so 20 ul in a
    # 350 ul well is still visible.
    peak: dict[str, float] = {}
    for frame in frames:
        for lw in frame["labware"]:
            top = max((c[0] for c in lw["cells"] if c), default=0.0)
            peak[lw["label"]] = max(peak.get(lw["label"], 0.0), top)
    for frame in frames:
        for lw in frame["labware"]:
            lw["scale"] = peak.get(lw["label"]) or lw["capacity"] or 1.0
    return {"title": title or getattr(wt, "name", "Protocol"), "reagents": list(reagents), "frames": frames}


_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>__TITLE__ — replay</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#f7f8fa; --fg:#1f2328; --muted:#636c76; --card:#fff; --line:#d0d7de; --accent:#0969da; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --card:#161b22; --line:#30363d; --accent:#4493f8; } }
body { margin:0; font:14px/1.4 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
header { padding:14px 18px; border-bottom:1px solid var(--line); }
h1 { font-size:18px; margin:0 0 8px; }
.controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.controls input[type=range] { flex:1; min-width:180px; }
button { background:var(--accent); color:#fff; border:0; border-radius:6px; padding:6px 12px; cursor:pointer; }
#stepname { font-weight:600; margin-top:8px; min-height:1.4em; }
main { padding:14px 18px; display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 10px; }
.card.mag { outline:2px solid #bf8700; }
.card h3 { font-size:13px; margin:0 0 2px; } .card .sub { color:var(--muted); font-size:11px; margin-bottom:6px; }
.card.changed { box-shadow:0 0 0 2px var(--accent); }
.legend { display:flex; gap:10px; flex-wrap:wrap; padding:0 18px 14px; font-size:12px; }
.legend span i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:middle; }
svg { width:100%; height:auto; }
</style></head><body>
<header><h1>__TITLE__</h1>
<div class="controls"><button id="play">▶ Play</button><input id="slider" type="range" min="0" value="0">
<span id="pos"></span></div><div id="stepname"></div></header>
<div class="legend" id="legend"></div><main id="deck"></main>
<script>
const DATA = __DATA__;
const palette = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b","#e377c2","#17becf","#bcbd22","#7f7f7f","#393b79","#637939","#8c6d31","#843c39","#7b4173"];
const color = i => i < 0 ? "transparent" : palette[i % palette.length];
const $ = id => document.getElementById(id);
$("legend").innerHTML = DATA.reagents.map((r, i) => `<span><i style="background:${color(i)}"></i>${r.replace(/</g,"&lt;")}</span>`).join("");
const slider = $("slider"); slider.max = Math.max(0, DATA.frames.length - 1);
function wellsSvg(lw) {
  const r = 9, gap = 22, w = lw.cols * gap + 8, h = lw.rows * gap + 8;
  let out = `<svg viewBox="0 0 ${w} ${h}">`;
  const cap = lw.scale || lw.capacity || 1;
  lw.cells.forEach((c, k) => {
    const col = Math.floor(k / lw.rows), row = k % lw.rows, x = 4 + col * gap + gap / 2, y = 4 + row * gap + gap / 2;
    out += `<circle cx="${x}" cy="${y}" r="${r}" fill="none" stroke="#8c959f" stroke-width="1"/>`;
    if (c && c[0] > 0) {
      const f = Math.max(0.15, Math.min(1, c[0] / cap));
      out += `<circle cx="${x}" cy="${y}" r="${(r - 1) * Math.sqrt(f)}" fill="${color(c[1])}"><title>${String.fromCharCode(65 + row)}${col + 1}: ${c[0]} ul ${DATA.reagents[c[1]] || ""}</title></circle>`;
    }
    if (c && c[2]) out += `<circle cx="${x}" cy="${y + r - 3}" r="2" fill="#6e4b00"/>`;
  });
  return out + "</svg>";
}
function troughSvg(lw) {
  const c = (lw.cells[0] || [0, -1]), cap = lw.scale || lw.capacity || 1, f = Math.min(1, c[0] / cap);
  return `<svg viewBox="0 0 200 26"><rect x="1" y="1" width="198" height="24" rx="4" fill="none" stroke="#8c959f"/>` +
    `<rect x="2" y="2" width="${196 * f}" height="22" rx="3" fill="${color(c[1])}"/>` +
    `<text x="100" y="17" text-anchor="middle" font-size="11" fill="currentColor">${c[0] >= 1000 ? (c[0]/1000).toFixed(1) + " ml" : Math.round(c[0]) + " ul"}</text></svg>`;
}
let prev = null;
function show(i) {
  const fr = DATA.frames[i]; slider.value = i;
  $("pos").textContent = `${i + 1} / ${DATA.frames.length}`;
  $("stepname").textContent = fr.step;
  const before = prev ? Object.fromEntries(prev.labware.map(l => [l.label, JSON.stringify(l.cells) + l.location + l.position])) : {};
  $("deck").innerHTML = fr.labware.filter(l => l.category !== "tip_box").map(l => {
    const changed = prev && before[l.label] !== JSON.stringify(l.cells) + l.location + l.position;
    const body = l.category === "magnet_rack" ? '<div class="sub">magnet</div>' : (l.rows * l.cols > 1) ? wellsSvg(l) : troughSvg(l);
    return `<div class="card${l.magnetized ? " mag" : ""}${changed ? " changed" : ""}"><h3>${l.label}${l.magnetized ? " · on magnet" : ""}</h3>` +
      `<div class="sub">${l.location} ${l.position} · ${l.catalog}</div>${body}</div>`;
  }).join("");
  prev = fr;
}
let timer = null;
$("play").onclick = () => {
  if (timer) { clearInterval(timer); timer = null; $("play").textContent = "▶ Play"; return; }
  $("play").textContent = "❚❚ Pause";
  timer = setInterval(() => { const n = +slider.value + 1; if (n >= DATA.frames.length) { $("play").click(); return; } show(n); }, 900);
};
slider.oninput = () => show(+slider.value);
show(Math.min(DATA.frames.length - 1, Math.max(0, parseInt((location.hash.match(/frame=(\d+)/) || [])[1] || "1") - 1)));
</script></body></html>
"""


def replay_html(frames: dict[str, Any]) -> str:
    """A standalone replay page for :func:`replay_frames` output."""
    data = json.dumps(frames, separators=(",", ":")).replace("</", "<\\/")
    return _PAGE.replace("__TITLE__", html.escape(str(frames.get("title") or "Protocol"))).replace("__DATA__", data)
