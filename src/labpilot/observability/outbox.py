from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from labpilot.db.uow import UnitOfWork
from labpilot.observability.models import OutboxRecordRow


async def claim_outbox_records(
    uow: UnitOfWork,
    *,
    owner: str,
    limit: int,
    lease_seconds: int,
) -> list[OutboxRecordRow]:
    db_now = await uow.session.scalar(select(func.clock_timestamp()))
    assert db_now is not None
    rows = list(
        (
            await uow.session.scalars(
                select(OutboxRecordRow)
                .where(
                    OutboxRecordRow.status == "pending",
                    OutboxRecordRow.available_at <= db_now,
                    (
                        OutboxRecordRow.leased_until.is_(None)
                        | (OutboxRecordRow.leased_until <= db_now)
                    ),
                )
                .order_by(OutboxRecordRow.available_at, OutboxRecordRow.outbox_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        row.lease_owner = owner
        row.lease_generation += 1
        row.leased_until = db_now + timedelta(seconds=lease_seconds)
        row.attempt_count += 1
    await uow.session.flush()
    return rows


async def mark_outbox_published(
    uow: UnitOfWork,
    *,
    outbox_id: UUID,
    owner: str,
    lease_generation: int,
) -> bool:
    db_now = await uow.session.scalar(select(func.clock_timestamp()))
    assert db_now is not None
    result = await uow.session.execute(
        update(OutboxRecordRow)
        .where(
            OutboxRecordRow.outbox_id == outbox_id,
            OutboxRecordRow.status == "pending",
            OutboxRecordRow.lease_owner == owner,
            OutboxRecordRow.lease_generation == lease_generation,
            OutboxRecordRow.leased_until > db_now,
        )
        .values(status="published", published_at=db_now)
    )
    return cast(CursorResult[Any], result).rowcount == 1
