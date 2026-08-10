from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.context.models import (
    RepositoryRegistrationRow,
    RepositorySnapshotFreshnessRow,
    RepositorySnapshotInspectionBundleEntryRow,
    RepositorySnapshotInspectionBundleRow,
    RepositorySnapshotRow,
)
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
                RepositoryRegistrationRow.repository_registration_id == repository_registration_id
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


class RepositorySnapshotRepository:
    async def get_by_identity(
        self,
        session: AsyncSession,
        registration_id: UUID,
        snapshot_identity: str,
    ) -> RepositorySnapshotRow | None:
        result = await session.execute(
            select(RepositorySnapshotRow).where(
                RepositorySnapshotRow.repository_registration_id == registration_id,
                RepositorySnapshotRow.repository_snapshot_identity == snapshot_identity,
            )
        )
        return result.scalar_one_or_none()

    async def get_bundle_by_snapshot(
        self,
        session: AsyncSession,
        repository_snapshot_id: UUID,
    ) -> RepositorySnapshotInspectionBundleRow | None:
        result = await session.execute(
            select(RepositorySnapshotInspectionBundleRow).where(
                RepositorySnapshotInspectionBundleRow.repository_snapshot_id
                == repository_snapshot_id
            )
        )
        return result.scalar_one_or_none()

    async def list_bundle_entries(
        self,
        session: AsyncSession,
        inspection_bundle_id: UUID,
    ) -> list[RepositorySnapshotInspectionBundleEntryRow]:
        result = await session.execute(
            select(RepositorySnapshotInspectionBundleEntryRow)
            .where(
                RepositorySnapshotInspectionBundleEntryRow.inspection_bundle_id
                == inspection_bundle_id
            )
            .order_by(RepositorySnapshotInspectionBundleEntryRow.stable_ordinal)
        )
        return list(result.scalars())

    async def next_sequence(self, session: AsyncSession, registration_id: UUID) -> int:
        from sqlalchemy import func

        value = await session.scalar(
            select(func.coalesce(func.max(RepositorySnapshotRow.snapshot_sequence), 0)).where(
                RepositorySnapshotRow.repository_registration_id == registration_id
            )
        )
        if value is None:
            raise RuntimeError("snapshot sequence aggregation returned no value")
        return value + 1

    async def add_snapshot(self, uow: UnitOfWork, row: RepositorySnapshotRow) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def add_bundle(
        self,
        uow: UnitOfWork,
        bundle: RepositorySnapshotInspectionBundleRow,
        entries: tuple[RepositorySnapshotInspectionBundleEntryRow, ...],
    ) -> None:
        uow.session.add(bundle)
        await uow.session.flush()
        uow.session.add_all(entries)
        await uow.session.flush()

    async def add_freshness(self, uow: UnitOfWork, row: RepositorySnapshotFreshnessRow) -> None:
        uow.session.add(row)
        await uow.session.flush()
