"""FakeGateway: canned, deterministic, zero network. The only gateway CI may use."""
from __future__ import annotations

from .base import CompletionResult, Usage


class FakeGateway:
    model_name = "fake-model"

    def __init__(self, canned: dict[str, str] | None = None, default: str = "{}") -> None:
        self._canned = dict(canned or {})
        self._default = default
        self.calls: list[dict] = []

    def set_response(self, stage: str, text: str) -> None:
        self._canned[stage] = text

    def complete(self, stage: str, messages: list[dict[str, str]]) -> CompletionResult:
        self.calls.append({"stage": stage, "messages": messages})
        text = self._canned.get(stage, self._default)
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        prompt_tokens = max(1, prompt_chars // 4)
        completion_tokens = max(1, len(text) // 4)
        return CompletionResult(
            stage=stage,
            model=self.model_name,
            text=text,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            latency_ms=0,
            cost_usd=0.0,
        )
