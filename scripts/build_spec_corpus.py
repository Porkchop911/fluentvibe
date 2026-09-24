"""Build ``fluentvibe/authoring/spec_corpus.json`` from converted Opentrons drafts.

Keeps each draft that parses as a Bench Spec and has at least two steps, one of
them on deck, and adds the protocol's own ``metadata`` (name, description) and
the triage-ledger family. The result is the retrieval index used by
:mod:`fluentvibe.authoring.spec_retrieval`.

    python scripts/build_spec_corpus.py [--specs build/eval/opentrons-specs]
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluentvibe.authoring.bench_spec import validate_bench_spec  # noqa: E402
from fluentvibe.authoring.spec_retrieval import CORPUS_PATH  # noqa: E402

LEDGER = REPO / "docs" / "skill-authoring" / "coverage-ledger.csv"
STEP_KEYS = ("id", "op", "text", "location", "volume_ul", "washes", "wash_ul", "elute_ul", "minutes", "temp_c")


def _metadata(protocol: Path) -> dict[str, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(protocol.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "metadata" for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return {}
            return {k: str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--specs", type=Path, default=REPO / "build" / "eval" / "opentrons-specs")
    ap.add_argument("--out", type=Path, default=CORPUS_PATH)
    args = ap.parse_args()

    families = {}
    if LEDGER.exists():
        with LEDGER.open(encoding="utf-8") as fh:
            families = {row["folder"]: row["bucket"] for row in csv.DictReader(fh)}
    entries = []
    for path in sorted(args.specs.glob("*.json")):
        if path.name == "summary.json":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        source = raw.pop("_source", {})
        spec, _problems = validate_bench_spec(raw)
        steps = raw.get("steps", [])
        if spec is None or len(steps) < 2 or not any(s.get("location") == "deck" for s in steps):
            continue
        meta = _metadata(Path(source.get("protocol", ""))) if source.get("protocol") else {}
        entries.append({
            "name": path.stem,
            "family": families.get(path.stem, "unclassified"),
            "title": meta.get("protocolName") or raw.get("title") or path.stem,
            "description": " ".join(meta.get("description", "").split())[:400],
            "sample_count": raw.get("sample_count"),
            "steps": [{k: s[k] for k in STEP_KEYS if s.get(k) not in (None, [], "")} for s in steps],
        })
    args.out.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
