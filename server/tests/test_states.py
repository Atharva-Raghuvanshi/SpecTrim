import pytest
from app.pipeline.states import InvalidTransition, State, transition


def test_happy_path_transitions() -> None:
    assert transition(State.queued, State.analyzing) is State.analyzing
    assert transition(State.analyzing, State.drafting) is State.drafting
    assert transition(State.drafting, State.validating) is State.validating
    assert transition(State.validating, State.ready) is State.ready
    assert transition(State.ready, State.revising) is State.revising
    assert transition(State.revising, State.validating) is State.validating


def test_question_round_trip() -> None:
    assert transition(State.analyzing, State.awaiting_user) is State.awaiting_user
    assert transition(State.awaiting_user, State.analyzing) is State.analyzing


def test_forbidden_transitions_raise() -> None:
    with pytest.raises(InvalidTransition):
        transition(State.queued, State.ready)
    with pytest.raises(InvalidTransition):
        transition(State.validating, State.queued)
    with pytest.raises(InvalidTransition):
        transition(State.ready, State.drafting)
    with pytest.raises(InvalidTransition):
        transition(State.awaiting_user, State.ready)


def test_terminal_states_frozen() -> None:
    for terminal in (State.failed, State.cancelled):
        for target in State:
            with pytest.raises(InvalidTransition):
                transition(terminal, target)
