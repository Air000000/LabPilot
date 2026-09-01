from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from labpilot.db.uow import SessionContextFactory
from labpilot.execution.batch_service import (
    BatchNotFound,
    TrialNotFound,
    can_create_next_attempt,
)
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.ports import VersionExecutionSemanticsPort
from labpilot.execution.types import TerminalKind


class TrialExecutionState(StrEnum):
    UNRESOLVED = "unresolved"
    SUCCESSFUL = "successful"
    EXHAUSTED_EXECUTION_FAILURE = "exhausted_execution_failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TrialExecutionOutcome:
    trial_id: UUID
    state: TrialExecutionState
    successful_attempt_id: UUID | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class BatchExecutionOutcomeSnapshot:
    batch_id: UUID
    batch_row_version: int
    attempt_ids: tuple[UUID, ...]
    outcomes: tuple[TrialExecutionOutcome, ...]


def _derive_trial_execution_outcome(
    *,
    trial_id: UUID,
    attempts: Sequence[AttemptRow],
    batch_cancelled: bool,
    max_retries: int,
) -> TrialExecutionOutcome:
    if any(attempt.terminal_at is None for attempt in attempts):
        return TrialExecutionOutcome(trial_id, TrialExecutionState.UNRESOLVED)

    successful_attempt = next(
        (
            attempt
            for attempt in attempts
            if attempt.terminal_kind == TerminalKind.PROCESS_EXITED.value
            and attempt.exit_code == 0
        ),
        None,
    )
    if successful_attempt is not None:
        return TrialExecutionOutcome(
            trial_id,
            TrialExecutionState.SUCCESSFUL,
            successful_attempt_id=successful_attempt.attempt_id,
        )
    if batch_cancelled:
        return TrialExecutionOutcome(trial_id, TrialExecutionState.CANCELLED)
    if can_create_next_attempt(attempts, max_retries=max_retries):
        return TrialExecutionOutcome(trial_id, TrialExecutionState.UNRESOLVED)
    return TrialExecutionOutcome(
        trial_id,
        TrialExecutionState.EXHAUSTED_EXECUTION_FAILURE,
    )


class TrialExecutionOutcomeService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        version_semantics: VersionExecutionSemanticsPort,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._version_semantics = version_semantics

    async def get(self, trial_id: UUID) -> TrialExecutionOutcome:
        async with self._session_context_factory() as session:
            trial = await session.get(TrialRow, trial_id)
            if trial is None:
                raise TrialNotFound(trial_id)
            batch = await session.get(BatchRow, trial.batch_id)
            if batch is None:
                raise BatchNotFound(trial.batch_id)
            attempts = tuple(
                (
                    await session.scalars(
                        select(AttemptRow)
                        .where(AttemptRow.trial_id == trial_id)
                        .order_by(AttemptRow.attempt_number)
                    )
                ).all()
            )
        semantics = await self._version_semantics.load(batch.experiment_version_id)
        return _derive_trial_execution_outcome(
            trial_id=trial_id,
            attempts=attempts,
            batch_cancelled=batch.cancel_requested_at is not None,
            max_retries=semantics.max_retries,
        )

    async def snapshot_batch(
        self,
        batch_id: UUID,
    ) -> BatchExecutionOutcomeSnapshot:
        async with self._session_context_factory() as session:
            batch = await session.get(BatchRow, batch_id)
            if batch is None:
                raise BatchNotFound(batch_id)
            trials = tuple(
                (
                    await session.scalars(
                        select(TrialRow)
                        .where(TrialRow.batch_id == batch_id)
                        .order_by(TrialRow.trial_ordinal)
                    )
                ).all()
            )
            trial_ids = tuple(trial.trial_id for trial in trials)
            attempts = (
                tuple(
                    (
                        await session.scalars(
                            select(AttemptRow)
                            .where(AttemptRow.trial_id.in_(trial_ids))
                            .order_by(AttemptRow.trial_id, AttemptRow.attempt_number)
                        )
                    ).all()
                )
                if trial_ids
                else ()
            )
        semantics = await self._version_semantics.load(batch.experiment_version_id)
        attempts_by_trial = {
            trial_id: tuple(
                attempt for attempt in attempts if attempt.trial_id == trial_id
            )
            for trial_id in trial_ids
        }
        return BatchExecutionOutcomeSnapshot(
            batch_id=batch.batch_id,
            batch_row_version=batch.row_version,
            attempt_ids=tuple(attempt.attempt_id for attempt in attempts),
            outcomes=tuple(
                _derive_trial_execution_outcome(
                    trial_id=trial.trial_id,
                    attempts=attempts_by_trial[trial.trial_id],
                    batch_cancelled=batch.cancel_requested_at is not None,
                    max_retries=semantics.max_retries,
                )
                for trial in trials
            ),
        )
