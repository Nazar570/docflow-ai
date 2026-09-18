from enum import StrEnum


class WorkflowState(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.RECEIVED: {WorkflowState.QUEUED, WorkflowState.FAILED},
    WorkflowState.QUEUED: {WorkflowState.PROCESSING, WorkflowState.FAILED},
    WorkflowState.PROCESSING: {
        WorkflowState.EXTRACTED,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.FAILED,
    },
    WorkflowState.EXTRACTED: {
        WorkflowState.APPROVED,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.FAILED,
    },
    WorkflowState.APPROVED: {WorkflowState.SUBMITTED, WorkflowState.FAILED},
    WorkflowState.NEEDS_REVIEW: {
        WorkflowState.APPROVED,
        WorkflowState.REJECTED,
    },
    WorkflowState.SUBMITTED: set(),
    WorkflowState.REJECTED: set(),
    WorkflowState.FAILED: set(),
}

TERMINAL_STATES = {
    WorkflowState.SUBMITTED,
    WorkflowState.REJECTED,
    WorkflowState.FAILED,
}

WORKER_NOOP_STATES = TERMINAL_STATES | {WorkflowState.NEEDS_REVIEW}


class IllegalStateTransitionError(Exception):
    def __init__(
        self,
        current_state: WorkflowState,
        target_state: WorkflowState,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Illegal transition from {current_state.value} to {target_state.value}",
        )


def assert_transition_allowed(
    current_state: WorkflowState,
    target_state: WorkflowState,
) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        raise IllegalStateTransitionError(current_state, target_state)
