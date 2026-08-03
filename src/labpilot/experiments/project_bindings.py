from uuid import UUID

from sqlalchemy import func, select

from labpilot.db.uow import UnitOfWork
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.project_repository import ProjectRepository
from labpilot.experiments.project_service import ProjectNotFound, ProjectVersionConflict


async def set_primary_repository_binding(
    uow: UnitOfWork,
    *,
    project_id: UUID,
    repository_registration_id: UUID,
    expected_project_row_version: int,
) -> ProjectRow:
    row = await ProjectRepository().get_for_update(uow.session, project_id)
    if row is None:
        raise ProjectNotFound(project_id)
    if row.row_version != expected_project_row_version:
        raise ProjectVersionConflict(
            project_id,
            expected_row_version=expected_project_row_version,
            current_row_version=row.row_version,
        )
    now = await uow.session.scalar(select(func.clock_timestamp()))
    assert now is not None
    row.primary_repository_registration_id = repository_registration_id
    row.row_version += 1
    row.updated_at = now
    await uow.session.flush()
    return row


async def clear_primary_repository_binding(
    uow: UnitOfWork,
    *,
    project_id: UUID,
    expected_registration_id: UUID,
    expected_project_row_version: int,
) -> ProjectRow:
    row = await ProjectRepository().get_for_update(uow.session, project_id)
    if row is None:
        raise ProjectNotFound(project_id)
    if row.row_version != expected_project_row_version:
        raise ProjectVersionConflict(
            project_id,
            expected_row_version=expected_project_row_version,
            current_row_version=row.row_version,
        )
    if row.primary_repository_registration_id != expected_registration_id:
        raise ProjectVersionConflict(
            project_id,
            expected_row_version=expected_project_row_version,
            current_row_version=row.row_version,
        )
    now = await uow.session.scalar(select(func.clock_timestamp()))
    assert now is not None
    row.primary_repository_registration_id = None
    row.row_version += 1
    row.updated_at = now
    await uow.session.flush()
    return row
