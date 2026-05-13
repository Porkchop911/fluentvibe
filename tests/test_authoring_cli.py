from __future__ import annotations

from pathlib import Path

from fluentvibe.authoring.models import (
    ApprovalRequest,
    AuthoringResult,
    AuthoringStatus,
    ClarificationQuestion,
    FailureCategory,
    ValidationReport,
)
from fluentvibe.cli import main


def test_author_cli_success_json(capsys, monkeypatch) -> None:
    class FakeService:
        def author(self, prompt, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None):
            xscr = output_dir / "fake.xscr"
            output_dir.mkdir(parents=True, exist_ok=True)
            xscr.write_text("<xml />", encoding="utf-8")
            return AuthoringResult(
                status=AuthoringStatus.SUCCESS,
                prompt=prompt,
                spec=None,
                generated_code="def build_worktable() -> Worktable:\n    ...\n",
                validation=ValidationReport(True, True, True, True, xscr_path=xscr),
                compiled_xscr=xscr,
                attempts=1,
            )

    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringService", FakeService)
    rc = main(
        [
            "author",
            "Transfer",
            "20",
            "uL",
            "from",
            "a",
            "96-well",
            "source",
            "plate",
            "to",
            "a",
            "96-well",
            "destination",
            "plate",
            "across",
            "all",
            "wells.",
            "--json",
            "--output-dir",
            str(Path("build") / "test_author_cli" / "success"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert '"status": "success"' in captured.out
    assert '"compiled_xscr":' in captured.out


def test_author_cli_success_text_handles_lm_result_without_spec(capsys, monkeypatch) -> None:
    class FakeService:
        def author(self, prompt, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None):
            xscr = output_dir / "fake.xscr"
            py = output_dir / "fake.py"
            output_dir.mkdir(parents=True, exist_ok=True)
            xscr.write_text("<xml />", encoding="utf-8")
            py.write_text("def build_worktable():\n    ...\n", encoding="utf-8")
            return AuthoringResult(
                status=AuthoringStatus.SUCCESS,
                prompt=prompt,
                spec=None,
                generated_code="def build_worktable():\n    ...\n",
                validation=ValidationReport(True, True, True, True, python_path=py, xscr_path=xscr),
                compiled_xscr=xscr,
                attempts=3,
            )

    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringService", FakeService)
    rc = main(
        [
            "author",
            "Transfer",
            "20",
            "uL",
            "--output-dir",
            str(Path("build") / "test_author_cli" / "text_success"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "Authored LM-authored protocol" in captured.out
    assert "Python:" in captured.out
    assert "XSCR:" in captured.out
    assert "Attempts: 3" in captured.out


def test_author_cli_failure_exit_code(capsys, monkeypatch) -> None:
    class FakeService:
        def author(self, prompt, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None):
            return AuthoringResult(
                status=AuthoringStatus.FAILURE,
                prompt=prompt,
                spec=None,
                generated_code=None,
                validation=None,
                compiled_xscr=None,
                failure_category=FailureCategory.MODEL_AUTHORING_FAILURE,
                failure_message="LM Studio request failed",
            )

    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringService", FakeService)
    rc = main(
        [
            "author",
            "Transfer",
            "from",
            "source",
            "plate",
            "to",
            "destination",
            "plate.",
            "--output-dir",
            str(Path("build") / "test_author_cli" / "failure"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "Authoring failed [model_authoring_failure]: LM Studio request failed" in captured.err


def test_chat_cli_clarification_then_success(capsys, monkeypatch) -> None:
    class FakeSession:
        def __init__(self, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None):
            self.output_dir = output_dir
            self.calls = 0

        def send(self, user_text):
            self.calls += 1
            if self.calls == 1:
                return AuthoringResult(
                    status=AuthoringStatus.CLARIFICATION_REQUIRED,
                    prompt=user_text,
                    spec=None,
                    generated_code=None,
                    validation=None,
                    compiled_xscr=None,
                    clarification_questions=(
                        ClarificationQuestion(
                            key="volume",
                            question="What transfer volume should I use?",
                            why_it_matters="Volume determines tips and liquid class.",
                        ),
                    ),
                    attempts=1,
                    tool_calls=(),
                )
            xscr = self.output_dir / "chat.xscr"
            py = self.output_dir / "chat.py"
            return AuthoringResult(
                status=AuthoringStatus.SUCCESS,
                prompt=user_text,
                spec=None,
                generated_code="def build_worktable():\n    ...\n",
                validation=ValidationReport(True, True, True, True, python_path=py, xscr_path=xscr),
                compiled_xscr=xscr,
                attempts=2,
                tool_calls=(
                    {"name": "lookup_workspace", "result": {"ok": True}},
                    {"name": "compile_and_simulate", "result": {"ok": True, "stage": "strict_simulation"}},
                ),
            )

        def validate_fluentcontrol_shell(self, xscr_path):
            return {"ok": True, "xscr_path": xscr_path}

    inputs = iter(["transfer from source to dest", "20 uL", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringSession", FakeSession)
    rc = main(["chat", "--output-dir", str(Path("build") / "test_author_cli" / "chat")])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Clarification required:" in captured.out
    assert "What transfer volume should I use?" in captured.out
    assert "tool lookup_workspace: ok" in captured.out
    assert "Authoring succeeded." in captured.out
    assert "Strict simulation: passed" in captured.out


def test_chat_cli_approval_then_success(capsys, monkeypatch) -> None:
    class FakeSession:
        def __init__(self, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None):
            self.output_dir = output_dir
            self.calls = 0

        def send(self, user_text):
            self.calls += 1
            if self.calls == 1:
                return AuthoringResult(
                    status=AuthoringStatus.APPROVAL_REQUIRED,
                    prompt=user_text,
                    spec=None,
                    generated_code=None,
                    validation=None,
                    compiled_xscr=None,
                    approval_request=ApprovalRequest(
                        kind="objects",
                        title="Approve Worktable Objects",
                        summary="Planned transfer worktable",
                        payload={
                            "workspace": {
                                "name": "SAT_Fluent_780_Rev3",
                                "workspace_guid": "291ba293-6361-4f8f-aa8d-7c2643d3f096",
                            },
                            "labware": [{"label": "SourcePlate", "role": "source"}],
                        },
                        question="Approve these worktable objects, or reply with changes.",
                    ),
                    attempts=1,
                    tool_calls=({"name": "present_object_draft", "result": {"ok": True}},),
                )
            xscr = self.output_dir / "chat.xscr"
            py = self.output_dir / "chat.py"
            return AuthoringResult(
                status=AuthoringStatus.SUCCESS,
                prompt=user_text,
                spec=None,
                generated_code="def build_worktable():\n    ...\n",
                validation=ValidationReport(True, True, True, True, python_path=py, xscr_path=xscr),
                compiled_xscr=xscr,
                attempts=2,
                tool_calls=(
                    {"name": "present_object_draft", "result": {"ok": True}},
                    {"name": "compile_and_simulate", "result": {"ok": True}},
                ),
            )

        def validate_fluentcontrol_shell(self, xscr_path):
            return {"ok": True, "xscr_path": xscr_path}

    inputs = iter(["transfer 20 uL", "approved", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringSession", FakeSession)
    rc = main(["chat", "--output-dir", str(Path("build") / "test_author_cli" / "chat_approval")])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Approval required:" in captured.out
    assert "Approve Worktable Objects" in captured.out
    assert "Worktable: SAT_Fluent_780_Rev3" in captured.out
    assert "Authoring failed" not in captured.err
    assert "Authoring succeeded." in captured.out


def test_chat_cli_reset_and_validate_fc(capsys, monkeypatch) -> None:
    created = []

    class FakeSession:
        def __init__(self, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None):
            created.append(self)

        def send(self, user_text):
            raise AssertionError("send should not be called")

        def validate_fluentcontrol_shell(self, xscr_path):
            return {"ok": True, "xscr_path": xscr_path, "message": "passed"}

    inputs = iter(["/help", "/validate-fc build/out.xscr", "/reset", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringSession", FakeSession)
    rc = main(["chat"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Commands:" in captured.out
    assert '"ok": true' in captured.out
    assert "Session reset." in captured.out
    assert len(created) == 2
