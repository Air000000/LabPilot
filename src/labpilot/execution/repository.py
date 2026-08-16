from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.db.uow import UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.types import (
    AttemptStatus,
    attempt_workspace_path,
    supervisor_execution_identity,
)


def build_preparing_attempt(
    *,
    attempt_id: UUID,
    trial_id: UUID,
    attempt_number: int,
    installation_identity: str,
    attempt_workspace_root: Path,
    created_at: datetime,
) -> AttemptRow:
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    return AttemptRow(
        attempt_id=attempt_id,
        trial_id=trial_id,
        attempt_number=attempt_number,
        status=AttemptStatus.PREPARING.value,
        supervisor_execution_identity=supervisor_execution_identity(
            installation_identity=installation_identity,
            attempt_id=attempt_id,
        ),
        workspace_path=attempt_workspace_path(attempt_workspace_root, attempt_id),
        frozen_execution_spec_json=None,
        frozen_execution_spec_hash=None,
        source_manifest_json=None,
        source_manifest_sha256=None,
        preparation_failure_code=None,
        prepared_at=None,
        terminal_at=None,
        created_at=created_at,
    )


class ExecutionRepository:
    async def get_batch(
        self, session: AsyncSession, batch_id: UUID
    ) -> BatchRow | None:
        return await session.get(BatchRow, batch_id)

    async def get_batch_for_update(
        self, session: AsyncSession, batch_id: UUID
    ) -> BatchRow | None:
        result = await session.execute(
            select(BatchRow).where(BatchRow.batch_id == batch_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_trial(
        self, session: AsyncSession, trial_id: UUID
    ) -> TrialRow | None:
        return await session.get(TrialRow, trial_id)

    async def get_attempt(
        self, session: AsyncSession, attempt_id: UUID
    ) -> AttemptRow | None:
        return await session.get(AttemptRow, attempt_id)

    async def get_attempt_for_update(
        self, session: AsyncSession, attempt_id: UUID
    ) -> AttemptRow | None:
        result = await session.execute(
            select(AttemptRow)
            .where(AttemptRow.attempt_id == attempt_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_batch(self, uow: UnitOfWork, row: BatchRow) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def add_trial(self, uow: UnitOfWork, row: TrialRow) -> None:
        uow.session.add(row)
        await uow.session.flush()

    async def add_attempt(self, uow: UnitOfWork, row: AttemptRow) -> None:
        uow.session.add(row)
        await uow.session.flush()
