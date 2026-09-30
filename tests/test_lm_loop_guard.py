"""Streamed replies that loop on one line are stopped and the turn retried once."""

from __future__ import annotations

import pytest

from fluentvibe.authoring import lm_client
from fluentvibe.authoring.lm_client import (
    LMReasoningOnlyError,
    LMRepetitionError,
    LMStudioChatClient,
    _RepetitionWatch,
)


def test_watch_spots_a_looping_line_but_not_varied_reasoning():
    loop = _RepetitionWatch()
    loop.feed("The mix fails because the tips hold liquid.\n")
    for _ in range(60):
        loop.feed("Let me try: 222 = 80 + 60 + 82?\n\n")
    assert loop.looping_line() == "Let me try: 222 = 80 + 60 + 82?"

    varied = _RepetitionWatch()
    for i in range(400):
        varied.feed(f"Step {i}: aspirate {i * 3} ul from column {i % 12 + 1}.\n")
    assert varied.looping_line() is None


def _client():
    return LMStudioChatClient(endpoint="http://127.0.0.1:9/v1/chat/completions", model="m")


def test_a_looping_turn_is_retried_once_with_a_nudge(monkeypatch):
    calls = []

    def fake_once(self, *, messages, tools, **_kw):
        calls.append(messages)
        if len(calls) == 1:
            raise LMRepetitionError("Let me try: 222 = 80 + 60 + 82?")
        return {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "edit_draft"}}]}

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    message = _client().complete(messages=[{"role": "user", "content": "go"}], tools=[])
    assert message["tool_calls"] and len(calls) == 2
    assert "stuck repeating" in calls[1][-1]["content"] and calls[1][:-1] == calls[0]


def test_the_guard_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("FLUENTVIBE_LM_LOOP_GUARD", "0")

    def fake_once(self, *, messages, tools, **_kw):
        raise lm_client.LMOutputLimitError("cut off")

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    with pytest.raises(lm_client.LMOutputLimitError):
        _client().complete(messages=[], tools=[])


_TOOLS = [{"type": "function", "function": {"name": "submit_bench_spec"}}]
_CUT = {"role": "assistant", "content": None, "tool_calls": [],
        "reasoning_fields": {"reasoning_content": "10. Wash 2-3 times with 1"}}
_DONE = {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "submit_bench_spec"}}],
         "reasoning_fields": {"reasoning_content": "X B&W. Done."}}


def test_a_reply_cut_off_inside_its_reasoning_is_continued_from_there(monkeypatch):
    """Seen on Dynabeads: the model ends its output mid-sentence of its
    reasoning (~15k tokens). The reply is continued, not started over."""
    continued = []
    monkeypatch.setattr(LMStudioChatClient, "_complete_once", lambda self, **kw: dict(_CUT))

    def fake_continue(self, *, messages, tools, effort, reasoning, budget):
        continued.append((reasoning, effort, budget))
        return dict(_DONE)

    monkeypatch.setattr(LMStudioChatClient, "_continue_reasoning", fake_continue)
    message = _client().complete(messages=[{"role": "user", "content": "go"}], tools=_TOOLS)
    assert message["tool_calls"] and continued == [("10. Wash 2-3 times with 1", None, 30000 - 6)]
    assert message["reasoning_fields"]["reasoning_content"] == "10. Wash 2-3 times with 1X B&W. Done."


def test_a_reply_that_keeps_ending_inside_its_reasoning_is_retried_at_medium_then_fails(monkeypatch):
    once, continued = [], []

    def fake_once(self, *, messages, tools, effort=None, **_kw):
        once.append((messages, effort))
        return {"role": "assistant", "content": None, "tool_calls": [],
                "reasoning_fields": {"reasoning_content": "def build_worktable(): ..."}}

    def fake_continue(self, *, effort, **_kw):
        continued.append(effort)
        return dict(_CUT)

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    monkeypatch.setattr(LMStudioChatClient, "_continue_reasoning", fake_continue)
    with pytest.raises(LMReasoningOnlyError, match="twice"):
        _client().complete(messages=[{"role": "user", "content": "go"}], tools=_TOOLS)
    assert [e for _, e in once] == [None, "medium"]                   # two logical attempts, no third
    assert continued == [None] * lm_client.MAX_CONTINUATIONS + ["medium"] * lm_client.MAX_CONTINUATIONS
    retry_messages = once[1][0]
    assert "ended inside your reasoning" in retry_messages[-1]["content"]
    assert "def build_worktable(): ..." in retry_messages[-2]["content"]   # its notes handed back


