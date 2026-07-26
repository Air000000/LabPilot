from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
@pytest.mark.destructive
async def test_upgrade_head_succeeds_on_an_isolated_empty_database(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)

    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert version == "0001_initial"
    finally:
        engine.dispose()

    assert Path("migrations/versions/0001_initial.py").exists()
