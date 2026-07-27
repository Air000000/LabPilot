import pytest
from sqlalchemy import text

from labpilot.db.session import Database


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgresql_skip_locked_returns_the_next_unlocked_row(
    isolated_database_url: str,
) -> None:
    database = Database(isolated_database_url)
    try:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE capability_jobs ("
                    "id integer PRIMARY KEY, status text NOT NULL)"
                )
            )
            await connection.execute(
                text("INSERT INTO capability_jobs VALUES (1, 'pending'), (2, 'pending')")
            )

        connection_a = await database.engine.connect()
        connection_b = await database.engine.connect()
        transaction_a = await connection_a.begin()
        transaction_b = await connection_b.begin()
        try:
            locked = await connection_a.scalar(
                text(
                    "SELECT id FROM capability_jobs "
                    "WHERE status = 'pending' ORDER BY id "
                    "FOR UPDATE LIMIT 1"
                )
            )
            skipped = await connection_b.scalar(
                text(
                    "SELECT id FROM capability_jobs "
                    "WHERE status = 'pending' ORDER BY id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            )
            assert locked == 1
            assert skipped == 2
        finally:
            await transaction_b.rollback()
            await transaction_a.rollback()
            await connection_b.close()
            await connection_a.close()
    finally:
        await database.dispose()
