from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.batch_service import AttemptCreationService
from labpilot.execution.execution_service import (
    AttemptExecutionService,
    ReconciliationReason,
)
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.ports import ExecutionInputError
from labpilot.execution.preparation_service import AttemptPreparationService
from labpilot.execution.types import AttemptStatus, TerminalKind
from labpilot.experiments.proposal_models import ExperimentVersionRow
from labpilot.results_validation.models import ValidationReportRow
from labpilot.results_validation.service import ResultApplicationService
from labpilot.results_validation.summary_service import BatchSummaryService
from labpilot.results_validation.types import ResultProcessingDeferred
from labpilot.results_validation.validator import (
    EXTRACTION_PROFILE_VERSION,
    VALIDATOR_VERSION,
)

_PHYSICAL_STATUSES = (
    AttemptStatus.LAUNCH_PENDING.value,
    AttemptStatus.RUNNING.value,
    AttemptStatus.UNKNOWN.value,
    AttemptStatus.IDENTITY_CONFLICT.value,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _StartupCandidate:
    attempt_id: UUID
    status: str
    cancelled: bool


class CoordinatorService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        attempt_creation: AttemptCreationService,
        preparation: AttemptPreparationService,
        execution: AttemptExecutionService,
        result_processing: ResultApplicationService | None = None,
        summary: BatchSummaryService | None = None,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._attempt_creation = attempt_creation
        self._preparation = preparation
        self._execution = execution
        self._result_processing = result_processing
        self._summary = summary

    async def run_startup_barrier(self, *, coordinator_start_id: UUID) -> None:
        for candidate in await self._startup_candidates():
            if candidate.cancelled and candidate.status != AttemptStatus.IDENTITY_CONFLICT:
                await self._execution.request_stop(
                    candidate.attempt_id,
                    reason=ReconciliationReason.STARTUP,
                    coordinator_start_id=coordinator_start_id,
                )
            else:
                await self._execution.reconcile_attempt(
                    candidate.attempt_id,
                    reason=ReconciliationReason.STARTUP,
                    coordinator_start_id=coordinator_start_id,
                )

    async def run_scan(self) -> None:
        await self._control_existing_work()
        await self._reconcile_physical_work()
        await self._process_successful_results()
        await self._prepare_eligible_work()
        await self._admit_launch_ready()
        await self._refresh_candidate_summaries()

    async def _control_existing_work(self) -> None:
        async with self._session_context_factory() as session:
            prelaunch = tuple(
                await session.scalars(
                    select(AttemptRow.attempt_id)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        BatchRow.cancel_requested_at.is_not(None),
                        AttemptRow.status.in_(
                            (
                                AttemptStatus.PREPARING.value,
                                AttemptStatus.LAUNCH_READY.value,
                            )
                        ),
                        AttemptRow.terminal_at.is_(None),
                    )
                    .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                )
            )
            physical = tuple(
                await session.scalars(
                    select(AttemptRow.attempt_id)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        BatchRow.cancel_requested_at.is_not(None),
                        AttemptRow.status.in_(_PHYSICAL_STATUSES[:-1]),
                        AttemptRow.terminal_at.is_(None),
                    )
                    .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                )
            )
        for attempt_id in prelaunch:
            await self._cancel_prelaunch(attempt_id)
        for attempt_id in physical:
            await self._execution.request_stop(
                attempt_id, reason=ReconciliationReason.PERIODIC
            )

    async def _cancel_prelaunch(self, attempt_id: UUID) -> None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await uow.session.scalar(
                select(AttemptRow)
                .where(AttemptRow.attempt_id == attempt_id)
                .with_for_update()
            )
            if attempt is not None and attempt.status in {
                AttemptStatus.PREPARING.value,
                AttemptStatus.LAUNCH_READY.value,
            }:
                trial = await uow.session.get(TrialRow, attempt.trial_id)
                batch = (
                    await uow.session.get(BatchRow, trial.batch_id)
                    if trial is not None
                    else None
                )
                if batch is not None and batch.cancel_requested_at is not None:
                    attempt.status = AttemptStatus.CANCELLED_PRE_LAUNCH.value
                    attempt.terminal_at = datetime.now(UTC)
            await uow.commit()

    async def _reconcile_physical_work(self) -> None:
        async with self._session_context_factory() as session:
            attempt_ids = tuple(
                await session.scalars(
                    select(AttemptRow.attempt_id)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        AttemptRow.status.in_(_PHYSICAL_STATUSES),
                        AttemptRow.terminal_at.is_(None),
                    )
                    .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                )
            )
        for attempt_id in attempt_ids:
            await self._execution.reconcile_attempt(
                attempt_id, reason=ReconciliationReason.PERIODIC
            )

    async def _process_successful_results(self) -> None:
        if self._result_processing is None:
            return
        for attempt_id in await self._result_candidate_attempts():
            try:
                await self._result_processing.process_attempt(attempt_id)
            except ResultProcessingDeferred:
                logger.info("result_processing_deferred attempt_id=%s", attempt_id)

    async def _result_candidate_attempts(self) -> tuple[UUID, ...]:
        active_report = exists(
            select(ValidationReportRow.validation_report_id).where(
                ValidationReportRow.attempt_id == AttemptRow.attempt_id,
                ValidationReportRow.validation_protocol_hash
                == ExperimentVersionRow.validation_protocol_hash,
                ValidationReportRow.extraction_profile_version
                == EXTRACTION_PROFILE_VERSION,
                ValidationReportRow.validator_version == VALIDATOR_VERSION,
            )
        )
        async with self._session_context_factory() as session:
            return tuple(
                await session.scalars(
                    select(AttemptRow.attempt_id)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .join(
                        ExperimentVersionRow,
                        ExperimentVersionRow.experiment_version_id
                        == BatchRow.experiment_version_id,
                    )
                    .where(
                        AttemptRow.status == AttemptStatus.TERMINAL.value,
                        AttemptRow.terminal_kind == TerminalKind.PROCESS_EXITED.value,
                        AttemptRow.exit_code == 0,
                        ~active_report,
                    )
                    .order_by(
                        BatchRow.created_at,
                        BatchRow.batch_id,
                        TrialRow.trial_ordinal,
                        AttemptRow.attempt_number,
                        AttemptRow.attempt_id,
                    )
                )
            )

    async def _prepare_eligible_work(self) -> None:
        for attempt_id in await self._preparing_attempts():
            await self._preparation.prepare_attempt(attempt_id)
        for trial_id in (
            *(await self._first_attempt_trials()),
            *(await self._retry_trials()),
        ):
            attempt = await self._attempt_creation.create_attempt_for_trial(trial_id)
            if attempt is not None:
                await self._preparation.prepare_attempt(attempt.attempt_id)

    async def _preparing_attempts(self) -> tuple[UUID, ...]:
        async with self._session_context_factory() as session:
            return tuple(
                await session.scalars(
                    select(AttemptRow.attempt_id)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        BatchRow.start_requested_at.is_not(None),
                        BatchRow.cancel_requested_at.is_(None),
                        AttemptRow.status == AttemptStatus.PREPARING.value,
                        AttemptRow.terminal_at.is_(None),
                    )
                    .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                )
            )

    async def _first_attempt_trials(self) -> tuple[UUID, ...]:
        has_attempt = exists(
            select(AttemptRow.attempt_id).where(AttemptRow.trial_id == TrialRow.trial_id)
        )
        return await self._eligible_trials(~has_attempt)

    async def _retry_trials(self) -> tuple[UUID, ...]:
        has_attempt = exists(
            select(AttemptRow.attempt_id).where(AttemptRow.trial_id == TrialRow.trial_id)
        )
        has_active_attempt = exists(
            select(AttemptRow.attempt_id).where(
                AttemptRow.trial_id == TrialRow.trial_id,
                AttemptRow.terminal_at.is_(None),
            )
        )
        return await self._eligible_trials(has_attempt & ~has_active_attempt)

    async def _eligible_trials(
        self, attempt_condition: ColumnElement[bool]
    ) -> tuple[UUID, ...]:
        async with self._session_context_factory() as session:
            return tuple(
                await session.scalars(
                    select(TrialRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        BatchRow.start_requested_at.is_not(None),
                        BatchRow.cancel_requested_at.is_(None),
                        attempt_condition,
                    )
                    .order_by(
                        BatchRow.created_at,
                        BatchRow.batch_id,
                        TrialRow.trial_ordinal,
                        TrialRow.trial_id,
                    )
                )
            )

    async def _admit_launch_ready(self) -> None:
        for attempt_id in await self._launch_ready_attempts():
            if await self._physical_active_count() >= 4:
                break
            try:
                await self._execution.dispatch_attempt(attempt_id)
            except (ConnectionError, ExecutionInputError):
                break
            if await self._attempt_status(attempt_id) == AttemptStatus.LAUNCH_READY.value:
                break

    async def _refresh_candidate_summaries(self) -> None:
        if self._summary is None:
            return
        for batch_id in await self._summary_candidate_batches():
            await self._summary.refresh_summary(batch_id)

    async def _summary_candidate_batches(self) -> tuple[UUID, ...]:
        async with self._session_context_factory() as session:
            return tuple(
                await session.scalars(
                    select(BatchRow.batch_id)
                    .where(
                        or_(
                            BatchRow.start_requested_at.is_not(None),
                            BatchRow.cancel_requested_at.is_not(None),
                        )
                    )
                    .order_by(BatchRow.created_at, BatchRow.batch_id)
                )
            )

    async def _launch_ready_attempts(self) -> tuple[UUID, ...]:
        async with self._session_context_factory() as session:
            return tuple(
                await session.scalars(
                    select(AttemptRow.attempt_id)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        BatchRow.cancel_requested_at.is_(None),
                        AttemptRow.status == AttemptStatus.LAUNCH_READY.value,
                        AttemptRow.terminal_at.is_(None),
                    )
                    .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                )
            )

    async def _physical_active_count(self) -> int:
        async with self._session_context_factory() as session:
            return int(
                await session.scalar(
                    select(func.count(AttemptRow.attempt_id)).where(
                        AttemptRow.status.in_(_PHYSICAL_STATUSES),
                        AttemptRow.terminal_at.is_(None),
                    )
                )
                or 0
            )

    async def _attempt_status(self, attempt_id: UUID) -> str | None:
        async with self._session_context_factory() as session:
            return cast(
                str | None,
                await session.scalar(
                    select(AttemptRow.status).where(
                        AttemptRow.attempt_id == attempt_id
                    )
                ),
            )

    async def _startup_candidates(self) -> tuple[_StartupCandidate, ...]:
        async with self._session_context_factory() as session:
            rows = (
                await session.execute(
                    select(
                        AttemptRow.attempt_id,
                        AttemptRow.status,
                        BatchRow.cancel_requested_at,
                    )
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .join(BatchRow, BatchRow.batch_id == TrialRow.batch_id)
                    .where(
                        AttemptRow.status.in_(_PHYSICAL_STATUSES),
                        AttemptRow.terminal_at.is_(None),
                    )
                    .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                )
            ).all()
        return tuple(
            _StartupCandidate(attempt_id, status, cancelled_at is not None)
            for attempt_id, status, cancelled_at in rows
        )
