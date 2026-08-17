from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from labpilot.common.errors import LabPilotError
from labpilot.db.uow import SessionContextFactory
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.types import AttemptStatus, TerminalKind


class _AttemptProjection(Protocol):
    status: str
    terminal_kind: str | None
    exit_code: int | None


def attempt_status(attempt: _AttemptProjection) -> str:
    return attempt.status


def derive_trial_presentation(
    attempts: Sequence[_AttemptProjection],
    *,
    started: bool,
    cancelled: bool,
) -> str:
    if any(
        attempt.status
        in {AttemptStatus.UNKNOWN.value, AttemptStatus.IDENTITY_CONFLICT.value}
        for attempt in attempts
    ):
        return "needs_attention"
    if any(
        attempt.terminal_kind == TerminalKind.PROCESS_EXITED.value
        and attempt.exit_code == 0
        for attempt in attempts
    ):
        return "succeeded"
    if any(attempt.status not in {
        AttemptStatus.PREPARATION_FAILED.value,
        AttemptStatus.CANCELLED_PRE_LAUNCH.value,
        AttemptStatus.TERMINAL.value,
    } for attempt in attempts):
        return "running"
    if cancelled and attempts:
        return "cancelled"
    if attempts:
        return "failed"
    return "pending" if started else "created"


def derive_batch_presentation(
    trial_presentations: Sequence[str],
    *,
    started: bool,
    cancelled: bool,
) -> str:
    if "needs_attention" in trial_presentations:
        return "failed_to_coordinate"
    if cancelled:
        return (
            "cancelling"
            if any(value in {"pending", "running"} for value in trial_presentations)
            else "cancelled"
        )
    if not started:
        return "created"
    if trial_presentations and all(value == "succeeded" for value in trial_presentations):
        return "succeeded"
    if any(value == "running" for value in trial_presentations):
        return "running"
    if trial_presentations and all(value == "failed" for value in trial_presentations):
        return "failed"
    return "pending"


class BatchQueryNotFound(LabPilotError):
    def __init__(self, batch_id: UUID) -> None:
        self.batch_id = batch_id
        super().__init__(f"batch not found: {batch_id}")


class TrialQueryNotFound(LabPilotError):
    def __init__(self, trial_id: UUID) -> None:
        self.trial_id = trial_id
        super().__init__(f"trial not found: {trial_id}")


class AttemptQueryNotFound(LabPilotError):
    def __init__(self, attempt_id: UUID) -> None:
        self.attempt_id = attempt_id
        super().__init__(f"attempt not found: {attempt_id}")


@dataclass(frozen=True, slots=True)
class BatchDetails:
    batch_id: UUID
    project_id: UUID
    experiment_version_id: UUID
    start_requested_at: datetime | None
    cancel_requested_at: datetime | None
    row_version: int
    created_at: datetime
    presentation_status: str
    trial_count: int
    trial_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class TrialDetails:
    trial_id: UUID
    batch_id: UUID
    trial_ordinal: int
    trial_identity: str
    resolved_parameters: dict[str, object]
    seed: int
    created_at: datetime
    presentation_status: str
    attempt_count: int
    attempt_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AttemptDetails:
    attempt_id: UUID
    trial_id: UUID
    batch_id: UUID
    attempt_number: int
    status: str
    supervisor_execution_identity: str
    created_at: datetime
    prepared_at: datetime | None
    launch_intent_committed_at: datetime | None
    physical_started_at: datetime | None
    physical_ended_at: datetime | None
    terminal_at: datetime | None
    terminal_kind: str | None
    exit_code: int | None
    termination_signal: int | None
    supervisor_result: str | None
    unknown_since: datetime | None
    attention_required_at: datetime | None
    execution_issue_code: str | None
    observed_environment: dict[str, object] | None