def test_a_failing_retry_is_final_and_stays_within_the_budget(monkeypatch):
    """Codex review: budget -> medium -> repetition used to start a third,
    unbudgeted request at xhigh."""
    calls = []

    def fake_once(self, *, messages, tools, effort=None, budget=None):
        calls.append((effort, budget))
        if len(calls) == 1:
            raise lm_client.LMReasoningBudgetError("notes", 30000)
        raise LMRepetitionError("Let me try again.")

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    with pytest.raises(LMRepetitionError):
        _client().complete(messages=[{"role": "user", "content": "go"}], tools=_TOOLS)
    assert calls == [(None, 30000), ("medium", 30000)]


def test_a_server_that_cannot_continue_falls_back_to_the_retry(monkeypatch):
    calls = []

    def fake_once(self, *, messages, tools, effort=None, **_kw):
        calls.append(effort)
        return dict(_CUT) if len(calls) == 1 else dict(_DONE)

    def refuse(self, **_kw):
        raise lm_client.LMContinuationUnsupported("tokenize: HTTP 404")

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    monkeypatch.setattr(LMStudioChatClient, "_continue_reasoning", refuse)
    client = _client()
    assert client.complete(messages=[{"role": "user", "content": "go"}], tools=_TOOLS)["tool_calls"]
    assert calls == [None, "medium"] and client._continuation_ok is False


def test_continuation_is_token_exact(monkeypatch):
    """The server renders the prompt with its own template (ending in an
    open <think>), the reasoning is appended as raw tokens, and the raw
    completion's XML tool call is typed by the tool schema."""
    import io
    import json as _json

    posted = []

    def fake_post(self, url, body, **_kw):
        posted.append((url, body))
        if "messages" in body:
            return {"tokens": [1, 2, 3], "token_strs": ["assistant", "\u010a", "<think>\u010a"],
                    "max_model_len": 1000}
        return {"tokens": [7, 8]}

    sent = {}

    class Stream(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        sent.update(_json.loads(req.data))
        text = ('51 = 391.\n</think>\n\n<tool_call>\n<function=submit>\n<parameter=value>\n391\n'
                '</parameter>\n<parameter=note>\n391\n</parameter>\n</function>\n</tool_call>')
        lines = [b"data: " + _json.dumps({"choices": [{"text": text[i:i + 20]}]}).encode() + b"\n"
                 for i in range(0, len(text), 20)]
        return Stream(b"".join(lines) + b"data: [DONE]\n")

    monkeypatch.setattr(LMStudioChatClient, "_post_json", fake_post)
    monkeypatch.setattr(lm_client.urllib.request, "urlopen", fake_urlopen)
    tools = [{"type": "function", "function": {"name": "submit", "parameters": {"type": "object", "properties": {
        "value": {"type": "number"}, "note": {"type": "string"}}}}}]
    client = LMStudioChatClient(endpoint="http://h:1/v1/chat/completions", model="m", reasoning_effort="xhigh")
    message = client._continue_reasoning(messages=[{"role": "user", "content": "go"}], tools=tools,
                                         effort="medium", reasoning="17*3 = ", budget=1000)
    assert posted[0][0] == "http://h:1/tokenize" and posted[0][1]["chat_template_kwargs"] == {"reasoning_effort": "medium"}
    assert posted[1][1] == {"model": "m", "prompt": "17*3 = ", "add_special_tokens": False}
    assert sent["prompt"] == [1, 2, 3, 7, 8] and sent["max_tokens"] == 995
    assert message["reasoning_fields"]["reasoning_content"] == "51 = 391.\n"
    assert _json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"value": 391, "note": "391"}


