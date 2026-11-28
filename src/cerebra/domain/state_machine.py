"""Proposal state machine (SRS B.3, FR-4.5, FR-4.7).

States: PROPOSED → CONFIRMED → CREATED | PARTIAL → CREATED
        PROPOSED → EXPIRED | CANCELLED
        CONFIRMED → FAILED
"""

from __future__ import annotations

from cerebra.domain.entities import ProposalState
from cerebra.domain.errors import (
    CerebraError,
    ErrorCode,
)


class StateMachineError(CerebraError):
    """Raised for invalid state transitions."""

    pass


def transition(
    proposal_id: str,
    current: ProposalState,
    target: ProposalState,
) -> ProposalState:
    """Validate and apply a state transition (SRS B.3).

    Raises StateMachineError for invalid transitions.
    Terminal states never transition.
    """
    if current.is_terminal():
        raise StateMachineError(
            code=ErrorCode.PROPOSAL_INVALID_STATE,
            message=(
                f"Proposal {proposal_id!r} is in terminal state {current.value} "
                f"and cannot transition to {target.value}."
            ),
            suggested_action="Run propose_playlist again to create a new proposal.",
        )
    if not current.can_transition_to(target):
        raise StateMachineError(
            code=ErrorCode.PROPOSAL_INVALID_STATE,
            message=(
                f"Proposal {proposal_id!r} cannot transition from "
                f"{current.value} to {target.value}."
            ),
            suggested_action="Check the proposal state and try again.",
        )
    return target
