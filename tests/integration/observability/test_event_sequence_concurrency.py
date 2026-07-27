import asyncio

import pytest
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event


async def append_one(database: Database, aggregate_id: str) -> int:
    async with UnitOfWork(database.session) as uow:
        event = await append_runtime_event(
            uow,
            RuntimeEventEnvelope.for_test(aggregate_id),
        )
        sequence = event.aggregate_sequence
        await uow.commit()
        return sequence


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_sequence_allocation_is_dense_and_unique(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        sequences = await asyncio.gather(
            *(append_one(database, "shared") for _ in range(20))
        )
        assert sorted(sequences) == list(range(1, 21))
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rolled_back_sequence_is_not_permanently_consumed(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            first = await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("rollback-sequence"),
            )
            assert first.aggregate_sequence == 1

        committed = await append_one(database, "rollback-sequence")
        assert committed == 1
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_different_aggregates_have_independent_sequences(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        assert await append_one(database, "aggregate-a") == 1
        assert await append_one(database, "aggregate-b") == 1
        assert await append_one(database, "aggregate-a") == 2
    finally:
        await database.dispose()
