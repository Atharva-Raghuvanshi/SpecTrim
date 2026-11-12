"""M9.2: bearer auth, spend cap, rate limit."""
from __future__ import annotations

from app.security import SlidingWindowRateLimiter, enforce_daily_spend


class _FakeSessions:
    def __init__(self, spent: float) -> None:
        self._spent = spent

    async def daily_spend(self) -> float:
        return self._spent


def test_rate_limiter_blocks_after_limit() -> None:
    limiter = SlidingWindowRateLimiter(per_minute=3)
    for _ in range(3):
        limiter.check("1.2.3.4")
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.status_code == 429
    limiter.check("5.6.7.8")  # other keys unaffected


def test_daily_spend_cap_blocks() -> None:
    import asyncio

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop().run_until_complete(
            enforce_daily_spend(_FakeSessions(10.01), cap_usd=10.0)
        )
    assert exc.value.status_code == 402
    asyncio.get_event_loop().run_until_complete(
        enforce_daily_spend(_FakeSessions(9.99), cap_usd=10.0)
    )  # under cap: no raise


def test_bearer_required_when_configured(app_client, monkeypatch) -> None:
    client, _ = app_client
    monkeypatch.setattr(client.app.state.settings, "api_bearer_token", "secret-token")
    import uuid

    r = client.post(
        "/sessions", json={"idea": "x"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    assert r.status_code == 401
    r2 = client.post(
        "/sessions",
        json={"idea": "x"},
        headers={"Idempotency-Key": f"k-{uuid.uuid4()}", "Authorization": "Bearer secret-token"},
    )
    assert r2.status_code == 201
