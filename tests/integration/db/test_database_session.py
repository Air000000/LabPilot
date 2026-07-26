import pytest
from sqlalchemy import text

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unit_of_work_commits_exactly_one_database_transaction(
    isolated_database_url: str,
) -> None:
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            value = await uow.session.scalar(text("SELECT 1"))
            assert value == 1
            await uow.commit()
    finally:
        await database.dispose()
