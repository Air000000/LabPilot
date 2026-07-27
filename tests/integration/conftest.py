import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest_asyncio
from sqlalchemy.engine import make_url


@pytest_asyncio.fixture
async def isolated_database_url() -> AsyncIterator[str]:
    admin_url = os.environ["LABPILOT_TEST_ADMIN_DATABASE_URL"]
    parsed = make_url(admin_url)
    database_name = f"labpilot_test_{uuid4().hex}"
    admin_dsn = parsed.render_as_string(hide_password=False).replace("+asyncpg", "")
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    test_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    try:
        yield test_url
    finally:
        connection = await asyncpg.connect(admin_dsn)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()
