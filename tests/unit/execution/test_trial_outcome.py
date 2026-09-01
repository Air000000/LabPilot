from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import UUID

import pytest

from labpilot.execution.types import AttemptStatus, TerminalKind

TRIAL_ID = UUID("10000000-0000-4000-8000-000000000001")
ATTEMPT_ID = UUID("20000000-0000-4000-8000-000000000001")


def _attempt(status: str, **values: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "attempt_id": ATTEMPT_ID,
        "status": status,
        "terminal_at": None,
        "terminal_kind": None,
        "exit_code": None,
        "preparation_failure_code": None,
        "execution_issue_code": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _terminal(**values: object) -> SimpleNamespace:
    return _attempt(AttemptStatus.TERMINAL.value, terminal_at=object(), **values)


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [
        ((), True),
        ((_attempt(AttemptStatus.PREPARING.value),), False),
        ((_terminal(terminal_kind=TerminalKind.PROCESS_EXITED.value, exit_code=0),), False),
        (
            (
                _attempt(
                    AttemptStatus.PREPARATION_FAILED.value,
                    terminal_at=object(),
                    preparation_failure_code="PREPARATION_TEMPORARY_IO",
                ),
            ),
            True,
        ),
        (
            (
                _attempt(
                    AttemptStatus.PREPARATION_FAILED.value,
                    terminal_at=object(),
                    preparation_failure_code="PREPARATION_TEMPORARY_IO",
                ),
            )
            * 2,
            False,
        ),
        ((_terminal(terminal_kind=TerminalKind.PROCESS_TERMINAL_UNCLASSIFIED.value),), False),
    ],
)
def test_can_create_next_attempt_preserves_slice2_retry_semantics(
    attempts: tuple[SimpleNamespace, ...], expected: bool
) -> None:
    service = importlib.import_module("labpilot.execution.batch_service")

    assert service.can_create_next_attempt(attempts, max_retries=1) is expected


@pytest.mark.parametrize(
    ("attempts", "cancelled", "state", "successful_attempt_id"),
    [
        ((), False, "unresolved", None),
        ((_attempt(AttemptStatus.PREPARING.value),), True, "unresolved", None),
        ((_attempt(AttemptStatus.LAUNCH_READY.value),), True, "unresolved", None),
        ((_attempt(AttemptStatus.LAUNCH_PENDING.value),), True, "unresolved", None),
        ((_attempt(AttemptStatus.RUNNING.value),), True, "unresolved", None),
        ((_attempt(AttemptStatus.UNKNOWN.value),), True, "unresolved", None),
        ((_attempt(AttemptStatus.IDENTITY_CONFLICT.value),), True, "unresolved", None),
        (
            (_terminal(terminal_kind=TerminalKind.PROCESS_EXITED.value, exit_code=0),),
            True,
            "successful",
            ATTEMPT_ID,
        ),
        ((), True, "cancelled", None),
        (
            (
                _attempt(
                    AttemptStatus.PREPARATION_FAILED.value,
                    terminal_at=object(),
                    preparation_failure_code="PREPARATION_TEMPORARY_IO",
                ),
            ),
            False,
            "unresolved",
            None,
        ),
        (
            (
                _attempt(
                    AttemptStatus.PREPARATION_FAILED.value,
                    terminal_at=object(),
                    preparation_failure_code="PREPARATION_TEMPORARY_IO",
                ),
            )
            * 2,
            False,
            "exhausted_execution_failure",
            None,
        ),
        (
            (_terminal(terminal_kind=TerminalKind.PROCESS_TERMINAL_UNCLASSIFIED.value),),
            False,
            "exhausted_execution_failure",
            None,
        ),
    ],
)
def test_trial_execution_outcome_uses_settlement_precedence(
    attempts: tuple[SimpleNamespace, ...],
    cancelled: bool,
    state: str,
    successful_attempt_id: UUID | None,
) -> None:
    outcome_module = importlib.import_module("labpilot.execution.trial_outcome")

    outcome = outcome_module._derive_trial_execution_outcome(
        trial_id=TRIAL_ID,
        attempts=attempts,
        batch_cancelled=cancelled,
        max_retries=1,
    )

    assert outcome.state.value == state
    assert outcome.successful_attempt_id == successful_attempt_id
    assert outcome.reason_code is None
