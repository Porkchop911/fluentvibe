"""OpenAI-compatible LM Studio chat client for protocol authoring."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .trace import ModelTraceRecorder

# The authoring loop talks to any OpenAI-compatible chat endpoint (LM Studio,
# Ollama, vLLM, …). Defaults target a local server; override per-machine with
# FLUENTVIBE_LM_ENDPOINT / FLUENTVIBE_LM_MODEL or the `fluentvibe author` flags.
DEFAULT_LM_STUDIO_ENDPOINT = os.environ.get(
    "FLUENTVIBE_LM_ENDPOINT", "http://localhost:1234/v1/chat/completions"
)
DEFAULT_LM_STUDIO_MODEL = os.environ.get("FLUENTVIBE_LM_MODEL", "qwen3.6-27b")
DEFAULT_REQUEST_TIMEOUT_S = 240.0


def _request_timeout_from_env() -> float:
    raw = os.environ.get("FLUENTVIBE_LM_TIMEOUT_S")
    if raw is None:
        return DEFAULT_REQUEST_TIMEOUT_S
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError("FLUENTVIBE_LM_TIMEOUT_S must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("FLUENTVIBE_LM_TIMEOUT_S must be a positive number")
    return timeout


@dataclass(frozen=True)
class LMStudioError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class LMStudioChatClient:
    """Small stdlib-only client for LM Studio's OpenAI chat endpoint."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT,
        model: str = DEFAULT_LM_STUDIO_MODEL,
        trace_recorder: ModelTraceRecorder | None = None,
        request_timeout_s: float | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.trace_recorder = trace_recorder
        self.request_timeout_s = (
            _request_timeout_from_env()
            if request_timeout_s is None
            else float(request_timeout_s)
        )
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be a positive number")
        self._run_deadline: float | None = None

    def start_run_budget(self, timeout_s: float) -> None:
        """Bound all model calls made by one authoring run."""
        timeout = float(timeout_s)
        if timeout <= 0:
            raise ValueError("run timeout must be a positive number")
        self._run_deadline = time.monotonic() + timeout

    def clear_run_budget(self) -> None:
        self._run_deadline = None

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "stream": True,
            "temperature": 0.2,
        }
        if self.trace_recorder is not None:
            self.trace_recorder.record(
                "request_payload",
                model=self.model,
                endpoint=self.endpoint,
                payload=payload,
            )
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        effective_timeout = self.request_timeout_s
        if self._run_deadline is not None:
            remaining = self._run_deadline - started
            if remaining <= 0:
                message = "LM Studio authoring run timed out before the next model request"
                if self.trace_recorder is not None:
                    self.trace_recorder.record(
                        "request_error", error=message, error_type="run_timeout"
                    )
                raise LMStudioError(message)
            effective_timeout = min(effective_timeout, remaining)
        deadline = started + effective_timeout
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    return self._read_stream(response, deadline=deadline)
                data = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            message = f"LM Studio request timed out after {effective_timeout:g}s"
            if self.trace_recorder is not None:
                self.trace_recorder.record(
                    "request_error",
                    error=message,
                    error_type="timeout",
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            raise LMStudioError(message) from exc
        except urllib.error.URLError as exc:
            is_timeout = isinstance(getattr(exc, "reason", None), TimeoutError)
            error = (
                f"LM Studio request timed out after {effective_timeout:g}s"
                if is_timeout
                else str(exc)
            )
            if self.trace_recorder is not None:
                self.trace_recorder.record(
                    "request_error",
                    error=error,
                    error_type="timeout" if is_timeout else type(exc).__name__,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            if is_timeout:
                raise LMStudioError(error) from exc
            raise LMStudioError(f"LM Studio request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            if self.trace_recorder is not None:
                self.trace_recorder.record("request_error", error=str(exc))
            raise LMStudioError(f"LM Studio returned invalid JSON: {exc}") from exc
        message = self._message_from_response(data)
        if self.trace_recorder is not None:
            self.trace_recorder.record(
                "response_final",
                model=self.model,
                endpoint=self.endpoint,
                finish_reason=message.get("finish_reason"),
                assistant_content=message.get("content"),
                tool_calls=message.get("tool_calls") or [],
                reasoning_fields=_reasoning_fields(message),
                response=data,
            )
        return message

    def _read_stream(self, response, *, deadline: float | None = None) -> dict[str, Any]:
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        reasoning_parts: dict[str, list[str]] = {}

        for raw_line in response:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError
            line = raw_line.decode("utf-8").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            if self.trace_recorder is not None:
                self.trace_recorder.record("raw_stream_line", raw_line=data_text)
            try:
                chunk = json.loads(data_text)
            except json.JSONDecodeError as exc:
                if self.trace_recorder is not None:
                    self.trace_recorder.record("request_error", error=str(exc), raw_line=data_text)
                raise LMStudioError(f"LM Studio stream returned invalid JSON: {exc}") from exc
            provider_error = _provider_error_message(chunk)
            if provider_error is not None:
                if self.trace_recorder is not None:
                    self.trace_recorder.record(
                        "request_error",
                        error=provider_error,
                        error_type="provider_error",
                        response=chunk,
                    )
                raise LMStudioError(f"LM Studio provider error: {provider_error}")
            if self.trace_recorder is not None:
                self.trace_recorder.record("raw_stream_chunk", chunk=chunk)
            choice = (chunk.get("choices") or [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            for key, value in _reasoning_fields(delta).items():
                if isinstance(value, str):
                    reasoning_parts.setdefault(key, []).append(value)
                else:
                    reasoning_parts.setdefault(key, []).append(json.dumps(value, default=str))
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index", 0))
                existing = tool_calls.setdefault(
                    index,
                    {
                        "id": call.get("id"),
                        "type": call.get("type", "function"),
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if call.get("id"):
                    existing["id"] = call["id"]
                if call.get("type"):
                    existing["type"] = call["type"]
                function = call.get("function") or {}
                if function.get("name"):
                    existing["function"]["name"] += function["name"]
                if function.get("arguments"):
                    existing["function"]["arguments"] += function["arguments"]

        message = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
            "finish_reason": finish_reason,
        }
        if reasoning_parts:
            message["reasoning_fields"] = {
                key: "".join(parts) for key, parts in reasoning_parts.items()
            }
        _recover_textual_tool_calls(message)
        if self.trace_recorder is not None:
            self.trace_recorder.record(
                "response_final",
                model=self.model,
                endpoint=self.endpoint,
                finish_reason=finish_reason,
                assistant_content=message.get("content"),
                tool_calls=message.get("tool_calls") or [],
                reasoning_fields=message.get("reasoning_fields") or {},
            )
        return message

    def _message_from_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            raise LMStudioError("LM Studio response did not include choices.")
        message = dict(choices[0].get("message") or {})
        message["finish_reason"] = choices[0].get("finish_reason")
        _recover_textual_tool_calls(message)
        return message


_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_FUNCTION_RE = re.compile(r"<function=([^>\s]+)\s*>(.*?)</function>", re.DOTALL | re.IGNORECASE)
_PARAMETER_RE = re.compile(r"<parameter=([^>\s]+)\s*>(.*?)</parameter>", re.DOTALL | re.IGNORECASE)


def _coerce_arg(value: str) -> Any:
    text = value.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _parse_textual_tool_calls(text: str) -> list[dict[str, Any]]:
    """Recover tool calls some models (e.g. qwen) emit as text in `content`
    instead of the OpenAI `tool_calls` field.

    Handles two block bodies inside `<tool_call>...</tool_call>`:
      * JSON: ``{"name": "lookup_api", "arguments": {...}}``
      * pseudo-XML: ``<function=NAME><parameter=P>VAL</parameter></function>``
    """
    if not text or "<tool_call" not in text:
        return []
    calls: list[dict[str, Any]] = []
    for idx, body in enumerate(_TOOL_CALL_BLOCK_RE.findall(text)):
        name: str | None = None
        arguments: str | None = None
        stripped = body.strip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
                name = obj.get("name") or obj.get("function")
                args = obj.get("arguments", obj.get("parameters", {}))
                arguments = args if isinstance(args, str) else json.dumps(args, default=str)
            except (json.JSONDecodeError, ValueError):
                name = None
        if name is None:
            fn = _FUNCTION_RE.search(body)
            if fn is None:
                continue
            name = fn.group(1).strip()
            params = {
                pname.strip(): _coerce_arg(pval)
                for pname, pval in _PARAMETER_RE.findall(fn.group(2))
            }
            arguments = json.dumps(params, default=str)
        calls.append({
            "id": f"textual-{idx}",
            "type": "function",
            "function": {"name": name, "arguments": arguments or "{}"},
        })
    return calls


def _recover_textual_tool_calls(message: dict[str, Any]) -> None:
    """If a message has no structured tool_calls but its content/reasoning
    embeds textual tool-call blocks, populate tool_calls and strip the
    blocks from content so the leftover prose is not mistaken for an answer.
    """
    if message.get("tool_calls"):
        return
    sources = [message.get("content") or ""]
    for value in (message.get("reasoning_fields") or {}).values():
        if isinstance(value, str):
            sources.append(value)
    for value in _reasoning_fields(message).values():
        if isinstance(value, str):
            sources.append(value)
    for src in sources:
        recovered = _parse_textual_tool_calls(src)
        if recovered:
            message["tool_calls"] = recovered
            if message.get("content"):
                message["content"] = _TOOL_CALL_BLOCK_RE.sub("", message["content"]).strip() or None
            return


def _reasoning_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if "reasoning" in key.lower() and value not in (None, "")
    }


def _provider_error_message(data: dict[str, Any]) -> str | None:
    """Return an OpenAI-compatible error message embedded in a stream chunk."""
    if data.get("choices"):
        return None
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("type")
        if message:
            return str(message)
    if isinstance(error, str) and error.strip():
        return error.strip()
    message = data.get("message")
    return str(message).strip() if message else None


def _endpoint_to_base_url(endpoint: str) -> str:
    """Strip the trailing /chat/completions path so ChatOpenAI can append it back."""
    suffix = "/chat/completions"
    if endpoint.endswith(suffix):
        return endpoint[: -len(suffix)]
    return endpoint.rstrip("/")


def make_chat_client(
    *,
    model: str = DEFAULT_LM_STUDIO_MODEL,
    endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT,
    api_key: str = "lm-studio",
    temperature: float = 0.2,
    streaming: bool = True,
) -> Any:
    """Return a `langchain_openai.ChatOpenAI` configured for the LM Studio endpoint.

    LM Studio speaks OpenAI's chat-completions protocol natively, so no custom
    adapter is needed.  The `api_key` is required by ChatOpenAI but ignored by
    LM Studio — the placeholder value is fine.

    Lazy-imported so the legacy `LMStudioChatClient` path keeps working when
    `langchain-openai` is not installed yet.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=_endpoint_to_base_url(endpoint),
        api_key=api_key,
        temperature=temperature,
        streaming=streaming,
        model_kwargs={"parallel_tool_calls": True},
    )
