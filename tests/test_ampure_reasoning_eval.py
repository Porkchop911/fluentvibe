from __future__ import annotations

import json
from pathlib import Path

from fluentvibe.authoring.ampure_reasoning_eval import (
    evaluate_ampure_reasoning_runs,
    write_ampure_reasoning_reports,
)


def _write_run(tmp_path: Path, run_name: str, trace_md: str, summary_md: str = "") -> Path:
    run_dir = tmp_path / run_name
    trace_dir = run_dir / "model_traces"
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.readable.md").write_text(trace_md, encoding="utf-8")
    if summary_md:
        (run_dir / "session_summary.md").write_text(summary_md, encoding="utf-8")
    return run_dir


def test_ampure_eval_flags_fast_mode_grounding_block(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "run_001",
        """
# Model Trace

### Messages Sent

#### 1. assistant

```text
need grounding
```

#### 1. `ground_in_parallel`

```text
{"categories": ["plates"]}
```

#### 2. tool

```text
{"ok": false, "category": "fast_mode_subagent_disabled"}
```
""",
        """
# AMPure Authoring Session Summary

## Round 1

- status: `failure`
- model_iterations: `2`

## Terminal

- terminal status: `failure`
""",
    )

    summary = evaluate_ampure_reasoning_runs(tmp_path)

    run = summary["runs"][0]
    assert "blocked_fast_mode_tool" in run["issues"]
    assert "trace_confusion" in run["issues"]
    assert run["tool_histogram"]["ground_in_parallel"] == 1


def test_ampure_eval_flags_repeated_fca_lookup_loop(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "run_001",
        """
# Model Trace

#### 1. `search_labware`

```text
{"query": "FCA96 tips"}
```

#### 2. `search_labware`

```text
{"query": "FCA96 tips"}
```

#### 3. `get_labware`

```text
{"name": "FCA96 tip box"}
```
""",
        """
# AMPure Authoring Session Summary

## Terminal

- terminal status: `failure`
""",
    )

    summary = evaluate_ampure_reasoning_runs(tmp_path)
    run = summary["runs"][0]

    assert "repeated_lookup_loop" in run["issues"]
    assert "fca_confusion" in run["issues"]
    assert run["repeated_lookup_queries"][0]["query"] == "fca96 tips"


def test_ampure_eval_records_guarded_lookup_suppression(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "run_001",
        """
# Model Trace

#### 1. `search_labware`

```text
{"query": "FCA"}
```

#### 2. `search_labware`

```text
{"query": "FCA tip"}
```
""",
        """
# AMPure Authoring Session Summary

## Round 1

- `search_labware` args={"query": "FCA"} ok=True category=None summary=None
- `search_labware` args={"query": "FCA tip"} ok=False category=repeated_lookup_guard summary=This search repeats a previous lookup.

## Terminal

- terminal status: `failure`
""",
    )

    summary = evaluate_ampure_reasoning_runs(tmp_path)
    run = summary["runs"][0]

    assert run["lookup_guard_active"] is True
    assert run["lookup_guard_events"][0]["category"] == "repeated_lookup_guard"
    assert "repeated_lookup_loop" not in run["issues"]


def test_ampure_eval_flags_object_nudge_ignored(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "run_001",
        """
# Model Trace

#### 1. user

```text
You have enough grounding; call present_object_draft now.
```

#### 1. `search_labware`

```text
{"query": "ABgene"}
```
""",
        """
# AMPure Authoring Session Summary

## Terminal

- terminal status: `failure`
""",
    )

    summary = evaluate_ampure_reasoning_runs(tmp_path)

    assert "ignored_stop_nudge" in summary["runs"][0]["issues"]


def test_ampure_eval_success_path_has_no_issue_flags(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "run_001",
        """
# Model Trace

#### 1. `lookup_workspace`

```text
{"name_or_guid": "SAT_Fluent_780_Rev3"}
```

#### 2. `present_object_draft`

```text
{"protocol_name": "AMPure cleanup", "labware": [{"label": "SamplePlate"}]}
```

#### 3. `present_functional_group_plan`

```text
{"groups": [{"name": "Variables"}, {"name": "Labware Placement"}]}
```

#### 4. `simulate_python_draft`

```text
{"source": "def build_worktable(): pass"}
```

#### 5. `compile_and_simulate`

```text
{"source": "def build_worktable(): pass"}
```
""",
        """
# AMPure Authoring Session Summary

## Round 1

- status: `success`
- model_iterations: `5`

## Terminal

- terminal status: `success`
""",
    )

    summary_path, report_path = write_ampure_reasoning_reports(tmp_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["runs"][0]["issues"] == []
    assert summary["runs"][0]["has_object_draft"] is True
    assert report_path.exists()
