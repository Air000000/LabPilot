from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from labpilot.execution.execution_service import AttemptExecutionService
from labpilot.execution.models import AttemptRow, BatchRow
from labpilot.execution.runner_protocol import RunnerLaunchRequest
from labpilot.execution.types import AttemptStatus
from labpilot.observability.models import RuntimeEventRow
from tests.integration.test_attempt_execution_service import FakeRunner, _active, _prepare
from tests.integration.test_attempt_preparation import _attempt, _prepared_system


class _NoAttempts:
    async def create_attempt_for_trial(self, trial_id: object):
        return None


class _NoPreparation:
    async def prepare_attempt(self, attempt_id: object) -> None:
        return None


class _NoExecution:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def request_stop(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("stop", args, kwargs))

    async def reconcile_attempt(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("reconcile", args, kwargs))

    async def dispatch_attempt(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("dispatch", args, kwargs))


@pytest.mark.integration
async def test_new_coordinator_instances_reconcile_persisted_attempt_once_per_start(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(system.workspace.parent, observations=[_active()])
        execution = AttemptExecutionService(
            system.database.session,
            runner,
            workspace_root=system.workspace.parent,
        )
        await execution.dispatch_attempt(system.attempt_id)
        launch_count = runner.count(RunnerLaunchRequest)
        first_start, second_start = uuid4(), uuid4()

        for start_id in (first_start, second_start):
            coordinator = CoordinatorService(
                system.database.session,
                attempt_creation=_NoAttempts(),
                preparation=_NoPreparation(),
                execution=execution,
            )
            await coordinator.run_startup_barrier(coordinator_start_id=start_id)

        async with system.database.session() as session:
            events = tuple(
                (
                    await session.scalars(
                        select(RuntimeEventRow)
                        .where(
                            RuntimeEventRow.aggregate_id == str(system.attempt_id),
                            RuntimeEventRow.event_type
                            == "attempt.reconciled_after_restart",
                        )
                        .order_by(RuntimeEventRow.occurred_at)
                    )
                ).all()
            )
        assert runner.count(RunnerLaunchRequest) == launch_count == 1
        assert {event.payload_json["coordinator_start_id"] for event in events} == {
            str(first_start),
            str(second_start),
        }
        assert len(events) == 2


@pytest.mark.integration
async def test_cancelled_preparing_attempt_terminalizes_without_runner_call(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _prepared_system(isolated_database_url, tmp_path) as system:
        async with system.database.session() as session:
            async with session.begin():
                batch = await session.get(BatchRow, system.batch_id)
                assert batch is not None
                batch.cancel_requested_at = datetime.now(UTC)
        execution = _NoExecution()
        coordinator = CoordinatorService(
            system.database.session,
            attempt_creation=_NoAttempts(),
            preparation=_NoPreparation(),
            execution=execution,
        )

        await coordinator.run_scan()

        row = await _attempt(system)
        assert row.status == AttemptStatus.CANCELLED_PRE_LAUNCH.value
        assert row.terminal_at is not None
        assert execution.calls == []


@pytest.mark.integration
async def test_cancelled_identity_conflict_is_reconciled_but_never_stopped(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _prepared_system(isolated_database_url, tmp_path) as system:
        async with system.database.session() as session:
            async with session.begin():
                batch = await session.get(BatchRow, system.batch_id)
                attempt = await session.get(AttemptRow, system.attempt_id)
                assert batch is not None and attempt is not None
                batch.cancel_requested_at = datetime.now(UTC)
                attempt.status = AttemptStatus.IDENTITY_CONFLICT.value
        execution = _NoExecution()

        await CoordinatorService(
            system.database.session,
            attempt_creation=_NoAttempts(),
            preparation=_NoPreparation(),
            execution=execution,
        ).run_scan()

        assert len(execution.calls) == 1
        operation, args, _ = execution.calls[0]
        assert operation == "reconcile"
        assert args == (system.attempt_id,)
