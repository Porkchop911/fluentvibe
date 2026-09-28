"""OpenAI-compatible LM Studio chat client for protocol authoring."""

from __future__ import annotations

import contextlib
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


class LMReasoningBudgetError(LMStudioError):
    """The reply reasoned past its budget without text or a tool call."""

    def __init__(self, notes: str, budget: int):
        super().__init__(f"Model reasoned past its budget (~{budget} tokens) without a tool call.")
        self.notes = notes


# Fallback for a turn whose first attempt failed inside its reasoning: the
# user's rule is xhigh, with medium only as the retry after a failed attempt.
FALLBACK_REASONING_EFFORT = "medium"


def _reasoning_budget_from_env() -> int | None:
    """Reasoning tokens a turn may use before it is stopped and retried
    (``FLUENTVIBE_LM_REASONING_BUDGET``; default 30000, 0 = no limit)."""
    raw = os.environ.get("FLUENTVIBE_LM_REASONING_BUDGET", "").strip()
    if not raw:
        return 30000
    value = int(raw)
    return value if value > 0 else None


# A reply cut off inside its reasoning is continued this many times.
MAX_CONTINUATIONS = 2


class LMContinuationUnsupported(LMStudioError):
    """The endpoint cannot continue a reply token-exactly (no /tokenize or
    /v1/completions, or a chat template without an open reasoning block)."""


def _count_streamed(token, text: str) -> None:
    token.streamed_chars = getattr(token, "streamed_chars", 0) + len(text)
    token.streamed_tokens = token.streamed_chars // 4


def _approx_tokens(text: str) -> int:
    """Token estimate for the reasoning budget (~4 characters per token). A
    budget, not a ceiling: the budget is a time bound, not an exact count."""
    return len(text) // 4


