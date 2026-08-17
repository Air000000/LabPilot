from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select

from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.types import AttemptStatus, TerminalKind
from labpilot.observability.models import RuntimeEventRow
from tests.integration.experiments.test_proposal_service import (
    _agent_revision,
    _context,
    _payload,
    _proposal_system,
)


@asynccontextmanager
async def _approved_version(
    isolated_database_url: str,
    tmp_path: Path,
) -> AsyncIterator[tuple[object, object]]:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        payload = _payload(
            system,
            parameter_matrix={"β": (2, 1), "a": ("second", "first")},
            seeds=(9, 7),
            train_argv_template=(
                "{parameter:a}",
                "{parameter:β}",
                "{seed}",
                "{dataset}",
                "{output_dir}",
            ),
        )
        revision = await _agent_revision(system, payload=payload)
        verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        yield system, version


async def _create(service: object, version_id: UUID, key: str = "create-batch"):
    from labpilot.execution.batch_service import CreateBatch

    return await service.create_batch(  # type: ignore[attr-defined]
        context=_context(),
        idempotency_key=key,
        command=CreateBatch(version_id),
    )


@pytest.mark.integration
async def test_create_batch_expands_every_trial_deterministically_and_zero_attempts(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.execution.batch_service import BatchApplicationService

    async with _approved_version(isolated_database_url, tmp_path) as (system, version):
        service = BatchApplicationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        first = await _create(service, version.experiment_version_id)
        replay = await _create(service, version.experiment_version_id)
        batch_id = UUID(first.envelope.primary_result_id)

        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
            trials = tuple(
                (
                    await session.scalars(
                        select(TrialRow)
                        .where(TrialRow.batch_id == batch_id)
                        .order_by(TrialRow.trial_ordinal)
                    )
                ).all()
            )
            attempt_count = await session.scalar(select(func.count(AttemptRow.attempt_id)))
            event_types = tuple(
                (
                    await session.scalars(
                        select(RuntimeEventRow.event_type)
                        .where(
                            RuntimeEventRow.aggregate_type == "batch",
                            RuntimeEventRow.aggregate_id == str(batch_id),
                        )
                        .order_by(RuntimeEventRow.aggregate_sequence)
                    )
                ).all()
            )

        assert batch is not None and batch.row_version == 1
        assert replay.replayed is True
        assert [(row.resolved_parameters_json, row.seed) for row in trials] == [
            ({"a": "second", "β": 2}, 9),
            ({"a": "second", "β": 2}, 7),
            ({"a": "second", "β": 1}, 9),
            ({"a": "second", "β": 1}, 7),
            ({"a": "first", "β": 2}, 9),
            ({"a": "first", "β": 2}, 7),
            ({"a": "first", "β": 1}, 9),
            ({"a": "first", "β": 1}, 7),
        ]
        assert [row.trial_ordinal for row in trials] == list(range(1, 9))
        assert attempt_count == 0
        assert event_types == ("batch.created",)


@pytest.mark.integration
async def test_receipt_replay_beats_stale_version_and_cancel_blocks_new_start(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.execution.batch_service import (
        BatchApplicationService,
        BatchStateConflict,
        BatchVersionConflict,
        CancelBatch,
        StartBatch,
    )

    async with _approved_version(isolated_database_url, tmp_path) as (system, version):
        service = BatchApplicationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        created = await _create(service, version.experiment_version_id)
        batch_id = UUID(created.envelope.primary_result_id)
        started = await service.start_batch(
            context=_context(),
            idempotency_key="start-batch",
            command=StartBatch(batch_id, expected_row_version=1),
        )
        replay = await service.start_batch(
            context=_context(),
            idempotency_key="start-batch",
            command=StartBatch(batch_id, expected_row_version=1),
        )
        with pytest.raises(BatchVersionConflict):
            await service.cancel_batch(
                context=_context(),
                idempotency_key="cancel-stale-new-key",
                command=CancelBatch(batch_id, expected_row_version=1),
            )
        cancelled = await service.cancel_batch(
            context=_context(),
            idempotency_key="cancel-batch",
            command=CancelBatch(batch_id, expected_row_version=2),
        )
        cancel_replay = await service.cancel_batch(
            context=_context(),
            idempotency_key="cancel-batch",
            command=CancelBatch(batch_id, expected_row_version=2),
        )

        second = await _create(service, version.experiment_version_id, "second-batch")
        second_id = UUID(second.envelope.primary_result_id)
        await service.cancel_batch(
            context=_context(),
            idempotency_key="cancel-before-start",
            command=CancelBatch(second_id, expected_row_version=1),
        )
        with pytest.raises(BatchStateConflict):
            await service.start_batch(
                context=_context(),
                idempotency_key="start-after-cancel",
                command=StartBatch(second_id, expected_row_version=2),
            )

        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
        assert started.replayed is False and replay.replayed is True
        assert cancelled.replayed is False and cancel_replay.replayed is True
        assert batch is not None and batch.row_version == 3


@pytest.mark.integration
async def test_internal_attempt_creation_revalidates_authority_and_retries_with_new_x(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.execution.batch_service import (
        AttemptCreationService,
        BatchApplicationService,
        CancelBatch,
        StartBatch,
    )
    from labpilot.execution.query_service import ExecutionQueryService

    async with _approved_version(isolated_database_url, tmp_path) as (system, version):
        commands = BatchApplicationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        created = await _create(commands, version.experiment_version_id)
        batch_id = UUID(created.envelope.primary_result_id)
        async with system.database.session() as session:
            trials = tuple(
                (
                    await session.scalars(
                        select(TrialRow)
                        .where(TrialRow.batch_id == batch_id)
                        .order_by(TrialRow.trial_ordinal)
                    )
                ).all()
            )
        workspace_root = tmp_path / "attempts"
        workspace_root.mkdir()
        attempts = AttemptCreationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
            installation_identity="installation-1",
            attempt_workspace_root=workspace_root,
        )

        assert await attempts.create_attempt_for_trial(trials[0].trial_id) is None
        await commands.start_batch(
            context=_context(),
            idempotency_key="start-for-attempts",
            command=StartBatch(batch_id, expected_row_version=1),
        )
        first = await attempts.create_attempt_for_trial(trials[0].trial_id)
        assert first is not None
        assert await attempts.create_attempt_for_trial(trials[0].trial_id) is None

        retry_source = await attempts.create_attempt_for_trial(trials[1].trial_id)
        unknown = await attempts.create_attempt_for_trial(trials[2].trial_id)
        conflict = await attempts.create_attempt_for_trial(trials[3].trial_id)
        unclassified = await attempts.create_attempt_for_trial(trials[4].trial_id)
        assert retry_source and unknown and conflict and unclassified
        now = datetime.now(UTC)
        async with system.database.session() as session:
            async with session.begin():
                retry_source.status = AttemptStatus.PREPARATION_FAILED.value
                retry_source.preparation_failure_code = "PREPARATION_TEMPORARY_IO"
                retry_source.terminal_at = now
                unknown.status = AttemptStatus.UNKNOWN.value
                conflict.status = AttemptStatus.IDENTITY_CONFLICT.value
                unclassified.status = AttemptStatus.TERMINAL.value
                unclassified.terminal_kind = (
                    TerminalKind.PROCESS_TERMINAL_UNCLASSIFIED.value
                )
                unclassified.terminal_at = now
                session.add_all((retry_source, unknown, conflict, unclassified))

        retry = await attempts.create_attempt_for_trial(trials[1].trial_id)
        assert retry is not None and retry.attempt_number == 2
        assert retry.attempt_id != retry_source.attempt_id
        assert (
            retry.supervisor_execution_identity
            != retry_source.supervisor_execution_identity
        )
        assert await attempts.create_attempt_for_trial(trials[2].trial_id) is None
        assert await attempts.create_attempt_for_trial(trials[3].trial_id) is None
        assert await attempts.create_attempt_for_trial(trials[4].trial_id) is None

        async with system.database.session() as session:
            async with session.begin():
                first.status = AttemptStatus.TERMINAL.value
                first.terminal_kind = TerminalKind.PROCESS_EXITED.value
                first.exit_code = 0
                first.terminal_at = now
                session.add(first)
        assert await attempts.create_attempt_for_trial(trials[0].trial_id) is None

        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
            assert batch is not None and batch.row_version == 2
        await commands.cancel_batch(
            context=_context(),
            idempotency_key="cancel-before-new-attempt",
            command=CancelBatch(batch_id, expected_row_version=2),
        )
        assert await attempts.create_attempt_for_trial(trials[5].trial_id) is None

        query = ExecutionQueryService(system.database.session)
        conflict_view = await query.get_attempt(conflict.attempt_id)
        batch_view = await query.get_batch(batch_id)
        assert conflict_view.status == "identity_conflict"
        assert batch_view.presentation_status == "failed_to_coordinate"
