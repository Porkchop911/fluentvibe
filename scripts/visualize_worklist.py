#!/usr/bin/env python
"""Visualize a FluentControl worklist (.gwl / .csv) as a plate picture.

Reads the worklist file, groups its records into tip sets (a `W;` record means
FluentControl drops the DiTis and picks fresh ones), and draws:

  * one grid per destination labware, coloured by the volume dispensed into
    each well, with the tip-set number that painted it;
  * the source consumption per labware;
  * the record timeline with tip changes and per-channel overfill flags.

Usage (from the repo root):

    python scripts/visualize_worklist.py build/eval/pi-fluentvibe-worklist.gwl
    python scripts/visualize_worklist.py build/eval/pi-fluentvibe-worklist.gwl --capacity 200
    python scripts/visualize_worklist.py picklist.csv --serve 8080

`--serve [PORT]` writes the page and serves it on http://127.0.0.1:PORT, then
opens the browser (Ctrl-C to stop). Without --serve it prints an ASCII grid and
writes the HTML next to the worklist.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fluentvibe.worklists import Gwl, standard_columns  # noqa: E402
from fluentvibe.simulator import worklist_sim  # noqa: E402

A_Z = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _row_of(address: str) -> str:
    letters = "".join(ch for ch in address if ch.isalpha())
    return letters.upper()


def _col_of(address: str) -> int:
    digits = "".join(ch for ch in address if ch.isdigit())
    return int(digits) if digits else 0


@dataclass
class Record:
    code: str          # A / D / W
    label: str = ""
    position: str = ""
    volume: float = 0.0
    tip_mask: int = 0  # 0 = unknown (mask field empty)
    line: int = 0
    tip_set: int = 1
    channel: int = 0   # 0-based channel for single-channel records


@dataclass
class Analysis:
    source: str
    capacity_ul: float
    tip_sets: int = 0
    records: list[Record] = field(default_factory=list)
    dest_wells: dict[str, dict[str, float]] = field(default_factory=dict)   # label -> well -> ul
    dest_paint: dict[str, dict[str, int]] = field(default_factory=dict)     # label -> well -> tip set
    source_used: dict[str, float] = field(default_factory=dict)
    overfills: list[str] = field(default_factory=list)

    @property
    def total_ul(self) -> float:
        return sum(v for wells in self.dest_wells.values() for v in wells.values())


def read_records(path: Path, capacity_ul: float) -> list[Record]:
    """A/D/W records with tip masks and tip-set numbers."""
    if path.suffix.lower() == ".csv":
        simple, _ = worklist_sim.records_from_csv(
            path, columns=_standard_mappings(), start_line=2, separator=",")
        return [Record(r.code, r.label, r.position, r.volume, 0, r.line) for r in simple]

    simple, skipped = worklist_sim.records_from_gwl(path)
    if skipped:
        print(f"note: record codes not visualized: {', '.join(sorted(set(s.split()[0] for s in skipped)))}",
              file=sys.stderr)
    masks = _masks_by_line(path)
    out: list[Record] = []
    tip_set = 1
    for r in simple:
        mask = masks.get(r.line, 0)
        rec = Record(r.code, r.label, r.position, r.volume, mask, r.line, tip_set)
        if rec.code != "W" and mask:
            rec.channel = (mask & -mask).bit_length() - 1
        if rec.code == "W":
            tip_set += 1
            continue
        out.append(rec)
    _ = capacity_ul
    return out


def _standard_mappings():
    from fluentvibe.ir.schema import WorklistColumnMapping
    return [WorklistColumnMapping(column_name=name, column_index=index, gwl_index=gwl_index)
            for name, index, gwl_index in _normalize()]


def _normalize():
    from fluentvibe.worklists import normalize_columns
    return normalize_columns(standard_columns())


def _masks_by_line(path: Path) -> dict[int, int]:
    """GWL field 9 (tip mask) per line, 1-based."""
    masks: dict[int, int] = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        parts = raw.strip().split(";")
        if parts and parts[0].strip().upper() in {"A", "D"} and len(parts) > 9:
            text = parts[9].strip()
            if text.isdigit():
                masks[line_no] = int(text)
    return masks


def analyze(path: Path, capacity_ul: float) -> Analysis:
    records = read_records(path, capacity_ul)
    a = Analysis(source=str(path), capacity_ul=capacity_ul)
    a.records = records
    a.tip_sets = max((r.tip_set for r in records), default=0)

    carried: dict[tuple[int, int], float] = {}   # (tip_set, channel) -> ul in the tip
    for r in records:
        channel = r.channel
        if r.code == "A":
            a.source_used[r.label] = a.source_used.get(r.label, 0.0) + r.volume
            key = (r.tip_set, channel)
            carried[key] = carried.get(key, 0.0) + r.volume
            if carried[key] > a.capacity_ul:
                a.overfills.append(
                    f"line {r.line}: tip set {r.tip_set} channel {channel + 1} carries "
                    f"{carried[key]:g} ul, over the {a.capacity_ul:g} ul tip")
        elif r.code == "D":
            well = r.position.strip().upper()
            wells = a.dest_wells.setdefault(r.label, {})
            wells[well] = wells.get(well, 0.0) + r.volume
            a.dest_paint.setdefault(r.label, {})[well] = r.tip_set
    return a


# ── ASCII rendering ────────────────────────────────────────────────────

def ascii_grid(a: Analysis) -> str:
    lines: list[str] = []
    for label, wells in sorted(a.dest_wells.items()):
        addresses = [w for w, v in wells.items() if v > 0]
        if not addresses:
            continue
        rows = sorted({_row_of(w) for w in addresses}, key=lambda r: (len(r), r))
        lo, hi = min(_col_of(w) for w in addresses), max(_col_of(w) for w in addresses)
        cols = list(range(lo, hi + 1))
        lines.append(f"\n{label}  ({len(addresses)} wells, {sum(wells.values()):g} ul)"
                     f"  cols {lo}-{hi}   '#' = dye, '.' = empty")
        lines.append("     " + "".join(f"{c:>3}" for c in cols))
        for row in rows:
            cells = ["  ." if wells.get(f"{row}{c}", 0.0) <= 0 else "  #"
                     for c in cols]
            lines.append(f"  {row:>2} " + "".join(cells))
    return "\n".join(lines)


def ascii_summary(a: Analysis) -> str:
    disp = [r for r in a.records if r.code == "D"]
    out = [
        f"worklist {a.source}",
        f"  records: {len(a.records)} pipetting, {a.tip_sets} tip set(s), "
        f"tip capacity {a.capacity_ul:g} ul",
        f"  dispensed: {len(disp)} wells, {a.total_ul:g} ul",
    ]
    for label, used in sorted(a.source_used.items()):
        out.append(f"  aspirated from {label}: {used:g} ul")
    for flag in a.overfills:
        out.append(f"  OVERFILL {flag}")
    return "\n".join(out)


# ── HTML rendering ─────────────────────────────────────────────────────

def to_html(a: Analysis) -> str:
    plates = []
    for label, wells in sorted(a.dest_wells.items()):
        lit = {w: v for w, v in wells.items() if v > 0}
        if not lit:
            continue
        rows = sorted({_row_of(w) for w in lit}, key=lambda r: (len(r), r))
        cols = sorted({_col_of(w) for w in lit})
        cells = [{
            "well": f"{row}{col}",
            "volume": round(wells.get(f"{row}{col}", 0.0), 3),
            "tipSet": a.dest_paint.get(label, {}).get(f"{row}{col}", 0),
        } for row in rows for col in cols]
        plates.append({"label": label, "rows": rows, "cols": cols, "cells": cells,
                       "lit": len(lit), "ul": round(sum(lit.values()), 3)})

    timeline = [{
        "line": r.line, "code": r.code, "label": r.label, "position": r.position,
        "volume": round(r.volume, 3), "tipSet": r.tip_set,
        "channel": r.channel + 1 if r.tip_mask else 0,
    } for r in a.records]

    payload = {
        "source": a.source,
        "capacityUl": a.capacity_ul,
        "tipSets": a.tip_sets,
        "totalUl": round(a.total_ul, 3),
        "wells": sum(p["lit"] for p in plates),
        "plates": plates,
        "sources": [{"label": k, "ul": round(v, 3)} for k, v in sorted(a.source_used.items())],
        "overfills": a.overfills,
        "timeline": timeline,
    }
    return _PAGE.replace("__DATA__", json.dumps(payload))


def _page() -> str:
    return r"""<!doctype html><meta charset="utf-8"><title>worklist viewer</title>
