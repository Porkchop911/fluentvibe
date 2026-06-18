"""Repeatable quality harness for the authoring pipeline.

Drives N real generations of a canonical case (default: the Nanopore
SQK-RBK114 library prep on the ``sat_1080_test`` profile), scores each with
:mod:`fluentvibe.authoring.eval_rubric`, and writes a timestamped report so
"generation quality" becomes a *measured number* — comparable before/after a
skill edit, a model swap, or a KV-cache setting change.

Needs the local LM up (LM Studio). Each run is independent; a transport error on
one run is recorded, not fatal.

Usage:
    python scripts/eval_authoring.py --runs 5
    python scripts/eval_authoring.py --runs 3 --out build/eval/before-skill-edit
    python scripts/eval_authoring.py --pdf path/to/protocol.pdf --prompt "..."
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluentvibe.authoring.eval_rubric import ALL_KEYS, RubricResult, score_protocol  # noqa: E402

DEFAULT_PROFILE = REPO / "build" / "workspaces" / "sat_1080_test"
DEFAULT_PDF = Path(
    r"C:\Users\Niko\Downloads"
    r"\rapid-sequencing-v14-amplicon-sequencing-sqk-rbk114-24-or-sqk-document-document"
    r"-MinION-en-RAA_9198_v114_revM_17Oct2025-38.pdf"
)
DEFAULT_PROMPT = "Automate the library prep described in the attached protocol document."


def _activate_profile(profile_dir: Path) -> tuple[str, str]:
    """Set the env a generation needs and return ``(workspace_name, guid)``.

    Mirrors ``cli.py:_activate_profile`` so the harness drives the exact same
    profile-bound path as ``python -m fluentvibe author --profile``.
    """
    from fluentvibe.authoring.grounding import CURRENT_WORKTABLE_ENV
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV, resolve_profile

    rp = resolve_profile(profile_dir)
    os.environ[PROFILE_DIR_ENV] = str(rp.root)
    os.environ.setdefault(CURRENT_WORKTABLE_ENV, str(rp.current_worktable))
    return rp.workspace_name, rp.workspace_guid


def _build_prompt(user_text: str, pdf: Path) -> tuple[str, str]:
    """Return ``(prompt_with_attachment, extracted_source_text)``.

    The marker ``Attached file context:`` is what turns on the pipeline's
    document-adherence coverage gate (see ``tools.py:_has_source_document_context``).
    """
    from fluentvibe.authoring.attachments import extract_file_text

    text, method, pages, warnings = extract_file_text(pdf)
    block = "\n".join(
        [
            user_text.strip(),
            "",
            "Attached file context:",
            "",
            f"--- Attached file: {pdf.name} ---",
            f"Extraction method: {method}",
            *([f"Page count: {pages}"] if pages is not None else []),
            *[f"Extraction warning: {w}" for w in warnings],
            "Extracted text follows:",
            text,
            f"--- End attached file: {pdf.name} ---",
        ]
    )
    return block, text


def _run_once(service, prompt: str, source_text: str, run_dir: Path, ws_name: str,
              ws_guid: str, lab_scope: str, simulate: bool, retry_budget: int):
    run_dir.mkdir(parents=True, exist_ok=True)
    result = service.author(
        prompt,
        output_dir=run_dir,
        workspace_name=ws_name,
        workspace_guid=ws_guid,
        lab_scope=lab_scope,
        retry_budget=retry_budget,
    )
    py_path = None
    if result.validation is not None and result.validation.python_path:
        py_path = Path(result.validation.python_path)
    # A run can end in `failure` (e.g. nudged past the retry budget) yet still
    # have authored a compiling draft — score what was actually produced, not
    # just the accepted result. Prefer (in order): the accepted python_path, the
    # best draft the graph kept, the final code string, then the newest
    # ``lm_authoring_attempt*.py`` the tool wrote to disk.
    if py_path is None or not py_path.exists():
        code = result.best_draft_code or result.generated_code
        if code:
            py_path = run_dir / "best_draft.py"
            py_path.write_text(code, encoding="utf-8")
        else:
            drafts = sorted(run_dir.glob("lm_authoring_attempt*.py"))
            py_path = drafts[-1] if drafts else None
    rubric: RubricResult | None = None
    if py_path is not None and py_path.exists():
        rubric = score_protocol(
            py_path.read_text(encoding="utf-8"),
            source_text=source_text,
            simulate=simulate,
        )
    return result, rubric, py_path


def _write_reports(out: Path, rows: list[dict]) -> None:
    fields = ["run", "status", "score", *ALL_KEYS, "python", "error"]
    with (out / "scores.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})

    n = len(rows)
    scored = [r for r in rows if r.get("score") != ""]
    lines = [
        "# Authoring eval summary",
        "",
        f"_Runs: {n}. Scored: {len(scored)}._",
        "",
        "## Per-invariant pass-rate (pass / applicable)",
        "",
        "| invariant | pass | fail | na | pass-rate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key in ALL_KEYS:
        statuses = [r.get(key, "") for r in rows]
        p = statuses.count("pass")
        f = statuses.count("fail")
        na = statuses.count("na")
        denom = p + f
        rate = f"{p / denom:.0%}" if denom else "n/a"
        lines.append(f"| {key} | {p} | {f} | {na} | {rate} |")
    avg = sum(float(r["score"]) for r in scored) / len(scored) if scored else 0.0
    lines += ["", f"**Mean score (scored runs): {avg:.2f}**", ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=None, help="output dir (default build/eval/<timestamp>)")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE))
    ap.add_argument("--pdf", default=str(DEFAULT_PDF))
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--lab-scope", default="skills")
    ap.add_argument("--retry-budget", type=int, default=2,
                    help="author retry budget; staged multi-stage protocols need more "
                         "(max_iterations = max(8, retry_budget + 8))")
    ap.add_argument("--model", default=None)
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--no-simulate", action="store_true", help="source tier only")
    args = ap.parse_args()

    profile_dir = Path(args.profile)
    if not (profile_dir / "workspace_profile.json").exists():
        raise SystemExit(f"profile not found: {profile_dir}")
    pdf = Path(args.pdf)
    if not pdf.exists():
        raise SystemExit(f"source PDF not found: {pdf}")

    ws_name, ws_guid = _activate_profile(profile_dir)
    prompt, source_text = _build_prompt(args.prompt, pdf)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else REPO / "build" / "eval" / stamp
    out.mkdir(parents=True, exist_ok=True)
    print(f"[eval] profile={ws_name} runs={args.runs} out={out}")

    from fluentvibe.authoring import PromptAuthoringService

    service_kwargs = {}
    if args.endpoint:
        service_kwargs["endpoint"] = args.endpoint
    if args.model:
        service_kwargs["model"] = args.model
    service = PromptAuthoringService(**service_kwargs)

    rows: list[dict] = []
    for i in range(1, args.runs + 1):
        run_dir = out / f"run-{i:02d}"
        row: dict = {"run": i}
        print(f"[eval] run {i}/{args.runs} …", flush=True)
        try:
            result, rubric, py_path = _run_once(
                service, prompt, source_text, run_dir, ws_name, ws_guid,
                args.lab_scope, not args.no_simulate, args.retry_budget,
            )
            row["status"] = result.status.value
            if result.failure_category is not None:
                row["error"] = result.failure_category.value
            if py_path is not None and py_path.exists():
                dest = out / f"run-{i:02d}.py"
                dest.write_text(py_path.read_text(encoding="utf-8"), encoding="utf-8")
                row["python"] = dest.name
            if rubric is not None:
                row["score"] = f"{rubric.score:.3f}"
                row.update({inv.key: inv.status for inv in rubric.invariants})
                print(f"        status={row['status']} score={row['score']}")
            else:
                row["score"] = ""
                print(f"        status={row['status']} (no protocol produced)")
        except Exception as exc:  # noqa: BLE001 - record, never abort the batch
            row["status"] = "error"
            row["score"] = ""
            row["error"] = f"{type(exc).__name__}: {exc}"
            (run_dir / "error.txt").parent.mkdir(parents=True, exist_ok=True)
            (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            print(f"        ERROR: {row['error']}")
        rows.append(row)

    _write_reports(out, rows)
    print(f"[eval] wrote {out / 'scores.csv'}")
    print(f"[eval] wrote {out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
