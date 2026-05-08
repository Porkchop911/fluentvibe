"""Deterministic harness for decompiled `.xscr` corpus validation."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .codegen import emit_python
from .xscr_parser import parse_xscr


@dataclass(frozen=True)
class CorpusResult:
    name: str
    xscr_path: str
    generated_python: str | None
    status: str
    classification: str
    modeled_coverage: float | None = None
    total_executed_steps: int | None = None
    unsupported_command_ids: dict[str, int] = field(default_factory=dict)
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "xscr_path": self.xscr_path,
            "generated_python": self.generated_python,
            "status": self.status,
            "classification": self.classification,
            "modeled_coverage": self.modeled_coverage,
            "total_executed_steps": self.total_executed_steps,
            "unsupported_command_ids": dict(self.unsupported_command_ids),
            "failure": None if self.failure is None else dict(self.failure),
        }


def run_decompiled_corpus(
    paths: Iterable[Path | str],
    *,
    output_dir: Path | str,
    strict: bool = True,
    fail_on_opaque: bool = True,
) -> list[CorpusResult]:
    """Decompile, execute, and simulate a fixed `.xscr` corpus."""
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[CorpusResult] = []
    for raw_path in paths:
        xscr_path = Path(raw_path)
        generated_python = output_root / f"{xscr_path.stem}_decompiled.py"
        wt = None
        try:
            proto = parse_xscr(xscr_path)
            generated_python.write_text(
                emit_python(proto, source_xscr=str(xscr_path)),
                encoding="utf-8",
            )
            module = _load_module(generated_python, alias=f"corpus_{xscr_path.stem}")
            wt = module.build_worktable()
            wt.simulate(strict=strict, fail_on_opaque=fail_on_opaque)
        except Exception as exc:
            simulation_report = getattr(wt, "simulation_report", None)
            failure = (
                simulation_report.failure.to_dict()
                if simulation_report is not None and simulation_report.failure is not None
                else _synthetic_failure(exc)
            )
            opaque = (
                {}
                if simulation_report is None
                else dict(simulation_report.unsupported_command_ids)
            )
            results.append(
                CorpusResult(
                    name=xscr_path.stem,
                    xscr_path=str(xscr_path),
                    generated_python=str(generated_python) if generated_python.exists() else None,
                    status="failed" if failure is not None else "passed",
                    classification=_classify_failure_bucket(failure["category"]),
                    modeled_coverage=(
                        None
                        if simulation_report is None
                        else simulation_report.modeled_coverage
                    ),
                    total_executed_steps=(
                        None
                        if simulation_report is None
                        else simulation_report.total_executed_steps
                    ),
                    unsupported_command_ids=opaque,
                    failure=failure,
                )
            )
            continue

        report = wt.simulation_report
        if report is None:
            raise RuntimeError("Corpus simulation completed without a simulation report.")
        results.append(
            CorpusResult(
                name=xscr_path.stem,
                xscr_path=str(xscr_path),
                generated_python=str(generated_python),
                status=report.status,
                classification="passes_strictly" if report.status == "passed" else "other",
                modeled_coverage=report.modeled_coverage,
                total_executed_steps=report.total_executed_steps,
                unsupported_command_ids=dict(report.unsupported_command_ids),
                failure=None if report.failure is None else report.failure.to_dict(),
            )
        )
    return results


def summarize_corpus_results(results: Iterable[CorpusResult]) -> dict[str, Any]:
    items = [result.to_dict() for result in results]
    counts = Counter(result["classification"] for result in items)
    status_counts = Counter(result["status"] for result in items)
    return {
        "protocols": items,
        "classification_counts": dict(sorted(counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
    }


def _load_module(path: Path, *, alias: str) -> Any:
    spec = importlib.util.spec_from_file_location(alias, str(path))
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original
    return module


def _synthetic_failure(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    lowered = message.lower()
    if "not bound to a specific fluentcontrol workspace" in lowered:
        category = "workspace_binding"
    elif "is not on workspace" in lowered:
        category = "workspace_slot"
    elif "worktableworkspace reference" in lowered:
        category = "workspace_binding"
    elif "catalog" in lowered or "installed labware" in lowered or "not found in catalog index" in lowered:
        category = "catalog"
    elif "opaque" in lowered:
        category = "opaque_policy"
    elif "coverage" in lowered:
        category = "coverage_policy"
    else:
        category = "simulation_state"
    return {
        "category": category,
        "exception_type": type(exc).__name__,
        "message": message,
        "step_index": None,
        "step_type": None,
        "command_id": None,
    }


def _classify_failure_bucket(category: str) -> str:
    if category in {"workspace_binding", "workspace_slot", "catalog"}:
        return "workspace_or_catalog"
    if category == "opaque_policy":
        return "unsupported_command"
    if category in {"liquid_state", "source_volume_short", "well_overflow"}:
        return "liquid_semantics"
    return "other"
