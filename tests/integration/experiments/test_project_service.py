from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from sqlalchemy import func, select
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.db.idempotency import IdempotencyKeyConflict
from labpilot.db.idempotency_models import CommandReceiptRow
from labpilot.db.session import Database
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.project_service import (
    ProjectApplicationService,
    ProjectNotFound,
    ProjectStateConflict,
    ProjectVersionConflict,
)
from labpilot.experiments.project_types import ArchiveProject, CreateProject, ProjectDetails
from labpilot.observability.models import (
    AggregateEventCursorRow,
    OutboxRecordRow,
    RuntimeEventRow,
)


def _context() -> CommandContext:
    return CommandContext(
        installation_identity="installation-1",
        actor=CommandActor(actor_type="local_user", actor_id="air"),
        interaction_correlation_id="interaction-1",
    )


def _create_command(*, name: str = "Causal Diffusion") -> CreateProject:
    return CreateProject(
        name=name,
        description="Counterfactual image experiments",
        artifact_root_ref="artifact-root:projects/causal-diffusion",
    )


@asynccontextmanager
async def _project_database(
    isolated_database_url: str,
) -> AsyncIterator[tuple[Database, ProjectApplicationService]]:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        yield database, ProjectApplicationService(database.session)
    finally:
        await database.dispose()


