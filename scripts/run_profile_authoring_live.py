"""Run a live skills-mode authoring generation against a workspace_app profile.

Unlike ``run_ampure_authoring_live.py`` (which targets the shipped default
780 deck), this points the whole skills scope at a profile produced by the
workspace setup app:

- grounding uses the profile's ``current_worktable.py`` snapshot (via env),
- the active **deck skill** is the profile's emitted ``deck-<name>.md`` (swapped
  in for the shipped always-on deck skill), and
- the labware / liquid-class whitelist comes from the profile's
  ``generation.profile.yaml`` ``lab_scope`` block.

This is the "activation" the workspace app does not yet wire automatically; it
mutates nothing on disk. Opt-in live harness — needs the LM Studio endpoint up.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fluentvibe.authoring import PromptAuthoringSession
from fluentvibe.authoring.grounding import CURRENT_WORKTABLE_ENV
from fluentvibe.authoring.lab_skills import _parse_skill

DEFAULT_PROMPT = Path(r"C:\Users\Niko\Desktop\ampure protocol.txt")
DEFAULT_ANSWERS = (
    "20 uL per well",
    "Water Free Single",
    "Use all 96 wells.",
    "ABgene plate acceptable; use exact catalog 96_ABgene_SuperPlate_Thermo_AB2800 for source and destination plates.",
    "Use sensible installed reservoirs and waste resources.",
)


def _profile_deck_skill_path(profile_dir: Path) -> Path:
    decks = sorted(profile_dir.glob("deck-*.md"))
    if not decks:
        raise SystemExit(f"no deck-*.md emitted in {profile_dir}; re-save the profile")
    return decks[0]


# The shipped always-on api skills (core-worktable-api, labware-and-liquid-
# classes) hardcode the default 780 workspace in their examples; rewrite those
# references so the whole injected context binds to the profile's deck.
_DEFAULT_WS_NAME = "SAT_Fluent_780_Rev3"
_DEFAULT_WS_GUID = "291ba293-6361-4f8f-aa8d-7c2643d3f096"


def _retarget_scope(scope, profile_dir: Path, ws_name: str, ws_guid: str):
    """Return a LabScope whose deck skill + whitelist come from the profile."""
    deck = _parse_skill(_profile_deck_skill_path(profile_dir))
    if deck is None:
        raise SystemExit("profile deck skill failed to parse")

    def _rebind(skill):
        body = skill.body.replace(_DEFAULT_WS_GUID, ws_guid).replace(_DEFAULT_WS_NAME, ws_name)
        return skill if body == skill.body else dataclasses.replace(skill, body=body)

    # drop every shipped deck skill, rebind the rest, splice in the profile deck
    catalog = tuple(_rebind(s) for s in scope.skill_catalog if s.axis != "deck") + (deck,)

    gen_profile = yaml.safe_load(
        (profile_dir / "generation.profile.yaml").read_text(encoding="utf-8")
    )
    lab_scope = (gen_profile or {}).get("lab_scope") or {}
    labware = frozenset(str(x).strip() for x in (lab_scope.get("labware") or []) if str(x).strip())
    liquid = frozenset(str(x).strip() for x in (lab_scope.get("liquid_classes") or []) if str(x).strip())
    return dataclasses.replace(
        scope,
        skill_catalog=catalog,
        labware=labware or scope.labware,
        liquid_classes=liquid or scope.liquid_classes,
    )


def _format_canned_answers(answers: list[str]) -> str:
    return (
        "\n".join(f"- {a}" for a in answers)
        + "\n- Yes, use Water Free Single exactly as the liquid class when asked to confirm it."
        + "\n- Use exact installed tip boxes with sufficient capacity for each planned phase."
    )


def _one_line(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--retry-budget", type=int, default=12)
    parser.add_argument("--max-rounds", type=int, default=None)
    args = parser.parse_args()

    profile = json.loads((args.profile_dir / "workspace_profile.json").read_text(encoding="utf-8"))
    ws_name = profile["workspace"]["name"]
    ws_guid = profile["workspace"]["guid"]
    output_dir = args.output_dir or (Path("build") / f"{args.profile_dir.name}_skills_gen")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_jsonl = output_dir / "trace.jsonl"

    # Grounding reads the profile's current-worktable snapshot.
    os.environ[CURRENT_WORKTABLE_ENV] = str(args.profile_dir / "current_worktable.py")

    session = PromptAuthoringSession(
        output_dir=output_dir,
        retry_budget=args.retry_budget,
        workspace_name=ws_name,
        workspace_guid=ws_guid,
        lab_scope="skills",
    )
    # Retarget the scope at the profile's deck + whitelist before the first send.
    session._lab_scope = _retarget_scope(session._lab_scope, args.profile_dir, ws_name, ws_guid)
    session._registry.lab_scope = session._lab_scope
    deck_names = [s.name for s in session._lab_scope.skill_catalog if s.axis == "deck"]
    print(f"[scope] workspace={ws_name!r} guid={ws_guid}")
    print(f"[scope] active deck skill(s): {deck_names}")
    print(f"[scope] whitelist labware={len(session._lab_scope.labware)} liquid={sorted(session._lab_scope.liquid_classes)}")

    prompt = args.prompt_file.read_text(encoding="utf-8")
    answers = list(DEFAULT_ANSWERS)
    canned = _format_canned_answers(answers)
    initial = prompt + "\n\nCanned authoring answers for this live run:\n" + canned
    pending = [initial, *([canned] * len(answers))]
    max_rounds = args.max_rounds or args.retry_budget * 4

    printed = 0
    round_index = 0
    result = None
    with log_jsonl.open("w", encoding="utf-8") as log:
        while round_index < max_rounds:
            if result is None:
                if not pending:
                    break
                user_text = pending.pop(0)
            elif result.status.value == "approval_required":
                user_text = "approve"
            elif result.status.value == "clarification_required":
                user_text = pending.pop(0) if pending else canned
            else:
                break
            round_index += 1
            result = session.send(user_text)
            calls = list(result.tool_calls)
            new_calls = calls[printed:]
            printed = len(calls)
            record = {
                "round": round_index,
                "status": result.status.value,
                "tool_calls": [
                    {
                        "name": c.get("name"),
                        "ok": (c.get("result") or {}).get("ok") if isinstance(c.get("result"), dict) else None,
                        "summary": _one_line((c.get("result") or {}).get("_summary")
                                             or (c.get("result") or {}).get("message")) if isinstance(c.get("result"), dict) else None,
                    }
                    for c in new_calls
                ],
                "compiled_xscr_path": str(result.compiled_xscr) if result.compiled_xscr else None,
            }
            log.write(json.dumps(record, default=str) + "\n")
            log.flush()
        if result is None:
            return 1
        log.write(json.dumps({"terminal": result.to_dict()}, default=str) + "\n")

    print(json.dumps({
        "status": result.status.value,
        "failure_category": result.failure_category.value if result.failure_category else None,
        "compiled_xscr": str(result.compiled_xscr) if result.compiled_xscr else None,
        "trace_path": str(log_jsonl),
    }, indent=2))
    return 0 if result.status.value == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
