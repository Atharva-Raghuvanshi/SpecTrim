"""Anthropic-compatible translations (FR-30): /v1/messages request/response
shapes mapped onto the internal OpenAI-style payload the chain operates on."""
from __future__ import annotations

import json
from typing import Any, Iterator


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in (None, "text")
        )
    return ""


def from_anthropic(body: dict) -> dict:
    """Anthropic request -> internal OpenAI-style request (chain input shape)."""
    messages: list[dict] = []
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _content_text(system)})
    for m in body.get("messages", []):
        messages.append({"role": m.get("role", "user"), "content": _content_text(m.get("content"))})
    out: dict[str, Any] = {"messages": messages, "model": body.get("model")}
    if body.get("metadata"):
        out["metadata"] = body["metadata"]
    if body.get("max_tokens") is not None:
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    if body.get("stream"):
        out["stream"] = True
    return out


def to_anthropic(resp: dict, cache_hit: bool = False) -> dict:
    """Internal OpenAI-style response -> Anthropic message response."""
    choices = resp.get("choices") or []
    text = choices[0]["message"]["content"] if choices else ""
    if not isinstance(text, str):
        text = _content_text(text)
    usage = resp.get("usage") or {}
    out = {
        "id": resp.get("id", "msg_proxy"),
        "type": "message",
        "role": "assistant",
        "model": resp.get("model", ""),
        "content": [{"type": "text", "text": text or ""}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }
    if cache_hit:
        out["cache_hit"] = True
    return out


def translate_stream_to_anthropic(openai_sse: Iterator[str]) -> Iterator[str]:
    """OpenAI-style SSE chunk stream -> Anthropic SSE event stream."""
    yield _sse(
        "message_start",
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
    )
    yield _sse(
        "content_block_start",
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
    )
    for raw in openai_sse:
        for line in raw.splitlines():
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            text = delta.get("content") or ""
            if text:
                yield _sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": text}},
                )
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
    yield _sse("message_stop", {"type": "message_stop"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