<style>
:root{--bg:#12161c;--panel:#1b212a;--ink:#e8edf4;--dim:#8b98a9;--accent:#4fc3f7;--dye:#37c2a6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:24px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:14px;color:var(--accent);margin:24px 0 8px}
.sub{color:var(--dim);margin-bottom:20px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 8px}
.stat{background:var(--panel);border:1px solid #2a3340;border-radius:8px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:20px;color:var(--dye)}
.stat span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.plate{background:var(--panel);border:1px solid #2a3340;border-radius:10px;padding:16px;overflow:auto}
table{border-collapse:separate;border-spacing:3px}
th{color:var(--dim);font-weight:400;font-size:11px}
td{width:26px;height:22px;text-align:center;border-radius:4px;background:#0d1117;
font-size:10px;color:#0b0f14;cursor:default;position:relative}
td.on{color:#04120e}
td:hover{outline:1px solid var(--accent)}
.legend{display:flex;align-items:center;gap:8px;color:var(--dim);font-size:11px;margin:8px 0 0}
.sw{width:18px;height:12px;border-radius:3px}
ol.tl{list-style:none;padding:0;margin:0;max-height:340px;overflow:auto;border:1px solid #2a3340;
border-radius:8px}
ol.tl li{display:grid;grid-template-columns:70px 40px 110px 80px 70px 70px;gap:8px;
padding:5px 12px;border-bottom:1px solid #202835;font-size:12px}
ol.tl li.wash{background:#202835;color:var(--accent)}
ol.tl li span:last-child{color:var(--dim)}
.warn{color:#ff8a80;margin:12px 0}
#tip{position:fixed;background:#0d1117;border:1px solid var(--accent);border-radius:6px;
padding:6px 10px;font-size:12px;pointer-events:none;display:none;z-index:9}
</style>
<h1>worklist viewer</h1><div class="sub" id="src"></div>
<div class="stats" id="stats"></div><div id="warn"></div><div id="plates"></div>
<h2>record timeline</h2><ol class="tl" id="tl"></ol>
<div id="tip"></div>
<script>
const D=__DATA__;const tip=document.getElementById('tip');
document.getElementById('src').textContent=D.source+' — FluentControl GWL worklist';
const stats=[['tip sets',D.tipSets],['wells painted',D.wells],['total volume',D.totalUl+' ul'],
['tip capacity',D.capacityUl+' ul'],['sources',D.sources.length]];
document.getElementById('stats').innerHTML=stats.map(s=>`<div class="stat"><b>${s[1]}</b><span>${s[0]}</span></div>`).join('');
document.getElementById('warn').innerHTML=D.overfills.length?D.overfills.map(o=>`<div class="warn">OVERFILL: ${o}</div>`).join(''):'';
const maxV=Math.max(0,...D.plates.flatMap(p=>p.cells.map(c=>c.volume)));
const col=v=>{const t=maxV?v/maxV:0;return `hsl(${168-70*t} 70% ${22+46*t}%)`};
document.getElementById('plates').innerHTML=D.plates.map(p=>{
 const head='<tr><th></th>'+p.cols.map(c=>`<th>${c}</th>`).join('')+'</tr>';
 const rows=p.rows.map(r=>'<tr><th>'+r+'</th>'+p.cells.filter(c=>c.well[0]===r).map(c=>
  `<td class="${c.volume>0?'on':''}" data-w="${c.well}" data-v="${c.volume}" data-t="${c.tipSet}"
   style="background:${c.volume>0?col(c.volume):'#0d1117'}">${c.volume>0?c.volume:''}</td>`).join('')+'</tr>').join('');
 return `<h2>${p.label} — ${p.lit} wells, ${p.ul} ul</h2>
 <div class="plate"><table>${head}${rows}</table>
 <div class="legend"><span class="sw" style="background:${col(maxV)}"></span>${maxV} ul
 <span class="sw" style="background:${col(maxV/2)}"></span>${(maxV/2).toFixed(1)} ul
 <span class="sw" style="background:#0d1117"></span>empty</div></div>`}).join('');
document.getElementById('tl').innerHTML=D.timeline.map(r=>r.code==='W'
 ?`<li class="wash"><span>line ${r.line}</span><span>W</span><span>new tips</span><span></span><span></span><span>set ${r.tipSet}</span></li>`
 :`<li><span>line ${r.line}</span><span>${r.code}</span><span>${r.label}</span><span>${r.position}</span>
   <span>${r.volume} ul</span><span>set ${r.tipSet}${r.channel?' ch'+r.channel:''}</span></li>`).join('');
document.querySelectorAll('td').forEach(td=>{
 td.addEventListener('mousemove',e=>{tip.style.display='block';
  tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';
  tip.textContent=`${td.dataset.w}  ${td.dataset.v} ul  tip set ${td.dataset.t}`});
 td.addEventListener('mouseleave',()=>tip.style.display='none')});
</script>"""


_PAGE = _page()


def serve(html_path: Path, port: int, open_browser: bool) -> None:
    html = html_path.read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    url = f"http://127.0.0.1:{port}/"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"worklist viewer on {url}  ({html_path})")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        httpd.server_close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Visualize a FluentControl worklist")
    p.add_argument("worklist", help=".gwl or .csv worklist file")
    p.add_argument("--capacity", type=float, default=200.0, help="DiTi capacity in ul (default 200)")
    p.add_argument("--out", help="HTML output path (default: <worklist>.html)")
    p.add_argument("--serve", nargs="?", const=8090, type=int, metavar="PORT",
                   help="serve the page on 127.0.0.1 and open the browser")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = p.parse_args(argv)

    path = Path(args.worklist)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    a = analyze(path, args.capacity)
    print(ascii_summary(a))
    print(ascii_grid(a))

    out = Path(args.out) if args.out else path.with_suffix(".html")
    out.write_text(to_html(a), encoding="utf-8")
    print(f"\nHTML: {out}")

    if args.serve:
        serve(out, args.serve, not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
