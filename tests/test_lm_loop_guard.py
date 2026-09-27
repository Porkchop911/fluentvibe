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

    def fake_once(self, *, messages, tools):
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

    def fake_once(self, *, messages, tools):
        raise lm_client.LMOutputLimitError("cut off")

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    with pytest.raises(lm_client.LMOutputLimitError):
        _client().complete(messages=[], tools=[])


def test_a_reply_that_ends_inside_its_reasoning_is_retried(monkeypatch):
    calls = []

    def fake_once(self, *, messages, tools):
        calls.append(messages)
        if len(calls) == 1:
            return {"role": "assistant", "content": None, "tool_calls": [],
                    "reasoning_fields": {"reasoning_content": "def build_worktable(): ..."}}
        return {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "simulate_python_draft"}}]}

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    message = _client().complete(messages=[{"role": "user", "content": "go"}], tools=[{"type": "function"}])
    assert message["tool_calls"] and "ended inside your reasoning" in calls[1][-1]["content"]
    # The retry gets the first attempt's reasoning back instead of starting over.
    assert "def build_worktable(): ..." in calls[1][-2]["content"] and calls[1][-2]["role"] == "assistant"


def test_a_second_reasoning_only_reply_fails_immediately(monkeypatch):
    calls = []

    def fake_once(self, *, messages, tools):
        calls.append(messages)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [],
            "reasoning_fields": {"reasoning_content": "Still thinking about the same repair."},
        }

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    with pytest.raises(LMReasoningOnlyError, match="twice"):
        _client().complete(
            messages=[{"role": "user", "content": "go"}],
            tools=[{"type": "function", "function": {"name": "edit_draft"}}],
        )
    assert len(calls) == 2


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
    monkeypatch.setenv("FLUENTVIBE_LM_TEMPERATURE", "1.0")
    assert _client().temperature == 1.0
    monkeypatch.delenv("FLUENTVIBE_LM_TEMPERATURE")
    assert _client().temperature == 0.2


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
