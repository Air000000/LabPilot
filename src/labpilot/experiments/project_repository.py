from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.db.uow import UnitOfWork
from labpilot.experiments.models import ProjectRow


class ProjectRepository:
    async def get(
        self,
        session: AsyncSession,
        project_id: UUID,
    ) -> ProjectRow | None:
        return await session.get(ProjectRow, project_id)

    async def get_for_update(
        self,
        session: AsyncSession,
        project_id: UUID,
    ) -> ProjectRow | None:
        result = await session.execute(
            select(ProjectRow)
            .where(ProjectRow.project_id == project_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(
        self,
        uow: UnitOfWork,
        row: ProjectRow,
    ) -> None:
        uow.session.add(row)
        await uow.session.flush()
