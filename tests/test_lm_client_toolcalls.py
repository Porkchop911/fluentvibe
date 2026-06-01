"""Recover tool calls some models (qwen3.6-27b) emit as text in `content`
instead of the OpenAI `tool_calls` field. Without this, such turns yield
zero tool calls and the graph hard-fails "Model returned no Python draft".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.authoring.lm_client import (  # noqa: E402
    _parse_textual_tool_calls,
    _recover_textual_tool_calls,
)


def test_parses_pseudo_xml_function_block():
    # Exact shape seen in build/labscope_enforcemanual4 turn004.
    text = (
        "Now I need to draft the Python code.\n"
        "<tool_call>\n<function=lookup_api>\n"
        "<parameter=object_or_class>\nwt.liha\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    calls = _parse_textual_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "lookup_api"
    assert json.loads(calls[0]["function"]["arguments"]) == {"object_or_class": "wt.liha"}
    assert calls[0]["type"] == "function"


def test_parses_json_block_and_multiple_calls():
    text = (
        '<tool_call>{"name": "lookup_workspace", "arguments": {"name_or_guid": "SAT"}}</tool_call>'
        "\nsome prose\n"
        "<tool_call><function=lookup_rules><parameter=protocol_type>transfer</parameter></function></tool_call>"
    )
    calls = _parse_textual_tool_calls(text)
    assert [c["function"]["name"] for c in calls] == ["lookup_workspace", "lookup_rules"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"name_or_guid": "SAT"}
    assert json.loads(calls[1]["function"]["arguments"]) == {"protocol_type": "transfer"}


def test_no_false_positive_on_plain_text():
    assert _parse_textual_tool_calls("just a normal answer, no tools") == []
    assert _parse_textual_tool_calls("") == []


def test_recover_populates_message_and_strips_content():
    msg = {
        "role": "assistant",
        "content": "Reasoning done.\n<tool_call><function=lookup_api>"
        "<parameter=object_or_class>wt.liha</parameter></function></tool_call>",
        "tool_calls": [],
    }
    _recover_textual_tool_calls(msg)
    assert msg["tool_calls"] and msg["tool_calls"][0]["function"]["name"] == "lookup_api"
    assert "<tool_call>" not in (msg["content"] or "")


def test_recover_is_noop_when_structured_tool_calls_present():
    msg = {
        "content": "<tool_call><function=foo></function></tool_call>",
        "tool_calls": [{"id": "1", "type": "function", "function": {"name": "real", "arguments": "{}"}}],
    }
    _recover_textual_tool_calls(msg)
    assert [c["function"]["name"] for c in msg["tool_calls"]] == ["real"]


def test_recover_reads_reasoning_field():
    msg = {
        "content": None,
        "tool_calls": [],
        "reasoning_content": "<tool_call><function=plan_protocol_resources>"
        "<parameter=phases>[]</parameter></function></tool_call>",
    }
    _recover_textual_tool_calls(msg)
    assert msg["tool_calls"][0]["function"]["name"] == "plan_protocol_resources"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"phases": []}
