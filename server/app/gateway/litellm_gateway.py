"""LiteLLM wrapper: the only place a provider SDK may be imported (FR-23).

- 429s: exponential backoff with jitter, then fallback model, then GatewayRateLimited.
- Permanent errors: no retry; straight to fallback if configured, else GatewayError.
- Records provider-reported usage, latency, and price-table cost per call (FR-25).
"""
from __future__ import annotations

import random
import time
from typing import Callable

import litellm

from .base import CompletionResult, GatewayError, GatewayRateLimited, Usage
from .config import GatewayConfig, StageModelCfg

SleepFn = Callable[[float], None]

MAX_RETRIES = 4  # extra attempts after the first (5 calls total)
BASE_DELAY_S = 0.5


def _is_rate_limited(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    rate_limit_exc = getattr(litellm.exceptions, "RateLimitError", None)
    return rate_limit_exc is not None and isinstance(exc, rate_limit_exc)


def _text_of(resp: object) -> str:
    try:
        choices = getattr(resp, "choices")  # noqa: B009 - duck-typed response
        return choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _usage_of(resp: object) -> Usage:
    usage = getattr(resp, "usage", None)
    get = lambda name: max(0, int(getattr(usage, name, 0) or 0))  # noqa: E731
    return Usage(
        prompt_tokens=get("prompt_tokens"),
        completion_tokens=get("completion_tokens"),
        total_tokens=get("total_tokens"),
    )


class LiteLLMGateway:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        sleep_fn: SleepFn = time.sleep,
        max_retries: int = MAX_RETRIES,
        base_delay: float = BASE_DELAY_S,
    ) -> None:
        self._config = config
        self._sleep = sleep_fn
        self._max_retries = max_retries
        self._base_delay = base_delay

    def complete(self, stage: str, messages: list[dict[str, str]]) -> CompletionResult:
        cfg = self._config.for_stage(stage)
        try:
            return self._call(stage, messages, cfg)
        except GatewayError:
            if not cfg.fallback_model:
                raise
            return self._call(stage, messages, cfg, model_override=cfg.fallback_model)

    def _call(
        self,
        stage: str,
        messages: list[dict[str, str]],
        cfg: StageModelCfg,
        model_override: str | None = None,
    ) -> CompletionResult:
        model = model_override or cfg.model
        last_rate_limit: Exception | None = None
        for attempt in range(self._max_retries + 1):
            start = time.perf_counter()
            try:
                resp = litellm.completion(
                    model=model,
                    messages=messages,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                    timeout=120,  # a hung provider call must not stall the run
                )
            except Exception as exc:  # noqa: BLE001 - gateway wraps all provider errors
                if _is_rate_limited(exc):
                    last_rate_limit = exc
                    if attempt == self._max_retries:
                        break
                    delay = self._base_delay * (2**attempt) + random.uniform(0, self._base_delay)
                    self._sleep(delay)
                    continue
                raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
            latency_ms = int((time.perf_counter() - start) * 1000)
            try:
                cost: float | None = float(litellm.completion_cost(completion_response=resp))
            except Exception:  # noqa: BLE001 - unknown pricing must not fail the call
                cost = None
            return CompletionResult(
                stage=stage,
                model=str(getattr(resp, "model", model)),
                text=_text_of(resp),
                usage=_usage_of(resp),
                latency_ms=latency_ms,
                cost_usd=cost,
            )
        raise GatewayRateLimited(
            f"rate limited after {self._max_retries + 1} attempts on {model}"
        ) from last_rate_limit
