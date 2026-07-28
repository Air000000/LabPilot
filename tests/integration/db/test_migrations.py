from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from tests.integration.db_helpers import upgrade_database_to_head


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("+asyncpg", "+psycopg"),
    )
    return config


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
            version = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert version == "0004_projects"
    finally:
        engine.dispose()

    assert Path("migrations/versions/0001_initial.py").exists()
    assert Path("migrations/versions/0002_runtime_events_outbox.py").exists()
    assert Path("migrations/versions/0003_command_receipts.py").exists()
    assert Path("migrations/versions/0004_projects.py").exists()


@pytest.mark.integration
@pytest.mark.destructive
async def test_projects_schema_matches_the_project_contract(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)

    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('public.projects')")) == (
                "projects"
            )
            column_rows = connection.execute(
                text(
                    """
                    SELECT
                        column_name,
                        data_type,
                        character_maximum_length,
                        is_nullable,
                        column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'projects'
                    """
                )
            ).mappings()
            columns = {row["column_name"]: row for row in column_rows}
            constraints = set(
                connection.execute(
                    text(
                        """
                        SELECT constraint_name
                        FROM information_schema.table_constraints
                        WHERE table_schema = 'public' AND table_name = 'projects'
                        """
                    )
                ).scalars()
            )
            foreign_keys = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.table_constraints
                    WHERE table_schema = 'public'
                      AND table_name = 'projects'
                      AND constraint_type = 'FOREIGN KEY'
                    """
                )
            )
    finally:
        engine.dispose()

    assert set(columns) == {
        "project_id",
        "project_identity",
        "name",
        "description",
        "status",
        "primary_repository_registration_id",
        "artifact_root_ref",
        "created_at",
        "updated_at",
        "row_version",
    }
    assert columns["project_id"]["data_type"] == "uuid"
    assert columns["project_identity"]["character_maximum_length"] == 64
    assert columns["name"]["character_maximum_length"] == 200
    assert columns["description"]["data_type"] == "text"
    assert columns["status"]["character_maximum_length"] == 30
    assert columns["primary_repository_registration_id"]["data_type"] == "uuid"
    assert columns["artifact_root_ref"]["character_maximum_length"] == 1000
    assert columns["created_at"]["data_type"] == "timestamp with time zone"
    assert columns["updated_at"]["data_type"] == "timestamp with time zone"
    assert columns["row_version"]["data_type"] == "bigint"
    assert columns["description"]["is_nullable"] == "YES"
    assert columns["primary_repository_registration_id"]["is_nullable"] == "YES"
    assert all(
        columns[name]["is_nullable"] == "NO"
        for name in set(columns) - {"description", "primary_repository_registration_id"}
    )
    assert columns["created_at"]["column_default"] == "clock_timestamp()"
    assert columns["updated_at"]["column_default"] == "clock_timestamp()"
    assert columns["row_version"]["column_default"] == "1"
    assert {
        "pk_projects",
        "uq_project_identity",
        "ck_project_status",
        "ck_project_row_version_positive",
    } <= constraints
    assert foreign_keys == 0


@pytest.mark.integration
@pytest.mark.destructive
async def test_task3_migration_upgrade_downgrade_upgrade_round_trip(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    config = _alembic_config(isolated_database_url)
    engine = create_engine(sync_url)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0004_projects"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) == "projects"

        command.downgrade(config, "0003_command_receipts")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0003_command_receipts"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) is None

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0004_projects"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) == "projects"
    finally:
        engine.dispose()
