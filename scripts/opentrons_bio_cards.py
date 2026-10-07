"""Biology cards: what each Opentrons protocol does, compact enough for a model to read.

From the baseline's traces (``build/eval/opentrons-baseline/<id>/trace.json``) and
the protocol index. One card per protocol: title, description, categories, the
labware with its labels, and the run as stages - consecutive moves of the same
kind between the same labware merged ("8x dispense 20 ul WORKING REAGENT ->
WORKING PLATE"), with mixes, waits, temperatures, shaking, magnet and operator
pauses in between. Cards are the input for distilling workflow skills.

    python scripts/opentrons_bio_cards.py --out build/eval/opentrons-bio/cards.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MAX_LINES = 120          # stage lines per card; long protocols are cut in the middle


def _label(trace: dict, key: str | None) -> str:
    if not key:
        return "?"
    info = trace["labware"].get(key) or {}
    return info.get("display") or info.get("load_name") or key.split(":")[-1]


def _secs(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?) minutes? and (\d+(?:\.\d+)?) seconds", text)
    return float(m.group(1)) * 60 + float(m.group(2)) if m else 0.0


def stages(trace: dict) -> list[str]:
    """The run as merged lines."""
    lines: list[str] = []
    last_key = None
    count = 0
    pending_wait = 0.0

    def flush_wait():
        nonlocal pending_wait
        if pending_wait >= 30:      # short delays are liquid handling, not biology
            lines.append(f"wait {pending_wait / 60:.1f} min")
        pending_wait = 0.0

    mix_left = 0                 # aspirate/dispense events that belong to the last mix
    mix_volume = 0.0
    for e in trace["events"]:
        kind = e["kind"]
        if mix_left and kind in ("aspirate", "dispense") and abs(float(e.get("volume") or 0) - mix_volume) < 0.01:
            mix_left -= 1
            continue
        mix_left = 0
        if kind == "delay":
            pending_wait += float(e.get("seconds") or _secs(e.get("text", "")))
            continue
        if kind in ("aspirate", "dispense"):
            key = (kind, _label(trace, e.get("labware")), round(float(e.get("volume") or 0), 1), e.get("channels", 1))
        elif kind == "mix":
            m = re.search(r"Mixing (\d+) times with a volume of ([\d.]+)", e.get("text", ""))
            key = ("mix", m.group(1) if m else "?", m.group(2) if m else "?")
            if m:
                mix_left, mix_volume = 2 * int(m.group(1)), float(m.group(2))
        elif kind in ("pick_up_tip", "drop_tip", "return_tip", "other"):
            if kind == "other" and re.match(r"(Transferring|Distributing|Consolidating)", e.get("text", "")):
                flush_wait()
                lines.append("# " + e["text"][:140])
                last_key = None
            continue
        else:
            key = (kind, e.get("text", "")[:140])
        flush_wait()
        if key == last_key:
            count += 1
            lines[-1] = _line(key, count)
        else:
            last_key, count = key, 1
            lines.append(_line(key, 1))
    flush_wait()
    lines = _fold_repeats(lines)
    if len(lines) > MAX_LINES:
        half = MAX_LINES // 2
        lines = lines[:half] + [f"... {len(lines) - MAX_LINES} lines left out ..."] + lines[-half:]
    return lines


def _fold_repeats(lines: list[str], longest: int = 8) -> list[str]:
    """A block of lines repeated back to back -> "4x [ a | b | c ]"."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        best_k, best_n = 1, 1
        for k in range(1, min(longest, (len(lines) - i) // 2) + 1):
            block = lines[i:i + k]
            n = 1
            while lines[i + n * k:i + (n + 1) * k] == block:
                n += 1
            if n > 1 and n * k > best_n * best_k:
                best_k, best_n = k, n
        if best_n > 1:
            block = lines[i:i + best_k]
            out.append(f"{best_n}x [ {' | '.join(block)} ]" if best_k > 1 else f"{best_n}x ( {block[0]} )")
            i += best_n * best_k
        else:
            out.append(lines[i])
            i += 1
    return out


def _line(key: tuple, n: int) -> str:
    times = f"{n}x " if n > 1 else ""
    if key[0] in ("aspirate", "dispense"):
        _, label, vol, ch = key
        # Per tip: an 8-channel aspirate of 750 ul is 750 ul in each of 8 tips (a
        # load that is dispensed in parts), not 750 ul per well.
        head = f" ({ch} tips at once)" if ch and ch > 1 else ""
        return f"{times}{key[0]} {vol:g} ul per tip {'from' if key[0] == 'aspirate' else 'into'} {label}{head}"
    if key[0] == "mix":
        return f"{times}mix {key[1]}x {key[2]} ul"
    return f"{times}{key[0]}: {key[1]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=Path, default=Path("build/eval/opentrons-baseline"),
                    help="the baseline run with a trace.json per protocol (relative to the working directory)")
    ap.add_argument("--out", type=Path, default=Path("build/eval/opentrons-bio/cards.jsonl"))
    args = ap.parse_args()

    from fluentvibe.protocol_index import load_index, related_protocols

    entries = {e.id: e for e in load_index()}
    related = related_protocols(list(entries.values()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with args.out.open("w", encoding="utf-8") as fh:
        for pid, e in sorted(entries.items()):
            path = args.baseline / pid.replace(":", "_").replace("/", "_") / "trace.json"
            if not path.exists():
                continue
            try:
                trace = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not any(ev["kind"] in ("aspirate", "dispense") for ev in trace["events"]):
                continue
            labware = sorted({f"{v.get('display') or v.get('load_name')} [{v.get('load_name')}]"
                              for v in trace["labware"].values() if not v.get("is_tiprack")})
            card = {
                "id": pid, "title": e.title, "categories": e.categories, "robot": e.robot,
                "description": e.description[:1200], "related": related.get(pid, [])[:6],
                "labware": labware, "modules": e.modules, "run": stages(trace),
            }
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} cards -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
