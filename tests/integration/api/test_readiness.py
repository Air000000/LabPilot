import pytest
from httpx import ASGITransport, AsyncClient
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.api.app import create_app
from labpilot.db.readiness import DatabaseReadinessProbe
from labpilot.db.session import Database


async def get_readiness(database: Database) -> tuple[int, dict[str, object]]:
    app = create_app(
        readiness_probe=DatabaseReadinessProbe(database.engine, "alembic.ini")
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")
    return response.status_code, response.json()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_readiness_is_503_before_migration(
    isolated_database_url: str,
) -> None:
    database = Database(isolated_database_url)
    try:
        status_code, payload = await get_readiness(database)
        assert status_code == 503
        assert payload["migration"] == "behind"
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_readiness_is_200_at_alembic_head(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        status_code, payload = await get_readiness(database)
        assert status_code == 200
        assert payload == {
            "status": "ready",
            "database": "ok",
            "migration": "head",
            "detail": None,
        }
    finally:
        await database.dispose()
