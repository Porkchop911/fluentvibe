"""Distil biology / biochemistry workflow skills from the Opentrons protocols, with Strata.

Two stages, both resumable (rows already written are skipped):

``extract``  Strata reads each biology card (``opentrons_bio_cards.py``) and returns
             what the protocol does: its workflow family (an existing family skill or
             a proposed new one), the stages with reagents, volumes, mixing,
             incubations, temperatures and magnet times, the rules a scientist would
             insist on, the operator steps, and problems of the source.
             -> ``<dir>/extractions.jsonl``

``draft``    Per family: the extractions are counted up (how many protocols, typical
             volumes and times), and Strata drafts the family skill from them and the
             current skill text. Drafts go to ``<dir>/staging/<family>.md`` for review;
             nothing under fluentvibe/_assets is touched.

    python scripts/opentrons_bio_distill.py extract --limit 20
    python scripts/opentrons_bio_distill.py draft --min-protocols 5
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SKILLS = REPO / "fluentvibe" / "_assets" / "config" / "skills" / "family"

EXTRACT_PROMPT = """You are a molecular biologist and biochemist reading what a liquid-handling robot did.
You get one Opentrons protocol as a card: its title, description, labware with the labels the author gave,
and the run, step by step as the robot executed it ("4x [ ... ]" = the bracketed steps four times; volumes
in ul; "8-ch" = 8 channels at once; waits, temperatures, shaking, magnet and operator pauses as logged).

