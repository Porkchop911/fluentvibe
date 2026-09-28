from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from fluentvibe.authoring.graph import build_authoring_graph
from fluentvibe.authoring.lm_client import LMStudioChatClient, LMStudioError
from fluentvibe.authoring.models import AuthoringStatus
from fluentvibe.authoring.tools import AuthoringToolRegistry
from fluentvibe.authoring.trace import ModelTraceConfig, ModelTraceRecorder, render_model_trace_file
from fluentvibe.cli import main


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_model_trace_recorder_writes_jsonl(tmp_path: Path) -> None:
    recorder = ModelTraceRecorder(
        ModelTraceConfig(enabled=True, output_dir=tmp_path, session_id="session-a")
    )
    recorder.start_turn(1)
    request_id = recorder.begin_request(model="test-model")
    recorder.record("model_turn", request_id=request_id, duration_ms=12.5)

    files = list((tmp_path / "model_traces").glob("*.jsonl"))
    assert len(files) == 1
    events = _events(files[0])
    assert [event["event"] for event in events] == ["request_start", "model_turn"]
    assert all(event["session_id"] == "session-a" for event in events)
    assert all(event["turn_index"] == 1 for event in events)
    assert all(event["request_id"] == request_id for event in events)
    readable = files[0].with_suffix(".readable.md")
    assert readable.exists()
    assert "## Request" in readable.read_text(encoding="utf-8")


def test_lmstudio_stream_trace_records_raw_lines_when_opted_in(monkeypatch, tmp_path: Path) -> None:
    class FakeHeaders:
        def get(self, name, default=None):
            return "text/event-stream" if name == "Content-Type" else default

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            chunks = [
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "hello",
                                "reasoning_content": "visible reasoning",
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup_workspace",
                                            "arguments": '{"name": "SAT"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ]
            for chunk in chunks:
                yield f"data: {json.dumps(chunk)}\n".encode("utf-8")
            yield b"data: [DONE]\n"

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: FakeResponse())
    recorder = ModelTraceRecorder(
        ModelTraceConfig(
            enabled=True,
            raw_stream=True,
            output_dir=tmp_path,
            session_id="lmstudio",
        )
    )
    recorder.start_turn(1)
    recorder.begin_request(model="test-model")
    client = LMStudioChatClient(
        endpoint="http://localhost:1234/v1/chat/completions",
        model="test-model",
        trace_recorder=recorder,
    )

    message = client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert message["content"] == "hello"
    assert message["tool_calls"][0]["function"]["name"] == "lookup_workspace"
    events = _events(next((tmp_path / "model_traces").glob("*.jsonl")))
    assert "request_payload" in [event["event"] for event in events]
    assert [event["raw_line"] for event in events if event["event"] == "raw_stream_line"]
    assert not [event for event in events if event["event"] == "raw_stream_chunk"]
    final = [event for event in events if event["event"] == "response_final"][-1]
    assert final["finish_reason"] == "tool_calls"
    assert final["reasoning_fields"]["reasoning_content"] == "visible reasoning"
    readable = next((tmp_path / "model_traces").glob("*.readable.md"))
    rendered = readable.read_text(encoding="utf-8")
    assert "Provider-Exposed Reasoning" in rendered
    assert "visible reasoning" in rendered
    assert "`lookup_workspace`" in rendered


def test_raw_stream_trace_is_off_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLUENTVIBE_MODEL_TRACE_RAW_STREAM", raising=False)
    config = ModelTraceConfig.from_env(output_dir=tmp_path, enabled=True)
    assert config.enabled is True
    assert config.raw_stream is False


def test_reasoning_fields_round_trip_through_langchain_adapter() -> None:
    from fluentvibe.authoring.graph import _lc_to_legacy_dict, _legacy_dict_to_aimessage

    message = _legacy_dict_to_aimessage({
        "content": "",
        "tool_calls": [],
        "reasoning_fields": {"reasoning_content": "preserve across tool turn"},
    })
    assert _lc_to_legacy_dict(message)["reasoning_content"] == "preserve across tool turn"


