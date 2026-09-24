"""Re-score saved authoring outputs with the current rubric.

Runs made at different times were scored by different rubric versions. This
scores every protocol again with the same rubric, source document and Bench
Spec, so runs are comparable:

    python scripts/rescore_eval.py build/eval/run-a build/eval/run-b \\
        --pdf-text path/to/extracted.txt --spec examples/ont_rbk114_spec.json

Each argument is either a ``.py`` protocol or a directory; for a directory the
newest ``lm_authoring_attempt*.py`` / ``best_draft.py`` under each run folder is
scored. Prints a Markdown table (one row per protocol, one column per
invariant) to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluentvibe.authoring.bench_spec import validate_bench_spec  # noqa: E402
from fluentvibe.authoring.eval_rubric import ALL_KEYS, score_protocol  # noqa: E402

_SHORT = {"pass": "✓", "fail": "✗", "na": "–"}


def _protocols(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    found: list[Path] = []
    run_dirs = sorted({p.parent for p in target.rglob("*.py")})
    for run_dir in run_dirs:
        candidates = sorted(run_dir.glob("lm_authoring_attempt*.py"),
                            key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0))
        best = run_dir / "best_draft.py"
        if candidates:
            found.append(candidates[-1])
        elif best.exists():
            found.append(best)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="+", type=Path)
    ap.add_argument("--pdf-text", type=Path, default=None, help="extracted source document text")
    ap.add_argument("--spec", type=Path, default=None, help="Bench Spec JSON to score conformance against")
    args = ap.parse_args()

    source_text = args.pdf_text.read_text(encoding="utf-8") if args.pdf_text else None
    spec = None
    if args.spec:
        spec, problems = validate_bench_spec(json.loads(args.spec.read_text(encoding="utf-8")))
        if spec is None:
            raise SystemExit(f"invalid spec: {[p.message for p in problems]}")

    header = ["protocol", "score", *ALL_KEYS]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for target in args.targets:
        for path in _protocols(target):
            result = score_protocol(
                path.read_text(encoding="utf-8"), source_text=source_text,
                filename=str(path.resolve()), spec=spec,
            )
            cells = [_SHORT.get(result.get(k).status, "?") if result.get(k) else "" for k in ALL_KEYS]
            label = str(path.relative_to(REPO) if path.is_relative_to(REPO) else path)
            print(f"| {label} | {result.score:.2f} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
