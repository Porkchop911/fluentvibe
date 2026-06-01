"""Mine one family bucket with the LOCAL LM — collect novelty fragments + verdicts.

Part 3 of the full-corpus pass harness. Given a bucket id (from the coverage
ledger that `triage_corpus.py` produced), this reads the bucket's still-`pending`
protocols and, **using the local LM only**, decides for each whether it adds
*material novelty* to the target skill. Claude does not read the protocols — this
script does, then Claude audits the verdicts and integrates the fragments.

KEY DESIGN: the LM never reproduces the (long) skill — a 27B local model
truncates it. Instead, per batch it returns:

    ```verdicts
    <folder>: nothing-new
    <folder>: novel: <one-line what-it-adds>
    <folder>: reassign: <bucket>          # belongs to a different family
    <folder>: out-of-scope: <why>          # no authorable liquid handling
    ```
    ```additions
    ### <folder>            (only for `novel` folders)
    - <new variable / technique / labware role / structural variant, in
      fluentvibe terms, to fold into the skill>
    ```

The current skill is shown to the LM as read-only context (so it judges novelty
against it). Novelty fragments accumulate (deduped by folder) into
`docs/skill-authoring/_drafts/<skill>.additions.md` — a PROPOSAL file Claude
reads (small) and integrates into the real skill by hand. The skill body is
never machine-rewritten. Verdicts update the ledger:
  novel       -> status=mined       (fragment captured; Claude integrates+validates -> amended)
  nothing-new -> status=nothing-new  (terminal)
  reassign    -> bucket changed, status stays pending (driver re-runs it there)
  out-of-scope-> status=out-of-scope (terminal)

NEVER writes into fluentvibe/_assets/config/skills/.

Usage:
    PYTHONPATH=. python scripts/mine_bucket.py bead-cleanup
    PYTHONPATH=. python scripts/mine_bucket.py pcr-setup --batch 4 --max-batches 3
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

from triage_corpus import BUCKET_TARGET, FIELDS, LEDGER, _read  # type: ignore

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "docs" / "skill-authoring"
DRAFTS = KIT / "_drafts"
SKILLS = REPO / "fluentvibe" / "_assets" / "config" / "skills"
CORPUS = Path(r"D:\Opentron_protocols")
PY_HEAD_LINES = 220

VALID_BUCKETS = set(BUCKET_TARGET)
_VERDICT_BLOCK = re.compile(r"```verdicts?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_ADDITIONS_BLOCK = re.compile(r"```additions?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_FOLDER_HEADER = re.compile(r"^#{2,4}\s*(.+?)\s*$")


def _read_py(folder_dir: Path) -> str:
    p = next((q for q in folder_dir.glob("*.py")), None)
    if not p:
        return "(ProtocolDesigner — no .py; recipe is in the README)"
    text = _read(p, limit=200_000)
    lines = text.splitlines()
    if len(lines) > PY_HEAD_LINES:
        text = "\n".join(lines[:PY_HEAD_LINES]) + f"\n# … ({len(lines)-PY_HEAD_LINES} more lines truncated)"
    return text


def _source_block(folder: str) -> str:
    d = CORPUS / folder
    if not d.is_dir():
        return f"## PROTOCOL {folder}\n(directory not found)"
    readme = _read(d / "README.md", limit=6000)
    return (f"## PROTOCOL {folder}\n### README\n{readme}\n\n"
            f"### protocol.py (head)\n```python\n{_read_py(d)}\n```")


def _current_skill(bucket: str) -> str:
    """The shipped skill body, shown to the LM as read-only context."""
    name = BUCKET_TARGET.get(bucket, "")
    shipped = (SKILLS / "family" / f"{name}.md") if name else None
    return shipped.read_text(encoding="utf-8") if (shipped and shipped.is_file()) else ""


def _build_messages(bucket: str, skill_text: str, folders: list[str]) -> list[dict]:
    cheatsheet = _read(KIT / "opentrons-translation.md", limit=12000)
    buckets_list = ", ".join(sorted(b for b in VALID_BUCKETS if b != "new-family-candidate"))
    system = (
        "You triage Opentrons protocols against ONE fluentvibe '--lab-scope skills' "
        "family skill. fluentvibe exposes wt.liha / wt.mca96 / wt.gripper / "
        "MagnetRack / plates (incl. 384 + deep-well) / troughs / tip boxes / "
        "wt.wait / wt.loop / wt.conditional, scalar aspirate/dispense volumes for "
        "uniform/column-wise work, AND worklists (wt.worklist(csv|gwl) / the Gwl "
        "builder) for PER-WELL distinct volumes and CSV pick lists. So CSV-driven "
        "cherrypicking, per-well normalization, and variable-volume distribution "
        "are IN SCOPE via a worklist — do NOT call per-well CSV unsupported. "
        "Modules (thermocycler/heater-shaker/temp) map to wt.wait/add_comment; "
        "pass variables by name; use native loops; derive dependent volumes; "
        "state capability gaps.\n\n"
        "BAR — material novelty only: a protocol is 'novel' ONLY if it adds a "
        "genuinely new variable, technique, labware role, or structural variant "
        "not already in the CURRENT SKILL. A protocol that is just another "
        "instance of what the skill covers is 'nothing-new'. Be strict — most "
        "protocols in a mature family are nothing-new.\n\n"
        "DO NOT reproduce or rewrite the skill. Output ONLY these two fenced "
        "blocks and nothing else:\n"
        "```verdicts\n"
        "<folder>: nothing-new\n"
        "<folder>: novel: <one short line: what it adds>\n"
        "<folder>: reassign: <bucket>\n"
        "<folder>: out-of-scope: <why>\n"
        "```\n"
        "```additions\n"
        "### <folder>            # ONE section per 'novel' folder only\n"
        "- <the new variable/technique/labware/variant to fold into the skill, "
        "in fluentvibe terms — a few bullet lines, runnable in shape>\n"
        "```\n"
        f"Valid buckets for reassign: {buckets_list}. Every folder gets exactly "
        "one verdict line; only 'novel' folders get an additions section."
    )
    if skill_text.strip():
        skill_section = f"# CURRENT SKILL (read-only context — do NOT reproduce)\n{skill_text}"
        task = f"Judge each protocol against the current '{bucket}' skill. "
    else:
        skill_section = (f"# CURRENT SKILL\n(none — bucket '{bucket}' has no skill yet. Treat "
                         f"protocols that form a coherent authorable family as 'novel' and "
                         f"describe what a new skill would need; reassign/out-of-scope the rest.)")
        task = f"Bucket '{bucket}' has no skill yet. "
    sources = "\n\n".join(_source_block(f) for f in folders)
    user = (
        f"# TRANSLATION CHEATSHEET\n{cheatsheet}\n\n{skill_section}\n\n"
        f"# PROTOCOLS TO MINE ({len(folders)})\n{sources}\n\n"
        f"# TASK\n{task}Emit the two fenced blocks per the contract. Every folder "
        f"above MUST get exactly one verdict line."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse(content: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return (verdicts_by_folder, additions_by_folder)."""
    verdicts: dict[str, str] = {}
    vm = _VERDICT_BLOCK.search(content)
    if vm:
        for line in vm.group(1).splitlines():
            line = line.strip().lstrip("-* ").strip()
            if ":" not in line:
                continue
            folder, verdict = line.split(":", 1)
            verdicts[folder.strip()] = verdict.strip()

    additions: dict[str, str] = {}
    am = _ADDITIONS_BLOCK.search(content)
    if am:
        cur: str | None = None
        buf: list[str] = []
        for line in am.group(1).splitlines():
            h = _FOLDER_HEADER.match(line)
            if h:
                if cur and buf:
                    additions[cur] = "\n".join(buf).strip()
                cur, buf = h.group(1).strip(), []
            elif cur:
                buf.append(line)
        if cur and buf:
            additions[cur] = "\n".join(buf).strip()
    return verdicts, additions


