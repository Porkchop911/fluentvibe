"""Measure which deck positions each pipetting arm can reach, using FluentControl.

FluentControl's context check decides reach per tip and well from each arm's
axis travel (``MoveableRange`` of the X/Y drives in the instrument
configuration) and the world position of the carrier site, and reports
"<labware> out of range. Arm cannot move to position" in the InfoPad
(``SmartCommandBaseContextCheckHelper.CheckLabwareRange`` ->
``RangeValidator.IsWellReachableFromGivenTip``). Rather than re-implement that
geometry, this script asks FluentControl: it builds probe scripts that put an
MCA-compatible SBS reservoir on every candidate position and aspirate from it
once with the MCA96 and once with the LiHa, opens them through the shell
validator, and reads which labware the InfoPad flags.

The result is written to ``<profile>/reach.json``; the profile loader merges it
into the deck rules (``reach``), where the Worktable compile check and the
skeleton builder use it. Re-run it when the workspace changes.

Needs FluentControl running with the UserSpecific "shell" script (see
docs/deployment.md, path B).

    python scripts/probe_deck_reach.py --profile build/workspaces/sat_1080_test --work <scratch dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Where probe reservoirs go, and which SBS reservoir connects there
# (verified in FluentControl on the 1080 deck).
PROBE_SITES = {
    "Nest61mm_Pos": "60ml SBS MCA96",
    "Nest7mm_Pos": "300ml SBS",
}
MCA_TIPS = "MCA96, 200ul, Box"
FCA_TIPS = ("FCA, 200ul SBS", "FCA200Box")
LC = "Water Free Single"
OUT_OF_RANGE = re.compile(r"^\s*\d+:\s*(?P<label>.+?) out of range\. Arm cannot move", re.IGNORECASE)


def _free_positions(profile: dict) -> dict[str, list[int]]:
    deck = profile.get("deck") or {}
    occupied = {(p["location"], int(p["position"])) for p in deck.get("positions") or [] if p.get("occupied_by")}
    summary = deck.get("position_summary_by_location") or {}
    return {loc: [p for p in summary.get(loc, []) if (loc, p) not in occupied] for loc in PROBE_SITES}


def _probe_source(ws_name: str, ws_guid: str, sites: list[tuple[str, int]], tips_at: tuple[str, int],
                  fca_at: tuple[str, int]) -> str:
    lines = [
        "from fluentvibe import FCA200Box, MCA200Box, Reagent, Trough25mL, Trough100mL, Worktable",
        "",
        "",
        "def build_worktable() -> Worktable:",
        f"    wt = Worktable.from_workspace({ws_name!r}, workspace_guid={ws_guid!r}, auto_place=False,",
        "                                  protocol_name='reach probe', comment='')",
        "    wt.group('Labware Placement')",
        f"    tips = wt.place(MCA200Box('ProbeMcaTips', catalog={MCA_TIPS!r}), {tips_at[0]!r}, {tips_at[1]})",
        f"    fca = wt.place({FCA_TIPS[1]}('ProbeFcaTips', catalog={FCA_TIPS[0]!r}), {fca_at[0]!r}, {fca_at[1]})",
        "    water = Reagent('Water')",
    ]
    names = []
    for loc, pos in sites:
        name = f"R_{loc}_{pos}"
        cls = "Trough25mL" if PROBE_SITES[loc] == "300ml SBS" else "Trough100mL"
        lines.append(f"    r = wt.place({cls}({name!r}, catalog={PROBE_SITES[loc]!r}), {loc!r}, {pos})")
        lines.append("    r.fill_all(water, 20000.0)")
        names.append(name)
    lines += ["    mca = wt.mca96", "    wt.group('MCA96 probe')", "    mca.mount_adapter()", "    mca.pick_up(tips)"]
    for name in names:
        lines.append(f"    mca.aspirate({name!r}, 10.0, liquid_class={LC!r})")
        lines.append(f"    mca.dispense({name!r}, 10.0, liquid_class={LC!r})")
    lines += ["    mca.return_tips(tips)", "    mca.drop_adapter()", "    liha = wt.liha", "    wt.group('LiHa probe')",
              "    liha.get_tips(fca)"]
    for name in names:
        lines.append(f"    liha.aspirate({name!r}, 10.0, liquid_class={LC!r})")
        lines.append(f"    liha.dispense({name!r}, 10.0, liquid_class={LC!r})")
    lines += ["    liha.drop_tips()", "    return wt", ""]
    return "\n".join(lines)


def _lines_by_probe(wt) -> dict[int, tuple[str, str]]:
    """InfoPad line number -> (arm, probe labware) for every probe aspirate/dispense."""
    out = {}
    for group in wt.to_protocol().groups:
        for step in group.steps:
            label = getattr(step, "labware_name", None) or ""
            if label.startswith("R_") and getattr(step, "line_number", None) is not None:
                arm = "liha" if type(step).__name__.startswith("Liha") else "mca96"
                out[int(step.line_number)] = (arm, label)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True, help="existing directory for probe scripts and results")
    args = ap.parse_args()

    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.fluentcontrol_shell import (
        DEFAULT_SHELL_XSCR,
        validate_generated_xscr_via_shell,
    )

    os.environ.pop("FLUENTVIBE_PROFILE_DIR", None)  # probe without guard rails
    profile = json.loads((args.profile / "workspace_profile.json").read_text(encoding="utf-8"))
    ws_name, ws_guid = profile["workspace"]["name"], profile["workspace"]["guid"]
    free = _free_positions(profile)
    all_sites = [(loc, p) for loc, ps in free.items() for p in ps]
    nest = "Nest61mm_Pos"
    # Two runs so every 61 mm nest is probed once while the tip boxes sit elsewhere.
    head_pair, tail_pair = free[nest][:2], free[nest][-2:]
    runs = [
        ([s for s in all_sites if not (s[0] == nest and s[1] in head_pair)], (nest, head_pair[0]), (nest, head_pair[1])),
        ([(nest, p) for p in head_pair], (nest, tail_pair[0]), (nest, tail_pair[1])),
    ]
    backup = args.work / "shell_backup.xscr"
    shutil.copy2(DEFAULT_SHELL_XSCR, backup)
    reach: dict[str, dict[str, set[int]]] = {"mca96": {}, "liha": {}}
    unreachable: dict[str, dict[str, set[int]]] = {"mca96": {}, "liha": {}}
    # Probe lines with another InfoPad error: the range check never ran there.
    unknown: dict[str, dict[str, set[int]]] = {"mca96": {}, "liha": {}}
    evidence = []
    try:
        for index, (sites, tips_at, fca_at) in enumerate(runs, 1):
            source = _probe_source(ws_name, ws_guid, sites, tips_at, fca_at)
            py = args.work / f"reach_probe_{index}.py"
            py.write_text(source, encoding="utf-8")
            wt = build_worktable_from_source(source, str(py))
            xscr = args.work / f"reach_probe_{index}.xscr"
            wt.compile(xscr)
            ui = validate_generated_xscr_via_shell(xscr)
            errors = list(getattr(ui, "error_lines", []) or [])
            (args.work / f"reach_probe_{index}.infopad.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
            if not ui.opened or ui.load_failed:
                raise SystemExit(f"probe {index} did not open in FluentControl: {ui.load_error_text}")
            probe_lines = _lines_by_probe(wt)
            flagged: dict[str, set[str]] = {"mca96": set(), "liha": set()}
            other: dict[str, set[str]] = {"mca96": set(), "liha": set()}
            for line in errors:
                try:
                    number = int(line.strip().split(":", 1)[0])
                except ValueError:
                    continue
                if number not in probe_lines:
                    continue
                arm, label = probe_lines[number]
                (flagged if OUT_OF_RANGE.match(line) else other)[arm].add(label)
            for loc, pos in sites:
                name = f"R_{loc}_{pos}"
                for arm in ("mca96", "liha"):
                    if name in flagged[arm]:
                        bucket = unreachable
                    elif name in other[arm]:
                        bucket = unknown
                    else:
                        bucket = reach
                    bucket[arm].setdefault(loc, set()).add(pos)
            evidence.append({"run": index, "sites": len(sites), "errors": errors[:200]})
            time.sleep(1.0)
    finally:
        shutil.copy2(backup, DEFAULT_SHELL_XSCR)

    result = {
        "workspace": {"name": ws_name, "guid": ws_guid},
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "method": "FluentControl InfoPad 'out of range' per probe aspirate (scripts/probe_deck_reach.py)",
        "reachable": {arm: {loc: sorted(ps) for loc, ps in locs.items()} for arm, locs in reach.items()},
        "unreachable": {arm: {loc: sorted(ps) for loc, ps in locs.items()} for arm, locs in unreachable.items()},
        "unknown": {arm: {loc: sorted(ps) for loc, ps in locs.items()} for arm, locs in unknown.items()},
    }
    (args.profile / "reach.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (args.work / "reach_evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