async def _row_count(database: Database, row_type: type[object]) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(row_type)))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_project_commits_project_event_outbox_and_receipt_atomically(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        outcome = await service.create_project(
            context=_context(),
            idempotency_key="create-project-1",
            command=_create_command(),
        )

        project_id = UUID(outcome.envelope.primary_result_id)
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            event = (
                await session.execute(
                    select(RuntimeEventRow).where(
                        RuntimeEventRow.aggregate_type == "project",
                        RuntimeEventRow.aggregate_id == str(project_id),
                    )
                )
            ).scalar_one()
            outbox = (
                await session.execute(
                    select(OutboxRecordRow).where(
                        OutboxRecordRow.event_id == event.event_id
                    )
                )
            ).scalar_one()
            receipt = (
                await session.execute(
                    select(CommandReceiptRow).where(
                        CommandReceiptRow.idempotency_key == "create-project-1"
                    )
                )
            ).scalar_one()

        assert outcome.replayed is False
        assert outcome.envelope.outcome_kind == "project_created"
        assert outcome.envelope.primary_result_type == "project"
        assert outcome.envelope.primary_result_identity == project.project_identity
        assert outcome.envelope.related_result_refs == ()
        assert outcome.envelope.committed_state == "active"
        assert project.status == "active"
        assert project.row_version == 1
        assert project.name == "Causal Diffusion"
        assert project.description == "Counterfactual image experiments"
        assert (
            project.artifact_root_ref
            == "artifact-root:projects/causal-diffusion"
        )
        assert project.primary_repository_registration_id is None
        assert project.created_at is not None
        assert project.updated_at is not None
        assert event.aggregate_sequence == 1
        assert event.event_type == "project.created"
        assert event.event_schema_version == 1
        assert event.project_id == project_id
        assert event.payload_json == {
            "project_id": str(project_id),
            "project_identity": project.project_identity,
            "status": "active",
        }
        assert event.interaction_correlation_id == "interaction-1"
        assert event.actor_type == "local_user"
        assert event.actor_id == "air"
        assert outbox.status == "pending"
        assert outbox.topic == "runtime-events"
        assert outbox.partition_key == f"project:{project_id}"
        assert receipt.outcome_kind == "project_created"
        assert receipt.primary_result_id == str(project_id)
        assert receipt.result_envelope_json == outcome.envelope.to_json()
        assert await _row_count(database, ProjectRow) == 1
        assert await _row_count(database, RuntimeEventRow) == 1
        assert await _row_count(database, OutboxRecordRow) == 1
        assert await _row_count(database, CommandReceiptRow) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_project_event_validation_failure_rolls_back_everything(
    isolated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labpilot.experiments import project_service

    async with _project_database(isolated_database_url) as (database, service):
        def fail_event(*args: object, **kwargs: object) -> object:
            raise CanonicalizationError("forced event payload validation failure")

        monkeypatch.setattr(project_service, "build_project_created_event", fail_event)

        with pytest.raises(
            CanonicalizationError,
            match="forced event payload validation failure",
        ):
            await service.create_project(
                context=_context(),
                idempotency_key="create-project-event-failure",
                command=_create_command(),
            )

        assert await _row_count(database, ProjectRow) == 0
        assert await _row_count(database, RuntimeEventRow) == 0
        assert await _row_count(database, OutboxRecordRow) == 0
        assert await _row_count(database, CommandReceiptRow) == 0
        assert await _row_count(database, AggregateEventCursorRow) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_project_replays_before_operation_and_conflicts_on_new_body(
    isolated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labpilot.experiments import project_service

    generated_project_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    uuid_calls = 0

    def generate_project_id() -> UUID:
        nonlocal uuid_calls
        uuid_calls += 1
        return generated_project_id

    monkeypatch.setattr(project_service, "uuid4", generate_project_id)
    async with _project_database(isolated_database_url) as (database, service):
        first = await service.create_project(
            context=_context(),
            idempotency_key="create-project-replay",
            command=_create_command(),
        )
        replay = await service.create_project(
            context=_context(),
            idempotency_key="create-project-replay",
            command=_create_command(),
        )

        assert first.replayed is False
        assert replay.replayed is True
        assert replay.envelope == first.envelope
        assert replay.envelope.primary_result_id == str(generated_project_id)
        assert uuid_calls == 1

        with pytest.raises(IdempotencyKeyConflict):
            await service.create_project(
                context=_context(),
                idempotency_key="create-project-replay",
                command=_create_command(name="Different Project"),
            )

        assert uuid_calls == 1
        assert await _row_count(database, ProjectRow) == 1
        assert await _row_count(database, RuntimeEventRow) == 1
        assert await _row_count(database, OutboxRecordRow) == 1
        assert await _row_count(database, CommandReceiptRow) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_project_updates_version_and_commits_event_outbox_receipt(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        created = await service.create_project(
            context=_context(),
            idempotency_key="create-before-archive",
            command=_create_command(),
        )
        project_id = UUID(created.envelope.primary_result_id)
        archived = await service.archive_project(
            context=_context(),
            idempotency_key="archive-project-1",
            command=ArchiveProject(project_id=project_id, expected_row_version=1),
        )

        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            events = (
                await session.execute(
                    select(RuntimeEventRow)
                    .where(
                        RuntimeEventRow.aggregate_type == "project",
                        RuntimeEventRow.aggregate_id == str(project_id),
                    )
                    .order_by(RuntimeEventRow.aggregate_sequence)
                )
            ).scalars().all()
            archive_receipt = (
                await session.execute(
                    select(CommandReceiptRow).where(
                        CommandReceiptRow.idempotency_key == "archive-project-1"
                    )
                )
            ).scalar_one()

        assert archived.replayed is False
        assert archived.envelope.outcome_kind == "project_archived"
        assert archived.envelope.primary_result_type == "project"
        assert archived.envelope.primary_result_id == str(project_id)
        assert archived.envelope.primary_result_identity == project.project_identity
        assert archived.envelope.committed_state == "archived"
        assert project.status == "archived"
        assert project.row_version == 2
        assert project.updated_at > project.created_at
        assert [event.aggregate_sequence for event in events] == [1, 2]
        assert events[1].event_type == "project.archived"
        assert events[1].payload_json == {
            "project_id": str(project_id),
            "project_identity": project.project_identity,
            "previous_status": "active",
            "status": "archived",
            "row_version": 2,
        }
        assert archive_receipt.outcome_kind == "project_archived"
        assert archive_receipt.result_envelope_json == archived.envelope.to_json()
        assert await _row_count(database, ProjectRow) == 1
        assert await _row_count(database, RuntimeEventRow) == 2
        assert await _row_count(database, OutboxRecordRow) == 2
        assert await _row_count(database, CommandReceiptRow) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_project_conflicts_leave_project_and_side_effects_unchanged(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        created = await service.create_project(
            context=_context(),
            idempotency_key="create-for-conflicts",
            command=_create_command(),
        )
        project_id = UUID(created.envelope.primary_result_id)

        with pytest.raises(ProjectVersionConflict):
            await service.archive_project(
                context=_context(),
                idempotency_key="archive-wrong-version",
                command=ArchiveProject(project_id=project_id, expected_row_version=2),
            )

        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
        assert project.status == "active"
        assert project.row_version == 1
        assert await _row_count(database, RuntimeEventRow) == 1
        assert await _row_count(database, OutboxRecordRow) == 1
        assert await _row_count(database, CommandReceiptRow) == 1

        await service.archive_project(
            context=_context(),
            idempotency_key="archive-original-key",
            command=ArchiveProject(project_id=project_id, expected_row_version=1),
        )
        with pytest.raises(ProjectStateConflict):
            await service.archive_project(
                context=_context(),
                idempotency_key="archive-new-key",
                command=ArchiveProject(project_id=project_id, expected_row_version=2),
            )

        assert await _row_count(database, RuntimeEventRow) == 2
        assert await _row_count(database, OutboxRecordRow) == 2
        assert await _row_count(database, CommandReceiptRow) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_project_same_key_replays_and_create_receipt_stays_active(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        created = await service.create_project(
            context=_context(),
            idempotency_key="stable-create-receipt",
            command=_create_command(),
        )
        project_id = UUID(created.envelope.primary_result_id)
        first_archive = await service.archive_project(
            context=_context(),
            idempotency_key="stable-archive-receipt",
            command=ArchiveProject(project_id=project_id, expected_row_version=1),
        )
        replay_archive = await service.archive_project(
            context=_context(),
            idempotency_key="stable-archive-receipt",
            command=ArchiveProject(project_id=project_id, expected_row_version=1),
        )
        replay_create = await service.create_project(
            context=_context(),
            idempotency_key="stable-create-receipt",
            command=_create_command(),
        )

        assert replay_archive.replayed is True
        assert replay_archive.envelope == first_archive.envelope
        assert replay_create.replayed is True
        assert replay_create.envelope == created.envelope
        assert replay_create.envelope.committed_state == "active"
        assert await _row_count(database, ProjectRow) == 1
        assert await _row_count(database, RuntimeEventRow) == 2
        assert await _row_count(database, OutboxRecordRow) == 2
        assert await _row_count(database, CommandReceiptRow) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_project_unknown_id_raises_not_found_without_side_effects(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        with pytest.raises(ProjectNotFound):
            await service.archive_project(
                context=_context(),
                idempotency_key="archive-missing-project",
                command=ArchiveProject(
                    project_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                    expected_row_version=1,
                ),
            )

        assert await _row_count(database, ProjectRow) == 0
        assert await _row_count(database, RuntimeEventRow) == 0
        assert await _row_count(database, OutboxRecordRow) == 0
        assert await _row_count(database, CommandReceiptRow) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_project_returns_immutable_details_without_side_effects(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        created = await service.create_project(
            context=_context(),
            idempotency_key="create-before-get",
            command=_create_command(),
        )
        project_id = UUID(created.envelope.primary_result_id)
        async with database.session() as session:
            row = await session.get(ProjectRow, project_id)
            assert row is not None
            expected = ProjectDetails(
                project_id=row.project_id,
                project_identity=row.project_identity,
                name="Causal Diffusion",
                description="Counterfactual image experiments",
                status="active",
                primary_repository_registration_id=None,
                artifact_root_ref="artifact-root:projects/causal-diffusion",
                created_at=row.created_at,
                updated_at=row.updated_at,
                row_version=1,
            )
        counts_before = (
            await _row_count(database, CommandReceiptRow),
            await _row_count(database, RuntimeEventRow),
            await _row_count(database, OutboxRecordRow),
        )

        details = await service.get_project(project_id=project_id)

        assert details == expected
        assert (
            await _row_count(database, CommandReceiptRow),
            await _row_count(database, RuntimeEventRow),
            await _row_count(database, OutboxRecordRow),
        ) == counts_before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_project_unknown_id_raises_not_found(
    isolated_database_url: str,
) -> None:
    async with _project_database(isolated_database_url) as (database, service):
        with pytest.raises(ProjectNotFound):
            await service.get_project(
                project_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
            )

        assert await _row_count(database, ProjectRow) == 0
        assert await _row_count(database, CommandReceiptRow) == 0
        assert await _row_count(database, RuntimeEventRow) == 0
        assert await _row_count(database, OutboxRecordRow) == 0
