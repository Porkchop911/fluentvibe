from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_qwen38_sampling.py"
    spec = importlib.util.spec_from_file_location("benchmark_qwen38_sampling", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sampling_presets_preserve_current_and_offer_qwen_controls() -> None:
    module = _module()

    assert module.PRESETS["current"].client_kwargs() == {"temperature": 0.2}
    recommended = module.PRESETS["qwen_recommended"].client_kwargs()
    assert recommended == {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
    }


def test_schedule_is_complete_balanced_and_reproducible() -> None:
    module = _module()
    presets = ["current", "balanced", "qwen_recommended"]
    cases = ["bead_cleanup_96", "dispense_96"]

    first = module.build_schedule(presets, cases, runs=3, seed=42)
    second = module.build_schedule(presets, cases, runs=3, seed=42)

    assert first == second
    assert len(first) == 18
    for replicate in range(1, 4):
        block = [job for job in first if job[0] == replicate]
        assert {(case, preset) for _, case, preset in block} == {
            (case, preset) for case in cases for preset in presets
        }


def test_builtin_cases_are_valid_bench_specs() -> None:
    from fluentvibe.authoring.bench_spec import validate_bench_spec

    module = _module()
    for raw in module.CASES.values():
        spec, problems = validate_bench_spec(raw)
        assert spec is not None
        assert problems == []