def _schema_calls(text: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool calls written as Qwen XML in raw completion text, with parameter
    values typed by the tool's schema (a string parameter stays a string)."""
    schemas = {str((t.get("function") or {}).get("name")): ((t.get("function") or {}).get("parameters") or {})
               for t in tools or ()}
    calls = []
    for idx, body in enumerate(_TOOL_CALL_BLOCK_RE.findall(text)):
        fn = _FUNCTION_RE.search(body)
        if fn is None:
            continue
        name = fn.group(1).strip()
        props = (schemas.get(name) or {}).get("properties") or {}
        args = {}
        for pname, pval in _PARAMETER_RE.findall(fn.group(2)):
            pname = pname.strip()
            kind = (props.get(pname) or {}).get("type")
            is_str = kind == "string" or (isinstance(kind, list) and kind and kind[0] == "string")
            args[pname] = pval.strip("\n") if is_str else _coerce_arg(pval)
        calls.append({"id": f"continued-{idx}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args, default=str)}})
    return calls


def _longest_reasoning(message: dict[str, Any]) -> str:
    return max((v for v in (message.get("reasoning_fields") or {}).values() if isinstance(v, str)),
               key=len, default="")


def _dump_reasoning_only(message: dict[str, Any], label: str) -> None:
    """Keep the raw reply of a reasoning-only turn for diagnosis
    (``FLUENTVIBE_LM_DUMP_DIR``, an existing folder)."""
    folder = os.environ.get("FLUENTVIBE_LM_DUMP_DIR", "").strip()
    if not folder or not os.path.isdir(folder):
        return
    with contextlib.suppress(OSError):
        path = os.path.join(folder, f"reasoning_only-{time.strftime('%Y%m%d-%H%M%S')}-{label}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(message, fh, ensure_ascii=False, indent=1, default=str)


def _reasoning_only(message: dict[str, Any]) -> bool:
    """A reply with reasoning but neither text nor a tool call."""
    if message.get("tool_calls") or (message.get("content") or "").strip():
        return False
    fields = message.get("reasoning_fields") or {}
    return any(str(value).strip() for value in fields.values())


# Qwen3's recommended sampling with thinking on (the model card: temperature
# 1.0, top_p 0.95, top_k 20). Until 2026-09-28 the default was 0.2: low
# temperature with long reasoning is a known cause of loops and replies cut
# off inside the reasoning.
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P = 0.95
DEFAULT_TOP_K = 20


def _number_from_env(name: str, default, kind=float):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return kind(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _temperature_from_env() -> float:
    raw = os.environ.get("FLUENTVIBE_LM_TEMPERATURE")
    if raw is None or not raw.strip():
        return DEFAULT_TEMPERATURE
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

    def __init__(self, *, min_repeats: int | None = None, share: float = 0.5) -> None:
        self._text: list[str] = []
        self._size = 0
        self._checked_at = 0
        self.min_repeats = min_repeats or self.MIN_REPEATS
        self.share = share

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
            if count >= self.min_repeats and count * len(line) >= min(0.4, self.share) * len(tail):
                return line
        # The same phrase over and over without line breaks.
        for size in (20, 40, 80, 160, 320):
            if len(tail) < size * self.min_repeats:
                break
            phrase = tail[-size:]
            if phrase.strip() and tail.count(phrase) >= self.min_repeats and \
                    tail.count(phrase) * size >= self.share * len(tail):
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
        # FLUENTVIBE_LM_TEMPERATURE / _TOP_P / _TOP_K set them globally; the
        # defaults are the model's recommended thinking-mode sampling.
        self.temperature = float(temperature) if temperature is not None else _temperature_from_env()
        self.top_p = float(top_p) if top_p is not None else _number_from_env("FLUENTVIBE_LM_TOP_P", DEFAULT_TOP_P)
        self.top_k = int(top_k) if top_k is not None else _number_from_env("FLUENTVIBE_LM_TOP_K", DEFAULT_TOP_K, int)
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
        from .. import cancel as _cancel

        try:
            return self._complete_turn(messages=messages, tools=tools)
        except Exception as exc:
            # Stop closes the stream; whatever the closed socket raised, the
            # cause is the user stopping the job.
            token = _cancel.current()
            if token is not None and token.cancelled:
                raise _cancel.Cancelled("stopped by the user") from exc
            raise

    def _complete_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """One model turn, bounded: at most two logical attempts -- the
        configured effort, then ONE recovery at FALLBACK_REASONING_EFFORT (the
        user's approved exception to always-xhigh) -- each within the
        reasoning budget and with up to MAX_CONTINUATIONS token-exact
        continuations of a reply cut off inside its reasoning. A failure of the
        recovery is final. ``FLUENTVIBE_LM_LOOP_GUARD=0`` turns recovery off."""
        if not _loop_guard_enabled():
            return self._complete_once(messages=messages, tools=tools)
        budget = _reasoning_budget_from_env() if tools else None
        notes = ""
        try:
            message = self._complete_continued(messages=messages, tools=tools, budget=budget)
            if not (tools and _reasoning_only(message)):
                return message
            _dump_reasoning_only(message, "first")
            notes = _longest_reasoning(message)[-8000:]
            why, reason = "ended inside your reasoning, without a tool call or any text", "reasoning_only"
        except LMReasoningBudgetError as exc:
            notes = exc.notes
            why, reason = "reasoned past its budget without a tool call", "reasoning_budget"
        except LMRepetitionError as exc:
            why, reason = f"got stuck repeating the same line ({exc.line[:120]!r}) and was stopped", "repetition"
        except LMOutputLimitError:
            why, reason = "ran out of output tokens before any tool call", "output_limit"
        if self.trace_recorder is not None:
            self.trace_recorder.record("turn_retry", reason=reason)
        print(f"[lm] reply {why} -- one retry at {FALLBACK_REASONING_EFFORT} effort", flush=True)
        # Hand the model its own reasoning back: re-deriving it from scratch
        # costs another 5-8 minutes.
        handback = ([{"role": "assistant", "content": f"(My notes from the previous attempt:)\n{notes}"}]
                    if notes.strip() else [])
        retry_messages = [*messages, *handback, {"role": "user", "content": (
            f"Your previous reply {why}. "
            + ("Your notes are above: do not re-derive them. " if handback else "Do not re-derive it. ")
            + f"Keep the reasoning short and make one of the offered tool calls now: {_tool_names(tools)}."
        )}]
        try:
            retry = self._complete_continued(messages=retry_messages, tools=tools,
                                             effort=FALLBACK_REASONING_EFFORT, budget=budget)
        except LMReasoningBudgetError as exc:
            raise LMReasoningOnlyError("Model reasoned past its budget on the retry too, without a tool call.") \
                from exc
        if tools and _reasoning_only(retry):
            _dump_reasoning_only(retry, "retry")
            raise LMReasoningOnlyError("Model ended inside reasoning twice without text or a tool call.")
        return retry

    def _complete_continued(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        effort: str | None = None,
        budget: int | None = None,
    ) -> dict[str, Any]:
        """One logical attempt. The model sometimes ends its output in
        mid-sentence of its reasoning (seen at ~15k reasoning tokens, xhigh and
        medium alike); that reply is continued from exactly where it stopped
        (:meth:`_continue_reasoning`) instead of started over."""
        message = self._complete_once(messages=messages, tools=tools, effort=effort, budget=budget)
        reasoning = _longest_reasoning(message)
        for _ in range(MAX_CONTINUATIONS):
            if not (tools and _reasoning_only(message)) or not getattr(self, "_continuation_ok", True):
                break
            _dump_reasoning_only(message, "cut")
            left = None if budget is None else budget - _approx_tokens(reasoning)
            if left is not None and left <= 0:
                raise LMReasoningBudgetError(reasoning[-8000:], budget)
            if self.trace_recorder is not None:
                self.trace_recorder.record("turn_retry", reason="reasoning_cut_continued",
                                           stop_reason=message.get("stop_reason"))
            print("[lm] reply stopped inside its reasoning -- continuing it from there", flush=True)
            try:
                message = self._continue_reasoning(messages=messages, tools=tools, effort=effort,
                                                   reasoning=reasoning, budget=left)
            except LMContinuationUnsupported as exc:
                self._continuation_ok = False
                print(f"[lm] this server cannot continue a reply ({exc}); retrying instead", flush=True)
                break
            except LMReasoningBudgetError as exc:
                raise LMReasoningBudgetError((reasoning + exc.notes)[-8000:], budget or 0) from exc
            reasoning += _longest_reasoning(message)
            message["reasoning_fields"] = {"reasoning_content": reasoning}
        return message

    def _post_json(self, url: str, body: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LMContinuationUnsupported(f"{url.rsplit('/', 1)[-1]}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LMContinuationUnsupported(f"{url.rsplit('/', 1)[-1]}: {exc}") from exc

    def _continue_reasoning(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        effort: str | None,
        reasoning: str,
        budget: int | None,
    ) -> dict[str, Any]:
        """Continue a reply cut off inside its reasoning, token-exact: the
        server renders the conversation with its own chat template (the
        generation prompt ends in an open ``<think>`` block), the reasoning so
        far is appended as raw tokens, and ``/v1/completions`` carries on.
        (A chat request with the reasoning as assistant content renders as an
        empty closed block plus a new one: not the same context.)"""
        root = self.endpoint.split("/v1/", 1)[0]
        request_tools = _strict_workflow_tools(tools) if _truthy(os.environ.get("FLUENTVIBE_LM_STRICT_WORKFLOW")) else tools
        body: dict[str, Any] = {"model": self.model, "messages": messages, "add_generation_prompt": True,
                                "return_token_strs": True}
        if request_tools:
            body["tools"] = request_tools
        chosen = effort or self.reasoning_effort
        if chosen is not None:
            body["chat_template_kwargs"] = {"reasoning_effort": chosen}
        prompt = self._post_json(root + "/tokenize", body)
        tail = "".join((prompt.get("token_strs") or [])[-4:])
        if not prompt.get("tokens") or "<think>" not in tail:
            raise LMContinuationUnsupported("the rendered prompt does not end in an open reasoning block")
        more = self._post_json(root + "/tokenize", {"model": self.model, "prompt": reasoning,
                                                    "add_special_tokens": False})
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt["tokens"] + (more.get("tokens") or []),
                                   "stream": True, "temperature": self.temperature, "skip_special_tokens": False}
        for name in ("top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        # /v1/completions defaults to 16 tokens (chat: the rest of the context).
        room = (int(prompt["max_model_len"]) - len(payload["prompt"])) if prompt.get("max_model_len") else 32768
        if room <= 0:
            raise LMOutputLimitError("the context is full; the reply cannot be continued")
        payload["max_tokens"] = min(self.max_tokens, room) if self.max_tokens is not None else room
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(root + "/v1/completions", data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        from .. import cancel as _cancel

        _cancel.check()
        token = _cancel.current()
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout_s) as response, \
                    (token.closing(response.close) if token is not None else contextlib.nullcontext()):
                text, finish, stop = self._read_text_stream(response, deadline=started + self.request_timeout_s,
                                                            budget=budget)
        except urllib.error.HTTPError as exc:
            raise LMContinuationUnsupported(f"completions: HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise LMStudioError(f"LM Studio request timed out after {self.request_timeout_s:g}s") from exc
        head, closed, rest = text.partition("</think>")
        rest = rest.replace("<|im_end|>", "").replace("<|endoftext|>", "").strip()
        calls = _schema_calls(rest, request_tools) if closed else []
        content = _TOOL_CALL_BLOCK_RE.sub("", rest).strip() if closed else ""
        message = {"role": "assistant", "content": content or None, "tool_calls": calls,
                   "finish_reason": "tool_calls" if calls else finish, "stop_reason": stop,
                   "reasoning_fields": {"reasoning_content": head}}
        if self.trace_recorder is not None:
            self.trace_recorder.record("response_final", model=self.model, endpoint=root + "/v1/completions",
                                       finish_reason=message["finish_reason"], assistant_content=message["content"],
                                       tool_calls=calls, reasoning_fields=message["reasoning_fields"])
        return message

    def _read_text_stream(self, response, *, deadline: float, budget: int | None) -> tuple[str, Any, Any]:
        """A streamed /v1/completions reply: its text, finish and stop reason.
        Reasoning (text before ``</think>``) counts against ``budget``."""
        from .. import cancel as _cancel

        token = _cancel.current()
        show_thinking = token is not None and _cancel.is_own_thread(token)
        watch = _RepetitionWatch() if _loop_guard_enabled() else None
        parts: list[str] = []
        finish = stop = None
        for raw_line in response:
            _cancel.check()
            if time.monotonic() > deadline:
                raise TimeoutError
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            chunk = json.loads(data_text)
            choice = (chunk.get("choices") or [{}])[0]
            finish = choice.get("finish_reason") or finish
            stop = choice.get("stop_reason", stop)
            piece = choice.get("text") or ""
            if not piece:
                continue
            parts.append(piece)
            if token is not None:
                _count_streamed(token, piece)
            joined = "".join(parts)
            if "</think>" not in joined:
                if show_thinking:
                    token.thinking = (token.thinking + piece)[-1200:]
                if watch is not None:
                    watch.feed(piece)
                    looping = watch.looping_line()
                    if looping is not None:
                        raise LMRepetitionError(looping)
                if budget is not None and _approx_tokens(joined) > budget:
                    response.close()
                    raise LMReasoningBudgetError(joined[-8000:], budget)
        return "".join(parts), finish, stop

    def _complete_once(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        effort: str | None = None,
        budget: int | None = None,
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
            payload["reasoning_effort"] = effort or self.reasoning_effort
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
        from .. import cancel as _cancel

        _cancel.check()
        token = _cancel.current()
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as response,                     (token.closing(response.close) if token is not None else contextlib.nullcontext()):
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    return _raise_if_truncated(self._read_stream(response, deadline=deadline, budget=budget))
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

    def _read_stream(self, response, *, deadline: float | None = None,
                     budget: int | None = None) -> dict[str, Any]:
        content_parts: list[str] = []
        reasoning_chars = 0
        stop_reason = None
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        reasoning_parts: dict[str, list[str]] = {}
        watch = _RepetitionWatch() if _loop_guard_enabled() else None
        # Tool-call arguments (a whole spec, a draft) can loop too; JSON repeats
        # structure legitimately, so this watch asks for more before it calls it.
        args_watch = _RepetitionWatch(min_repeats=12, share=0.6) if _loop_guard_enabled() else None

        from .. import cancel as _cancel

        token = _cancel.current()
        # Only the job's own thread shows its thinking; a parallel call (the
        # instruction checklist) would interleave into unreadable text.
        show_thinking = token is not None and _cancel.is_own_thread(token)
        if show_thinking:
            token.thinking = ""
        for raw_line in response:
            _cancel.check()
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
            stop_reason = choice.get("stop_reason", stop_reason)
            delta = choice.get("delta") or {}
            if token is not None:
                # What the page shows: an estimate from the text streamed so far.
                _count_streamed(token, (delta.get("content") or "")
                                + "".join(v for v in _reasoning_fields(delta).values() if isinstance(v, str))
                                + "".join((c.get("function") or {}).get("arguments") or ""
                                          for c in delta.get("tool_calls") or []))
            if delta.get("content"):
                content_parts.append(delta["content"])
                if watch is not None:
                    watch.feed(delta["content"])
            for key, value in _reasoning_fields(delta).items():
                if isinstance(value, str):
                    reasoning_parts.setdefault(key, []).append(value)
                    if watch is not None:
                        watch.feed(value)
                    if show_thinking:
                        # The page shows what the model is thinking, as a chat does.
                        token.thinking = (token.thinking + value)[-1200:]
                    reasoning_chars += len(value)
                    # ~4 characters per token; only while nothing is answered yet.
                    if budget is not None and reasoning_chars // 4 > budget and not tool_calls \
                            and not "".join(content_parts).strip():
                        response.close()
                        notes = max(("".join(v) for v in reasoning_parts.values()), key=len, default="")
                        raise LMReasoningBudgetError(notes[-8000:], budget)
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
                    if args_watch is not None:
                        args_watch.feed(function["arguments"])
                        looping = args_watch.looping_line()
                        if looping is not None:
                            if self.trace_recorder is not None:
                                self.trace_recorder.record("request_error", error_type="repetition",
                                                           line=looping, where="tool_call_arguments")
                            raise LMRepetitionError(looping)

        message = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
            "finish_reason": finish_reason,
            # vLLM: the token id / string that stopped generation (None = EOS).
            "stop_reason": stop_reason,
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
    temperature: float | None = None,
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
        temperature=temperature if temperature is not None else _temperature_from_env(),
        streaming=streaming,
        model_kwargs={"parallel_tool_calls": True},
    )
