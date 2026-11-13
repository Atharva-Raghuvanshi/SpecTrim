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
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, list):
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            for b in content:
                if isinstance(b, dict):
                    b_type = b.get("type")
                    if b_type in (None, "text"):
                        text_parts.append(str(b.get("text", "")))
                    elif b_type == "tool_use":
                        tool_calls.append({
                            "id": str(b.get("id", "")),
                            "type": "function",
                            "function": {
                                "name": str(b.get("name", "")),
                                "arguments": json.dumps(b.get("input", {}))
                                if not isinstance(b.get("input"), str)
                                else b.get("input"),
                            },
                        })
                    elif b_type == "tool_result":
                        res_content = b.get("content", "")
                        if not isinstance(res_content, str):
                            res_content = json.dumps(res_content)
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": str(b.get("tool_use_id", "")),
                            "content": res_content,
                        })
            if role == "assistant":
                msg_dict: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                messages.append(msg_dict)
            else:
                for tr in tool_results:
                    messages.append(tr)
                if text_parts or not tool_results:
                    messages.append({"role": role, "content": "".join(text_parts)})
        else:
            messages.append({"role": role, "content": str(content or "")})
    out: dict[str, Any] = {"messages": messages, "model": body.get("model")}
    if body.get("tools"):
        tools = []
        for t in body["tools"]:
            if "input_schema" in t:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                })
            elif t.get("type") == "function":
                tools.append(t)
        out["tools"] = tools
    if body.get("tool_choice"):
        tc = body["tool_choice"]
        if isinstance(tc, dict):
            if tc.get("type") == "auto":
                out["tool_choice"] = "auto"
            elif tc.get("type") == "any":
                out["tool_choice"] = "required"
            elif tc.get("type") == "tool" and tc.get("name"):
                out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
            else:
                out["tool_choice"] = tc
        else:
            out["tool_choice"] = tc
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
    msg = choices[0].get("message", {}) if choices else {}
    text = msg.get("content") or ""
    if not isinstance(text, str):
        text = _content_text(text)

    content_blocks: list[dict[str, Any]] = []
    if text:
        content_blocks.append({"type": "text", "text": text})

    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments", "{}")
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            parsed_args = {"raw": raw_args}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", "call_default"),
            "name": fn.get("name", ""),
            "input": parsed_args if isinstance(parsed_args, dict) else {},
        })

    stop_reason = "end_turn"
    if tool_calls:
        stop_reason = "tool_use"
    elif choices and choices[0].get("finish_reason") == "length":
        stop_reason = "max_tokens"

    usage = resp.get("usage") or {}
    out = {
        "id": resp.get("id", "msg_proxy"),
        "type": "message",
        "role": "assistant",
        "model": resp.get("model", ""),
        "content": content_blocks if content_blocks else [{"type": "text", "text": ""}],
        "stop_reason": stop_reason,
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
