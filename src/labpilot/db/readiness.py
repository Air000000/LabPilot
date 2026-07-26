from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from labpilot.common.readiness import ReadinessResult


class DatabaseReadinessProbe:
    def __init__(self, engine: AsyncEngine, alembic_config_path: str) -> None:
        self._engine = engine
        self._alembic_config_path = Path(alembic_config_path)

    async def check(self) -> ReadinessResult:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                current = await connection.run_sync(
                    lambda sync_connection: MigrationContext.configure(
                        sync_connection
                    ).get_current_revision()
                )
        except Exception as exc:
            return ReadinessResult(
                ready=False,
                database="unavailable",
                migration="unknown",
                detail=type(exc).__name__,
            )

        config = Config(str(self._alembic_config_path))
        heads = set(ScriptDirectory.from_config(config).get_heads())
        at_head = current is not None and current in heads and len(heads) == 1
        return ReadinessResult(
            ready=at_head,
            database="ok",
            migration="head" if at_head else "behind",
            detail=None if at_head else f"current={current}, heads={sorted(heads)}",
        )