def _apply_verdict(row: dict[str, str], verdict: str) -> None:
    low = verdict.lower()
    if low.startswith("nothing-new"):
        row["status"], row["contributed"] = "nothing-new", "-"
    elif low.startswith("novel"):
        row["status"] = "mined"
        row["contributed"] = verdict.split(":", 1)[-1].strip() if ":" in verdict else verdict
    elif low.startswith("reassign"):
        target = verdict.split(":", 1)[-1].strip().split()[0] if ":" in verdict else ""
        if target in VALID_BUCKETS:
            row["bucket"], row["target_skill"] = target, BUCKET_TARGET.get(target, "")
            row["status"], row["contributed"] = "pending", "-"
        else:
            row["status"], row["contributed"] = "out-of-scope", f"reassign to unknown bucket {target!r}"
    elif low.startswith("out-of-scope"):
        row["status"] = "out-of-scope"
        row["contributed"] = verdict.split(":", 1)[-1].strip() if ":" in verdict else "-"
    else:
        row["contributed"] = f"unparsed verdict: {verdict[:60]}"


def _existing_addition_folders(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {m.group(1).strip() for m in
            (_FOLDER_HEADER.match(l) for l in path.read_text(encoding="utf-8").splitlines()) if m}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bucket")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-batches", type=int, default=0, help="0 = all pending")
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    if args.bucket not in VALID_BUCKETS:
        return print(f"unknown bucket {args.bucket!r}. Valid: {sorted(VALID_BUCKETS)}") or 2
    if args.bucket == "new-family-candidate":
        return print("new-family-candidate is heterogeneous — Claude reviews these "
                     "by hand (they are not auto-mined into one skill).") or 0

    rows = list(csv.DictReader(LEDGER.open(encoding="utf-8")))
    by_folder = {r["folder"]: r for r in rows}
    pending = [r["folder"] for r in rows if r["bucket"] == args.bucket and r["status"] == "pending"]
    if not pending:
        return print(f"[mine] bucket {args.bucket}: no pending protocols.") or 0

    from fluentvibe.authoring.lm_client import (
        DEFAULT_LM_STUDIO_ENDPOINT, DEFAULT_LM_STUDIO_MODEL, LMStudioChatClient,
    )
    client = LMStudioChatClient(
        endpoint=args.endpoint or DEFAULT_LM_STUDIO_ENDPOINT,
        model=args.model or DEFAULT_LM_STUDIO_MODEL,
    )

    skill_text = _current_skill(args.bucket)
    name = BUCKET_TARGET.get(args.bucket) or args.bucket
    add_path = DRAFTS / f"{name}.additions.md"
    seen = _existing_addition_folders(add_path)
    batches = [pending[i:i + args.batch] for i in range(0, len(pending), args.batch)]
    if args.max_batches:
        batches = batches[: args.max_batches]
    print(f"[mine] bucket={args.bucket} pending={len(pending)} "
          f"batches={len(batches)} (batch={args.batch})")

    DRAFTS.mkdir(parents=True, exist_ok=True)
    tally = {"novel": 0, "nothing-new": 0, "reassign": 0, "out-of-scope": 0, "unparsed": 0}
    new_fragments: list[str] = []
    for bi, folders in enumerate(batches, 1):
        print(f"[mine]  batch {bi}/{len(batches)}: {', '.join(folders)}")
        msg = client.complete(messages=_build_messages(args.bucket, skill_text, folders), tools=[])
        content = (msg.get("content") or "").strip()
        verdicts, additions = _parse(content)
        for folder in folders:
            verdict = verdicts.get(folder, "")
            if not verdict:
                by_folder[folder]["contributed"] = "no verdict returned"
                tally["unparsed"] += 1
                continue
            low = verdict.lower()
            if low.startswith("novel"):
                frag = additions.get(folder, "").strip()
                if not frag:
                    by_folder[folder]["status"] = "pending"
                    by_folder[folder]["contributed"] = "novel but no additions fragment; retry"
                    tally["unparsed"] += 1
                    print(f"[mine]    {folder}: novel but NO fragment — re-queued")
                    continue
                _apply_verdict(by_folder[folder], verdict)
                tally["novel"] += 1
                if folder not in seen:
                    new_fragments.append(f"### {folder}\n_{verdict.split(':',1)[-1].strip()}_\n\n{frag}\n")
                    seen.add(folder)
            else:
                _apply_verdict(by_folder[folder], verdict)
                key = ("nothing-new" if low.startswith("nothing-new")
                       else "reassign" if low.startswith("reassign")
                       else "out-of-scope" if low.startswith("out-of-scope")
                       else "unparsed")
                tally[key] += 1
            print(f"[mine]    {folder}: {verdict[:80]}")

    # append new novelty fragments to the proposal file (never into skills/)
    if new_fragments:
        header = "" if add_path.is_file() else (
            f"# Mined additions for `{name}` — PROPOSALS, pending Claude integration\n\n"
            f"_Generated by scripts/mine_bucket.py. Each section is one protocol's "
            f"material novelty for Claude to fold into the real skill, then validate "
            f"(SOP §7). This file is NOT a skill._\n\n")
        with add_path.open("a", encoding="utf-8") as fh:
            if header:
                fh.write(header)
            fh.write(f"<!-- batch run {date.today().isoformat()} -->\n")
            fh.write("\n".join(new_fragments) + "\n")
        print(f"[mine] additions: {add_path} (+{len(new_fragments)} fragment(s))")
    else:
        print("[mine] no new novelty fragments this run")

    with LEDGER.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[mine] verdicts: {tally}")
    print("[mine] PROPOSALS only — Claude integrates fragments + validates "
          "(integrity test + generation run) before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
