from dataclasses import fields, is_dataclass
from pathlib import Path
from uuid import UUID

import pytest
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.context.models import RepositoryRegistrationRow
from labpilot.context.repository_repository import RepositoryRegistrationRepository
from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.experiments.models import ProjectRow


def test_repository_command_contract() -> None:
    from labpilot.context.repository_types import (
        CheckRepositoryAvailability,
        RegisterLocalRepository,
        RevokeRepositoryRegistration,
    )

    expected_fields = {
        RegisterLocalRepository: ("project_id", "user_path", "expected_project_row_version"),
        CheckRepositoryAvailability: ("repository_registration_id", "expected_row_version"),
        RevokeRepositoryRegistration: (
            "repository_registration_id",
            "expected_row_version",
            "expected_project_row_version",
        ),
    }
    for command_type, names in expected_fields.items():
        assert is_dataclass(command_type)
        assert command_type.__dataclass_params__.frozen
        assert command_type.__slots__ == names
        assert tuple(field.name for field in fields(command_type)) == names

    project_id = UUID("11111111-1111-4111-8111-111111111111")
    registration_id = UUID("22222222-2222-4222-8222-222222222222")
    with pytest.raises(TypeError):
        RegisterLocalRepository(
            project_id,
            Path("/repositories/project"),
            1,
            canonical_root_path="/derived",
        )
    with pytest.raises(TypeError):
        CheckRepositoryAvailability(
            registration_id,
            1,
            canonical_root_path="/derived",
        )
    with pytest.raises(TypeError):
        RevokeRepositoryRegistration(
            registration_id,
            1,
            1,
            canonical_root_path="/derived",
        )


def test_repository_event_contract() -> None:
    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.events import (
        build_repository_registration_availability_changed_event,
        build_repository_registration_created_event,
        build_repository_registration_revoked_event,
    )

    project_id = UUID("11111111-1111-4111-8111-111111111111")
    registration_id = UUID("22222222-2222-4222-8222-222222222222")
    context = CommandContext(
        installation_identity="installation",
        actor=CommandActor(actor_type="user", actor_id="user-1"),
        interaction_correlation_id="interaction-1",
    )
    events = (
        build_repository_registration_created_event(
            context=context,
            project_id=project_id,
            repository_registration_id=registration_id,
            root_path_identity="a" * 64,
            row_version=1,
        ),
        build_repository_registration_availability_changed_event(
            context=context,
            project_id=project_id,
            repository_registration_id=registration_id,
            old_status="active",
            new_status="unavailable",
            reason_code="repository_unavailable",
            row_version=2,
        ),
        build_repository_registration_revoked_event(
            context=context,
            project_id=project_id,
            repository_registration_id=registration_id,
            previous_status="active",
            root_path_identity="a" * 64,
            row_version=2,
        ),
    )

    assert tuple(event.event_type for event in events) == (
        "repository.registration.created",
        "repository.registration.availability_changed",
        "repository.registration.revoked",
    )
    for event in events:
        assert event.aggregate_type == "repository_registration"
        assert event.aggregate_id == str(registration_id)
        payload = event.payload.persisted_json()
        assert payload["project_id"] == str(project_id)
        assert payload["repository_registration_id"] == str(registration_id)
        assert not {
            "canonical_root_path",
            "user_path",
            "allowed_root",
            "remote_origin_sanitized",
            "remote_url",
        } & set(payload)


