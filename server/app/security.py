"""Bearer auth, rate limiting, and daily spend cap (M9.2 / FR-26)."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from app.settings import Settings


def require_bearer(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    settings: Settings = request.app.state.settings
    token = settings.api_bearer_token
    if not token or token == "change-me":
        return  # local dev: auth disabled
    if authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or invalid bearer token")


class SlidingWindowRateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            raise HTTPException(429, "rate limit exceeded, retry in a minute")
        window.append(now)


async def enforce_daily_spend(sessions_repo, cap_usd: float) -> None:
    spent = await sessions_repo.daily_spend()
    if spent >= cap_usd:
        raise HTTPException(
            402, f"daily spend cap reached (${spent:.2f} >= ${cap_usd:.2f}); new sessions rejected"
        )
