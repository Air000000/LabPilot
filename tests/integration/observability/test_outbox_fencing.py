from datetime import timedelta

import pytest
from sqlalchemy import func, select
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import OutboxRecordRow
from labpilot.observability.outbox import claim_outbox_records, mark_outbox_published


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_lease_uses_postgresql_clock(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("database-clock"),
            )
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            db_before = await uow.session.scalar(select(func.clock_timestamp()))
            claimed = (
                await claim_outbox_records(
                    uow,
                    owner="clock-worker",
                    limit=1,
                    lease_seconds=30,
                )
            )[0]
            assert db_before is not None
            assert claimed.leased_until is not None
            assert claimed.leased_until >= db_before + timedelta(seconds=30)
            await uow.commit()
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_outbox_lease_generation_cannot_publish(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            event = await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("fencing"),
            )
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            old_claim = (
                await claim_outbox_records(
                    uow,
                    owner="old-worker",
                    limit=1,
                    lease_seconds=0,
                )
            )[0]
            old_generation = old_claim.lease_generation
            outbox_id = old_claim.outbox_id
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            new_claim = (
                await claim_outbox_records(
                    uow,
                    owner="new-worker",
                    limit=1,
                    lease_seconds=30,
                )
            )[0]
            assert new_claim.lease_generation == old_generation + 1
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            published = await mark_outbox_published(
                uow,
                outbox_id=outbox_id,
                owner="old-worker",
                lease_generation=old_generation,
            )
            assert published is False
            await uow.commit()

        async with database.session() as session:
            row = await session.scalar(
                select(OutboxRecordRow).where(OutboxRecordRow.event_id == event.event_id)
            )
        assert row is not None
        assert row.status == "pending"
        assert row.lease_owner == "new-worker"
    finally:
        await database.dispose()
