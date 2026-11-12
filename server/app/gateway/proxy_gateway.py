"""Raw completion boundary for the proxy (litellm may only be imported here)."""
from __future__ import annotations

import json
from typing import Any, Iterator

import litellm


def raw_completion(payload: dict[str, Any]) -> dict[str, Any]:
    """One non-streaming call; returns an OpenAI-shaped dict with provider usage."""
    resp = litellm.completion(**payload)
    out = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    return out


def raw_usage(resp: dict[str, Any]) -> tuple[int, float | None]:
    usage = resp.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    cost = resp.get("cost")
    return tokens, (float(cost) if cost is not None else None)


def raw_stream(payload: dict[str, Any]) -> Iterator[str]:
    """SSE passthrough of a streaming call."""
    stream = litellm.completion(**payload, stream=True)
    for chunk in stream:
        data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
        yield f"data: {json.dumps(data)}\n\n"
    yield "data: [DONE]\n\n"


def baseline_cost_est(resp: dict[str, Any], tokens_baseline_est: int) -> float:
    """Counterfactual baseline cost estimate from the actual cost per token."""
    tokens, cost = raw_usage(resp)
    if cost is not None and tokens > 0:
        return round(cost / tokens * tokens_baseline_est, 6)
    return 0.0