class ExecutionQueryService:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory

    async def get_batch(self, batch_id: UUID) -> BatchDetails:
        async with self._session_context_factory() as session:
            batch = await session.get(BatchRow, batch_id)
            if batch is None:
                raise BatchQueryNotFound(batch_id)
            trials = tuple(
                (
                    await session.scalars(
                        select(TrialRow)
                        .where(TrialRow.batch_id == batch_id)
                        .order_by(TrialRow.trial_ordinal)
                    )
                ).all()
            )
            attempts = tuple(
                (
                    await session.scalars(
                        select(AttemptRow).where(
                            AttemptRow.trial_id.in_(
                                [trial.trial_id for trial in trials]
                            )
                        )
                    )
                ).all()
            ) if trials else ()
        by_trial = {
            trial.trial_id: tuple(
                attempt for attempt in attempts if attempt.trial_id == trial.trial_id
            )
            for trial in trials
        }
        presentations = tuple(
            derive_trial_presentation(
                by_trial[trial.trial_id],
                started=batch.start_requested_at is not None,
                cancelled=batch.cancel_requested_at is not None,
            )
            for trial in trials
        )
        return BatchDetails(
            batch.batch_id,
            batch.project_id,
            batch.experiment_version_id,
            batch.start_requested_at,
            batch.cancel_requested_at,
            batch.row_version,
            batch.created_at,
            derive_batch_presentation(
                presentations,
                started=batch.start_requested_at is not None,
                cancelled=batch.cancel_requested_at is not None,
            ),
            len(trials),
            tuple(trial.trial_id for trial in trials),
        )

    async def get_trial(self, trial_id: UUID) -> TrialDetails:
        async with self._session_context_factory() as session:
            trial = await session.get(TrialRow, trial_id)
            if trial is None:
                raise TrialQueryNotFound(trial_id)
            batch = await session.get(BatchRow, trial.batch_id)
            if batch is None:
                raise BatchQueryNotFound(trial.batch_id)
            attempts = tuple(
                (
                    await session.scalars(
                        select(AttemptRow)
                        .where(AttemptRow.trial_id == trial_id)
                        .order_by(AttemptRow.attempt_number)
                    )
                ).all()
            )
        return TrialDetails(
            trial.trial_id,
            trial.batch_id,
            trial.trial_ordinal,
            trial.trial_identity,
            trial.resolved_parameters_json,
            trial.seed,
            trial.created_at,
            derive_trial_presentation(
                attempts,
                started=batch.start_requested_at is not None,
                cancelled=batch.cancel_requested_at is not None,
            ),
            len(attempts),
            tuple(attempt.attempt_id for attempt in attempts),
        )

    async def get_attempt(self, attempt_id: UUID) -> AttemptDetails:
        async with self._session_context_factory() as session:
            attempt = await session.get(AttemptRow, attempt_id)
            if attempt is None:
                raise AttemptQueryNotFound(attempt_id)
            trial = await session.get(TrialRow, attempt.trial_id)
            if trial is None:
                raise TrialQueryNotFound(attempt.trial_id)
        return AttemptDetails(
            attempt.attempt_id,
            attempt.trial_id,
            trial.batch_id,
            attempt.attempt_number,
            attempt.status,
            attempt.supervisor_execution_identity,
            attempt.created_at,
            attempt.prepared_at,
            attempt.launch_intent_committed_at,
            attempt.physical_started_at,
            attempt.physical_ended_at,
            attempt.terminal_at,
            attempt.terminal_kind,
            attempt.exit_code,
            attempt.termination_signal,
            attempt.supervisor_result,
            attempt.unknown_since,
            attempt.attention_required_at,
            attempt.execution_issue_code,
            _bounded_observed_environment(attempt.observed_environment_json),
        )


def _bounded_observed_environment(
    value: dict[str, object] | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    allowed = {
        "schema_version",
        "environment_expectation_hash",
        "python_profile",
        "runtime_path",
        "python_version",
        "framework_versions",
        "cuda_runtime_version",
        "cuda_device_count",
        "probe_issue_codes",
    }
    return {key: item for key, item in value.items() if key in allowed}
