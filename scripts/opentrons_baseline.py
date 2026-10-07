"""Baseline: convert every Opentrons protocol in the inventory, well by well.

    python scripts/opentrons_baseline.py --profile build/workspaces/1080_Dev \
        --out build/eval/opentrons-baseline [--limit 50] [--only library]

One protocol at a time, each in its own process (``convert_opentrons``: trace,
conversion, authoring gate, per-well fidelity, compile; no model, no
FluentControl) inside a Windows job object with a hard memory limit, so a
runaway protocol is killed instead of filling the machine (the model server
lives here too). Before each protocol it waits while free memory is low.
Results: one JSON line per protocol in ``<out>/results.jsonl`` (resumable: ids
already there are skipped) and the inventory CSV with the outcome columns
added (``<out>/opentrons_protocols_baseline.csv``).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

WORKER = r"""
import json, sys
from pathlib import Path
from fluentvibe.authoring.opentrons_import import convert_opentrons
s = convert_opentrons(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
keep = ("stage", "error", "liquid_events", "gate", "fidelity", "unconverted", "fca_tips_used", "total_s")
print("BASELINE " + json.dumps({k: s.get(k) for k in keep}))
"""


def _free_gb() -> float:
    """Free physical memory and free commit (RAM + page file), whichever is lower:
    a full commit is what made programs fail on this machine, with RAM to spare."""
    phys, commit = _memory()
    return min(phys, commit)


def _memory() -> tuple[float, float]:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullAvailPhys / 2**30, status.ullAvailPageFile / 2**30
    except Exception:  # noqa: BLE001
        return 999.0, 999.0


def _limited_run(cmd: list[str], limit_gb: float, timeout_s: float, cwd: Path) -> tuple[int, str, str]:
    """Run ``cmd`` in a job object: all its processes together may use at most
    ``limit_gb``; closing the job kills them (also on timeout)."""
    import win32api
    import win32con
    import win32job

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["JobMemoryLimit"] = int(limit_gb * 2**30)
    info["BasicLimitInformation"]["LimitFlags"] |= (win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
                                                     | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid)
    win32job.AssignProcessToJobObject(job, handle)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        win32job.TerminateJobObject(job, 1)
        out, err = proc.communicate()
        return -9, out, (err or "") + f"\ntimeout after {timeout_s:g} s"
    finally:
        win32api.CloseHandle(handle)
        win32api.CloseHandle(job)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new protocols (0: all)")
    ap.add_argument("--only", choices=("library", "git"), default=None)
    ap.add_argument("--memory-gb", type=float, default=8.0, help="hard limit per protocol (all its processes)")
    ap.add_argument("--min-free-gb", type=float, default=10.0,
                    help="wait while free memory (RAM or commit, the lower) is below this")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--rerun", default="",
                    help="comma-separated outcomes to run again: stages (gate, deck, convert, trace, crash) "
                         "or 'mismatch' (done, but not every well matches); the newest result counts")
    ap.add_argument("--recheck-matching", type=int, default=0,
                    help="also run this many protocols again (random, fixed seed) whose wells all matched")
    args = ap.parse_args()

    from fluentvibe.protocol_index import load_index, related_protocols

    entries = [e for e in load_index() if e.source in ("library", "git") and e.convertible
               and (args.only is None or e.source == args.only)]
    entries.sort(key=lambda e: (e.source != "library", e.id))
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    done = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["id"]] = row
    started = time.monotonic()
    new = 0
    rerun = {x.strip() for x in args.rerun.split(",") if x.strip()}
    matching = sorted(i for i, r in done.items() if r.get("stage") == "done"
                      and (r.get("fidelity") or {}).get("wells_matching") == (r.get("fidelity") or {}).get("wells_checked"))
    recheck = set(random.Random(0).sample(matching, min(args.recheck_matching, len(matching))))

    def again(row: dict) -> bool:
        f = row.get("fidelity") or {}
        mismatch = row.get("stage") == "done" and f.get("wells_matching") != f.get("wells_checked")
        return row.get("stage") in rerun or ("mismatch" in rerun and mismatch) or row["id"] in recheck

    for e in entries:
        if e.id in done and not again(done[e.id]):
            continue
        if args.limit and new >= args.limit:
            break
        waited = 0
        while _free_gb() < args.min_free_gb:
            if waited == 0:
                print(f"waiting: {_free_gb():.1f} GB free (< {args.min_free_gb:g})", flush=True)
            time.sleep(30)
            waited += 30
            if waited >= 1800:
                print("stopping: free memory stayed low for 30 min", flush=True)
                return 2
        slug = e.id.replace(":", "_").replace("/", "_")
        t0 = time.monotonic()
        code, out, err = _limited_run([sys.executable, "-c", WORKER, e.path, str(args.profile),
                                       str(args.out / slug)], args.memory_gb, args.timeout, REPO)
        line = next((ln for ln in out.splitlines()[::-1] if ln.startswith("BASELINE ")), None)
        row = {"id": e.id, "title": e.title, "seconds": round(time.monotonic() - t0, 1)}
        if line:
            row.update(json.loads(line[len("BASELINE "):]))
        else:
            tail = (err or "").strip().splitlines()[-1:] or [""]
            # No result line: killed by the memory limit or the timeout, or crashed.
            row.update(stage="crash", error=f"exit {code}: {tail[0][:300]}")
        if row.get("unconverted") is not None:
            row["unconverted"] = len(row["unconverted"])
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        done[e.id] = row
        new += 1
        f = row.get("fidelity") or {}
        print(f"{new:4d} {e.id:<36} {row.get('stage'):<6} wells {f.get('wells_matching', '-')}/"
              f"{f.get('wells_checked', '-')} {row['seconds']:>6}s {str(row.get('error') or '')[:70]}", flush=True)

    # The inventory with the outcome per protocol.
    related = related_protocols(load_index())
    with (args.out / "opentrons_protocols_baseline.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "source", "title", "path", "description", "related_protocols", "stage",
                    "wells_matching", "wells_checked", "liquid_events", "not_converted_steps", "error"])
        for e in sorted(load_index(), key=lambda x: (x.source != "library", x.id)):
            if e.source not in ("library", "git"):
                continue
            r = done.get(e.id, {})
            f = r.get("fidelity") or {}
            w.writerow([e.id, e.source, e.title, e.path, e.description or "; ".join(e.steps),
                        "; ".join(related.get(e.id, [])), r.get("stage", "not run"), f.get("wells_matching", ""),
                        f.get("wells_checked", ""), r.get("liquid_events", ""), r.get("unconverted", ""),
                        str(r.get("error") or "")[:300]])
    print(f"done: {new} new protocols in {time.monotonic() - started:.0f} s; {len(done)} total", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
