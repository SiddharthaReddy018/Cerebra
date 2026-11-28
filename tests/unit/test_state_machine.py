"""Unit tests for domain/state_machine.py (B.3, FR-4.5, FR-4.7)."""

import pytest

from cerebra.domain.entities import ProposalState
from cerebra.domain.state_machine import StateMachineError, transition


# mark: FR-4.7, FR-4.13
class TestProposalStateMachine:
    # Valid transitions per SRS B.3
    @pytest.mark.parametrize(
        "current,target",
        [
            (ProposalState.PROPOSED, ProposalState.CONFIRMED),
            (ProposalState.PROPOSED, ProposalState.EXPIRED),
            (ProposalState.PROPOSED, ProposalState.CANCELLED),
            (ProposalState.CONFIRMED, ProposalState.CREATED),
            (ProposalState.CONFIRMED, ProposalState.PARTIAL),
            (ProposalState.CONFIRMED, ProposalState.FAILED),
            (ProposalState.PARTIAL, ProposalState.CREATED),
        ],
    )
    def test_valid_transition(self, current, target):
        result = transition("test-id", current, target)
        assert result == target

    # Invalid transitions
    @pytest.mark.parametrize(
        "current,target",
        [
            (ProposalState.PROPOSED, ProposalState.CREATED),  # skip CONFIRMED
            (ProposalState.PROPOSED, ProposalState.PARTIAL),  # skip CONFIRMED
            (ProposalState.PROPOSED, ProposalState.FAILED),  # skip CONFIRMED
            (ProposalState.CONFIRMED, ProposalState.PROPOSED),  # backwards
            (ProposalState.CONFIRMED, ProposalState.EXPIRED),  # invalid
            (ProposalState.CONFIRMED, ProposalState.CANCELLED),
            (ProposalState.PARTIAL, ProposalState.FAILED),  # SRS B.3: PARTIAL -> CREATED only
        ],
    )
    def test_invalid_transition_raises(self, current, target):
        with pytest.raises(StateMachineError):
            transition("test-id", current, target)

    # Terminal states never transition
    @pytest.mark.parametrize(
        "terminal",
        [
            ProposalState.CREATED,
            ProposalState.EXPIRED,
            ProposalState.CANCELLED,
            ProposalState.FAILED,
        ],
    )
    def test_terminal_states_never_transition(self, terminal):
        for target in ProposalState:
            with pytest.raises(StateMachineError):
                transition("test-id", terminal, target)

    def test_is_terminal_correct(self):
        assert ProposalState.CREATED.is_terminal()
        assert ProposalState.EXPIRED.is_terminal()
        assert ProposalState.CANCELLED.is_terminal()
        assert ProposalState.FAILED.is_terminal()
        assert not ProposalState.PROPOSED.is_terminal()
        assert not ProposalState.CONFIRMED.is_terminal()
        assert not ProposalState.PARTIAL.is_terminal()