def test_lmstudio_reasoning_effort_is_opt_in(monkeypatch) -> None:
    seen = {}

    class Response:
        headers = {"Content-Type": "application/json"}
        def read(self):
            return b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
        def __enter__(self): return self
        def __exit__(self, *args): return False

    def capture(req, timeout=None):
        seen.update(json.loads(req.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", capture)
    client = LMStudioChatClient(reasoning_effort="medium")
    client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert seen["reasoning_effort"] == "medium"


def test_lmstudio_sampling_controls_are_explicit_and_opt_in(monkeypatch) -> None:
    seen: dict = {}
    body = b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
    monkeypatch.setattr("urllib.request.urlopen", _json_response(body, seen))

    LMStudioChatClient(
        temperature=1.0,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        presence_penalty=0.0,
        repetition_penalty=1.0,
    ).complete(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert seen["temperature"] == 1.0
    assert seen["top_p"] == 0.95
    assert seen["top_k"] == 20
    assert seen["min_p"] == 0.0
    assert seen["presence_penalty"] == 0.0
    assert seen["repetition_penalty"] == 1.0

    seen.clear()
    LMStudioChatClient().complete(messages=[{"role": "user", "content": "hi"}], tools=[])
    # Defaults: the model's recommended thinking-mode sampling.
    assert (seen["temperature"], seen["top_p"], seen["top_k"]) == (1.0, 0.95, 20)
    assert "min_p" not in seen
    assert "presence_penalty" not in seen
    assert "repetition_penalty" not in seen


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"temperature": -0.1}, "temperature"),
        ({"top_p": 1.1}, "top_p"),
        ({"top_k": -1}, "top_k"),
        ({"min_p": -0.1}, "min_p"),
        ({"presence_penalty": 2.1}, "presence_penalty"),
        ({"repetition_penalty": 0}, "repetition_penalty"),
    ],
)
def test_lmstudio_rejects_invalid_sampling_controls(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        LMStudioChatClient(**kwargs)


def test_lmstudio_request_timeout_is_bounded_and_traced(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, float] = {}

    def time_out(req, timeout=None):
        seen["timeout"] = timeout
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", time_out)
    recorder = ModelTraceRecorder(
        ModelTraceConfig(enabled=True, output_dir=tmp_path, session_id="timeout")
    )
    recorder.start_turn(1)
    client = LMStudioChatClient(
        trace_recorder=recorder,
        request_timeout_s=12.5,
    )

    with pytest.raises(LMStudioError, match="timed out after 12.5s"):
        client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert seen["timeout"] == 12.5
    events = _events(next((tmp_path / "model_traces").glob("*.jsonl")))
    error = [event for event in events if event["event"] == "request_error"][-1]
    assert error["error_type"] == "timeout"


def test_lmstudio_run_budget_caps_the_next_request(monkeypatch) -> None:
    seen: dict[str, float] = {}

    def time_out(req, timeout=None):
        seen["timeout"] = timeout
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", time_out)
    client = LMStudioChatClient(request_timeout_s=120)
    client.start_run_budget(7.5)

    with pytest.raises(LMStudioError, match="timed out"):
        client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert 0 < seen["timeout"] <= 7.5


def test_lmstudio_stream_surfaces_provider_error(monkeypatch) -> None:
    class FakeHeaders:
        def get(self, name, default=None):
            return "text/event-stream" if name == "Content-Type" else default

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'data: {"error":{"message":"Model unloaded."}}\n'

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: FakeResponse())
    client = LMStudioChatClient()

    with pytest.raises(LMStudioError, match="provider error: Model unloaded"):
        client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])


def test_trace_disabled_creates_no_files(tmp_path: Path) -> None:
    recorder = ModelTraceRecorder(ModelTraceConfig(enabled=False, output_dir=tmp_path))
    recorder.start_turn(1)
    recorder.begin_request(model="test-model")
    recorder.record("model_turn", duration_ms=1.0)
    assert not (tmp_path / "model_traces").exists()


def test_render_model_trace_file_converts_existing_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    events = [
        {
            "event": "request_start",
            "timestamp": "2026-05-16T00:00:00+00:00",
            "session_id": "s",
            "turn_index": 1,
            "request_id": "r",
            "model": "m",
        },
        {
            "event": "response_final",
            "timestamp": "2026-05-16T00:00:01+00:00",
            "session_id": "s",
            "turn_index": 1,
            "request_id": "r",
            "finish_reason": "tool_calls",
            "assistant_content": "I need a detail.",
            "reasoning_fields": {"reasoning_content": "visible reasoning"},
            "tool_calls": [
                {
                    "function": {
                        "name": "ask_user",
                        "arguments": '{"question": "Which plate?"}',
                    }
                }
            ],
        },
    ]
    path.write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )

    out = render_model_trace_file(path)

    text = out.read_text(encoding="utf-8")
    assert "visible reasoning" in text
    assert "I need a detail." in text
    assert "`ask_user`" in text


def test_cli_author_model_trace_propagates(capsys, monkeypatch, tmp_path: Path) -> None:
    seen = {}

    class FakeService:
        def author(self, prompt, *, output_dir, retry_budget, workspace_name=None, workspace_guid=None, trace_config=None):
            seen["trace_config"] = trace_config
            from fluentvibe.authoring.models import (
                AuthoringResult,
                AuthoringStatus,
                ValidationReport,
            )

            xscr = output_dir / "fake.xscr"
            return AuthoringResult(
                status=AuthoringStatus.SUCCESS,
                prompt=prompt,
                spec=None,
                generated_code="",
                validation=ValidationReport(True, True, True, True, xscr_path=xscr),
                compiled_xscr=xscr,
                attempts=1,
            )

    monkeypatch.setattr("fluentvibe.authoring.PromptAuthoringService", FakeService)
    rc = main(["author", "transfer", "--output-dir", str(tmp_path), "--model-trace-live"])
    captured = capsys.readouterr()
    assert rc == 0
    assert seen["trace_config"].enabled is True
    assert seen["trace_config"].live is True
    assert "Model trace:" in captured.err