def test_no_tool_fields_are_sent_without_tools(monkeypatch):
    sent = {}

    class Stop(Exception):
        pass

    def fake_urlopen(req, timeout):
        import json
        sent.update(json.loads(req.data))
        raise Stop

    monkeypatch.setattr(lm_client.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(Exception):
        _client()._complete_once(messages=[{"role": "user", "content": "pick skills"}], tools=[])
    assert "tools" not in sent and "tool_choice" not in sent


def test_temperature_comes_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("FLUENTVIBE_LM_TEMPERATURE", "0.6")
    monkeypatch.setenv("FLUENTVIBE_LM_TOP_P", "0.8")
    assert (_client().temperature, _client().top_p) == (0.6, 0.8)
    monkeypatch.delenv("FLUENTVIBE_LM_TEMPERATURE")
    monkeypatch.delenv("FLUENTVIBE_LM_TOP_P")
    # Default: the model's recommended thinking-mode sampling (not 0.2).
    c = _client()
    assert (c.temperature, c.top_p, c.top_k) == (0.8, 0.95, 20)


def _stream(arguments_chunks):
    import json as _json

    lines = []
    for i, chunk in enumerate(arguments_chunks):
        delta = {"tool_calls": [{"index": 0, "function": {"name": "submit_bench_spec" if i == 0 else "",
                                                           "arguments": chunk}}]}
        lines.append(("data: " + _json.dumps({"choices": [{"delta": delta}]})).encode())
    lines.append(b"data: [DONE]")
    return lines


def test_looping_tool_call_arguments_are_stopped():
    chunks = ['{"title": "AMPure", "notes": ["'] + ["!!!!!!!!!!"] * 1500
    with pytest.raises(LMRepetitionError):
        _client()._read_stream(_stream(chunks))


def test_a_long_spec_with_repeated_structure_is_not_a_loop():
    import json as _json

    spec = {"title": "wash x40", "steps": [
        {"id": f"s{i}", "op": ["separate", "remove", "separate", "add", "mix"][i % 5], "text": f"wash {i // 5} step {i % 5}",
         "location": "deck", "volume_ul": 20 + i % 3, "engage": i % 5 == 0} for i in range(200)]}
    text = _json.dumps(spec)
    chunks = [text[i:i + 40] for i in range(0, len(text), 40)]
    message = _client()._read_stream(_stream(chunks))
    assert _json.loads(message["tool_calls"][0]["function"]["arguments"])["title"] == "wash x40"


class _Response(list):
    closed = False

    def close(self):
        self.closed = True


def _reasoning_stream(text_chunks):
    import json as _json

    return _Response([("data: " + _json.dumps({"choices": [{"delta": {"reasoning_content": c}}]})).encode()
                      for c in text_chunks] + [b"data: [DONE]"])


def test_reasoning_past_the_budget_is_stopped():
    response = _reasoning_stream([f"Let me reconsider the volume of step {i}: {i * 7} ul. " for i in range(400)])
    with pytest.raises(lm_client.LMReasoningBudgetError) as info:
        _client()._read_stream(response, budget=1000)
    assert response.closed and "reconsider" in info.value.notes
    # Without a budget the same reply is read to the end.
    assert _client()._read_stream(_reasoning_stream([f"step {i} " for i in range(400)]))["tool_calls"] == []


def test_a_turn_over_budget_is_retried_once_at_medium_with_its_notes(monkeypatch):
    calls = []

    def fake_once(self, *, messages, tools, effort=None, budget=None):
        calls.append((messages, effort, budget))
        if len(calls) == 1:
            raise lm_client.LMReasoningBudgetError("volumes: 50 ul beads, 100 ul DNA", 30000)
        return {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "submit_bench_spec"}}]}

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    client = LMStudioChatClient(endpoint="http://127.0.0.1:9/v1/chat/completions", model="m",
                                reasoning_effort="xhigh")
    message = client.complete(messages=[{"role": "user", "content": "go"}],
                              tools=[{"type": "function", "function": {"name": "submit_bench_spec"}}])
    assert message["tool_calls"] and len(calls) == 2
    assert calls[0][1] is None and calls[0][2] == 30000          # first: the client's xhigh, with a budget
    assert calls[1][1] == "medium"                                 # retry: medium
    assert "50 ul beads" in calls[1][0][-2]["content"]             # its notes handed back
