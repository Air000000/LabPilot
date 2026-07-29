from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    installation_identity: str = Field(min_length=1, max_length=200)
    local_actor_id: str = Field(min_length=1, max_length=200)

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
