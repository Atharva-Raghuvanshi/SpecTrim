"""Session state machine (PRD 5.2). All changes go through transition()."""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    queued = "queued"
    analyzing = "analyzing"
    awaiting_user = "awaiting_user"
    drafting = "drafting"
    validating = "validating"
    ready = "ready"
    revising = "revising"
    failed = "failed"
    cancelled = "cancelled"


class InvalidTransition(RuntimeError):
    pass


TERMINAL = {State.failed, State.cancelled}

TRANSITIONS: dict[State, set[State]] = {
    State.queued: {State.analyzing, State.cancelled},
    State.analyzing: {State.awaiting_user, State.drafting, State.failed, State.cancelled},
    State.awaiting_user: {State.analyzing, State.cancelled},
    State.drafting: {State.validating, State.failed, State.cancelled},
    State.validating: {State.drafting, State.ready, State.failed},
    State.ready: {State.revising},
    State.revising: {State.validating, State.failed, State.cancelled},
    State.failed: set(),
    State.cancelled: set(),
}


def transition(current: State, target: State) -> State:
    if current in TERMINAL:
        raise InvalidTransition(f"terminal state {current.value} cannot transition to {target.value}")
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(f"transition {current.value} -> {target.value} is not allowed")
    return target
