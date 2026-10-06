from unittest.mock import MagicMock, patch

from fluentvibe.authoring.strata_eval import run_strata_eval, strata_scenarios


def test_comparison_checkpoints_errors_and_dispatch_failures(tmp_path):
    def evaluate(**kwargs):
        scenario = kwargs["scenarios"][0]
        return {"scenarios": [{
            "selected_required_tools": dict.fromkeys(scenario.expected_tools, True),
            "dispatched_calls": [{"name": name, "result": {"ok": name != "lookup_api"}}
                                 for name in scenario.expected_tools],
        }]}

    def factory(**kwargs):
        if kwargs["model"] == "offline":
            raise ConnectionError("unavailable")
        return MagicMock()

    output = tmp_path / "report.json"
    with patch("fluentvibe.authoring.strata_eval.run_lookup_eval", side_effect=evaluate):
        report = run_strata_eval(models=["working", "offline"], repeats=2,
                                 output=output, client_factory=factory)
    assert len(report["runs"]) == 12
    assert report["summary"][0]["passed"] == 4
    assert report["summary"][0]["mean_required_tool_recall"] == 1
    assert report["summary"][1]["errors"] == 6
    assert output.exists()
    assert len({scenario.name for scenario in strata_scenarios()}) == 3


def test_invalid_repeat_count(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        run_strata_eval(models=["model"], repeats=0, output=tmp_path / "report.json")
