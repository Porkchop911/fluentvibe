"""OpenAI-compatible LM Studio chat client for protocol authoring."""

from __future__ import annotations

import copy
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
# FLUENTVIBE_LM_API_KEY supplies an optional Bearer token for secured endpoints.
DEFAULT_LM_STUDIO_ENDPOINT = os.environ.get(
    "FLUENTVIBE_LM_ENDPOINT", "http://localhost:18020/v1/chat/completions"
)
DEFAULT_LM_STUDIO_MODEL = os.environ.get("FLUENTVIBE_LM_MODEL", "qwen3.8-27b")
DEFAULT_REQUEST_TIMEOUT_S = 240.0
_ALLOWED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def _max_tokens_from_env() -> int | None:
    raw = os.environ.get("FLUENTVIBE_LM_MAX_TOKENS")
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("FLUENTVIBE_LM_MAX_TOKENS must be a positive integer") from exc
    if value <= 0:
        raise ValueError("FLUENTVIBE_LM_MAX_TOKENS must be a positive integer")
    return value


def _raise_if_truncated(message: dict[str, Any]) -> dict[str, Any]:
    """A response cut off by the output-token limit before any tool call is a
    configuration problem, not an empty model turn: say so explicitly."""
    if message.get("finish_reason") == "length" and not message.get("tool_calls"):
        raise LMOutputLimitError(
            "Model response hit the output-token limit (finish_reason=length) before "
            "calling a tool. Raise FLUENTVIBE_LM_MAX_TOKENS / --max-tokens or lower "
            "the reasoning effort."
        )
    return message


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


class LMOutputLimitError(LMStudioError):
    """The reply hit the output-token limit before any tool call."""


class LMRepetitionError(LMStudioError):
    """The reply kept repeating one line; the stream was stopped."""

    def __init__(self, line: str) -> None:
        super().__init__(f"Model reply is looping on one line: {line[:160]!r}")
        self.line = line


class LMReasoningOnlyError(LMStudioError):
    """Both the original turn and its single retry ended without an action."""


def _tool_names(tools: list[dict[str, Any]]) -> str:
    names = [str((t.get("function") or {}).get("name") or t.get("name") or "") for t in tools or ()]
    return ", ".join(n for n in names if n) or "the next tool call"


def _reasoning_only(message: dict[str, Any]) -> bool:
    """A reply with reasoning but neither text nor a tool call."""
    if message.get("tool_calls") or (message.get("content") or "").strip():
        return False
    fields = message.get("reasoning_fields") or {}
    return any(str(value).strip() for value in fields.values())


def _temperature_from_env() -> float:
    raw = os.environ.get("FLUENTVIBE_LM_TEMPERATURE")
    if raw is None or not raw.strip():
        return 0.2
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError("FLUENTVIBE_LM_TEMPERATURE must be a number") from exc


def _loop_guard_enabled() -> bool:
    return os.environ.get("FLUENTVIBE_LM_LOOP_GUARD", "1").strip().lower() not in {"0", "false", "no", "off"}


class _RepetitionWatch:
    """Spots a streamed reply that keeps repeating one line or phrase."""

    CHECK_EVERY = 1500   # characters between checks
    TAIL = 6000          # characters examined
    MIN_REPEATS = 8

    def __init__(self) -> None:
        self._text: list[str] = []
        self._size = 0
        self._checked_at = 0

    def feed(self, piece: str) -> None:
        self._text.append(piece)
        self._size += len(piece)

    def looping_line(self) -> str | None:
        if self._size - self._checked_at < self.CHECK_EVERY:
            return None
        self._checked_at = self._size
        tail = "".join(self._text)[-self.TAIL:]
        lines = [line.strip() for line in tail.splitlines() if len(line.strip()) >= 12]
        if lines:
            from collections import Counter

            line, count = Counter(lines).most_common(1)[0]
            if count >= self.MIN_REPEATS and count * len(line) >= 0.4 * len(tail):
                return line
        # The same phrase over and over without line breaks.
        for size in (20, 40, 80, 160, 320):
            if len(tail) < size * self.MIN_REPEATS:
                break
            phrase = tail[-size:]
            if phrase.strip() and tail.count(phrase) >= self.MIN_REPEATS and \
                    tail.count(phrase) * size >= 0.5 * len(tail):
                return phrase.strip()
        return None


