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


def test_a_reply_cut_off_inside_its_reasoning_is_continued_from_there(monkeypatch):
    """Seen on Dynabeads: the model ends its output mid-sentence of its
    reasoning (~15k tokens). The reply is continued, not started over."""
    calls = []

    def fake_once(self, *, messages, tools, continue_final=False, **_kw):
        calls.append((messages, continue_final))
        if len(calls) == 1:
            return {"role": "assistant", "content": None, "tool_calls": [],
                    "reasoning_fields": {"reasoning_content": "10. Wash 2-3 times with 1"}}
        return {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "submit_bench_spec"}}],
                "reasoning_fields": {"reasoning_content": "X B&W. Done."}}

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    message = _client().complete(messages=[{"role": "user", "content": "go"}], tools=[{"type": "function"}])
    assert message["tool_calls"] and len(calls) == 2 and calls[1][1] is True
    assert calls[1][0][-1] == {"role": "assistant", "content": "<think>\n10. Wash 2-3 times with 1"}
    assert message["reasoning_fields"]["reasoning_content"] == "10. Wash 2-3 times with 1X B&W. Done."


def test_a_reply_that_keeps_ending_inside_its_reasoning_is_retried_at_medium_then_fails(monkeypatch):
    calls = []

    def fake_once(self, *, messages, tools, effort=None, continue_final=False, **_kw):
        calls.append((messages, effort, continue_final))
        return {"role": "assistant", "content": None, "tool_calls": [],
                "reasoning_fields": {"reasoning_content": "def build_worktable(): ..."}}

    monkeypatch.setattr(LMStudioChatClient, "_complete_once", fake_once)
    with pytest.raises(LMReasoningOnlyError, match="twice"):
        _client().complete(messages=[{"role": "user", "content": "go"}],
                           tools=[{"type": "function", "function": {"name": "edit_draft"}}])
    per_attempt = 1 + lm_client.MAX_CONTINUATIONS
    assert len(calls) == 2 * per_attempt
    assert [c[2] for c in calls[:per_attempt]] == [False] + [True] * lm_client.MAX_CONTINUATIONS
    retry_messages, retry_effort, _ = calls[per_attempt]
    assert retry_effort == "medium" and "ended inside your reasoning" in retry_messages[-1]["content"]
    # The retry gets the first attempt's reasoning back instead of starting over.
    assert "def build_worktable(): ..." in retry_messages[-2]["content"]


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
            raise lm_client.LMReasoningBudgetError("volumes: 50 ul beads, 100 ul DNA", 20000)
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
