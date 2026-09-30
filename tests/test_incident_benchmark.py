import json
from pathlib import Path

import pytest

from scripts.benchmark_authoring_incident import (
    ORACLE,
    acceptance,
    repo_path,
    schedule,
    trace_metrics,
)


def test_schedule_has_three_trials_in_every_cell():
    jobs = schedule()
    assert len(jobs) == 12
    for mode in ("fast", "full"):
        for temp in (0.2, 1.0):
            assert sum(m == mode and t == temp for m, t, _ in jobs) == 3
    assert jobs == schedule()
    with pytest.raises(ValueError):
        schedule(2)


def test_missing_extra_or_unreviewed_chemistry_cannot_pass():
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    review = {"reviewer": "human", "checks": {c["id"]: {"status": "pass", "evidence": "draft.py:42, executed operations"}
                                               for c in oracle["checks"]}, "extra_steps": [], "extra_reagents": []}
    assert acceptance(review, oracle) == "pass"
    review["extra_steps"] = ["NaOH elution"]
    assert acceptance(review, oracle) == "fail"
    review["extra_steps"] = []
    review["checks"]["equal_binding"] = {"status": "fail", "evidence": "5 vs 20 ul"}
    assert acceptance(review, oracle) == "fail"
    del review["checks"]["equal_binding"]
    assert acceptance(review, oracle) == "unreviewed"
    assert acceptance({}, oracle) == "unreviewed"


def test_unknown_extras_are_not_zero_extras():
    oracle = {"checks": [{"id": "binding"}]}
    review = {"reviewer": "human", "checks": {"binding": {"status": "pass", "evidence": "line 1"}}}
    assert acceptance(review, oracle) == "unreviewed"


def test_paths_cannot_escape_repository():
    with pytest.raises(ValueError):
        repo_path(Path("../outside"))


def test_bad_arguments_count_as_malformed_not_proven_model_truncation(tmp_path):
    folder = tmp_path / "model_traces"
    folder.mkdir()
    event = {"event": "response_final", "finish_reason": "tool_calls", "stop_reason": None,
             "tool_calls": [{"function": {"name": "simulate_python_draft", "arguments": '{"source":"cut'}}]}
    (folder / "turn.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    metrics = trace_metrics(tmp_path)
    assert metrics["malformed_json_calls"] == 1
    assert metrics["length_cutoffs"] == 0
    assert metrics["stop_reasons"][0]["stop_reason"] is None