class LMStudioChatClient:
    """Small stdlib-only client for LM Studio's OpenAI chat endpoint."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT,
        model: str = DEFAULT_LM_STUDIO_MODEL,
        trace_recorder: ModelTraceRecorder | None = None,
        request_timeout_s: float | None = None,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        presence_penalty: float | None = None,
        repetition_penalty: float | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("FLUENTVIBE_LM_API_KEY")
        self.reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else os.environ.get("FLUENTVIBE_LM_REASONING_EFFORT")
        )
        if self.reasoning_effort is not None:
            self.reasoning_effort = self.reasoning_effort.strip().lower()
            if self.reasoning_effort not in _ALLOWED_REASONING_EFFORTS:
                raise ValueError(
                    "reasoning_effort must be one of low, medium, high, xhigh"
                )
        # Output-token cap per model response. Some servers (e.g. NInfer) apply
        # a small default when it is omitted, which cuts long reasoning turns
        # off before any tool call. FLUENTVIBE_LM_MAX_TOKENS sets it globally.
        self.max_tokens = max_tokens if max_tokens is not None else _max_tokens_from_env()
        # FLUENTVIBE_LM_TEMPERATURE sets it globally (benchmarks); 0.2 otherwise.
        self.temperature = float(temperature) if temperature is not None else _temperature_from_env()
        self.top_p = None if top_p is None else float(top_p)
        self.top_k = None if top_k is None else int(top_k)
        self.min_p = None if min_p is None else float(min_p)
        self.presence_penalty = (
            None if presence_penalty is None else float(presence_penalty)
        )
        self.repetition_penalty = (
            None if repetition_penalty is None else float(repetition_penalty)
        )
        if self.temperature < 0:
            raise ValueError("temperature must be non-negative")
        if self.top_p is not None and not 0 <= self.top_p <= 1:
            raise ValueError("top_p must be between 0 and 1")
        if self.top_k is not None and self.top_k < 0:
            raise ValueError("top_k must be non-negative")
        if self.min_p is not None and not 0 <= self.min_p <= 1:
            raise ValueError("min_p must be between 0 and 1")
        if self.presence_penalty is not None and not -2 <= self.presence_penalty <= 2:
            raise ValueError("presence_penalty must be between -2 and 2")
        if self.repetition_penalty is not None and self.repetition_penalty <= 0:
            raise ValueError("repetition_penalty must be positive")
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
        """One model turn. A reply that loops on one line, or runs out of
        output tokens before any tool call, is retried once with a short
        instruction to act (``FLUENTVIBE_LM_LOOP_GUARD=0`` turns this off)."""
        try:
            message = self._complete_once(messages=messages, tools=tools)
            if tools and _loop_guard_enabled() and _reasoning_only(message):
                # The turn ended inside the reasoning (seen with vLLM: the draft
                # written in reasoning, finish_reason=stop, no text, no tool call).
                if self.trace_recorder is not None:
                    self.trace_recorder.record("turn_retry", reason="reasoning_only")
                print("[lm] reply ended inside its reasoning without a tool call -- retrying the turn once",
                      flush=True)
                retry = self._complete_once(messages=[*messages, {"role": "user", "content": (
                    "Your previous reply ended inside your reasoning, without a tool call or any text. "
                    f"Keep the reasoning short and make one of the offered tool calls now: {_tool_names(tools)}."
                )}], tools=tools)
                if _reasoning_only(retry):
                    raise LMReasoningOnlyError(
                        "Model ended inside reasoning twice without text or a tool call."
                    )
                return retry
            return message
        except (LMRepetitionError, LMOutputLimitError) as exc:
            if not _loop_guard_enabled():
                raise
            if self.trace_recorder is not None:
                self.trace_recorder.record("turn_retry", reason=type(exc).__name__, error=str(exc))
            print(f"[lm] {exc} -- retrying the turn once", flush=True)
            if isinstance(exc, LMRepetitionError):
                nudge = (
                    f"Your previous reply got stuck repeating the same line ({exc.line[:120]!r}) and was "
                    "stopped. Do not re-derive it. Decide now and make one of the offered tool calls: "
                    f"{_tool_names(tools)}."
                )
            else:
                nudge = (
                    "Your previous reply ran out of output tokens before any tool call. Keep the "
                    f"reasoning short and make one of the offered tool calls now: {_tool_names(tools)}."
                )
            return self._complete_once(messages=[*messages, {"role": "user", "content": nudge}], tools=tools)

    def _complete_once(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        request_tools = _strict_workflow_tools(tools) if _truthy(os.environ.get("FLUENTVIBE_LM_STRICT_WORKFLOW")) else tools
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": self.temperature,
        }
        for name in (
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "repetition_penalty",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if request_tools:
            # vLLM rejects `tools: []` (HTTP 400); omit tool fields when none are offered.
            payload.update({"tools": request_tools, "tool_choice": "auto", "parallel_tool_calls": True})
        # Qwen-compatible servers accept this optional control. Omit it by
        # default so other OpenAI-compatible providers retain their behavior.
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.trace_recorder is not None:
            self.trace_recorder.record(
                "request_payload",
                model=self.model,
                endpoint=self.endpoint,
                payload=payload,
            )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
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
                    return _raise_if_truncated(self._read_stream(response, deadline=deadline))
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                raw_body = exc.read(8192).decode("utf-8", errors="replace")
            except Exception:
                raw_body = ""
            detail = _safe_http_error_detail(raw_body)
            error = f"HTTP {exc.code} {exc.reason}"
            if detail:
                error += f": {detail}"
            if self.trace_recorder is not None:
                self.trace_recorder.record(
                    "request_error", error=error, error_type="HTTPError",
                    status_code=exc.code,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            raise LMStudioError(f"LM Studio request failed: {error}") from exc
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
        return _raise_if_truncated(message)

    def _read_stream(self, response, *, deadline: float | None = None) -> dict[str, Any]:
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        reasoning_parts: dict[str, list[str]] = {}
        watch = _RepetitionWatch() if _loop_guard_enabled() else None

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
            if (
                self.trace_recorder is not None
                and self.trace_recorder.raw_stream_enabled
            ):
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
            choice = (chunk.get("choices") or [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
                if watch is not None:
                    watch.feed(delta["content"])
            for key, value in _reasoning_fields(delta).items():
                if isinstance(value, str):
                    reasoning_parts.setdefault(key, []).append(value)
                    if watch is not None:
                        watch.feed(value)
                else:
                    reasoning_parts.setdefault(key, []).append(json.dumps(value, default=str))
            if watch is not None and not tool_calls:
                looping = watch.looping_line()
                if looping is not None:
                    if self.trace_recorder is not None:
                        self.trace_recorder.record("request_error", error_type="repetition", line=looping)
                    raise LMRepetitionError(looping)
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


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _strict_workflow_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Opt into vLLM structured tool decoding for the workflow declaration only."""
    cloned = copy.deepcopy(tools)
    for tool in cloned:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name") == "declare_protocol_workflow":
            function["strict"] = True
    return cloned
