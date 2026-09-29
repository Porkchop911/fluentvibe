"""Score one pi run of the Dynabeads test (see docs/pi-incremental-test.md).

    python scripts/score_pi_run.py build/eval/pi-dyna-B-1.py [--session <pi session .jsonl>]

The protocol: does it simulate (strict), pass `check`, compile; which
reagents and steps it has, checked against the datasheet (the hand-transcribed
oracle tests/fixtures/dynabeads_m280_dna_oracle.json when present), with
invented chemistry counted as a failure. The session (default: the newest pi
session for this folder): how pi worked, i.e. time, turns, how much it thought
at once, how it wrote the file (whole-file writes vs edits) and how often it ran
the simulator.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROFILE = REPO / "build" / "workspaces" / "1080_Dev"
SESSIONS = Path.home() / ".pi" / "agent" / "sessions" / "--D--python-fluentvibe--"

# From the datasheet's "Immobilize nucleic acids" protocol (MAN0014017 B.0, p. 2).
EXPECTED = {
    "beads (Dynabeads M-280)": r"dynabead|m-?280|streptavidin",
    "2X B&W buffer": r"2\s*x\s*b\s*&?\s*w|b&w.{0,20}2\s*x|binding and washing.{0,30}2\s*x",
    "biotinylated DNA added": r"biotinylated|dna",
    "15 min binding incubation": r"15\s*min|900",
    "magnet separation": r"separate\(|magnet",
    "1X B&W washes": r"1\s*x\s*b\s*&?\s*w|b&w.{0,20}1\s*x",
    "low-salt final resuspension": r"low[- ]salt",
}
# Chemistry the protocol does not have (the oracle's forbidden list, as words).
FORBIDDEN = {
    "NaOH / Solution A": r"naoh|solution a\b",
    "elution": r"\belut",
    "neutralisation": r"neutrali[sz]",
    "1 M Tris neutraliser": r"\b1\s*m tris",
    "ethanol / SPRI": r"ethanol|spri|ampure",
    "PBS/BSA (protein branch)": r"pbs\s*/\s*bsa|\bbsa\b",
}


def run(args: list[str]) -> tuple[int, str]:
    env = {**os.environ, "FLUENTVIBE_PROFILE_DIR": str(PROFILE), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "fluentvibe.cli", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = "\n".join(line for line in (proc.stdout + proc.stderr).splitlines()
                    if not re.search(r"pydantic|FieldInfo", line, re.I))
    return proc.returncode, out


def score_protocol(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    code_sim, out_sim = run(["simulate", str(path), "--strict"])
    code_chk, out_chk = run(["check", str(path)])
    xscr = path.with_suffix(".score.xscr")
    code_cmp, _ = run(["compile", str(path), "-o", str(xscr)])
    xscr.unlink(missing_ok=True)
    reagents = re.findall(r"Reagent\(\s*[\"']([^\"']+)", source)
    text = source.lower()
    # Invented chemistry is judged on what the protocol does: reagent names
    # (not their descriptions: the bead stock is "... in PBS pH 7.4, 0.1% BSA")
    # and step names / operator texts.
    cores = [re.split(r"[(,;]", r, maxsplit=1)[0] for r in reagents]
    acts = re.findall(r"name=[\"']([^\"']+)", source) + re.findall(r"offdeck_step\(\s*wt,\s*[\"']([^\"']+)", source)
    doing = " | ".join(cores + acts).lower()
    oracle = REPO / "tests" / "fixtures" / "dynabeads_m280_dna_oracle.json"
    return {
        "simulate_strict": code_sim == 0,
        "simulate_error": "" if code_sim == 0 else (out_sim.strip().splitlines() or [""])[-1][:200],
        "check_clean": code_chk == 0 and "no problems found" in out_chk,
        "compiles": code_cmp == 0,
        "reagents": sorted(set(reagents)),
        "expected_found": {k: bool(re.search(p, text, re.I)) for k, p in EXPECTED.items()},
        "forbidden_found": [k for k, p in FORBIDDEN.items() if re.search(p, doing, re.I)],
        "oracle": str(oracle.relative_to(REPO)) if oracle.exists() else None,
        "lines": source.count("\n"),
    }


def newest_session() -> Path | None:
    files = sorted(SESSIONS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def score_session(path: Path, protocol: Path) -> dict:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    stamps = [datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) for e in events if e.get("timestamp")]
    tools: dict[str, int] = {}
    writes, edits, sims, sim_fail, thinking = [], 0, 0, 0, []
    first_write = None
    results = {}
    model = next((f"{e.get('provider')}/{e.get('modelId')}" for e in events if e.get("type") == "model_change"), None)
    level = next((e.get("thinkingLevel") for e in events if e.get("type") == "thinking_level_change"), None)
    for e in events:
        if e.get("type") != "message":
            continue
        m = e["message"]
        if m.get("role") == "toolResult":
            results[m.get("toolCallId")] = " ".join(c.get("text", "") for c in m.get("content") or [])
        for c in m.get("content") or [] if isinstance(m.get("content"), list) else []:
            if c.get("type") == "thinking":
                thinking.append(len(c.get("thinking") or ""))
            if c.get("type") != "toolCall":
                continue
            name, args = c.get("name"), c.get("arguments") or {}
            tools[name] = tools.get(name, 0) + 1
            target = str(args.get("path") or "")
            if name == "write" and protocol.name in target.replace("\\", "/"):
                writes.append(len(str(args.get("content") or "")))
                first_write = first_write or e.get("timestamp")
            if name == "edit" and protocol.name in target.replace("\\", "/"):
                edits += 1
            if name == "bash" and "simulate" in str(args.get("command") or ""):
                sims += 1
                c_id = c.get("id")
                if c_id and re.search(r"error|traceback|failed", results.get(c_id, ""), re.I):
                    sim_fail += 1
    start = stamps[0] if stamps else None
    fw = datetime.fromisoformat(first_write.replace("Z", "+00:00")) if first_write else None
    return {
        "session": path.name,
        "model": model,
        "thinking_level": level,
        "minutes": round((stamps[-1] - start).total_seconds() / 60, 1) if stamps else None,
        "minutes_to_first_write": round((fw - start).total_seconds() / 60, 1) if fw and start else None,
        "tool_calls": tools,
        "whole_file_writes": len(writes),
        "largest_write_chars": max(writes) if writes else 0,
        "edits": edits,
        "simulate_runs": sims,
        "simulate_runs_with_errors": sim_fail,
        "thinking_chars_total": sum(thinking),
        "thinking_chars_max_turn": max(thinking) if thinking else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("protocol", type=Path)
    ap.add_argument("--session", type=Path, default=None, help="pi session .jsonl (default: newest)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    protocol = args.protocol if args.protocol.is_absolute() else REPO / args.protocol
    result = {"protocol": str(protocol.relative_to(REPO)) if protocol.is_relative_to(REPO) else str(protocol)}
    result.update(score_protocol(protocol) if protocol.exists() else {"missing": True})
    session = args.session or newest_session()
    if session and session.exists():
        result["how"] = score_session(session, protocol)
    exp = result.get("expected_found") or {}
    result["verdict"] = ("PASS" if result.get("simulate_strict") and result.get("compiles")
                         and not result.get("forbidden_found") and exp and all(exp.values()) else "FAIL")
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{result['protocol']}: {result['verdict']}")
    if result.get("missing"):
        print("  protocol file not found")
    else:
        print(f"  simulate --strict {'ok' if result['simulate_strict'] else 'FAIL: ' + result['simulate_error']}"
              f" | check {'clean' if result['check_clean'] else 'findings'}"
              f" | compile {'ok' if result['compiles'] else 'FAIL'} | {result['lines']} lines")
        print(f"  missing steps: {[k for k, v in exp.items() if not v] or 'none'}")
        print(f"  invented chemistry: {result['forbidden_found'] or 'none'}")
        print(f"  reagents: {result['reagents']}")
    how = result.get("how")
    if how:
        print(f"  pi: {how['model']} thinking={how['thinking_level']} | {how['minutes']} min "
              f"(first write after {how['minutes_to_first_write']} min)")
        print(f"      whole-file writes {how['whole_file_writes']} (largest {how['largest_write_chars']} chars), "
              f"edits {how['edits']}, simulate runs {how['simulate_runs']} "
              f"({how['simulate_runs_with_errors']} with errors)")
        print(f"      thinking {how['thinking_chars_total']} chars total, {how['thinking_chars_max_turn']} max in one turn;"
              f" tools {how['tool_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
