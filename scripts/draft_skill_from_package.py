"""Draft a fluentvibe skill from an Opentrons work package, using the local LM.

Agent-agnostic helper for the skill-authoring kit (docs/skill-authoring/). Given
a package id from work-packages.yaml, it gathers the package's source protocols
(README + .py) plus the SOP and translation cheatsheet, asks the local LM Studio
model to draft the skill markdown, and writes a DRAFT to a staging dir for human
review.

It NEVER writes into fluentvibe/_assets/config/skills/ — a human runs the SOP's
validation (discover + integrity test + a generation run) and moves the draft in
only once it passes. The kit also works without this script: a Claude subagent
or a human can follow SOP.md directly.

Usage:
    PYTHONPATH=. python scripts/draft_skill_from_package.py protein-assay
    PYTHONPATH=. python scripts/draft_skill_from_package.py protein-assay --out-dir docs/skill-authoring/_drafts
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "docs" / "skill-authoring"
SKILLS = REPO / "fluentvibe" / "_assets" / "config" / "skills"
PY_HEAD_LINES = 280  # cap each source .py to keep the prompt bounded


def _load_package(pkg_id: str) -> dict:
    data = yaml.safe_load((KIT / "work-packages.yaml").read_text(encoding="utf-8"))
    for p in data.get("packages", []):
        if p.get("id") == pkg_id:
            return p
    ids = ", ".join(p.get("id", "?") for p in data.get("packages", []))
    sys.exit(f"package {pkg_id!r} not found. Available: {ids}")


def _read(path: Path, max_lines: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if max_lines is not None:
        lines = text.splitlines()
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines]) + f"\n# … ({len(lines)-max_lines} more lines truncated)"
    return text


def _gather_sources(pkg: dict) -> str:
    chunks: list[str] = []
    for src in pkg.get("sources", []):
        d = Path(src)
        if not d.is_dir():
            chunks.append(f"## SOURCE {src}\n(directory not found on this machine)")
            continue
        readme = next((p for p in d.glob("*.md")), None)
        py = next((p for p in d.glob("*.py")), None)
        block = [f"## SOURCE {d.name}"]
        if readme:
            block.append(f"### README ({readme.name})\n{_read(readme)}")
        if py:
            block.append(f"### PROTOCOL ({py.name})\n```python\n{_read(py, PY_HEAD_LINES)}\n```")
        chunks.append("\n\n".join(block))
    return "\n\n".join(chunks)


def _template_skill(axis: str) -> str:
    """An existing skill in the same axis, to anchor the format/idioms."""
    folder = SKILLS / axis
    existing = sorted(folder.glob("*.md")) if folder.is_dir() else []
    return _read(existing[0]) if existing else ""


def _build_messages(pkg: dict) -> list[dict]:
    sop = _read(KIT / "SOP.md")
    cheatsheet = _read(KIT / "opentrons-translation.md")
    template = _template_skill(pkg.get("axis", "family"))
    target = pkg["target"]
    mode = pkg.get("mode", "create")
    system = (
        "You convert Opentrons protocols into a fluentvibe '--lab-scope skills' "
        "markdown file. Output ONLY the skill file content (YAML frontmatter + "
        "markdown body) — no prose before or after, no code fence around the "
        "whole thing. Follow the SOP and the translation cheatsheet exactly: "
        "fluentvibe has only wt.liha / wt.mca96 / wt.gripper / MagnetRack / plates "
        "/ troughs / tip boxes / wt.wait / wt.loop, scalar volumes, and the "
        "approved whitelist. Map modules to wt.wait/add_comment, pass variables "
        "by name, use native loops, derive dependent volumes, and state any "
        "capability gaps in the body rather than faking them."
    )
    user = (
        f"# SOP\n{sop}\n\n# TRANSLATION CHEATSHEET\n{cheatsheet}\n\n"
        f"# TEMPLATE SKILL (same axis — match this format)\n{template}\n\n"
        f"# WORK PACKAGE\n{yaml.safe_dump(pkg, sort_keys=False)}\n\n"
        f"# SOURCE PROTOCOLS\n{_gather_sources(pkg)}\n\n"
        f"# TASK\nMode: {mode}. Produce the complete skill markdown for "
        f"`{target}` (axis `{pkg.get('axis')}`, always_on: false). The "
        f"description must be one rich sentence dense with trigger words. If "
        f"mode is enhance, output the full revised file."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package_id")
    ap.add_argument("--out-dir", default=str(KIT / "_drafts"))
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    from fluentvibe.authoring.lm_client import (
        DEFAULT_LM_STUDIO_ENDPOINT,
        DEFAULT_LM_STUDIO_MODEL,
        LMStudioChatClient,
    )

    pkg = _load_package(args.package_id)
    client = LMStudioChatClient(
        endpoint=args.endpoint or DEFAULT_LM_STUDIO_ENDPOINT,
        model=args.model or DEFAULT_LM_STUDIO_MODEL,
    )
    print(f"[draft] package={args.package_id} target={pkg['target']} mode={pkg.get('mode')}")
    message = client.complete(messages=_build_messages(pkg), tools=[])
    content = (message.get("content") or "").strip()
    if not content:
        sys.exit("LM returned empty content")
    # strip an accidental ```markdown … ``` wrapper if present
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3].rstrip()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (Path(pkg["target"]).name + ".draft.md")
    out_path.write_text(content, encoding="utf-8")
    print(f"[draft] wrote {out_path}")
    print("[draft] DRAFT only — review, then run SOP §7 validation before "
          "moving it into fluentvibe/_assets/config/skills/.")


if __name__ == "__main__":
    main()
