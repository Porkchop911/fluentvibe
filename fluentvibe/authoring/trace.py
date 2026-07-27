"""Local JSONL tracing for authoring model requests."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModelTraceConfig:
    enabled: bool = False
    live: bool = False
    output_dir: Path = Path("build")
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def from_env(
        cls,
        *,
        output_dir: Path,
        enabled: bool | None = None,
        live: bool | None = None,
        session_id: str | None = None,
    ) -> "ModelTraceConfig":
        env_enabled = _truthy(os.environ.get("FLUENTVIBE_MODEL_TRACE"))
        env_live = _truthy(os.environ.get("FLUENTVIBE_MODEL_TRACE_LIVE"))
        resolved_live = env_live if live is None else live
        resolved_enabled = env_enabled if enabled is None else enabled
        if resolved_live:
            resolved_enabled = True
        return cls(
            enabled=resolved_enabled,
            live=resolved_live,
            output_dir=output_dir,
            session_id=session_id or uuid.uuid4().hex[:12],
        )


class ModelTraceRecorder:
    """Write one JSONL trace file per user authoring/chat turn."""

    def __init__(self, config: ModelTraceConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._turn_index = 0
        self._request_id = ""
        self._path: Path | None = None
        self._last_live_stream_at = 0.0

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def trace_dir(self) -> Path:
        return self.config.output_dir / "model_traces"

    @property
    def current_path(self) -> Path | None:
        return self._path

    @property
    def current_readable_path(self) -> Path | None:
        if self._path is None:
            return None
        return readable_trace_path(self._path)

    @classmethod
    def disabled(cls) -> "ModelTraceRecorder":
        return cls(ModelTraceConfig(enabled=False))

    def start_turn(self, turn_index: int | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._turn_index = int(turn_index or (self._turn_index + 1))
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            safe_session = "".join(
                ch if ch.isalnum() or ch in {"-", "_"} else "_"
                for ch in self.config.session_id
            )
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            self._path = self.trace_dir / (
                f"{timestamp}_{safe_session}_turn{self._turn_index:03d}.jsonl"
            )
            readable_trace_path(self._path).write_text(
                f"# Model Trace\n\n"
                f"- session: `{self.config.session_id}`\n"
                f"- turn: `{self._turn_index}`\n"
                f"- jsonl: `{self._path.name}`\n\n",
                encoding="utf-8",
            )

    def begin_request(self, **fields: Any) -> str:
        if not self.enabled:
            return ""
        with self._lock:
            if self._path is None:
                self.start_turn()
            self._request_id = uuid.uuid4().hex
            request_id = self._request_id
        self.record("request_start", request_id=request_id, **fields)
        return request_id

    def record(
        self,
        event: str,
        *,
        request_id: str | None = None,
        turn_index: int | None = None,
        **fields: Any,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._path is None:
                self.start_turn(turn_index=turn_index)
            rid = request_id or self._request_id or uuid.uuid4().hex
            if not self._request_id:
                self._request_id = rid
            item = {
                "event": event,
                "timestamp": datetime.now(UTC).isoformat(),
                "session_id": self.config.session_id,
                "turn_index": int(turn_index or self._turn_index or 1),
                "request_id": rid,
            }
            item.update(fields)
            assert self._path is not None
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            readable = _render_readable_event(item)
            if readable:
                with readable_trace_path(self._path).open("a", encoding="utf-8") as fh:
                    fh.write(readable)
        if self.config.live:
            self._print_live(item)

    def _print_live(self, item: dict[str, Any]) -> None:
        if item["event"] == "raw_stream_line":
            return
        if item["event"] == "raw_stream_chunk":
            now = time.monotonic()
            if now - self._last_live_stream_at < 5.0:
                return
            self._last_live_stream_at = now
            print("[model-trace] stream_active", file=sys.stderr, flush=True)
            return
        bits = [f"[model-trace] {item['event']}"]
        if item.get("model"):
            bits.append(f"model={item['model']}")
        if item.get("duration_ms") is not None:
            bits.append(f"duration_ms={item['duration_ms']:.1f}")
        if item.get("finish_reason"):
            bits.append(f"finish={item['finish_reason']}")
        if item.get("tool_calls") is not None:
            try:
                bits.append(f"tool_calls={len(item['tool_calls'])}")
            except TypeError:
                pass
        print(" ".join(bits), file=sys.stderr, flush=True)


def trace_config_from_env(
    *,
    output_dir: Path,
    enabled: bool | None = None,
    live: bool | None = None,
) -> ModelTraceConfig:
    return ModelTraceConfig.from_env(output_dir=output_dir, enabled=enabled, live=live)


def readable_trace_path(path: Path) -> Path:
    return path.with_suffix(".readable.md")


def render_model_trace_file(path: Path, output: Path | None = None) -> Path:
    """Render a JSONL trace into a compact Markdown file for human inspection."""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    out = output or readable_trace_path(path)
    if events:
        first = events[0]
        text = (
            "# Model Trace\n\n"
            f"- session: `{first.get('session_id', '')}`\n"
            f"- turn: `{first.get('turn_index', '')}`\n"
            f"- jsonl: `{path.name}`\n\n"
        )
    else:
        text = f"# Model Trace\n\n- jsonl: `{path.name}`\n\n"
    for event in events:
        readable = _render_readable_event(event)
        if readable:
            text += readable
    out.write_text(text, encoding="utf-8")
    return out


def _render_readable_event(item: dict[str, Any]) -> str:
    event = item.get("event")
    if event == "request_start":
        bits = [
            "## Request",
            "",
            f"- timestamp: `{item.get('timestamp', '')}`",
            f"- request_id: `{item.get('request_id', '')}`",
        ]
        if item.get("model"):
            bits.append(f"- model: `{item['model']}`")
        if item.get("endpoint"):
            bits.append(f"- endpoint: `{item['endpoint']}`")
        if item.get("iteration") is not None:
            bits.append(f"- iteration: `{item['iteration']}`")
        if item.get("phase"):
            bits.append(f"- phase: `{item['phase']}`")
        return "\n".join(bits) + "\n\n"
    if event == "request_payload":
        if "messages" in item:
            return "### Graph-Normalized State Messages\n\n" + _format_messages(item["messages"]) + "\n"
        payload = item.get("payload")
        if isinstance(payload, dict):
            messages = payload.get("messages") or []
            tools = payload.get("tools") or []
            return (
                "### Exact Provider Payload\n\n"
                f"- model: `{payload.get('model', item.get('model', ''))}`\n"
                f"- message_count: `{len(messages)}`\n"
                f"- tool_count: `{len(tools)}`\n"
                f"- stream: `{payload.get('stream')}`\n"
                f"- temperature: `{payload.get('temperature')}`\n\n"
            )
        return ""
    if event == "response_final":
        text = "## Final Response\n\n"
        if item.get("finish_reason"):
            text += f"- finish_reason: `{item['finish_reason']}`\n\n"
        reasoning = _flatten_reasoning(item.get("reasoning_fields"))
        if reasoning:
            text += "### Provider-Exposed Reasoning\n\n"
            text += _fence(reasoning) + "\n"
        assistant = item.get("assistant_content")
        if assistant:
            text += "### Assistant Content\n\n"
            text += _fence(str(assistant)) + "\n"
        tool_calls = item.get("tool_calls") or []
        if tool_calls:
            text += "### Tool Calls\n\n"
            text += _format_tool_calls(tool_calls) + "\n"
        return text
    if event == "tool_result":
        return (
            "### Tool Result Message\n\n"
            f"- tool: `{item.get('tool_name', item.get('name', ''))}`\n"
            f"- ok: `{item.get('ok', '')}`\n\n"
            + _fence(json.dumps(item.get("result", item), indent=2, ensure_ascii=False, default=str))
        )
    if event == "model_turn":
        text = "## Model Turn\n\n"
        if item.get("duration_ms") is not None:
            text += f"- duration_ms: `{float(item['duration_ms']):.1f}`\n"
        if item.get("model"):
            text += f"- model: `{item['model']}`\n"
        assistant = item.get("assistant") or {}
        reasoning = _flatten_reasoning(assistant.get("reasoning_fields"))
        if reasoning:
            text += "\n### Normalized Reasoning Fields\n\n"
            text += _fence(reasoning) + "\n"
        return text + "\n"
    if event == "request_error":
        return (
            "## Request Error\n\n"
            f"- type: `{item.get('error_type', '')}`\n"
            f"- error: `{item.get('error', '')}`\n\n"
        )
    if event == "model_retry":
        return (
            "## Model Retry\n\n"
            f"- iteration: `{item.get('iteration', '')}`\n"
            f"- error: `{item.get('error', '')}`\n\n"
        )
    return ""


def _format_messages(messages: Any) -> str:
    if not isinstance(messages, list):
        return _fence(json.dumps(messages, indent=2, ensure_ascii=False, default=str)) + "\n"
    parts = []
    for idx, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            parts.append(f"#### {idx}. message\n\n{_fence(str(message))}")
            continue
        role = message.get("role") or "message"
        content = str(message.get("content") or "")
        if role == "system" and len(content) > 1200:
            content = content[:1200] + "\n\n... [system prompt truncated in readable trace]"
        parts.append(f"#### {idx}. {role}\n\n{_fence(content)}")
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            parts.append(_format_tool_calls(tool_calls))
    return "\n\n".join(parts) + "\n"


def _format_tool_calls(tool_calls: Any) -> str:
    if not isinstance(tool_calls, list):
        return _fence(json.dumps(tool_calls, indent=2, ensure_ascii=False, default=str))
    parts = []
    for idx, call in enumerate(tool_calls, start=1):
        name = _tool_call_name(call) or f"tool_call_{idx}"
        args = _tool_call_arguments(call)
        parts.append(f"#### {idx}. `{name}`\n\n{_fence(args)}")
    return "\n\n".join(parts)


def _tool_call_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    if call.get("name"):
        return str(call["name"])
    function = call.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return ""


def _tool_call_arguments(call: Any) -> str:
    if not isinstance(call, dict):
        return str(call)
    args = call.get("args")
    if args is None:
        function = call.get("function")
        if isinstance(function, dict):
            args = function.get("arguments")
    if isinstance(args, str):
        try:
            return json.dumps(json.loads(args), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return args
    return json.dumps(args if args is not None else call, indent=2, ensure_ascii=False, default=str)


def _flatten_reasoning(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        nested = value.get("reasoning_fields")
        if isinstance(nested, dict):
            value = nested
        parts = []
        for key, part in value.items():
            if part in (None, ""):
                continue
            if key in {"reasoning", "reasoning_content"}:
                parts.append(str(part))
            else:
                parts.append(f"{key}:\n{part}")
        return "\n\n".join(parts).strip()
    return str(value).strip()


def _fence(text: str) -> str:
    return "```text\n" + text.rstrip() + "\n```\n"
