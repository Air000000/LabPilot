from pathlib import Path

import pytest
from pydantic import ValidationError

from labpilot.settings import Settings


def test_settings_require_a_postgresql_async_url() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(Path("/repositories"),),
        repository_content_store_root=Path("/content"),
        attempt_workspace_root=Path("/tmp"),
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
    monkeypatch.setenv(
        "LABPILOT_REPOSITORY_ALLOWED_ROOTS",
        '["/repositories", "/worktrees"]',
    )
    monkeypatch.setenv("LABPILOT_REPOSITORY_CONTENT_STORE_ROOT", "/content")
    monkeypatch.setenv("LABPILOT_ATTEMPT_WORKSPACE_ROOT", "/tmp")

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

    monkeypatch.setenv("LABPILOT_REPOSITORY_ALLOWED_ROOTS", '["/repositories"]')
    monkeypatch.setenv("LABPILOT_REPOSITORY_CONTENT_STORE_ROOT", "/content")
    with pytest.raises(ValidationError) as captured:
        Settings(
            database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
            _env_file=None,
        )

    assert {error["loc"] for error in captured.value.errors(include_url=False)} == {
        ("installation_identity",),
        ("local_actor_id",),
    }


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
        "repository_allowed_roots": (Path("/repositories"),),
        "repository_content_store_root": Path("/content"),
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
            repository_allowed_roots=(Path("/repositories"),),
            repository_content_store_root=Path("/content"),
        )


def test_settings_accept_local_actor_id_at_utf8_byte_boundary() -> None:
    actor_id = "界" * 66

    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id=actor_id,
        repository_allowed_roots=(Path("/repositories"),),
        repository_content_store_root=Path("/content"),
        attempt_workspace_root=Path("/tmp"),
    )

    assert settings.local_actor_id == actor_id
    assert len(settings.local_actor_id) == 66
    assert len(settings.local_actor_id.encode("utf-8")) == 198


def test_settings_parse_repository_roots_from_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LABPILOT_DATABASE_URL",
        "postgresql+asyncpg://user:pass@db:5432/labpilot",
    )
    monkeypatch.setenv("LABPILOT_INSTALLATION_IDENTITY", "installation-1")
    monkeypatch.setenv("LABPILOT_LOCAL_ACTOR_ID", "air")
    monkeypatch.setenv(
        "LABPILOT_REPOSITORY_ALLOWED_ROOTS",
        '["/repositories", "/worktrees"]',
    )
    monkeypatch.setenv("LABPILOT_REPOSITORY_CONTENT_STORE_ROOT", "/content")
    monkeypatch.setenv("LABPILOT_ATTEMPT_WORKSPACE_ROOT", "/tmp")

    settings = Settings(_env_file=None)

    assert settings.repository_allowed_roots == (
        Path("/repositories"),
        Path("/worktrees"),
    )
    assert isinstance(settings.repository_allowed_roots, tuple)
    assert settings.repository_content_store_root == Path("/content")


def test_settings_allow_empty_repository_roots_in_test_environment() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(),
        repository_content_store_root=Path("/path/need/not/exist"),
    )

    assert settings.repository_allowed_roots == ()


def test_settings_allow_missing_attempt_workspace_root_only_in_test_mode() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(),
        repository_content_store_root=Path("/content"),
    )

    assert settings.attempt_workspace_root is None


def test_settings_require_existing_canonical_attempt_workspace_root_outside_tests(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "attempts"
    canonical.mkdir()
    accepted = Settings(
        environment="development",
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(Path("/repositories"),),
        repository_content_store_root=Path("/content"),
        attempt_workspace_root=canonical,
    )
    assert accepted.attempt_workspace_root == canonical

    for invalid in (None, Path("relative"), canonical / "missing"):
        with pytest.raises(ValidationError, match="attempt_workspace_root"):
            Settings(
                environment="development",
                database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
                installation_identity="installation-1",
                local_actor_id="air",
                repository_allowed_roots=(Path("/repositories"),),
                repository_content_store_root=Path("/content"),
                attempt_workspace_root=invalid,
            )


def test_settings_reject_attempt_workspace_root_with_symlink_component(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValidationError, match="attempt_workspace_root"):
        Settings(
            database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
            installation_identity="installation-1",
            local_actor_id="air",
            repository_allowed_roots=(Path("/repositories"),),
            repository_content_store_root=Path("/content"),
            attempt_workspace_root=linked,
        )


@pytest.mark.parametrize("environment", ["development", "production"])
def test_settings_reject_empty_repository_roots_outside_tests(environment: str) -> None:
    with pytest.raises(ValidationError, match="repository_allowed_roots"):
        Settings(
            environment=environment,
            database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
            installation_identity="installation-1",
            local_actor_id="air",
            repository_allowed_roots=(),
            repository_content_store_root=Path("/content"),
        )


def test_settings_keep_repository_paths_unresolved_and_frozen() -> None:
    relative_store = Path("missing/../content")
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(Path("/missing/root"),),
        repository_content_store_root=relative_store,
    )

    assert settings.repository_allowed_roots == (Path("/missing/root"),)
    assert settings.repository_content_store_root == relative_store
    with pytest.raises(ValidationError):
        settings.repository_git_timeout_seconds = 20  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_git_timeout_seconds", 0),
        ("repository_git_timeout_seconds", 121),
        ("repository_snapshot_max_entries", 0),
        ("repository_snapshot_max_file_bytes", 0),
        ("repository_snapshot_max_total_bytes", 0),
        ("repository_inspection_max_files", 0),
        ("repository_inspection_max_total_bytes", 0),
    ],
)
def test_settings_reject_invalid_repository_limits(field: str, value: int) -> None:
    values = {
        "environment": "test",
        "database_url": "postgresql+asyncpg://user:pass@db:5432/labpilot",
        "installation_identity": "installation-1",
        "local_actor_id": "air",
        "repository_allowed_roots": (),
        "repository_content_store_root": Path("/content"),
        field: value,
    }

    with pytest.raises(ValidationError):
        Settings(**values)


def test_data_egress_policy_defaults_local_only() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(Path("/repositories"),),
        repository_content_store_root=Path("/content"),
        attempt_workspace_root=Path("/tmp"),
    )

    assert settings.data_egress_policy == "local_only"


def test_data_egress_policy_accepts_remote_provider_allowed() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
        installation_identity="installation-1",
        local_actor_id="air",
        repository_allowed_roots=(Path("/repositories"),),
        repository_content_store_root=Path("/content"),
        attempt_workspace_root=Path("/tmp"),
        data_egress_policy="remote_provider_allowed",
    )

    assert settings.data_egress_policy == "remote_provider_allowed"


def test_data_egress_policy_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="data_egress_policy"):
        Settings(
            database_url="postgresql+asyncpg://user:pass@db:5432/labpilot",
            installation_identity="installation-1",
            local_actor_id="air",
            repository_allowed_roots=(Path("/repositories"),),
            repository_content_store_root=Path("/content"),
            data_egress_policy="anything_else",
        )
