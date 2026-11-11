"""Gateway contracts shared by all implementations (FR-23/25)."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class GatewayError(RuntimeError):
    """Permanent gateway failure (after retries/fallbacks are exhausted)."""


class GatewayRateLimited(GatewayError):
    """Rate limited (429) even after backoff retries."""


class Usage(BaseModel):
    """Provider-reported token usage. Never estimated here."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CompletionResult(BaseModel):
    stage: str
    model: str
    text: str
    usage: Usage
    latency_ms: int
    cost_usd: float | None = None  # provider price-table estimate, None when unknown


class Gateway(Protocol):
    def complete(self, stage: str, messages: list[dict[str, str]]) -> CompletionResult: ...
