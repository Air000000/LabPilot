from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LABPILOT_",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field(min_length=1)
    log_level: str = "INFO"
    data_egress_policy: Literal["local_only", "remote_provider_allowed"] = "local_only"
    agent_provider_model: str | None = None
    agent_provider_api_key: SecretStr | None = None
    agent_provider_timeout_seconds: int = Field(default=60, ge=1, le=300)
    agent_provider_max_response_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
    )
    installation_identity: str = Field(min_length=1, max_length=200)
    local_actor_id: str = Field(min_length=1, max_length=200)
    repository_allowed_roots: tuple[Path, ...]
    repository_content_store_root: Path
    repository_git_timeout_seconds: int = Field(default=15, ge=1, le=120)
    repository_snapshot_max_entries: int = Field(default=10_000, ge=1)
    repository_snapshot_max_file_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    repository_snapshot_max_total_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    repository_inspection_max_files: int = Field(default=50_000, ge=1)
    repository_inspection_max_total_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    attempt_workspace_root: Path | None = None
    runner_socket_path: Path | None = None
    runner_request_timeout_seconds: int = Field(default=10, ge=1, le=300)
    unknown_attention_grace_seconds: int = Field(default=60, ge=1, le=300)
    train_gid: int | None = Field(default=None, gt=0)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use postgresql+asyncpg")
        return value

    @field_validator("local_actor_id")
    @classmethod
    def validate_local_actor_id_utf8_bytes(cls, value: str) -> str:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("local_actor_id must be valid UTF-8") from exc
        if size > 200:
            raise ValueError("local_actor_id exceeds 200 UTF-8 bytes")
        return value

    @field_validator("runner_socket_path")
    @classmethod
    def validate_runner_socket_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("runner_socket_path must be absolute")
        return value

    @model_validator(mode="after")
    def validate_repository_allowed_roots(self) -> Self:
        if self.environment != "test" and not self.repository_allowed_roots:
            raise ValueError(
                "repository_allowed_roots must not be empty outside the test environment"
            )
        root = self.attempt_workspace_root
        if root is None:
            return self
        if not root.is_absolute():
            raise ValueError("attempt_workspace_root must be absolute")
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("attempt_workspace_root must exist") from exc
        if resolved != root or not root.is_dir():
            raise ValueError(
                "attempt_workspace_root must be an existing canonical directory"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
