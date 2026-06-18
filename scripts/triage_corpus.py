"""Triage the whole Opentrons corpus into family buckets — deterministic, no LM.

Part 1 of the full-corpus pass harness (see
docs/skill-authoring/coverage-ledger.md). This script costs **zero** model
tokens: it is pure-Python regex over the local protocol files. It scans every
protocol dir under `D:\\Opentron_protocols` (enumerated from `index.json`),
extracts signals (modules, pipette/channel type, key terms), assigns each dir to
one family bucket, and writes/merges the coverage ledger CSV.

Idempotent + resumable: a row whose `status` is anything other than `pending` is
left untouched (its human/Claude-set disposition wins); only `pending` rows get
their `bucket` / `candidate_tags` / `signals` / `target_skill` (re)filled.

Usage:
    PYTHONPATH=. python scripts/triage_corpus.py
    PYTHONPATH=. python scripts/triage_corpus.py --corpus D:\\Opentron_protocols
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "docs" / "skill-authoring"
LEDGER = KIT / "coverage-ledger.csv"
SUMMARY = KIT / "coverage-ledger.md"
DEFAULT_CORPUS = Path(r"D:\Opentron_protocols")

FIELDS = [
    "folder", "slug", "bucket", "candidate_tags", "signals",
    "status", "contributed", "target_skill",
]

# bucket id -> target skill name (the skill the bucket's protocols feed).
# `new-family-candidate` has no target yet — Claude decides axis/name on review.
BUCKET_TARGET = {
    "bead-cleanup": "family-bead-cleanup-spri",
    "protein-assay": "family-protein-assay",
    "elisa": "family-elisa",
    "serial-dilution": "family-serial-dilution",
    "normalization": "family-normalize-to-target",
    "cherrypicking": "family-cherrypicking",
    "pcr-setup": "family-pcr-setup",
    "ngs-library-prep": "family-ngs-library-prep",
    "cell-seeding": "family-cell-seeding",
    "purification": "family-purification",
    "pooling": "family-pooling",
    "reagent-distribution": "family-reagent-distribution",
    "plate-reformatting": "family-plate-reformatting",
    "simple-transfer": "family-simple-transfer",
    "new-family-candidate": "",
}

# Ordered classification rules: first bucket whose pattern matches the blob wins.
# Order matters — most specific / highest-signal families first. Each protocol
# also collects ALL matching tags into `candidate_tags` for transparency.
RULES: list[tuple[str, str]] = [
    # specific assays / preps before the generic families
    ("elisa", r"\belisa\b|signal development|target capture|tmb|hrp conjugate"),
    ("ngs-library-prep", r"library prep|nextera|illumina dna|illumina rna|kinnex|tagment|adapter ligation|"
                         r"size selection.{0,20}librar"),
    ("pcr-setup", r"mastermix|master mix|\bqpcr\b|rt-pcr|thermocycler|colony pcr|"
                  r"pcr (setup|prep|reaction|assembl|plate setup)|reaction mix|pcr_prep"),
    ("bead-cleanup", r"ampure|spri|magbead|mag-?bind|magnetic bead|nucleomag|rnadvance|"
                     r"size selection|kingfisher|dna extraction|rna extraction|nucleic acid|"
                     r"bind.{0,12}wash.{0,12}elut|zymo|omega bio|maxwell"),
    ("protein-assay", r"\bbca\b|bradford|lowry|protein assay|protein quant|peptide assay"),
    ("purification", r"\bc18\b|purification|desalt|solid[- ]phase|spe\b|resin|his-?tag|affinity|filter plate"),
    ("serial-dilution", r"serial dilution|serial-dilution|titration|dose[- ]response|standard curve dilution"),
    ("normalization", r"normaliz|equal.{0,8}concentration|dna norm|od[- ]?normaliz|od600|target concentration"),
    ("cherrypicking", r"cherry[- ]?pick|hit[- ]?picking|pick[- ]?list|consolidat.{0,12}csv"),
    ("pooling", r"pooling|\bpool\b"),
    ("cell-seeding", r"cell seed|seeding|transfection|cell viability|cytotox|passage|splitting|"
                     r"culture plate|spheroid|organoid"),
    ("reagent-distribution", r"distribut|broth|plate fill|plate-fill|reagent fill|aliquot|"
                             r"reservoir to plate|lb (broth|distribution)|dispense .* into"),
    ("plate-reformatting", r"reformat|384.*96|96.*384|copy plate|replicat|rearray|interleav"),
    ("simple-transfer", r"simple transfer|\btransfer\b"),
]

# new-family-candidate sub-tags: clusters none of the 14 families cover, but which
# are still liquid-handling. Tagged for Claude's review when bucket falls through.
NEW_FAMILY_HINTS: list[tuple[str, str]] = [
    ("staining", r"stain|h&e|immunostain|fixation|permeabiliz"),
    ("absorbance-read", r"absorbance|plate read|od read|fluoresc|luminesc|endpoint read"),
    ("washing", r"\bwash(es|ing)?\b|wash cycle|plate wash"),
    ("colony-agar", r"colony|agar|inoculat|streak|spread plate"),
    ("blood-clinical", r"\bblood\b|plasma|serum|saliva|swab|whole blood"),
]

MODULE_RE = re.compile(r"load_module\(\s*['\"]?([a-zA-Z0-9_\- ]+)", re.IGNORECASE)
PIPETTE_RE = re.compile(r"(flex_8channel|flex_96channel|_8channel|_multi|_single|96[- ]?channel)", re.IGNORECASE)


def _read(path: Path, limit: int = 8000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _enumerate_corpus(corpus: Path) -> list[dict[str, str]]:
    index = corpus / "index.json"
    if index.is_file():
        data = json.loads(index.read_text(encoding="utf-8"))
        rows = []
        for e in data.get("downloaded", []):
            folder = e.get("folder") or e.get("slug")
            if folder and (corpus / folder).is_dir():
                rows.append({"folder": folder, "slug": e.get("slug", folder),
                             "protocol_file": e.get("protocol_file", "")})
        if rows:
            return rows
    # fallback: every subdir
    return [{"folder": d.name, "slug": d.name, "protocol_file": ""}
            for d in sorted(corpus.iterdir()) if d.is_dir()]


def _signals(folder_dir: Path, blob: str) -> str:
    mods = sorted({m.strip().lower() for m in MODULE_RE.findall(blob)})
    mods = [m for m in mods if m and not m.startswith(("smart", "context"))]
    pips = sorted({p.lower().replace(" ", "") for p in PIPETTE_RE.findall(blob)})
    parts = []
    if mods:
        parts.append("modules=" + "|".join(mods[:4]))
    if pips:
        parts.append("pipettes=" + "|".join(pips[:3]))
    return "; ".join(parts) or "-"


def classify(folder_dir: Path, protocol_file: str) -> tuple[str, str, str]:
    """Return (bucket, candidate_tags, signals) for one protocol dir."""
    name = folder_dir.name.lower()
    readme = _read(folder_dir / "README.md")
    py_path = (folder_dir / protocol_file) if protocol_file else None
    if not (py_path and py_path.is_file()):
        py_path = next((p for p in folder_dir.glob("*.py")), None)
    py = _read(py_path) if py_path else ""
    blob = f"{name}\n{readme}\n{py}".lower()

    tags: list[str] = [b for b, pat in RULES if re.search(pat, blob)]
    new_tags: list[str] = [t for t, pat in NEW_FAMILY_HINTS if re.search(pat, blob)]

    bucket = tags[0] if tags else "new-family-candidate"
    all_tags = tags + [f"new:{t}" for t in new_tags]
    if not all_tags:
        all_tags = ["unclassified"]
    return bucket, ",".join(all_tags), _signals(folder_dir, blob)


def _load_existing() -> dict[str, dict[str, str]]:
    if not LEDGER.is_file():
        return {}
    with LEDGER.open(newline="", encoding="utf-8") as fh:
        return {r["folder"]: r for r in csv.DictReader(fh)}


def write_summary(rows: list[dict[str, str]]) -> None:
    by_bucket = Counter(r["bucket"] for r in rows)
    by_status = Counter(r["status"] for r in rows)
    lines = [
        "# Coverage ledger summary",
        "",
        f"_Generated by `scripts/triage_corpus.py`. {len(rows)} protocols._",
        "",
        "## By bucket",
        "",
        "| bucket | count | target skill |",
        "| --- | --- | --- |",
    ]
    for b, n in by_bucket.most_common():
        lines.append(f"| {b} | {n} | {BUCKET_TARGET.get(b, '')} |")
    lines += ["", "## By status", "", "| status | count |", "| --- | --- |"]
    for s, n in by_status.most_common():
        lines.append(f"| {s} | {n} |")
    pending = by_status.get("pending", 0)
    lines += ["", f"**Pending (not yet mined): {pending}** — full pass is done when this hits 0.", ""]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    args = ap.parse_args()
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        raise SystemExit(f"corpus dir not found: {corpus}")

    existing = _load_existing()
    entries = _enumerate_corpus(corpus)
    rows: list[dict[str, str]] = []
    refreshed = preserved = 0
    for e in entries:
        folder = e["folder"]
        prev = existing.get(folder)
        if prev and prev.get("status", "pending") != "pending":
            rows.append(prev)
            preserved += 1
            continue
        bucket, tags, signals = classify(corpus / folder, e.get("protocol_file", ""))
        rows.append({
            "folder": folder,
            "slug": e.get("slug", folder),
            "bucket": bucket,
            "candidate_tags": tags,
            "signals": signals,
            "status": (prev or {}).get("status", "pending"),
            "contributed": (prev or {}).get("contributed", "-"),
            "target_skill": BUCKET_TARGET.get(bucket, ""),
        })
        refreshed += 1

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    write_summary(rows)

    dist = Counter(r["bucket"] for r in rows)
    print(f"[triage] {len(rows)} protocols  (refreshed={refreshed} preserved={preserved})")
    print(f"[triage] ledger: {LEDGER}")
    print(f"[triage] summary: {SUMMARY}")
    for b, n in dist.most_common():
        print(f"  {n:4d}  {b}")


if __name__ == "__main__":
    main()
