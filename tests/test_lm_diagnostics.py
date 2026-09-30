import io
import json

from fluentvibe.authoring.lm_client import LMStudioChatClient
from fluentvibe.authoring.trace import ModelTraceConfig, ModelTraceRecorder


def test_stream_trace_preserves_stop_reason_and_raw_token_ids(tmp_path):
    recorder = ModelTraceRecorder(ModelTraceConfig(enabled=True, raw_stream=True, output_dir=tmp_path))
    client = LMStudioChatClient(trace_recorder=recorder)
    chunk = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop", "stop_reason": 248046,
                          "token_ids": [10, 248046]}]}
    stream = io.BytesIO(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n").encode())
    message = client._read_stream(stream)
    assert message["stop_reason"] == 248046
    events = [json.loads(line) for line in recorder.current_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["stop_reason"] == 248046
    assert json.loads(next(e["raw_line"] for e in events if e["event"] == "raw_stream_line")) == chunk


def test_non_stream_response_preserves_stop_reason():
    client = LMStudioChatClient()
    assert client._message_from_response({"choices": [{"message": {"content": "done"},
                                                       "finish_reason": "stop", "stop_reason": 42}]})["stop_reason"] == 42


def test_diagnostic_extension_is_opt_in(monkeypatch):
    requests = []

    class Response(io.BytesIO):
        headers = {"Content-Type": "application/json"}

    def urlopen(request, **kwargs):
        requests.append(json.loads(request.data))
        return Response(b'{"choices":[{"message":{"content":"done"},"finish_reason":"stop"}]}')

    monkeypatch.setattr("fluentvibe.authoring.lm_client.urllib.request.urlopen", urlopen)
    for enabled in (False, True):
        LMStudioChatClient(capture_token_ids=enabled).complete(messages=[], tools=[])
    assert "return_token_ids" not in requests[0]
    assert requests[1]["return_token_ids"] is True
    assert requests[1]["stream_options"] == {"include_usage": True}
