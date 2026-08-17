from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select

from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.db.idempotency import (
    CommandResultEnvelope,
    IdempotentCommandOutcome,
    async_noop_prepare,
    execute_idempotent_command,
    prepare_command_identity,
    prepare_create_scope,
)
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.ports import VersionExecutionSemanticsPort
from labpilot.execution.repository import build_preparing_attempt
from labpilot.execution.types import (
    ParameterValue,
    TerminalKind,
    VersionExecutionSemantics,
    trial_identity,
)
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.payloads import EventJsonObject, prepare_runtime_event_payload

_RETRYABLE_INFRASTRUCTURE_CODES = frozenset(
    {
        "PREPARATION_TEMPORARY_IO",
        "RUNNER_UNAVAILABLE_BEFORE_SEND",
        "PHYSICAL_PREEMPTION_PROVEN",
    }
)


@dataclass(frozen=True, slots=True)
class ExpandedTrialInput:
    trial_ordinal: int
    resolved_parameters: dict[str, ParameterValue]
    seed: int


class _AttemptFailure(Protocol):
    preparation_failure_code: str | None
    execution_issue_code: str | None


def expand_trial_inputs(
    semantics: VersionExecutionSemantics,
) -> tuple[ExpandedTrialInput, ...]:
    keys = tuple(sorted(semantics.parameter_matrix, key=lambda key: key.encode("utf-8")))
    combinations = product(*(semantics.parameter_matrix[key] for key in keys))
    return tuple(
        ExpandedTrialInput(ordinal, dict(zip(keys, values, strict=True)), seed)
        for ordinal, (values, seed) in enumerate(
            ((values, seed) for values in combinations for seed in semantics.seeds),
            start=1,
        )
    )


def is_retryable_infrastructure_failure(attempt: _AttemptFailure) -> bool:
    return (
        attempt.preparation_failure_code in _RETRYABLE_INFRASTRUCTURE_CODES
        or attempt.execution_issue_code in _RETRYABLE_INFRASTRUCTURE_CODES
    )


@dataclass(frozen=True, slots=True)
class CreateBatch:
    experiment_version_id: UUID


@dataclass(frozen=True, slots=True)
class StartBatch:
    batch_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        if self.expected_row_version <= 0:
            raise ValueError("expected_row_version must be positive")


@dataclass(frozen=True, slots=True)
class CancelBatch:
    batch_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        if self.expected_row_version <= 0:
            raise ValueError("expected_row_version must be positive")


class BatchNotFound(LabPilotError):
    def __init__(self, batch_id: UUID) -> None:
        self.batch_id = batch_id
        super().__init__(f"batch not found: {batch_id}")


class TrialNotFound(LabPilotError):
    def __init__(self, trial_id: UUID) -> None:
        self.trial_id = trial_id
        super().__init__(f"trial not found: {trial_id}")


class BatchVersionConflict(LabPilotError):
    def __init__(
        self,
        batch_id: UUID,
        *,
        expected_row_version: int,
        current_row_version: int,
    ) -> None:
        self.batch_id = batch_id
        self.expected_row_version = expected_row_version
        self.current_row_version = current_row_version
        super().__init__(
            f"batch {batch_id} row version is {current_row_version}, "
            f"expected {expected_row_version}"
        )


class BatchStateConflict(LabPilotError):
    def __init__(
        self,
        batch_id: UUID,
        *,
        current_state: str,
        expected_state: str,
    ) -> None:
        self.batch_id = batch_id
        self.current_state = current_state
        self.expected_state = expected_state
        super().__init__(
            f"batch {batch_id} state is {current_state}, expected {expected_state}"
        )


