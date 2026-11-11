from types import SimpleNamespace

import litellm
import pytest
from app.gateway.base import GatewayError, GatewayRateLimited
from app.gateway.config import GatewayConfig, StageModelCfg
from app.gateway.litellm_gateway import LiteLLMGateway

PRIMARY = "groq/llama-3.3-70b-versatile"
FALLBACK = "groq/llama-3.1-8b-instant"


class RateLimitError(Exception):
    status_code = 429


def make_config(fallback: str | None = None) -> GatewayConfig:
    return GatewayConfig(
        default=StageModelCfg(
            model=PRIMARY, temperature=0.1, max_tokens=32, fallback_model=fallback
        ),
        stages={},
    )


def fake_resp(model: str = "groq/actual") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model=model,
    )


class Stub:
    """Replaces litellm.completion; fails `failures` times, then succeeds."""

    def __init__(self, failures: int = 0, error_factory=None, model: str = "groq/actual") -> None:
        self.failures = failures
        self.error_factory = error_factory or (lambda i: RuntimeError(f"boom {i}"))
        self.model = model
        self.calls: list[dict] = []

    def __call__(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise self.error_factory(len(self.calls))
        return fake_resp(self.model)


def install(
    monkeypatch: pytest.MonkeyPatch, stub: Stub, cost: float | None = 1.25e-05
) -> list[float]:
    monkeypatch.setattr(litellm, "completion", stub)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: cost)
    sleeps: list[float] = []
    return sleeps


def test_success_captures_usage_latency_cost_and_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Stub()
    sleeps = install(monkeypatch, stub)
    gw = LiteLLMGateway(make_config(), sleep_fn=sleeps.append, max_retries=2)
    res = gw.complete("classifier", [{"role": "user", "content": "ping"}])

    assert res.text == "pong"
    assert res.model == "groq/actual"
    assert res.usage.prompt_tokens == 10
    assert res.usage.total_tokens == 15
    assert res.cost_usd == pytest.approx(1.25e-05)
    assert res.latency_ms >= 0
    assert stub.calls[0]["model"] == PRIMARY
    assert stub.calls[0]["temperature"] == 0.1
    assert stub.calls[0]["max_tokens"] == 32
    assert sleeps == []


def test_429_retries_with_backoff_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Stub(failures=2, error_factory=lambda i: RateLimitError(f"429 #{i}"))
    sleeps = install(monkeypatch, stub)
    gw = LiteLLMGateway(make_config(), sleep_fn=sleeps.append, max_retries=2, base_delay=0.5)
    res = gw.complete("classifier", [{"role": "user", "content": "ping"}])

    assert res.text == "pong"
    assert len(stub.calls) == 3  # 2 failures + success
    # exponential backoff with jitter, bounded: delay_i in [base*2^i, base*2^i + base]
    assert 0.5 <= sleeps[0] <= 1.0
    assert 1.0 <= sleeps[1] <= 1.5


def test_429_exhausted_raises_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Stub(failures=99, error_factory=lambda i: RateLimitError(f"429 #{i}"))
    sleeps = install(monkeypatch, stub)
    gw = LiteLLMGateway(make_config(), sleep_fn=sleeps.append, max_retries=2, base_delay=0.5)

    with pytest.raises(GatewayRateLimited):
        gw.complete("classifier", [{"role": "user", "content": "ping"}])
    assert len(stub.calls) == 3  # max_retries + 1 attempts
    assert len(sleeps) == 2


def test_429_exhausted_then_fallback_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def dispatcher(**kwargs: object) -> SimpleNamespace:
        model = str(kwargs["model"])
        calls.append(model)
        if model == PRIMARY:
            raise RateLimitError("429")
        return fake_resp(model)

    monkeypatch.setattr(litellm, "completion", dispatcher)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: 0.0)
    gw = LiteLLMGateway(
        make_config(fallback=FALLBACK), sleep_fn=lambda s: None, max_retries=1, base_delay=0.5
    )

    res = gw.complete("classifier", [{"role": "user", "content": "ping"}])
    assert res.model == FALLBACK
    assert calls == [PRIMARY, PRIMARY, FALLBACK]  # 2 attempts (max_retries=1), then fallback


def test_permanent_error_falls_back_without_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def dispatcher(**kwargs: object) -> SimpleNamespace:
        model = str(kwargs["model"])
        calls.append(model)
        if model == PRIMARY:
            raise RuntimeError("bad request")
        return fake_resp(model)

    monkeypatch.setattr(litellm, "completion", dispatcher)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: 0.0)
    gw = LiteLLMGateway(make_config(fallback=FALLBACK), sleep_fn=lambda s: None, max_retries=2)

    res = gw.complete("classifier", [{"role": "user", "content": "ping"}])
    assert res.model == FALLBACK
    assert calls == [PRIMARY, FALLBACK]  # permanent error: no retry on the primary


def test_permanent_error_without_fallback_raises_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = Stub(failures=1, error_factory=lambda i: RuntimeError("bad request"))
    install(monkeypatch, stub)
    gw = LiteLLMGateway(make_config(), sleep_fn=lambda s: None, max_retries=2)

    with pytest.raises(GatewayError, match="bad request"):
        gw.complete("classifier", [{"role": "user", "content": "ping"}])
    assert len(stub.calls) == 1