@pytest.mark.integration
async def test_repository_registration_repository_uses_caller_owned_uow_and_session(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    repository = RepositoryRegistrationRepository()
    project_id = UUID("11111111-1111-4111-8111-111111111111")
    registration_id = UUID("22222222-2222-4222-8222-222222222222")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="a" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.session.flush()
            row = RepositoryRegistrationRow(
                repository_registration_id=registration_id,
                project_id=project_id,
                repository_type="local_git",
                canonical_root_path="/workspace/repository",
                root_path_identity="b" * 64,
                remote_origin_sanitized=None,
                default_branch=None,
                status="active",
                last_access_check_at=None,
                row_version=1,
            )
            await repository.add_registration(uow, row)
            assert uow.session.in_transaction()
            await uow.commit()

        async with database.session() as session:
            loaded = await repository.get_registration(session, registration_id)
            assert loaded is not None
            assert loaded.repository_registration_id == registration_id
            assert (
                await repository.get_registration(
                    session,
                    UUID("33333333-3333-4333-8333-333333333333"),
                )
                is None
            )
            assert await repository.get_registration_for_update(session, registration_id)
            registrations = await repository.list_project_registrations(session, project_id)
            assert [registration.repository_registration_id for registration in registrations] == [
                registration_id
            ]
            assert registrations[0].root_path_identity == "b" * 64
    finally:
        await database.dispose()


async def _seed_binding_database(
    database: Database,
    *,
    project_id: UUID,
    registration_id: UUID,
    registration_project_id: UUID,
) -> None:
    async with UnitOfWork(database.session) as uow:
        uow.session.add(
            ProjectRow(
                project_id=project_id,
                project_identity="c" * 64,
                name="Project",
                description=None,
                status="active",
                primary_repository_registration_id=None,
                artifact_root_ref="artifact-root",
                row_version=1,
            )
        )
        if registration_project_id != project_id:
            uow.session.add(
                ProjectRow(
                    project_id=registration_project_id,
                    project_identity="d" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
        await uow.session.flush()
        uow.session.add(
            RepositoryRegistrationRow(
                repository_registration_id=registration_id,
                project_id=registration_project_id,
                repository_type="local_git",
                canonical_root_path="/workspace/repository",
                root_path_identity="e" * 64,
                remote_origin_sanitized=None,
                default_branch=None,
                status="active",
                last_access_check_at=None,
                row_version=1,
            )
        )
        await uow.session.flush()
        await uow.commit()


@pytest.mark.integration
async def test_set_primary_repository_binding_binds_same_project_registration(
    isolated_database_url: str,
) -> None:
    from labpilot.experiments.project_bindings import set_primary_repository_binding

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("10000000-0000-4000-8000-000000000001")
    registration_id = UUID("20000000-0000-4000-8000-000000000001")
    try:
        await _seed_binding_database(
            database,
            project_id=project_id,
            registration_id=registration_id,
            registration_project_id=project_id,
        )
        async with UnitOfWork(database.session) as uow:
            row = await set_primary_repository_binding(
                uow,
                project_id=project_id,
                repository_registration_id=registration_id,
                expected_project_row_version=1,
            )
            assert row.primary_repository_registration_id == registration_id
            assert row.row_version == 2
            assert row.updated_at > row.created_at
            await uow.commit()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_set_primary_repository_binding_rejects_stale_project_version(
    isolated_database_url: str,
) -> None:
    from labpilot.experiments.project_bindings import set_primary_repository_binding
    from labpilot.experiments.project_service import ProjectVersionConflict

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("10000000-0000-4000-8000-000000000002")
    registration_id = UUID("20000000-0000-4000-8000-000000000002")
    try:
        await _seed_binding_database(
            database,
            project_id=project_id,
            registration_id=registration_id,
            registration_project_id=project_id,
        )
        async with UnitOfWork(database.session) as uow:
            with pytest.raises(ProjectVersionConflict):
                await set_primary_repository_binding(
                    uow,
                    project_id=project_id,
                    repository_registration_id=registration_id,
                    expected_project_row_version=2,
                )
        async with database.session() as session:
            row = await session.get(ProjectRow, project_id)
            assert (
                row is not None
                and row.primary_repository_registration_id is None
                and row.row_version == 1
            )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_set_primary_repository_binding_rejects_cross_project_registration(
    isolated_database_url: str,
) -> None:
    from sqlalchemy.exc import IntegrityError

    from labpilot.experiments.project_bindings import set_primary_repository_binding

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("10000000-0000-4000-8000-000000000003")
    other_project_id = UUID("10000000-0000-4000-8000-000000000004")
    registration_id = UUID("20000000-0000-4000-8000-000000000003")
    try:
        await _seed_binding_database(
            database,
            project_id=project_id,
            registration_id=registration_id,
            registration_project_id=other_project_id,
        )
        with pytest.raises(IntegrityError):
            async with UnitOfWork(database.session) as uow:
                await set_primary_repository_binding(
                    uow,
                    project_id=project_id,
                    repository_registration_id=registration_id,
                    expected_project_row_version=1,
                )
        async with database.session() as session:
            row = await session.get(ProjectRow, project_id)
            assert (
                row is not None
                and row.primary_repository_registration_id is None
                and row.row_version == 1
            )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_set_primary_repository_binding_does_not_commit(isolated_database_url: str) -> None:
    from labpilot.db.uow import UnitOfWorkOutcome, UnitOfWorkState
    from labpilot.experiments.project_bindings import set_primary_repository_binding

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("10000000-0000-4000-8000-000000000005")
    registration_id = UUID("20000000-0000-4000-8000-000000000005")
    try:
        await _seed_binding_database(
            database,
            project_id=project_id,
            registration_id=registration_id,
            registration_project_id=project_id,
        )
        async with UnitOfWork(database.session) as uow:
            await set_primary_repository_binding(
                uow,
                project_id=project_id,
                repository_registration_id=registration_id,
                expected_project_row_version=1,
            )
            assert uow.state is UnitOfWorkState.ACTIVE
            assert uow.outcome is UnitOfWorkOutcome.NONE
            await uow.commit()
    finally:
        await database.dispose()


async def _seed_bound_project(database: Database, project_id: UUID, registration_id: UUID) -> None:
    from labpilot.experiments.project_bindings import set_primary_repository_binding

    await _seed_binding_database(
        database,
        project_id=project_id,
        registration_id=registration_id,
        registration_project_id=project_id,
    )
    async with UnitOfWork(database.session) as uow:
        await set_primary_repository_binding(
            uow,
            project_id=project_id,
            repository_registration_id=registration_id,
            expected_project_row_version=1,
        )
        await uow.commit()


@pytest.mark.integration
async def test_clear_primary_repository_binding_clears_expected_registration(
    isolated_database_url: str,
) -> None:
    from labpilot.experiments.project_bindings import clear_primary_repository_binding

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("30000000-0000-4000-8000-000000000001")
    registration_id = UUID("40000000-0000-4000-8000-000000000001")
    try:
        await _seed_bound_project(database, project_id, registration_id)
        async with UnitOfWork(database.session) as uow:
            row = await clear_primary_repository_binding(
                uow,
                project_id=project_id,
                expected_registration_id=registration_id,
                expected_project_row_version=2,
            )
            assert row.primary_repository_registration_id is None and row.row_version == 3
            await uow.commit()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_clear_primary_repository_binding_rejects_stale_project_version(
    isolated_database_url: str,
) -> None:
    from labpilot.experiments.project_bindings import clear_primary_repository_binding
    from labpilot.experiments.project_service import ProjectVersionConflict

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("30000000-0000-4000-8000-000000000002")
    registration_id = UUID("40000000-0000-4000-8000-000000000002")
    try:
        await _seed_bound_project(database, project_id, registration_id)
        async with UnitOfWork(database.session) as uow:
            with pytest.raises(ProjectVersionConflict):
                await clear_primary_repository_binding(
                    uow,
                    project_id=project_id,
                    expected_registration_id=registration_id,
                    expected_project_row_version=1,
                )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_clear_primary_repository_binding_rejects_unexpected_current_registration(
    isolated_database_url: str,
) -> None:
    from labpilot.experiments.project_bindings import clear_primary_repository_binding
    from labpilot.experiments.project_service import ProjectVersionConflict

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("30000000-0000-4000-8000-000000000003")
    registration_id = UUID("40000000-0000-4000-8000-000000000003")
    try:
        await _seed_bound_project(database, project_id, registration_id)
        async with UnitOfWork(database.session) as uow:
            with pytest.raises(ProjectVersionConflict):
                await clear_primary_repository_binding(
                    uow,
                    project_id=project_id,
                    expected_registration_id=UUID("40000000-0000-4000-8000-000000000004"),
                    expected_project_row_version=2,
                )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_clear_primary_repository_binding_does_not_commit(isolated_database_url: str) -> None:
    from labpilot.db.uow import UnitOfWorkOutcome, UnitOfWorkState
    from labpilot.experiments.project_bindings import clear_primary_repository_binding

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("30000000-0000-4000-8000-000000000005")
    registration_id = UUID("40000000-0000-4000-8000-000000000005")
    try:
        await _seed_bound_project(database, project_id, registration_id)
        async with UnitOfWork(database.session) as uow:
            row = await clear_primary_repository_binding(
                uow,
                project_id=project_id,
                expected_registration_id=registration_id,
                expected_project_row_version=2,
            )
            assert row.primary_repository_registration_id is None and row.row_version == 3
            assert uow.state is UnitOfWorkState.ACTIVE and uow.outcome is UnitOfWorkOutcome.NONE
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_register_local_repository_rejects_relative_path_without_side_effects(
    isolated_database_url: str,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import (
        RegisterLocalRepository,
        RepositoryPathMustBeAbsolute,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class NeverProbeGitAdapter:
        async def probe(self, repository_root: Path) -> object:
            raise AssertionError(f"probe must not run for {repository_root}")

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("44444444-4444-4444-8444-444444444444")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="f" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()

        service = RepositoryApplicationService(
            database.session,
            git_adapter=NeverProbeGitAdapter(),
            allowed_roots=(Path("/repositories"),),
        )
        with pytest.raises(RepositoryPathMustBeAbsolute):
            await service.register_local_repository(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user-1"),
                    interaction_correlation_id="interaction-1",
                ),
                idempotency_key="register-relative",
                command=RegisterLocalRepository(
                    project_id=project_id,
                    user_path=Path("relative/repository"),
                    expected_project_row_version=1,
                ),
            )

        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            assert project is not None
            assert project.primary_repository_registration_id is None
            assert project.row_version == 1
            registration_count = await session.scalar(
                select(func.count()).select_from(RepositoryRegistrationRow)
            )
            event_count = await session.scalar(select(func.count()).select_from(RuntimeEventRow))
            outbox_count = await session.scalar(select(func.count()).select_from(OutboxRecordRow))
            receipt_count = await session.scalar(
                select(func.count()).select_from(CommandReceiptRow)
            )
            assert registration_count == event_count == outbox_count == receipt_count == 0
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_register_local_repository_rejects_absolute_path_outside_allowed_root(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import (
        RegisterLocalRepository,
        RepositoryPathOutsideAllowedRoot,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class NeverProbeGitAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def probe(self, repository_root: Path) -> object:
            self.calls += 1
            raise AssertionError(f"probe must not run for {repository_root}")

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("55555555-5555-4555-8555-555555555555")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="e" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()
        git_adapter = NeverProbeGitAdapter()
        service = RepositoryApplicationService(
            database.session,
            git_adapter=git_adapter,
            allowed_roots=(allowed,),
        )
        with pytest.raises(RepositoryPathOutsideAllowedRoot):
            await service.register_local_repository(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user-1"),
                    interaction_correlation_id="interaction-1",
                ),
                idempotency_key="register-outside",
                command=RegisterLocalRepository(project_id, outside, 1),
            )
        assert git_adapter.calls == 0
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            assert project is not None
            assert project.primary_repository_registration_id is None
            assert project.row_version == 1
            assert (
                await session.scalar(select(func.count()).select_from(RepositoryRegistrationRow))
                == 0
            )
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 0
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 0
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 0
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_register_local_repository_rejects_symlink_escape_without_side_effects(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import (
        RegisterLocalRepository,
        RepositorySymlinkEscape,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class NeverProbeGitAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def probe(self, repository_root: Path) -> object:
            self.calls += 1
            raise AssertionError(f"probe must not run for {repository_root}")

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    link = allowed / "escaped-link"
    allowed.mkdir()
    outside.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    assert link.is_symlink()
    assert link.is_absolute()
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("66666666-6666-4666-8666-666666666666")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="d" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()
        git_adapter = NeverProbeGitAdapter()
        service = RepositoryApplicationService(
            database.session,
            git_adapter=git_adapter,
            allowed_roots=(allowed.resolve(),),
        )
        with pytest.raises(RepositorySymlinkEscape):
            await service.register_local_repository(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user-1"),
                    interaction_correlation_id="interaction-1",
                ),
                idempotency_key="register-symlink-escape",
                command=RegisterLocalRepository(project_id, link, 1),
            )
        assert git_adapter.calls == 0
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            assert project is not None
            assert project.primary_repository_registration_id is None
            assert project.row_version == 1
            assert (
                await session.scalar(select(func.count()).select_from(RepositoryRegistrationRow))
                == 0
            )
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 0
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 0
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 0
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_register_local_repository_rejects_non_git_directory_without_side_effects(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import RegisterLocalRepository, RepositoryNotGit
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class NonGitAdapter:
        def __init__(self) -> None:
            self.paths: list[Path] = []

        async def probe(self, repository_root: Path) -> object:
            self.paths.append(repository_root)
            raise RepositoryNotGit(repository_root)

    allowed = tmp_path / "allowed"
    user_path = allowed / "ordinary-directory"
    user_path.mkdir(parents=True)
    assert user_path.is_absolute() and user_path.is_dir() and not user_path.is_symlink()
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("77777777-7777-4777-8777-777777777777")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="c" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()
        adapter = NonGitAdapter()
        service = RepositoryApplicationService(
            database.session, git_adapter=adapter, allowed_roots=(allowed.resolve(),)
        )
        with pytest.raises(RepositoryNotGit):
            await service.register_local_repository(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user-1"),
                    interaction_correlation_id="interaction-1",
                ),
                idempotency_key="register-non-git",
                command=RegisterLocalRepository(project_id, user_path, 1),
            )
        assert adapter.paths == [user_path.resolve()]
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            assert (
                project is not None
                and project.primary_repository_registration_id is None
                and project.row_version == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(RepositoryRegistrationRow))
                == 0
            )
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 0
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 0
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 0
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_register_local_repository_replays_existing_receipt_before_prepare(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import RegisterLocalRepository
    from labpilot.db.idempotency import (
        CommandReceiptRepository,
        CommandResultEnvelope,
        prepare_command_identity,
        prepare_project_scope,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class NeverProbeGitAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def probe(self, repository_root: Path) -> object:
            self.calls += 1
            raise AssertionError("probe must not run on replay")

    allowed = tmp_path / "allowed"
    repository = allowed / "repository"
    repository.mkdir(parents=True)
    context = CommandContext("installation", CommandActor("user", "user-1"), "interaction-1")
    project_id = UUID("88888888-8888-4888-8888-888888888888")
    command = RegisterLocalRepository(project_id, repository, 1)
    scope = prepare_project_scope(context, project_id=project_id)
    identity = prepare_command_identity(
        scope_identity=scope,
        command_name="RegisterLocalRepository",
        command_version=1,
        idempotency_key="register-replay",
        normalized_request={
            "authority_scope_identity": scope,
            "command_name": "RegisterLocalRepository",
            "command_version": 1,
            "expected_project_row_version": 1,
            "project_id": str(project_id),
            "user_path": str(repository),
        },
    )
    envelope = CommandResultEnvelope(
        "repository_registration_reused",
        "repository_registration",
        "99999999-9999-4999-8999-999999999999",
        None,
        (),
        "active",
    )
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="b" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await CommandReceiptRepository().add(uow, identity=identity, envelope=envelope)
            await uow.commit()
        repository.rmdir()
        adapter = NeverProbeGitAdapter()
        outcome = await RepositoryApplicationService(
            database.session, git_adapter=adapter, allowed_roots=(allowed.resolve(),)
        ).register_local_repository(
            context=context, idempotency_key="register-replay", command=command
        )
        assert outcome.replayed and outcome.envelope == envelope and adapter.calls == 0
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            assert (
                project is not None
                and project.primary_repository_registration_id is None
                and project.row_version == 1
            )
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(RepositoryRegistrationRow))
                == 0
            )
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 0
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 0
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_register_local_repository_creates_registration_and_primary_binding(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import RegisterLocalRepository, RepositoryProbe
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class ProbeGitAdapter:
        def __init__(self) -> None:
            self.paths: list[Path] = []

        async def probe(self, repository_root: Path) -> RepositoryProbe:
            self.paths.append(repository_root)
            return RepositoryProbe(
                canonical_root_path=str(repository_root),
                head_commit="a" * 40,
                current_branch="main",
                is_detached_head=False,
                tracked_tree_identity="b" * 40,
                status_entries=(),
                remote_origin_sanitized="https://example.invalid/repository.git",
                default_branch="main",
                observation_profile_version="wp1-a-git-observation:v1",
            )

    allowed = tmp_path / "allowed"
    repository = allowed / "repository"
    repository.mkdir(parents=True)
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("99999999-9999-4999-8999-999999999991")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="a" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()

        adapter = ProbeGitAdapter()
        outcome = await RepositoryApplicationService(
            database.session,
            git_adapter=adapter,
            allowed_roots=(allowed.resolve(),),
        ).register_local_repository(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user-1"),
                interaction_correlation_id="interaction-1",
            ),
            idempotency_key="register-created",
            command=RegisterLocalRepository(project_id, repository, 1),
        )

        assert not outcome.replayed
        assert outcome.envelope.outcome_kind == "repository_registration_created"
        assert adapter.paths == [repository.resolve()]
        async with database.session() as session:
            registration = await session.get(
                RepositoryRegistrationRow,
                UUID(outcome.envelope.primary_result_id),
            )
            project = await session.get(ProjectRow, project_id)
            assert registration is not None
            assert registration.canonical_root_path == str(repository.resolve())
            assert registration.repository_type == "local_git"
            assert registration.status == "active" and registration.row_version == 1
            assert registration.last_access_check_at is not None
            assert project is not None
            assert (
                project.primary_repository_registration_id
                == registration.repository_registration_id
            )
            assert project.row_version == 2
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 1
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 1
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_check_repository_availability_marks_unavailable_and_writes_event(
    isolated_database_url: str,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import (
        CheckRepositoryAvailability,
        RepositoryUnavailable,
    )
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class UnavailableGitAdapter:
        async def probe(self, repository_root: Path) -> object:
            raise RepositoryUnavailable(repository_root)

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    registration_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    try:
        await _seed_bound_project(database, project_id, registration_id)
        outcome = await RepositoryApplicationService(
            database.session,
            git_adapter=UnavailableGitAdapter(),
            allowed_roots=(),
        ).check_repository_availability(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user-1"),
                interaction_correlation_id="interaction-1",
            ),
            idempotency_key="availability-unavailable",
            command=CheckRepositoryAvailability(registration_id, 1),
        )
        assert outcome.envelope.committed_state == "unavailable"

        class NeverProbeGitAdapter:
            def __init__(self) -> None:
                self.calls = 0

            async def probe(self, repository_root: Path) -> object:
                self.calls += 1
                raise AssertionError("replay must not probe")

        replay_adapter = NeverProbeGitAdapter()
        replay = await RepositoryApplicationService(
            database.session,
            git_adapter=replay_adapter,
            allowed_roots=(),
        ).check_repository_availability(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user-1"),
                interaction_correlation_id="interaction-1",
            ),
            idempotency_key="availability-unavailable",
            command=CheckRepositoryAvailability(registration_id, 1),
        )
        assert replay.replayed and replay.envelope == outcome.envelope

        class AvailableGitAdapter:
            async def probe(self, repository_root: Path) -> object:
                return object()

        active = await RepositoryApplicationService(
            database.session,
            git_adapter=AvailableGitAdapter(),
            allowed_roots=(),
        ).check_repository_availability(
            context=CommandContext("installation", CommandActor("user", "user-1"), "interaction-1"),
            idempotency_key="availability-active",
            command=CheckRepositoryAvailability(registration_id, 2),
        )
        assert active.envelope.committed_state == "active"

        class InvalidGitAdapter:
            async def probe(self, repository_root: Path) -> object:
                from labpilot.context.repository_types import RepositoryNotGit

                raise RepositoryNotGit(repository_root)

        invalid = await RepositoryApplicationService(
            database.session,
            git_adapter=InvalidGitAdapter(),
            allowed_roots=(),
        ).check_repository_availability(
            context=CommandContext("installation", CommandActor("user", "user-1"), "interaction-1"),
            idempotency_key="availability-invalid",
            command=CheckRepositoryAvailability(registration_id, 3),
        )
        assert invalid.envelope.committed_state == "invalid_git_repository"
        async with database.session() as session:
            registration = await session.get(RepositoryRegistrationRow, registration_id)
            project = await session.get(ProjectRow, project_id)
            assert registration is not None
            assert registration.status == "invalid_git_repository" and registration.row_version == 4
            assert project is not None
            assert project.primary_repository_registration_id == registration_id
            assert project.row_version == 2
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 3
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 3
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_revoke_repository_registration_clears_primary_and_writes_event(
    isolated_database_url: str,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import RevokeRepositoryRegistration
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    registration_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    try:
        await _seed_bound_project(database, project_id, registration_id)
        outcome = await RepositoryApplicationService(
            database.session,
            git_adapter=object(),
            allowed_roots=(),
        ).revoke_repository_registration(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user-1"),
                interaction_correlation_id="interaction-1",
            ),
            idempotency_key="revoke-primary",
            command=RevokeRepositoryRegistration(registration_id, 1, 2),
        )
        assert outcome.envelope.committed_state == "revoked"
        async with database.session() as session:
            registration = await session.get(RepositoryRegistrationRow, registration_id)
            project = await session.get(ProjectRow, project_id)
            assert registration is not None
            assert registration.status == "revoked" and registration.row_version == 2
            assert project is not None
            assert project.primary_repository_registration_id is None and project.row_version == 3
        replay = await RepositoryApplicationService(
            database.session,
            git_adapter=object(),
            allowed_roots=(),
        ).revoke_repository_registration(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user-1"),
                interaction_correlation_id="interaction-1",
            ),
            idempotency_key="revoke-primary",
            command=RevokeRepositoryRegistration(registration_id, 1, 2),
        )
        assert replay.replayed and replay.envelope == outcome.envelope
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 1
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_revoke_non_primary_registration_keeps_primary_pointer(
    isolated_database_url: str,
) -> None:
    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import RevokeRepositoryRegistration

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    primary_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    secondary_id = UUID("11111111-1111-4111-8111-111111111112")
    try:
        await _seed_bound_project(database, project_id, primary_id)
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                RepositoryRegistrationRow(
                    repository_registration_id=secondary_id,
                    project_id=project_id,
                    repository_type="local_git",
                    canonical_root_path="/workspace/secondary",
                    root_path_identity="f" * 64,
                    remote_origin_sanitized=None,
                    default_branch=None,
                    status="active",
                    last_access_check_at=None,
                    row_version=1,
                )
            )
            await uow.commit()
        outcome = await RepositoryApplicationService(
            database.session,
            git_adapter=object(),
            allowed_roots=(),
        ).revoke_repository_registration(
            context=CommandContext("installation", CommandActor("user", "user-1"), "interaction-1"),
            idempotency_key="revoke-secondary",
            command=RevokeRepositoryRegistration(secondary_id, 1, 2),
        )
        assert outcome.envelope.committed_state == "revoked"
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            secondary = await session.get(RepositoryRegistrationRow, secondary_id)
            assert project is not None
            assert project.primary_repository_registration_id == primary_id
            assert project.row_version == 2
            assert secondary is not None
            assert secondary.status == "revoked" and secondary.row_version == 2
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_reregister_same_root_after_revoke_creates_new_registration(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import (
        RegisterLocalRepository,
        RepositoryProbe,
        RevokeRepositoryRegistration,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class ProbeGitAdapter:
        async def probe(self, root: Path) -> RepositoryProbe:
            return RepositoryProbe(
                str(root),
                "a" * 40,
                "main",
                False,
                "b" * 40,
                (),
                None,
                "main",
                "wp1-a-git-observation:v1",
            )

    allowed = tmp_path / "allowed"
    root = allowed / "repository"
    root.mkdir(parents=True)
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("22222222-2222-4222-8222-222222222222")
    context = CommandContext("installation", CommandActor("user", "user-1"), "interaction-1")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="a" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()
        service = RepositoryApplicationService(
            database.session, git_adapter=ProbeGitAdapter(), allowed_roots=(allowed.resolve(),)
        )
        first = await service.register_local_repository(
            context=context,
            idempotency_key="register-a",
            command=RegisterLocalRepository(project_id, root, 1),
        )
        old_id = UUID(first.envelope.primary_result_id)
        await service.revoke_repository_registration(
            context=context,
            idempotency_key="revoke-a",
            command=RevokeRepositoryRegistration(old_id, 1, 2),
        )
        second = await service.register_local_repository(
            context=context,
            idempotency_key="register-b",
            command=RegisterLocalRepository(project_id, root, 3),
        )
        new_id = UUID(second.envelope.primary_result_id)
        assert (
            second.envelope.outcome_kind == "repository_registration_created" and new_id != old_id
        )
        async with database.session() as session:
            old = await session.get(RepositoryRegistrationRow, old_id)
            new = await session.get(RepositoryRegistrationRow, new_id)
            project = await session.get(ProjectRow, project_id)
            assert old is not None and old.status == "revoked"
            assert (
                new is not None
                and new.status == "active"
                and new.canonical_root_path == str(root.resolve())
                and new.row_version == 1
            )
            assert (
                project is not None
                and project.primary_repository_registration_id == new_id
                and project.row_version == 4
            )
            assert await session.scalar(select(func.count()).select_from(RuntimeEventRow)) == 3
            assert await session.scalar(select(func.count()).select_from(OutboxRecordRow)) == 3
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 3
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_concurrent_register_local_repository_has_one_committed_winner(
    isolated_database_url: str, tmp_path: Path
) -> None:
    import asyncio

    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.context.repository_types import RegisterLocalRepository, RepositoryProbe
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    class GateGitAdapter:
        def __init__(self) -> None:
            self.calls = 0
            self.release = asyncio.Event()

        async def probe(self, root: Path) -> RepositoryProbe:
            self.calls += 1
            if self.calls == 2:
                self.release.set()
            else:
                await self.release.wait()
            return RepositoryProbe(
                str(root),
                "a" * 40,
                "main",
                False,
                "b" * 40,
                (),
                None,
                "main",
                "wp1-a-git-observation:v1",
            )

    allowed = tmp_path / "allowed"
    root = allowed / "repository"
    root.mkdir(parents=True)
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("44444444-4444-4444-8444-444444444444")
    context = CommandContext("installation", CommandActor("user", "user-1"), "interaction-1")
    try:
        async with UnitOfWork(database.session) as uow:
            uow.session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="a" * 64,
                    name="Project",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                    row_version=1,
                )
            )
            await uow.commit()
        adapter = GateGitAdapter()
        service = RepositoryApplicationService(
            database.session, git_adapter=adapter, allowed_roots=(allowed.resolve(),)
        )
        results = await asyncio.gather(
            service.register_local_repository(
                context=context,
                idempotency_key="concurrent-a",
                command=RegisterLocalRepository(project_id, root, 1),
            ),
            service.register_local_repository(
                context=context,
                idempotency_key="concurrent-b",
                command=RegisterLocalRepository(project_id, root, 1),
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, BaseException) for result in results) == 1
        assert sum(isinstance(result, BaseException) for result in results) == 1
        assert adapter.calls == 2
        async with database.session() as session:
            project = await session.get(ProjectRow, project_id)
            registrations = await session.scalar(
                select(func.count()).select_from(RepositoryRegistrationRow)
            )
            events = await session.scalar(select(func.count()).select_from(RuntimeEventRow))
            outbox = await session.scalar(select(func.count()).select_from(OutboxRecordRow))
            receipts = await session.scalar(select(func.count()).select_from(CommandReceiptRow))
            assert (
                project is not None
                and project.primary_repository_registration_id is not None
                and project.row_version == 2
            )
            assert registrations == events == outbox == receipts == 1
    finally:
        await database.dispose()