class BatchApplicationService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        version_semantics: VersionExecutionSemanticsPort,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._version_semantics = version_semantics

    async def create_batch(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: CreateBatch,
    ) -> IdempotentCommandOutcome:
        scope = prepare_create_scope(context)
        identity = prepare_command_identity(
            scope_identity=scope,
            command_name="CreateBatch",
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope,
                "command_name": "CreateBatch",
                "command_version": 1,
                "experiment_version_id": str(command.experiment_version_id),
            },
        )

        async def prepare() -> VersionExecutionSemantics:
            return await self._version_semantics.load(command.experiment_version_id)

        async def operation(
            uow: UnitOfWork,
            semantics: VersionExecutionSemantics,
        ) -> CommandResultEnvelope:
            expanded = expand_trial_inputs(semantics)
            if (
                semantics.experiment_version_id != command.experiment_version_id
                or not expanded
            ):
                raise ValueError("approved ExperimentVersion has invalid execution semantics")
            now = await _db_now(uow)
            batch_id = uuid4()
            uow.session.add(
                BatchRow(
                    batch_id=batch_id,
                    project_id=semantics.project_id,
                    experiment_version_id=semantics.experiment_version_id,
                    start_requested_at=None,
                    cancel_requested_at=None,
                    row_version=1,
                    created_at=now,
                )
            )
            await uow.session.flush()
            uow.session.add_all(
                TrialRow(
                    trial_id=uuid4(),
                    batch_id=batch_id,
                    trial_ordinal=item.trial_ordinal,
                    trial_identity=trial_identity(
                        experiment_version_identity=(
                            semantics.experiment_version_identity
                        ),
                        resolved_parameters=item.resolved_parameters,
                        seed=item.seed,
                    ),
                    resolved_parameters_json=item.resolved_parameters,
                    seed=item.seed,
                    created_at=now,
                )
                for item in expanded
            )
            await uow.session.flush()
            await _append_batch_event(
                uow,
                context=context,
                project_id=semantics.project_id,
                batch_id=batch_id,
                event_type="batch.created",
                payload={
                    "batch_id": str(batch_id),
                    "experiment_version_id": str(semantics.experiment_version_id),
                    "trial_count": len(expanded),
                    "row_version": 1,
                },
                occurred_at=now,
            )
            return CommandResultEnvelope(
                outcome_kind="batch_created",
                primary_result_type="batch",
                primary_result_id=str(batch_id),
                primary_result_identity=None,
                related_result_refs=(),
                committed_state="created",
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=prepare,
            operation=operation,
        )

    async def start_batch(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: StartBatch,
    ) -> IdempotentCommandOutcome:
        return await self._control_batch(
            context=context,
            idempotency_key=idempotency_key,
            command_name="StartBatch",
            batch_id=command.batch_id,
            expected_row_version=command.expected_row_version,
            start=True,
        )

    async def cancel_batch(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: CancelBatch,
    ) -> IdempotentCommandOutcome:
        return await self._control_batch(
            context=context,
            idempotency_key=idempotency_key,
            command_name="CancelBatch",
            batch_id=command.batch_id,
            expected_row_version=command.expected_row_version,
            start=False,
        )

    async def _control_batch(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command_name: str,
        batch_id: UUID,
        expected_row_version: int,
        start: bool,
    ) -> IdempotentCommandOutcome:
        scope = prepare_create_scope(context)
        identity = prepare_command_identity(
            scope_identity=scope,
            command_name=command_name,
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope,
                "batch_id": str(batch_id),
                "command_name": command_name,
                "command_version": 1,
                "expected_row_version": expected_row_version,
            },
        )

        async def operation(
            uow: UnitOfWork,
            prepared_none: None,
        ) -> CommandResultEnvelope:
            del prepared_none
            batch = await uow.session.scalar(
                select(BatchRow)
                .where(BatchRow.batch_id == batch_id)
                .with_for_update()
            )
            if batch is None:
                raise BatchNotFound(batch_id)
            if batch.row_version != expected_row_version:
                raise BatchVersionConflict(
                    batch_id,
                    expected_row_version=expected_row_version,
                    current_row_version=batch.row_version,
                )
            if start:
                if batch.cancel_requested_at is not None:
                    raise BatchStateConflict(
                        batch_id,
                        current_state="cancelled",
                        expected_state="not_cancelled",
                    )
                if batch.start_requested_at is not None:
                    raise BatchStateConflict(
                        batch_id,
                        current_state="started",
                        expected_state="not_started",
                    )
            elif batch.cancel_requested_at is not None:
                raise BatchStateConflict(
                    batch_id,
                    current_state="cancelled",
                    expected_state="not_cancelled",
                )
            now = await _db_now(uow)
            if start:
                batch.start_requested_at = now
            else:
                batch.cancel_requested_at = now
            batch.row_version += 1
            await uow.session.flush()
            state = "started" if start else "cancel_requested"
            await _append_batch_event(
                uow,
                context=context,
                project_id=batch.project_id,
                batch_id=batch_id,
                event_type=f"batch.{state}",
                payload={"batch_id": str(batch_id), "row_version": batch.row_version},
                occurred_at=now,
            )
            return CommandResultEnvelope(
                outcome_kind=f"batch_{state}",
                primary_result_type="batch",
                primary_result_id=str(batch_id),
                primary_result_identity=None,
                related_result_refs=(),
                committed_state=state,
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=async_noop_prepare,
            operation=operation,
        )