Say what the protocol does biologically. Use only what the card shows or what follows from the named kit or
assay with certainty; mark anything else as inferred. Answer with ONE JSON object, nothing else:
{
 "family": one of FAMILIES, or "new:<short-kebab-name>" when none fits,
 "workflow": "one sentence: the assay or prep and its product",
 "kit_or_method": "named kit / method, or null",
 "sample": "what goes in (e.g. 'cell lysate', 'gDNA 100 ng', 'serum'), or null",
 "product": "what comes out and where it ends up",
 "stages": [{"name": "...", "purpose": "...", "reagent": "...", "volume_ul": number or null,
             "per": "well|sample|column|plate", "mix": "e.g. '10x 50 ul' or null",
             "incubation_min": number or null, "temperature_c": number or null, "shake_rpm": number or null,
             "magnet_min": number or null, "repeats": number or null, "inferred": true|false}],
 "critical_rules": ["rules this workflow depends on, e.g. 'fresh tips for each eluate', 'ethanol 80 % made
                     fresh', 'beads resuspended before every draw'; only ones this protocol shows"],
 "operator_steps": ["what a person does off the robot: sealing, centrifuge, thermocycler, reader"],
 "source_problems": ["anything that looks wrong or risky in the protocol itself"]
}
FAMILIES: """


DRAFT_PROMPT = """You maintain the skill file for one workflow family of a protocol-authoring assistant for a
Tecan Fluent liquid handler. The assistant writes protocols from requests and documents; the skill tells it what
the workflow is biologically, which steps and values must be kept, typical values, what to ask when the request
leaves numbers open, and how it maps onto the deck (blocks such as distribute_reagent, stamp, mix_wells,
separate/remove_liquid/release for the magnet, offdeck_step, thermal_step).

You get the current skill (if any), counts over N real protocols of this family, and up to 12 of their
extractions. Write the improved skill body in Markdown, same section layout as the current one
("## What the product is", "## Steps and what must be kept", "## Typical values and what to ask",
"## On this deck"). Rules:
- Every typical value must come from the counts or extractions; give ranges and say how many protocols.
- Keep what the current skill says unless the evidence contradicts it; then say what changed and why.
- Name variants that differ biologically (e.g. RNA vs DNA, single vs dual size selection) as separate bullets.
- No vendor marketing, no values you cannot point to. At most ~70 lines.
Start with one line "description: <one sentence for selection: what the family covers, words that trigger it>",
then the body."""


def _families() -> list[str]:
    return sorted(p.stem for p in SKILLS.glob("family-*.md"))


def _client():
    from fluentvibe.authoring.lm_client import LMStudioChatClient
    return LMStudioChatClient(request_timeout_s=1800)


def _json(content: str) -> dict | None:
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def extract(args) -> int:
    cards = [json.loads(line) for line in args.cards.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        wanted = set(args.only.split(","))
        cards = [c for c in cards if c["id"] in wanted]
    out = args.dir / "extractions.jsonl"
    done = _done(out)
    client = _client()
    system = EXTRACT_PROMPT + ", ".join(_families())
    n = 0
    for card in cards:
        if card["id"] in done:
            continue
        if args.limit and n >= args.limit:
            break
        t0 = time.monotonic()
        row = {"id": card["id"], "title": card["title"]}
        try:
            message = client.complete(messages=[{"role": "system", "content": system},
                                                {"role": "user", "content": json.dumps(card, ensure_ascii=False)}],
                                      tools=[])
            data = _json(message.get("content") or "")
            row.update(data if data else {"error": "unreadable", "raw": (message.get("content") or "")[:1500]})
        except Exception as exc:  # noqa: BLE001 - recorded, the next card goes on
            row["error"] = f"{type(exc).__name__}: {exc}"[:400]
        row["seconds"] = round(time.monotonic() - t0, 1)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        n += 1
        print(f"{n:4d} {card['id']:<40} {row.get('family') or row.get('error')!s:<40} {row['seconds']:>6}s",
              flush=True)
    return 0


def _stats(rows: list[dict]) -> dict:
    """Per stage purpose word: how often, volumes and times seen."""
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for st in r.get("stages") or []:
            key = (st.get("name") or "?").lower()[:40]
            for field in ("volume_ul", "incubation_min", "temperature_c", "shake_rpm", "magnet_min", "repeats"):
                if isinstance(st.get(field), (int, float)):
                    by[key][field].append(st[field])
            by[key]["protocols"].append(r["id"])

    def span(values):
        values = sorted(values)
        return {"n": len(values), "min": values[0], "median": statistics.median(values), "max": values[-1]}

    stats = {}
    for key, fields in sorted(by.items(), key=lambda kv: -len(set(kv[1]["protocols"]))):
        protocols = len(set(fields.pop("protocols")))
        if protocols < 2:
            continue
        stats[key] = {"protocols": protocols, **{f: span(v) for f, v in fields.items() if v}}
    return dict(list(stats.items())[:40])


def draft(args) -> int:
    rows = [json.loads(line) for line in (args.dir / "extractions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    rows = [r for r in rows if not r.get("error") and r.get("family")]
    by_family: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_family[str(r["family"]).strip()].append(r)
    staging = args.dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    client = _client()
    for family, members in sorted(by_family.items(), key=lambda kv: -len(kv[1])):
        if len(members) < args.min_protocols or (args.only and family not in args.only.split(",")):
            continue
        target = staging / f"{family.replace('new:', 'new-')}.md"
        if target.exists():
            continue
        current = SKILLS / f"{family}.md"
        material = {
            "family": family, "protocols": len(members),
            "current_skill": current.read_text(encoding="utf-8") if current.exists() else None,
            "stage_counts": _stats(members),
            "critical_rules_seen": _top([x for r in members for x in r.get("critical_rules") or []]),
            "operator_steps_seen": _top([x for r in members for x in r.get("operator_steps") or []]),
            "extractions": [{k: r.get(k) for k in ("id", "title", "workflow", "kit_or_method", "sample", "product",
                                                    "stages", "critical_rules")} for r in members[:12]],
        }
        t0 = time.monotonic()
        message = client.complete(messages=[{"role": "system", "content": DRAFT_PROMPT},
                                            {"role": "user", "content": json.dumps(material, ensure_ascii=False)}],
                                  tools=[])
        sources = ", ".join(r["id"] for r in members[:40])
        target.write_text(f"<!-- draft by Strata from {len(members)} Opentrons protocols: {sources} -->\n"
                          + (message.get("content") or ""), encoding="utf-8")
        print(f"{family:<40} {len(members):4d} protocols -> {target.name} ({time.monotonic() - t0:.0f}s)", flush=True)
    return 0


def _top(items: list[str], n: int = 25) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for x in items:
        counts[x.strip().lower()[:160]] += 1
    return [f"{c}x {k}" for k, c in sorted(counts.items(), key=lambda kv: -kv[1])[:n]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("extract", "draft"))
    ap.add_argument("--dir", type=Path, default=Path("build/eval/opentrons-bio"))
    ap.add_argument("--cards", type=Path, default=None, help="default: <dir>/cards.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="extract: stop after this many new cards")
    ap.add_argument("--only", default="", help="comma-separated protocol ids (extract) or families (draft)")
    ap.add_argument("--min-protocols", type=int, default=5, help="draft: families with at least this many")
    args = ap.parse_args()
    args.cards = args.cards or args.dir / "cards.jsonl"
    return extract(args) if args.stage == "extract" else draft(args)


if __name__ == "__main__":
    raise SystemExit(main())