def _safe_http_error_detail(raw_body: str) -> str:
    """Extract a concise provider message without exposing auth secrets."""
    text = (raw_body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        detail = text
    else:
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or error.get("detail") or error.get("type") or text
            else:
                detail = parsed.get("message") or parsed.get("detail") or text
        else:
            detail = text
    detail = str(detail).replace("\r", " ").replace("\n", " ").strip()
    detail = re.sub(r"(?i)(authorization|api[-_ ]?key|token)\s*[:=]\s*[^,; ]+", r"\1: [redacted]", detail)
    return detail[:1000]
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
    api_key: str | None = None,
    temperature: float = 0.2,
    streaming: bool = True,
) -> Any:
    """Return a `langchain_openai.ChatOpenAI` configured for the LM Studio endpoint.

    The optional `FLUENTVIBE_LM_API_KEY` is passed as a Bearer token for local
    servers that require authentication; unauthenticated servers use a placeholder.

    Lazy-imported so the legacy `LMStudioChatClient` path keeps working when
    `langchain-openai` is not installed yet.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=_endpoint_to_base_url(endpoint),
        api_key=api_key or os.environ.get("FLUENTVIBE_LM_API_KEY") or "lm-studio",
        temperature=temperature,
        streaming=streaming,
        model_kwargs={"parallel_tool_calls": True},
    )