def test_cli_render_trace(capsys, tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "event": "response_final",
                "timestamp": "2026-05-16T00:00:01+00:00",
                "session_id": "s",
                "turn_index": 1,
                "request_id": "r",
                "assistant_content": "hello",
            }
        ),
        encoding="utf-8",
    )

    rc = main(["render-trace", str(path)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Rendered" in captured.out
    assert path.with_suffix(".readable.md").exists()


def test_graph_records_normalized_trace_for_injected_client(tmp_path: Path) -> None:
    class FakeChat:
        model = "fake-chat"

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(
                content="No code yet",
                response_metadata={"finish_reason": "stop"},
            )

    recorder = ModelTraceRecorder(
        ModelTraceConfig(enabled=True, output_dir=tmp_path, session_id="normalized")
    )
    recorder.start_turn(1)
    registry = AuthoringToolRegistry(output_dir=tmp_path)
    graph = build_authoring_graph(
        registry=registry,
        client=FakeChat(),
        output_dir=tmp_path,
        retry_budget=1,
        trace_recorder=recorder,
    )
    final = graph.invoke(
        {
            "messages": [HumanMessage(content="transfer 20 uL")],
            "iterations": 0,
            "tool_call_count": 0,
            "best_code": None,
            "last_validation": None,
            "current_group_index": 0,
            "last_accepted_source_hash": None,
            "result": None,
            "prompt": "transfer 20 uL",
        }
    )

    assert final["result"].status is AuthoringStatus.FAILURE
    events = _events(next((tmp_path / "model_traces").glob("*.jsonl")))
    payload = [event for event in events if event["event"] == "request_payload"][-1]
    assert payload["messages"][0]["role"] == "user"
    turn = [event for event in events if event["event"] == "model_turn"][-1]
    assert turn["model"] == "fake-chat"
    assert turn["assistant"]["content"] == "No code yet"
    assert not any("raw_line" in event for event in events)

def test_lmstudio_strict_workflow_marks_only_declaration(monkeypatch):
    seen = {}
    class Response:
        headers = {"Content-Type": "application/json"}
        def read(self):
            return b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
        def __enter__(self): return self
        def __exit__(self, *args): return False
    def capture(req, timeout=None):
        seen.update(json.loads(req.data.decode("utf-8")))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", capture)
    monkeypatch.setenv("FLUENTVIBE_LM_STRICT_WORKFLOW", "1")
    tools = [
        {"type": "function", "function": {"name": "declare_protocol_workflow", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "ask_user", "parameters": {"type": "object"}}},
    ]
    client = LMStudioChatClient(reasoning_effort="xhigh")
    client.complete(messages=[{"role": "user", "content": "hi"}], tools=tools)
    assert seen["reasoning_effort"] == "xhigh"
    assert seen["tools"][0]["function"]["strict"] is True
    assert "strict" not in seen["tools"][1]["function"]
    assert "strict" not in tools[0]["function"]


def _json_response(body: bytes, seen: dict):
    class Response:
        headers = {"Content-Type": "application/json"}
        def read(self):
            return body
        def __enter__(self): return self
        def __exit__(self, *args): return False
    def capture(req, timeout=None):
        seen.update(json.loads(req.data.decode("utf-8")))
        return Response()
    return capture


def test_lmstudio_sends_max_tokens_from_env(monkeypatch):
    seen: dict = {}
    body = b'{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
    monkeypatch.setattr("urllib.request.urlopen", _json_response(body, seen))
    monkeypatch.setenv("FLUENTVIBE_LM_MAX_TOKENS", "32768")
    LMStudioChatClient().complete(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert seen["max_tokens"] == 32768
    monkeypatch.delenv("FLUENTVIBE_LM_MAX_TOKENS")
    seen.clear()
    LMStudioChatClient().complete(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert "max_tokens" not in seen


def test_lmstudio_reports_output_limit_instead_of_an_empty_turn(monkeypatch):
    import pytest

    from fluentvibe.authoring.lm_client import LMStudioError

    body = b'{"choices":[{"message":{"role":"assistant","content":""},"finish_reason":"length"}]}'
    monkeypatch.setattr("urllib.request.urlopen", _json_response(body, {}))
    with pytest.raises(LMStudioError, match="output-token limit"):
        LMStudioChatClient().complete(messages=[{"role": "user", "content": "hi"}], tools=[])
