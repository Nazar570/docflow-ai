from uuid import uuid4

import pytest

from services.worker.executor.erp_client import ErpClientError
from services.worker.providers.errors import (
    ProviderErrorClass,
    RecoverableProviderError,
)
from services.worker.rules.states import (
    IllegalStateTransitionError,
    WorkflowState,
    assert_transition_allowed,
)
from services.worker.transient import (
    TransientWorkerError,
    classify_transient_error,
    is_transient_erp_error,
)


def test_async_state_machine_allows_queued_and_processing_path() -> None:
    assert_transition_allowed(WorkflowState.RECEIVED, WorkflowState.QUEUED)
    assert_transition_allowed(WorkflowState.QUEUED, WorkflowState.PROCESSING)
    assert_transition_allowed(WorkflowState.PROCESSING, WorkflowState.NEEDS_REVIEW)
    assert_transition_allowed(WorkflowState.APPROVED, WorkflowState.FAILED)


def test_async_state_machine_rejects_concurrent_terminal_replay() -> None:
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.FAILED, WorkflowState.PROCESSING)
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.SUBMITTED, WorkflowState.QUEUED)
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.REJECTED, WorkflowState.APPROVED)


def test_review_transitions_are_legal() -> None:
    assert_transition_allowed(WorkflowState.NEEDS_REVIEW, WorkflowState.APPROVED)
    assert_transition_allowed(WorkflowState.NEEDS_REVIEW, WorkflowState.REJECTED)
    assert_transition_allowed(WorkflowState.APPROVED, WorkflowState.SUBMITTED)


def test_classify_recoverable_provider_as_transient() -> None:
    exc = RecoverableProviderError(
        "timeout",
        error_class=ProviderErrorClass.TIMEOUT,
        provider="fake",
    )
    classified = classify_transient_error(exc)
    assert classified is not None
    assert classified.error_class == ProviderErrorClass.TIMEOUT.value


def test_classify_transient_erp_status_codes() -> None:
    assert is_transient_erp_error(ErpClientError("x", status_code=503)) is True
    assert is_transient_erp_error(ErpClientError("x", status_code=400)) is False
    assert is_transient_erp_error(ErpClientError("x", status_code=None)) is True


def test_transient_worker_error_preserves_safe_metadata() -> None:
    error = TransientWorkerError("provider timeout", error_class="timeout")
    assert error.error_class == "timeout"
    assert "timeout" in error.message
    assert str(uuid4()) not in error.message
