from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.context.models import RepositoryRegistrationRow
from labpilot.db.uow import UnitOfWork


class RepositoryRegistrationRepository:
    async def get_registration(
        self,
        session: AsyncSession,
        repository_registration_id: UUID,
    ) -> RepositoryRegistrationRow | None:
        return await session.get(RepositoryRegistrationRow, repository_registration_id)

    async def get_registration_for_update(
        self,
        session: AsyncSession,
        repository_registration_id: UUID,
    ) -> RepositoryRegistrationRow | None:
        result = await session.execute(
            select(RepositoryRegistrationRow)
            .where(
                RepositoryRegistrationRow.repository_registration_id
                == repository_registration_id
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_registration(
        self,
        uow: UnitOfWork,
        row: RepositoryRegistrationRow,
    ) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def list_project_registrations(
        self,
        session: AsyncSession,
        project_id: UUID,
    ) -> list[RepositoryRegistrationRow]:
        result = await session.execute(
            select(RepositoryRegistrationRow)
            .where(RepositoryRegistrationRow.project_id == project_id)
            .order_by(RepositoryRegistrationRow.repository_registration_id)
        )
        return list(result.scalars())
