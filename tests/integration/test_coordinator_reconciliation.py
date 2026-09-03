from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.execution_service import AttemptExecutionService
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.runner_protocol import RunnerLaunchRequest
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.execution.types import AttemptStatus, TerminalKind
from labpilot.observability.models import RuntimeEventRow
from labpilot.results_validation.models import (
    BatchSummaryRevisionRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.service import ResultApplicationService
from labpilot.results_validation.summary_service import BatchSummaryService
from labpilot.results_validation.types import ResultProcessingDeferred
from labpilot.results_validation.validator import (
    EXTRACTION_PROFILE_VERSION,
    VALIDATOR_VERSION,
)
from tests.integration.test_attempt_execution_service import FakeRunner, _active, _prepare
from tests.integration.test_attempt_preparation import _attempt, _prepared_system
from tests.integration.test_result_processing_service import (
    _seed_result_attempt,
    _set_attempt,
)


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


class _NoopScanCoordinatorMixin:
    async def _control_existing_work(self) -> None:
        pass

    async def _reconcile_physical_work(self) -> None:
        pass

    async def _prepare_eligible_work(self) -> None:
        pass

    async def _admit_launch_ready(self) -> None:
        pass


def _outcomes(session_context_factory: object) -> TrialExecutionOutcomeService:
    return TrialExecutionOutcomeService(
        session_context_factory,
        version_semantics=VersionExecutionSemanticsAdapter(session_context_factory),
    )


async def _attempt_facts(database: object, attempt_id: object) -> tuple[object, ...]:
    async with database.session() as session:
        attempt = await session.get(AttemptRow, attempt_id)
        assert attempt is not None
        return (
            attempt.attempt_id,
            attempt.trial_id,
            attempt.attempt_number,
            attempt.status,
            attempt.terminal_kind,
            attempt.exit_code,
            attempt.terminal_at,
            attempt.workspace_path,
            deepcopy(attempt.observed_environment_json),
        )


async def _start_batch(database: object, batch_id: object) -> None:
    async with database.session() as session:
        batch = await session.get(BatchRow, batch_id)
        assert batch is not None
        batch.start_requested_at = datetime.now(UTC)
        await session.commit()


async def _result_state(
    database: object, batch_id: object, attempt_id: object
) -> tuple[int, int, int, tuple[str, ...]]:
    async with database.session() as session:
        reports = tuple(
            await session.scalars(
                select(ValidationReportRow).where(
                    ValidationReportRow.attempt_id == attempt_id
                )
            )
        )
        selections = tuple(
            await session.scalars(
                select(TrialResultSelectionRow).where(
                    TrialResultSelectionRow.attempt_id == attempt_id
                )
            )
        )
        revisions = tuple(
            await session.scalars(
                select(BatchSummaryRevisionRow).where(
                    BatchSummaryRevisionRow.batch_id == batch_id
                )
            )
        )
        events = tuple(
            await session.scalars(
                select(RuntimeEventRow).where(
                    RuntimeEventRow.aggregate_id == str(batch_id),
                    RuntimeEventRow.event_type.like("result.%"),
                )
            )
        )
    return (
        len(reports),
        len(selections),
        len(revisions),
        tuple(sorted(event.event_type for event in events)),
    )


@pytest.mark.integration
async def test_result_candidates_require_terminal_success_and_current_semantic_report(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        coordinator = CoordinatorService(
            system.database.session,
            attempt_creation=_NoAttempts(),
            preparation=_NoPreparation(),
            execution=_NoExecution(),
        )

        await _set_attempt(
            system.database,
            attempt_id,
            status=AttemptStatus.RUNNING.value,
            terminal_kind=None,
            exit_code=None,
            terminal_at=None,
        )
        assert await coordinator._result_candidate_attempts() == ()

        await _set_attempt(
            system.database,
            attempt_id,
            status=AttemptStatus.TERMINAL.value,
            terminal_kind=TerminalKind.PROCESS_EXITED.value,
            exit_code=1,
            terminal_at=datetime.now(UTC),
        )
        assert await coordinator._result_candidate_attempts() == ()

        await _set_attempt(
            system.database,
            attempt_id,
            terminal_kind=TerminalKind.PROCESS_TERMINAL_UNCLASSIFIED.value,
            exit_code=None,
            termination_signal=None,
            terminal_at=datetime.now(UTC),
        )
        assert await coordinator._result_candidate_attempts() == ()

        await _set_attempt(
            system.database,
            attempt_id,
            terminal_kind=TerminalKind.PROCESS_EXITED.value,
            exit_code=0,
            termination_signal=None,
            terminal_at=datetime.now(UTC),
        )
        assert await coordinator._result_candidate_attempts() == (attempt_id,)

        await ResultApplicationService(system.database.session).process_attempt(
            attempt_id
        )
        assert await coordinator._result_candidate_attempts() == ()

        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(
                    ValidationReportRow.attempt_id == attempt_id
                )
            )
            assert report is not None
            original = (
                report.validation_protocol_hash,
                report.extraction_profile_version,
                report.validator_version,
            )
            report_id = report.validation_report_id

            report.validator_version = f"{VALIDATOR_VERSION}-historical"
            await session.commit()
        assert await coordinator._result_candidate_attempts() == (attempt_id,)

        async with system.database.session() as session:
            report = await session.get(ValidationReportRow, report_id)
            assert report is not None
            report.validator_version = original[2]
            report.extraction_profile_version = (
                f"{EXTRACTION_PROFILE_VERSION}-historical"
            )
            await session.commit()
        assert await coordinator._result_candidate_attempts() == (attempt_id,)

        async with system.database.session() as session:
            report = await session.get(ValidationReportRow, report_id)
            assert report is not None
            report.extraction_profile_version = original[1]
            report.validation_protocol_hash = (
                "0" * 64 if original[0] != "0" * 64 else "1" * 64
            )
            await session.commit()
        assert await coordinator._result_candidate_attempts() == (attempt_id,)


@pytest.mark.integration
async def test_result_candidates_are_deterministically_ordered_across_trials(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        second_trial_id, second_attempt_id = uuid4(), uuid4()
        async with system.database.session() as session:
            first_trial = await session.get(TrialRow, trial_id)
            first_attempt = await session.get(AttemptRow, attempt_id)
            assert first_trial is not None and first_attempt is not None
            session.add(
                TrialRow(
                    trial_id=second_trial_id,
                    batch_id=batch_id,
                    trial_ordinal=2,
                    trial_identity="result-candidate-second-trial",
                    resolved_parameters_json=deepcopy(
                        first_trial.resolved_parameters_json
                    ),
                    seed=first_trial.seed + 1,
                )
            )
            await session.flush()
            session.add(
                AttemptRow(
                    attempt_id=second_attempt_id,
                    trial_id=second_trial_id,
                    attempt_number=1,
                    status=AttemptStatus.TERMINAL.value,
                    supervisor_execution_identity=f"result-candidate-{second_attempt_id}",
                    workspace_path=first_attempt.workspace_path,
                    observed_environment_json=deepcopy(
                        first_attempt.observed_environment_json
                    ),
                    observed_environment_recorded_at=(
                        first_attempt.observed_environment_recorded_at
                    ),
                    terminal_kind=TerminalKind.PROCESS_EXITED.value,
                    exit_code=0,
                    terminal_at=first_attempt.terminal_at,
                )
            )
            await session.commit()

        coordinator = CoordinatorService(
            system.database.session,
            attempt_creation=_NoAttempts(),
            preparation=_NoPreparation(),
            execution=_NoExecution(),
        )
        assert await coordinator._result_candidate_attempts() == (
            attempt_id,
            second_attempt_id,
        )


@pytest.mark.integration
async def test_deferred_result_attempt_is_unchanged_then_recovered_on_next_scan(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        class DeferredThenRealProcessor:
            def __init__(self) -> None:
                self.calls: list[object] = []
                self._real = ResultApplicationService(system.database.session)

            async def process_attempt(self, candidate_id: object) -> object:
                self.calls.append(candidate_id)
                if len(self.calls) == 1:
                    raise ResultProcessingDeferred("retry")
                return await self._real.process_attempt(candidate_id)

        processor = DeferredThenRealProcessor()

        class RecoveryCoordinator(_NoopScanCoordinatorMixin, CoordinatorService):
            pass

        coordinator = RecoveryCoordinator(
            system.database.session,
            attempt_creation=_NoAttempts(),
            preparation=_NoPreparation(),
            execution=_NoExecution(),
        )
        coordinator._result_processing = processor
        await _start_batch(system.database, batch_id)
        coordinator._summary = BatchSummaryService(
            system.database.session,
            trial_execution_outcome_service=_outcomes(system.database.session),
        )
        before = await _attempt_facts(system.database, attempt_id)

        await coordinator.run_scan()

        assert processor.calls == [attempt_id]
        assert await _attempt_facts(system.database, attempt_id) == before
        assert await _result_state(system.database, batch_id, attempt_id) == (
            0,
            0,
            0,
            (),
        )

        await coordinator.run_scan()

        assert processor.calls == [attempt_id, attempt_id]
        assert await _attempt_facts(system.database, attempt_id) == before
        reports, selections, revisions, _events = await _result_state(
            system.database, batch_id, attempt_id
        )
        assert (reports, selections, revisions) == (1, 1, 1)


@pytest.mark.integration
async def test_restart_recovery_is_idempotent_and_refreshes_summary(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.coordinator.service import CoordinatorService

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        class RecoveryCoordinator(_NoopScanCoordinatorMixin, CoordinatorService):
            pass

        class RecordingResultProcessor:
            def __init__(self) -> None:
                self.calls: list[object] = []
                self._real = ResultApplicationService(system.database.session)

            async def process_attempt(self, candidate_id: object) -> object:
                self.calls.append(candidate_id)
                return await self._real.process_attempt(candidate_id)

        class RecordingSummaryService:
            def __init__(self) -> None:
                self.calls: list[object] = []
                self._real = BatchSummaryService(
                    system.database.session,
                    trial_execution_outcome_service=_outcomes(system.database.session),
                )

            async def refresh_summary(self, candidate_id: object) -> object:
                self.calls.append(candidate_id)
                return await self._real.refresh_summary(candidate_id)

        result_processing = RecordingResultProcessor()
        summary = RecordingSummaryService()

        async def scan_once() -> None:
            coordinator = RecoveryCoordinator(
                system.database.session,
                attempt_creation=_NoAttempts(),
                preparation=_NoPreparation(),
                execution=_NoExecution(),
            )
            coordinator._result_processing = result_processing
            coordinator._summary = summary
            await coordinator.run_scan()

        before = await _attempt_facts(system.database, attempt_id)
        await _start_batch(system.database, batch_id)
        await scan_once()
        first_state = await _result_state(system.database, batch_id, attempt_id)
        assert result_processing.calls == [attempt_id]
        assert summary.calls == [batch_id]
        await scan_once()

        assert first_state == (
            1,
            1,
            1,
            (
                "result.artifact_registered",
                "result.summary_created",
                "result.trial_selected",
                "result.validation_completed",
            ),
        )
        assert await _result_state(
            system.database, batch_id, attempt_id
        ) == first_state
        assert result_processing.calls == [attempt_id]
        assert summary.calls == [batch_id, batch_id]
        assert await _attempt_facts(system.database, attempt_id) == before


@pytest.mark.integration
async def test_summary_candidates_are_rescanned_without_new_result_insertion() -> None:
    from labpilot.coordinator.service import CoordinatorService

    batch_id = uuid4()

    class SummaryRecorder:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def refresh_summary(self, candidate_id: object) -> None:
            self.calls.append(candidate_id)

    summary = SummaryRecorder()

    class SummaryCoordinator(_NoopScanCoordinatorMixin, CoordinatorService):
        async def _process_successful_results(self) -> None:
            pass

        async def _summary_candidate_batches(self) -> tuple[object, ...]:
            return (batch_id,)

    coordinator = SummaryCoordinator(
        object(),
        attempt_creation=_NoAttempts(),
        preparation=_NoPreparation(),
        execution=_NoExecution(),
    )
    coordinator._summary = summary

    await coordinator.run_scan()
    await coordinator.run_scan()

    assert summary.calls == [batch_id, batch_id]
