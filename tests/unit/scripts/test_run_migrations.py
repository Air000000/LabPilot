import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).parents[3]))

run_migrations = import_module("scripts.run_migrations")


@pytest.mark.parametrize(
    ("source", "expected_alembic", "expected_direct"),
    [
        (
            "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres",
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql://postgres:postgres@postgres:5432/postgres",
        ),
        (
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql://postgres:postgres@postgres:5432/postgres",
        ),
        (
            "postgresql://postgres:postgres@postgres:5432/postgres",
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql://postgres:postgres@postgres:5432/postgres",
        ),
    ],
)
def test_migration_urls_route_each_postgresql_driver(
    source: str,
    expected_alembic: str,
    expected_direct: str,
) -> None:
    assert run_migrations.migration_urls(source) == (
        expected_alembic,
        expected_direct,
    )


def test_migration_urls_reject_non_postgresql_url() -> None:
    with pytest.raises(
        ValueError,
        match="LABPILOT_DATABASE_URL must use PostgreSQL",
    ):
        run_migrations.migration_urls("sqlite:///labpilot.db")


def test_migration_urls_preserve_escaped_credentials_and_query() -> None:
    source = (
        "postgresql+asyncpg://user%40example.com:p%40ss%2Fword"
        "@postgres:5432/postgres"
        "?application_name=LabPilot%20Migrate&sslmode=require"
    )
    source_url = make_url(source)
    alembic_dsn, direct_dsn = run_migrations.migration_urls(source)

    for dsn in (alembic_dsn, direct_dsn):
        parsed = make_url(dsn)
        assert parsed.username == source_url.username
        assert parsed.password == source_url.password
        assert parsed.database == source_url.database
        assert parsed.query == source_url.query
        assert "***" not in dsn


def test_main_preserves_percent_encoded_url_through_alembic_config(
    monkeypatch,
) -> None:
    source = (
        "postgresql+asyncpg://user%40example.com:p%40ss%2Fword"
        "@postgres:5432/postgres"
        "?application_name=LabPilot%20Migrate&sslmode=require"
    )
    expected_alembic = (
        "postgresql+psycopg://user%40example.com:p%40ss%2Fword"
        "@postgres:5432/postgres"
        "?application_name=LabPilot+Migrate&sslmode=require"
    )

    monkeypatch.setenv("LABPILOT_DATABASE_URL", source)

    connect = MagicMock()
    monkeypatch.setattr(run_migrations.psycopg, "connect", connect)

    captured: dict[str, str] = {}

    def capture_upgrade(config, revision: str) -> None:
        captured["revision"] = revision
        captured["url"] = config.get_main_option("sqlalchemy.url")

    monkeypatch.setattr(
        run_migrations.command,
        "upgrade",
        capture_upgrade,
    )

    run_migrations.main()

    captured_url = make_url(captured["url"])
    expected_url = make_url(expected_alembic)
    assert captured["revision"] == "head"
    assert captured_url.username == expected_url.username
    assert captured_url.password == expected_url.password
    assert captured_url.query == expected_url.query
    assert "***" not in captured["url"]


def test_main_routes_alembic_and_psycopg_urls(monkeypatch) -> None:
    source = "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres"
    monkeypatch.setenv("LABPILOT_DATABASE_URL", source)

    connect = MagicMock()
    monkeypatch.setattr(run_migrations.psycopg, "connect", connect)
    config = MagicMock()
    monkeypatch.setattr(run_migrations, "Config", lambda _: config)
    upgrade = MagicMock()
    monkeypatch.setattr(run_migrations.command, "upgrade", upgrade)

    run_migrations.main()

    connect.assert_called_once_with(
        "postgresql://postgres:postgres@postgres:5432/postgres",
        autocommit=True,
    )
    config.set_main_option.assert_called_once_with(
        "sqlalchemy.url",
        "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
    )
    upgrade.assert_called_once_with(config, "head")