class AttemptCreationService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        version_semantics: VersionExecutionSemanticsPort,
        installation_identity: str,
        attempt_workspace_root: Path,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._version_semantics = version_semantics
        self._installation_identity = installation_identity
        self._attempt_workspace_root = attempt_workspace_root

    async def create_attempt_for_trial(self, trial_id: UUID) -> AttemptRow | None:
        async with self._session_context_factory() as session:
            trial = await session.get(TrialRow, trial_id)
            if trial is None:
                raise TrialNotFound(trial_id)
            batch = await session.get(BatchRow, trial.batch_id)
            if batch is None:
                raise BatchNotFound(trial.batch_id)
            experiment_version_id = batch.experiment_version_id
        semantics = await self._version_semantics.load(experiment_version_id)

        async with UnitOfWork(self._session_context_factory) as uow:
            trial = await uow.session.scalar(
                select(TrialRow)
                .where(TrialRow.trial_id == trial_id)
                .with_for_update()
            )
            if trial is None:
                raise TrialNotFound(trial_id)
            batch = await uow.session.scalar(
                select(BatchRow)
                .where(BatchRow.batch_id == trial.batch_id)
                .with_for_update()
            )
            if batch is None:
                raise BatchNotFound(trial.batch_id)
            if (
                batch.experiment_version_id != semantics.experiment_version_id
                or batch.start_requested_at is None
                or batch.cancel_requested_at is not None
            ):
                await uow.commit()
                return None
            existing = tuple(
                (
                    await uow.session.scalars(
                        select(AttemptRow)
                        .where(AttemptRow.trial_id == trial_id)
                        .order_by(AttemptRow.attempt_number)
                    )
                ).all()
            )
            if any(attempt.terminal_at is None for attempt in existing):
                await uow.commit()
                return None
            if any(
                attempt.terminal_kind == TerminalKind.PROCESS_EXITED.value
                and attempt.exit_code == 0
                for attempt in existing
            ):
                await uow.commit()
                return None
            next_number = len(existing) + 1
            if next_number > 1 + semantics.max_retries:
                await uow.commit()
                return None
            if next_number > 1 and not is_retryable_infrastructure_failure(existing[-1]):
                await uow.commit()
                return None
            now = await _db_now(uow)
            attempt = build_preparing_attempt(
                attempt_id=uuid4(),
                trial_id=trial_id,
                attempt_number=next_number,
                installation_identity=self._installation_identity,
                attempt_workspace_root=self._attempt_workspace_root,
                created_at=now,
            )
            uow.session.add(attempt)
            await uow.session.flush()
            await uow.commit()
            return attempt
        return None


async def _db_now(uow: UnitOfWork) -> datetime:
    now = await uow.session.scalar(select(func.clock_timestamp()))
    assert now is not None
    return cast(datetime, now)


async def _append_batch_event(
    uow: UnitOfWork,
    *,
    context: CommandContext,
    project_id: UUID,
    batch_id: UUID,
    event_type: str,
    payload: EventJsonObject,
    occurred_at: datetime,
) -> None:
    await append_runtime_event(
        uow,
        RuntimeEventEnvelope(
            project_id=project_id,
            aggregate_type="batch",
            aggregate_id=str(batch_id),
            event_type=event_type,
            event_schema_version=1,
            payload=prepare_runtime_event_payload(
                event_type=event_type,
                event_schema_version=1,
                payload=payload,
            ),
            occurred_at=occurred_at,
            interaction_correlation_id=context.interaction_correlation_id,
            workflow_correlation_id=None,
            causation_id=None,
            actor_type=context.actor.actor_type,
            actor_id=context.actor.actor_id,
            visibility_class="internal",
            topic="runtime-events",
            partition_key=f"batch:{batch_id}",
        ),
    )
