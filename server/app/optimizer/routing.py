"""P8 routing/cascade (M13.1) and P11 budget governor + circuit breakers (M13.2)."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

CHEAP = "groq/openai/gpt-oss-20b"
STRONG = "groq/openai/gpt-oss-120b"

HARD_STEP = re.compile(
    r"\b(architect|migrat|security|payment|concurr|race|schema design|refactor)\b", re.IGNORECASE
)
VALIDATION_FAILURE = re.compile(
    r"\b(schema validation failed|lint failed|build failed|tests? failed)\b", re.IGNORECASE
)


def route(messages: list[dict], budget_remaining_usd: float | None = None) -> str:
    """Cheap-first cascade: hard steps and validation-failure retries go strong."""
    text = " ".join(str(m.get("content", "")) for m in messages)
    if VALIDATION_FAILURE.search(text):
        return STRONG  # validator failure escalates (M13.1 exit check)
    if HARD_STEP.search(text):
        return STRONG
    return CHEAP


@dataclass
class BudgetGovernor:
    """P11: per-session token/cost budgets; degrade before refusing (M13.2)."""

    session_budget_usd: float = 0.50
    spent_usd: float = 0.0
    degraded: bool = False

    def observe(self, cost_usd: float | None) -> None:
        if cost_usd is not None:
            self.spent_usd += cost_usd
        if self.spent_usd >= 0.8 * self.session_budget_usd:
            self.degraded = True  # cheaper model + smaller context, not refusal

    def allow(self) -> bool:
        return self.spent_usd < self.session_budget_usd

    def model_override(self, requested: str) -> str:
        if self.degraded and requested == STRONG:
            return CHEAP
        return requested


@dataclass
class PassBreaker:
    """Per-pass circuit breaker: open past the failure threshold in a window."""

    failure_threshold: int = 3
    window_s: float = 60.0
    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self.failures.append(now)
        self._trim(now)
        if len(self.failures) >= self.failure_threshold:
            self.opened_at = now

    def _trim(self, now: float) -> None:
        while self.failures and now - self.failures[0] > self.window_s:
            self.failures.popleft()

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        now = time.monotonic()
        if now - self.opened_at > self.window_s:
            self.opened_at = None  # half-open: try again after a window
            self.failures.clear()
            return True
        return False


class BreakerBoard:
    def __init__(self, threshold: int = 3) -> None:
        self._breakers: dict[str, PassBreaker] = defaultdict(lambda: PassBreaker(threshold))

    def breaker(self, pass_id: str) -> PassBreaker:
        return self._breakers[pass_id]

    def disabled_passes(self) -> list[str]:
        return [p for p, b in self._breakers.items() if not b.allow()]
