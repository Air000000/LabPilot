import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from tests.integration.context.test_repository_snapshot import (
    _insert_snapshot,
    _seed_registration,
    _snapshot_values,
)
from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
async def test_repository_snapshot_freshness_schema_enforces_current_projection(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            project_id = "60000000-0000-4000-8000-000000000001"
            registration_id = "70000000-0000-4000-8000-000000000001"
            snapshot_id = "80000000-0000-4000-8000-000000000001"
            connection.execute(
                text(
                    """
                    INSERT INTO projects(
                        project_id, project_identity, name, status, artifact_root_ref
                    )
                    VALUES (:project_id, :identity, 'Project', 'active', 'artifact-root')
                    """
                ),
                {"project_id": project_id, "identity": "j" * 64},
            )
            _seed_registration(connection, project_id, registration_id, "k")
            _insert_snapshot(
                connection,
                _snapshot_values(snapshot_id, registration_id),
            )
            freshness_sql = text(
                """
                INSERT INTO repository_snapshot_freshness(
                    repository_snapshot_id, status, reason_code, current_probe_identity,
                    probe_version, checked_at, row_version
                ) VALUES (:snapshot_id, :status, :reason, :probe, 'v1', now(), :row_version)
                """
            )
            values = {
                "snapshot_id": snapshot_id,
                "status": "current",
                "reason": None,
                "probe": None,
                "row_version": 1,
            }
            connection.execute(freshness_sql, values)
            for status in ("stale", "unverifiable", "invalid"):
                connection.execute(
                    text(
                        """
                        UPDATE repository_snapshot_freshness
                        SET status = :status, reason_code = 'reason',
                            current_probe_identity = :probe
                        WHERE repository_snapshot_id = :snapshot_id
                        """
                    ),
                    {"status": status, "probe": "l" * 64, "snapshot_id": snapshot_id},
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        """
                        UPDATE repository_snapshot_freshness SET status = 'other'
                        WHERE repository_snapshot_id = :snapshot_id
                        """
                    ),
                    {"snapshot_id": snapshot_id},
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        """
                        UPDATE repository_snapshot_freshness SET row_version = 0
                        WHERE repository_snapshot_id = :snapshot_id
                        """
                    ),
                    {"snapshot_id": snapshot_id},
                )
            for changes in (
                {"status": "other"},
                {"snapshot_id": snapshot_id},
            ):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(freshness_sql, values | changes)
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM repository_snapshots WHERE repository_snapshot_id = :id"),
                    {"id": snapshot_id},
                )
    finally:
        engine.dispose()
