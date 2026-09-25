"""Compare Qwen3.8 sampling presets on the real authoring workflow.

The benchmark deliberately disables the deterministic skeleton so every run
exercises model planning, tool use, repair, compilation, and strict simulation.
It uses approved, embedded Bench Specs rather than a local PDF, which makes the
input identical across machines and runs.

Examples::

    python scripts/benchmark_qwen38_sampling.py --dry-run
    python scripts/benchmark_qwen38_sampling.py --runs 3
    python scripts/benchmark_qwen38_sampling.py --runs 1 --cases dispense_96

Set ``FLUENTVIBE_LM_API_KEY`` before running when the endpoint requires a
Bearer token. Endpoint/model default to the normal authoring configuration.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

DEFAULT_PROFILE = REPO / "build" / "workspaces" / "1080_Dev"
DEFAULT_PROMPT = (
    "Automate this protocol on the deck, following the approved Bench Spec. "
    "All protocol decisions in the spec are approved; use compatible assets "
    "from the active workspace and complete compilation and strict simulation."
)


@dataclass(frozen=True)
class SamplingPreset:
    name: str
    temperature: float
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None

    def client_kwargs(self) -> dict[str, int | float]:
        values = asdict(self)
        values.pop("name")
        return {key: value for key, value in values.items() if value is not None}


# ``current`` reproduces production exactly. The other two use Qwen3.8's
# recommended shared controls and differ only in temperature.
PRESETS = {
    preset.name: preset
    for preset in (
        SamplingPreset("current", temperature=0.2),
        SamplingPreset(
            "balanced",
            temperature=0.8,
            top_p=0.95,
            top_k=20,
            min_p=0.0,
            presence_penalty=0.0,
            repetition_penalty=1.0,
        ),
        SamplingPreset(
            "qwen_recommended",
            temperature=1.0,
            top_p=0.95,
            top_k=20,
            min_p=0.0,
            presence_penalty=0.0,
            repetition_penalty=1.0,
        ),
    )
}


CASES: dict[str, dict[str, Any]] = {
    "bead_cleanup_96": {
        "title": "96-sample 0.8x magnetic bead cleanup",
        "sample_count": 96,
        "sample_volume_ul": 50.0,
        "reagents": [
            {"id": "samples", "name": "DNA samples", "role": "sample"},
            {"id": "beads", "name": "magnetic cleanup beads", "role": "bead_carrier"},
            {"id": "ethanol", "name": "80 percent ethanol", "role": "wash"},
            {"id": "elution", "name": "elution buffer", "role": "eluent"},
            {"id": "product", "name": "clean DNA eluate", "role": "product"},
        ],
        "steps": [
            {
                "id": "cleanup",
                "op": "bead_cleanup",
                "text": (
                    "Clean 96 DNA samples of 50 uL each with 0.8x magnetic beads. "
                    "Bind on the magnet, remove supernatant, wash twice with 200 uL "
                    "80 percent ethanol, move off the magnet for 30 uL elution, "
                    "re-engage the magnet, and recover the clean eluate into a "
                    "separate destination plate."
                ),
                "location": "deck",
                "reagent": "beads",
                "target": "product",
                "ratio": 0.8,
                "washes": 2,
                "wash_ul": 200.0,
                "elute_ul": 30.0,
            }
        ],
        "notes": [
            "Use the active 1080_DEV_TABLE profile and workspace-compatible labware.",
            "Keep the analyte role-tagged and never discard the recovered eluate to waste.",
        ],
    },
    "dispense_96": {
        "title": "96-well reagent dispense",
        "sample_count": 96,
        "sample_volume_ul": None,
        "reagents": [
            {
                "id": "buffer",
                "name": "assay buffer",
                "role": "reagent",
                "supply_ul": 6000.0,
                "supply_count": 1,
            }
        ],
        "steps": [
            {
                "id": "dispense",
                "op": "add",
                "text": "Dispense 50 uL assay buffer into every well of one 96-well plate.",
                "location": "deck",
                "reagent": "buffer",
                "target": "96-well assay plate",
                "volume_ul": 50.0,
            }
        ],
        "notes": ["Use a workspace-compatible reservoir, plate, tips, and liquid class."],
    },
}


def build_schedule(
    preset_names: list[str], case_names: list[str], runs: int, seed: int
) -> list[tuple[int, str, str]]:
    """Return balanced jobs with a reproducibly shuffled order per replicate."""
    jobs: list[tuple[int, str, str]] = []
    for replicate in range(1, runs + 1):
        block = [(replicate, case, preset) for case in case_names for preset in preset_names]
        random.Random(seed + replicate).shuffle(block)
        jobs.extend(block)
    return jobs


def _parse_names(raw: str, available: dict[str, Any], label: str) -> list[str]:
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [name for name in names if name not in available]
    if not names or unknown:
        choices = ", ".join(available)
        problem = "no values supplied" if not names else f"unknown: {', '.join(unknown)}"
        raise SystemExit(f"invalid --{label} ({problem}); choices: {choices}")
    return names


def _trace_retry_count(run_dir: Path) -> int:
    count = 0
    for path in (run_dir / "model_traces").glob("*.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if json.loads(line).get("event") == "turn_retry":
                    count += 1
        except (OSError, ValueError):
            continue
    return count


FIELDS = (
    "job",
    "replicate",
    "case",
    "preset",
    "temperature",
    "status",
    "success",
    "score",
    "rubric_failures",
    "compile_ok",
    "strict_simulation_ok",
    "authoring_attempts",
    "tool_calls",
    "failed_tool_calls",
    "turn_retries",
    "elapsed_s",
    "failure_category",
    "error",
    "run_dir",
)


def write_results(out: Path, rows: list[dict[str, Any]], preset_names: list[str]) -> None:
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)

    lines = [
        "# Qwen3.8 authoring sampling benchmark",
        "",
        "| Preset | Success | Strict sim | Mean score | Median seconds | Mean tools | Retries |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in preset_names:
        group = [row for row in rows if row.get("preset") == name]
        scored = [float(row["score"]) for row in group if row.get("score") != ""]
        elapsed = [float(row["elapsed_s"]) for row in group if row.get("elapsed_s") != ""]
        tools = [int(row["tool_calls"]) for row in group if row.get("tool_calls") != ""]
        total = len(group)
        successes = sum(bool(row.get("success")) for row in group)
        strict = sum(row.get("strict_simulation_ok") is True for row in group)
        score = statistics.mean(scored) if scored else 0.0
        seconds = statistics.median(elapsed) if elapsed else 0.0
        mean_tools = statistics.mean(tools) if tools else 0.0
        retries = sum(int(row.get("turn_retries") or 0) for row in group)
        lines.append(
            f"| {name} | {successes}/{total} | {strict}/{total} | {score:.3f} | "
            f"{seconds:.1f} | {mean_tools:.1f} | {retries} |"
        )
    lines.extend(
        [
            "",
            "Choose a preset primarily by successful strict simulations, then rubric score; "
            "use latency and retry count as tie-breakers.",
            "",
        ]
    )
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--runs", type=int, default=3, help="replicates per case/preset")
    parser.add_argument("--cases", default="bead_cleanup_96", help="comma-separated case names")
    parser.add_argument(
        "--presets",
        default="current,balanced,qwen_recommended",
        help="comma-separated preset names",
    )
    parser.add_argument("--seed", type=int, default=38027, help="run-order shuffle seed")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--run-timeout", type=float, default=1800.0)
    parser.add_argument("--retry-budget", type=int, default=3)
    parser.add_argument("--lab-scope", default="skills")
    parser.add_argument("--live-trace", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="write/print the run plan only")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if not (args.profile / "workspace_profile.json").exists():
        raise SystemExit(f"profile not found: {args.profile}")
    preset_names = _parse_names(args.presets, PRESETS, "presets")
    case_names = _parse_names(args.cases, CASES, "cases")
    schedule = build_schedule(preset_names, case_names, args.runs, args.seed)

    from eval_authoring import _activate_profile, _git_commit, _run_once

    from fluentvibe.authoring import PromptAuthoringService
    from fluentvibe.authoring.bench_spec import spec_context_block, validate_bench_spec
    from fluentvibe.authoring.eval_rubric import ALL_KEYS, score_protocol
    from fluentvibe.authoring.grounding import CURRENT_WORKTABLE_ENV
    from fluentvibe.authoring.lm_client import (
        DEFAULT_LM_STUDIO_ENDPOINT,
        DEFAULT_LM_STUDIO_MODEL,
        LMStudioChatClient,
    )
    from fluentvibe.authoring.profile import resolve_profile
    from fluentvibe.authoring.trace import ModelTraceConfig

    endpoint = args.endpoint or DEFAULT_LM_STUDIO_ENDPOINT
    model = args.model or DEFAULT_LM_STUDIO_MODEL
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or REPO / "build" / "eval" / f"qwen38-sampling-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    ws_name, ws_guid = _activate_profile(args.profile)
    # The shared evaluator preserves an explicitly configured worktable path.
    # A benchmark must instead guarantee that --profile is the deck under test.
    os.environ[CURRENT_WORKTABLE_ENV] = str(resolve_profile(args.profile).current_worktable)

    specs = {}
    for name in case_names:
        spec, problems = validate_bench_spec(CASES[name])
        if spec is None or problems:
            raise SystemExit(f"invalid built-in case {name}: {[p.message for p in problems]}")
        specs[name] = spec

    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": _git_commit(),
        "profile": str(args.profile.resolve()),
        "workspace_name": ws_name,
        "workspace_guid": ws_guid,
        "endpoint": endpoint,
        "model": model,
        "max_tokens": os.environ.get("FLUENTVIBE_LM_MAX_TOKENS"),
        "reasoning_effort": os.environ.get("FLUENTVIBE_LM_REASONING_EFFORT"),
        "api_key_present": bool(os.environ.get("FLUENTVIBE_LM_API_KEY")),
        "deterministic_skeleton": False,
        "runs": args.runs,
        "seed": args.seed,
        "presets": [asdict(PRESETS[name]) for name in preset_names],
        "cases": {name: CASES[name] for name in case_names},
        "schedule": [
            {"replicate": replicate, "case": case, "preset": preset}
            for replicate, case, preset in schedule
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"[sampling-bench] workspace={ws_name} model={model} jobs={len(schedule)} out={out}",
        flush=True,
    )
    for index, (replicate, case, preset) in enumerate(schedule, start=1):
        print(f"  {index:02d}. replicate={replicate} case={case} preset={preset}")
    if args.dry_run:
        print("[sampling-bench] dry run; no model requests sent")
        return 0

    # Force model-authored drafts: deterministic skeleton output would conceal
    # the effect of sampling controls. This changes only this benchmark process.
    os.environ["FLUENTVIBE_SKELETON"] = "0"
    rows: list[dict[str, Any]] = []
    for index, (replicate, case_name, preset_name) in enumerate(schedule, start=1):
        preset = PRESETS[preset_name]
        spec = specs[case_name]
        run_dir = out / f"job-{index:02d}-{case_name}-{preset_name}-r{replicate}"
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{DEFAULT_PROMPT}\n\n{spec_context_block(spec)}"
        client = LMStudioChatClient(
            endpoint=endpoint,
            model=model,
            request_timeout_s=args.request_timeout,
            **preset.client_kwargs(),
        )
        service = PromptAuthoringService(client=client)
        row: dict[str, Any] = {
            "job": index,
            "replicate": replicate,
            "case": case_name,
            "preset": preset_name,
            "temperature": preset.temperature,
            "run_dir": str(run_dir.relative_to(out)),
        }
        started = time.monotonic()
        print(
            f"[sampling-bench] {index}/{len(schedule)} {case_name} {preset_name} "
            f"(temperature={preset.temperature})",
            flush=True,
        )
        try:
            trace = ModelTraceConfig(
                enabled=True,
                live=args.live_trace,
                output_dir=run_dir,
                session_id=f"sampling-{index:02d}-{preset_name}",
            )
            result, _rubric, py_path = _run_once(
                service,
                prompt,
                None,
                run_dir,
                ws_name,
                ws_guid,
                args.lab_scope,
                True,
                args.retry_budget,
                trace,
                args.run_timeout,
            )
            (run_dir / "result.json").write_text(
                json.dumps(result.to_dict(), indent=2), encoding="utf-8"
            )
            validation = result.validation
            row.update(
                status=result.status.value,
                success=result.status.value == "success",
                compile_ok=validation.compile_ok if validation is not None else False,
                strict_simulation_ok=(
                    validation.strict_simulation_ok if validation is not None else False
                ),
                authoring_attempts=result.attempts,
                tool_calls=len(result.tool_calls),
                failed_tool_calls=sum(call.get("ok") is False for call in result.tool_calls),
                failure_category=(
                    result.failure_category.value if result.failure_category is not None else ""
                ),
            )
            if py_path is not None and py_path.exists():
                rubric = score_protocol(
                    py_path.read_text(encoding="utf-8"), filename=str(py_path), spec=spec
                )
                row["score"] = f"{rubric.score:.3f}"
                failures = [inv.key for inv in rubric.invariants if inv.status == "fail"]
                row["rubric_failures"] = " ".join(failures)
                row.update({inv.key: inv.status for inv in rubric.invariants})
                (run_dir / "rubric.json").write_text(
                    json.dumps(
                        [
                            {"key": inv.key, "status": inv.status, "evidence": inv.evidence}
                            for inv in rubric.invariants
                        ],
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            else:
                row["score"] = ""
                row["rubric_failures"] = "no_protocol"
        except Exception as exc:  # noqa: BLE001 - one failed run must not end the matrix
            row.update(
                status="error",
                success=False,
                score="",
                rubric_failures="",
                error=f"{type(exc).__name__}: {exc}",
            )
            (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            row["elapsed_s"] = f"{time.monotonic() - started:.3f}"
            row["turn_retries"] = _trace_retry_count(run_dir)
            rows.append(row)
            # Keep useful partial reports if a long benchmark is interrupted.
            write_results(out, rows, preset_names)
        print(
            f"  status={row.get('status')} score={row.get('score', '')} "
            f"strict={row.get('strict_simulation_ok', False)} elapsed={row['elapsed_s']}s",
            flush=True,
        )

    # Preserve per-invariant data separately without bloating the headline CSV.
    with (out / "invariants.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["job", "case", "preset", *ALL_KEYS]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    print(f"[sampling-bench] wrote {out / 'summary.md'}")
    print(f"[sampling-bench] wrote {out / 'results.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
