"""OpenAI-compatible LM Studio chat client for protocol authoring."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_LM_STUDIO_ENDPOINT = "http://192.168.0.126:1234/v1/chat/completions"
DEFAULT_LM_STUDIO_MODEL = "qwen3.6-27b"


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
    ) -> None:
        self.endpoint = endpoint
        self.model = model

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
            "stream": True,
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=None) as response:
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    return self._read_stream(response)
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LMStudioError(f"LM Studio request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LMStudioError(f"LM Studio returned invalid JSON: {exc}") from exc
        return self._message_from_response(data)

    def _read_stream(self, response) -> dict[str, Any]:
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            try:
                chunk = json.loads(data_text)
            except json.JSONDecodeError as exc:
                raise LMStudioError(f"LM Studio stream returned invalid JSON: {exc}") from exc
            choice = (chunk.get("choices") or [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
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

        return {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
            "finish_reason": finish_reason,
        }

    def _message_from_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            raise LMStudioError("LM Studio response did not include choices.")
        message = dict(choices[0].get("message") or {})
        message["finish_reason"] = choices[0].get("finish_reason")
        return message


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
    )
