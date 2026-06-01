"""Drive the LOCAL LM through the whole corpus, bucket by bucket.

Part 4 of the full-corpus pass harness. Thin loop over every family bucket with
`pending` rows in the coverage ledger; calls `mine_bucket.py` for each. Because a
`reassign:` verdict moves a protocol to a different bucket (still `pending`), the
driver repeats in ROUNDS until no reassignments remain (or --max-rounds), so a
mis-triaged protocol gets mined under its corrected family. Prints a final
summary; Claude reads only that, not the 248 protocols.

Usage:
    PYTHONPATH=. python scripts/mine_corpus.py
    PYTHONPATH=. python scripts/mine_corpus.py --batch 4 --max-rounds 3
    PYTHONPATH=. python scripts/mine_corpus.py --only bead-cleanup,pcr-setup
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

from triage_corpus import BUCKET_TARGET, LEDGER  # type: ignore

HERE = Path(__file__).resolve().parent
MINE = HERE / "mine_bucket.py"
SKIP = {"new-family-candidate"}


def _ledger_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(LEDGER.open(encoding="utf-8")))


def _pending_by_bucket(rows: list[dict[str, str]]) -> Counter:
    return Counter(r["bucket"] for r in rows if r["status"] == "pending" and r["bucket"] not in SKIP)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated buckets to restrict to")
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    if not LEDGER.is_file():
        return print(f"no ledger at {LEDGER} — run scripts/triage_corpus.py first.") or 2
    only = {b.strip() for b in args.only.split(",") if b.strip()}

    extra = []
    if args.endpoint:
        extra += ["--endpoint", args.endpoint]
    if args.model:
        extra += ["--model", args.model]

    for rnd in range(1, args.max_rounds + 1):
        pending = _pending_by_bucket(_ledger_rows())
        if only:
            pending = Counter({b: n for b, n in pending.items() if b in only})
        if not pending:
            print(f"[corpus] round {rnd}: nothing pending — done.")
            break
        print(f"[corpus] === round {rnd}/{args.max_rounds}: "
              f"{sum(pending.values())} pending across {len(pending)} buckets ===")
        for bucket in sorted(pending, key=lambda b: -pending[b]):
            cmd = [sys.executable, str(MINE), bucket, "--batch", str(args.batch), *extra]
            print(f"[corpus] -> {bucket} ({pending[bucket]} pending)")
            subprocess.run(cmd, cwd=str(HERE.parent), check=False)
    else:
        print(f"[corpus] hit max-rounds={args.max_rounds}; some reassignments may remain.")

    # final summary
    rows = _ledger_rows()
    by_status = Counter(r["status"] for r in rows)
    print("\n[corpus] ===== FINAL SUMMARY =====")
    print(f"[corpus] {len(rows)} protocols")
    for s, n in by_status.most_common():
        print(f"  {n:4d}  {s}")
    mined = [r for r in rows if r["status"] == "mined"]
    if mined:
        print(f"\n[corpus] {len(mined)} protocols flagged NOVEL — fragments are in "
              f"docs/skill-authoring/_drafts/<skill>.additions.md; Claude audits + "
              f"integrates + validates before committing:")
        for r in mined:
            print(f"  ({r['bucket']}) {r['folder']}: {r['contributed'][:70]}")
    nfc = [r["folder"] for r in rows if r["bucket"] == "new-family-candidate" and r["status"] == "pending"]
    if nfc:
        print(f"\n[corpus] {len(nfc)} new-family-candidate (Claude reviews by hand): "
              f"{', '.join(nfc)}")
    pending_left = by_status.get("pending", 0) - len(nfc)
    print(f"\n[corpus] pending in known families: {max(pending_left,0)} "
          f"(0 = mining complete; drafts await Claude validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
