# tests/models/test_states.py

import pytest
from quantforge.models.states import PaperState, is_valid_transition, is_terminal


def test_valid_forward_transitions():
    assert is_valid_transition(PaperState.DISCOVERED, PaperState.NORMALIZED)
    assert is_valid_transition(PaperState.NORMALIZED, PaperState.EVALUATED)
    assert is_valid_transition(PaperState.EVALUATED, PaperState.CANDIDATE)
    assert is_valid_transition(PaperState.CANDIDATE, PaperState.APPROVED)
    assert is_valid_transition(PaperState.APPROVED, PaperState.ACQUIRED)
    assert is_valid_transition(PaperState.ACQUIRED, PaperState.PROCESSED)


def test_any_state_can_transition_to_rejected():
    rejectable = [
        PaperState.DISCOVERED,
        PaperState.NORMALIZED,
        PaperState.EVALUATED,
        PaperState.CANDIDATE,
        PaperState.APPROVED,
        PaperState.ACQUIRED,
    ]
    for state in rejectable:
        assert is_valid_transition(state, PaperState.REJECTED)


def test_invalid_transitions():
    # Cannot skip states
    assert not is_valid_transition(PaperState.DISCOVERED, PaperState.ACQUIRED)
    assert not is_valid_transition(PaperState.DISCOVERED, PaperState.PROCESSED)
    assert not is_valid_transition(PaperState.NORMALIZED, PaperState.APPROVED)

    # Cannot go backwards
    assert not is_valid_transition(PaperState.EVALUATED, PaperState.DISCOVERED)
    assert not is_valid_transition(PaperState.ACQUIRED, PaperState.CANDIDATE)


def test_terminal_states():
    assert is_terminal(PaperState.PROCESSED)
    assert is_terminal(PaperState.REJECTED)


def test_non_terminal_states():
    assert not is_terminal(PaperState.DISCOVERED)
    assert not is_terminal(PaperState.CANDIDATE)
    assert not is_terminal(PaperState.APPROVED)


def test_rejected_cannot_transition_further():
    for state in PaperState:
        if state != PaperState.REJECTED:
            assert not is_valid_transition(PaperState.REJECTED, state)


def test_processed_cannot_transition_further():
    for state in PaperState:
        if state != PaperState.PROCESSED:
            assert not is_valid_transition(PaperState.PROCESSED, state)