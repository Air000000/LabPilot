from labpilot.settings import Settings


def test_settings_require_a_postgresql_async_url() -> None:
    settings = Settings(database_url="postgresql+asyncpg://user:pass@db:5432/labpilot")
    assert settings.database_url.startswith("postgresql+asyncpg://")
