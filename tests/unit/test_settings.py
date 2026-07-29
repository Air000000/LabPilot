import pytest
from pydantic import ValidationError

from labpilot.settings import Settings


def test_settings_require_a_postgresql_async_url() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_settings_load_server_actor_from_exact_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LABPILOT_DATABASE_URL",
        "postgresql+asyncpg://user:pass@db:5432/labpilot",
    )
    monkeypatch.setenv("LABPILOT_INSTALLATION_IDENTITY", "installation-1")
    monkeypatch.setenv("LABPILOT_LOCAL_ACTOR_ID", "air")

    settings = Settings(_env_file=None)

    assert settings.installation_identity == "installation-1"
    assert settings.local_actor_id == "air"


def test_settings_require_server_actor_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "LABPILOT_INSTALLATION_IDENTITY",
        raising=False,
    )
    monkeypatch.delenv("LABPILOT_LOCAL_ACTOR_ID", raising=False)

    with pytest.raises(ValidationError) as captured:
        Settings(
            database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
            _env_file=None,
        )

    assert {
        error["loc"] for error in captured.value.errors(include_url=False)
    } == {("installation_identity",), ("local_actor_id",)}


@pytest.mark.parametrize("field", ["installation_identity", "local_actor_id"])
@pytest.mark.parametrize("value", ["", "x" * 201])
def test_settings_reject_invalid_server_actor_values(
    field: str,
    value: str,
) -> None:
    values = {
        "database_url": "postgresql+asyncpg://user:pass@db:5432/labpilot",
        "installation_identity": "installation-1",
        "local_actor_id": "air",
        field: value,
    }

    with pytest.raises(ValidationError):
        Settings(**values)


def test_settings_reject_local_actor_id_over_200_utf8_bytes() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
            installation_identity="installation-1",
            local_actor_id="界" * 67,
        )


def test_settings_accept_local_actor_id_at_utf8_byte_boundary() -> None:
    actor_id = "界" * 66

    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id=actor_id,
    )

    assert settings.local_actor_id == actor_id
    assert len(settings.local_actor_id) == 66
    assert len(settings.local_actor_id.encode("utf-8")) == 198
