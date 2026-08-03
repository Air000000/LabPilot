from uuid import UUID

import pytest
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.context.models import RepositoryRegistrationRow
from labpilot.context.repository_repository import RepositoryRegistrationRepository
from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.experiments.models import ProjectRow


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
