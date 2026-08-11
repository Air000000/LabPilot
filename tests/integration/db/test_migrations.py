from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
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
        assert version == "0007_experiment_proposals"
    finally:
        engine.dispose()

    assert Path("migrations/versions/0001_initial.py").exists()
    assert Path("migrations/versions/0002_runtime_events_outbox.py").exists()
    assert Path("migrations/versions/0003_command_receipts.py").exists()
    assert Path("migrations/versions/0004_projects.py").exists()
    assert Path("migrations/versions/0005_repository_registrations.py").exists()
    assert Path("migrations/versions/0006_repository_snapshots.py").exists()
    assert Path(
        "migrations/versions/0007_experiment_proposals.py"
    ).exists()


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
    assert foreign_keys == 1


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
            ) == "0007_experiment_proposals"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) == "projects"

        command.downgrade(config, "0004_projects")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0004_projects"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) == "projects"

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0007_experiment_proposals"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) == "projects"
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.destructive
async def test_repository_registration_schema_enforces_task7_constraints(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            assert connection.scalar(
                text("SELECT to_regclass('public.repository_registrations')")
            ) == "repository_registrations"
            columns = set(
                connection.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'repository_registrations'
                        """
                    )
                ).scalars()
            )
            assert columns == {
                "repository_registration_id",
                "project_id",
                "repository_type",
                "canonical_root_path",
                "root_path_identity",
                "remote_origin_sanitized",
                "default_branch",
                "status",
                "last_access_check_at",
                "created_at",
                "updated_at",
                "row_version",
            }
            constraints = set(
                connection.execute(
                    text(
                        """
                        SELECT constraint_name FROM information_schema.table_constraints
                        WHERE table_schema = 'public'
                          AND table_name = 'repository_registrations'
                        """
                    )
                ).scalars()
            )
            assert {
                "pk_repository_registrations",
                "fk_repository_registrations_project_id_projects",
                "ck_repository_registrations_repository_type",
                "ck_repository_registrations_status",
                "ck_repository_registrations_row_version_positive",
                "uq_repository_registrations_project_registration",
            } <= constraints
            assert connection.scalar(
                text(
                    """
                    SELECT confdeltype = 'r' FROM pg_constraint
                    WHERE conname = 'fk_repository_registrations_project_id_projects'
                    """
                )
            ) is True
            assert connection.scalar(
                text(
                    """
                    SELECT pg_get_expr(indpred, indrelid) FROM pg_index
                    WHERE indexrelid = 'uq_repository_registration_live_root'::regclass
                    """
                )
            ) is not None
            assert connection.scalar(
                text(
                    """
                    SELECT count(*) FROM pg_constraint
                    WHERE conrelid = 'projects'::regclass
                      AND conname = 'fk_projects_primary_repository_registration'
                    """
                )
            ) == 1
            assert connection.scalar(
                text(
                    """
                    SELECT pg_get_expr(indpred, indrelid) FROM pg_index
                    WHERE indexrelid = 'uq_projects_primary_repository_registration'::regclass
                    """
                )
            ) == "(primary_repository_registration_id IS NOT NULL)"

            projects = (
                ("11111111-1111-4111-8111-111111111111", "a" * 64),
                ("22222222-2222-4222-8222-222222222222", "b" * 64),
            )
            for project_id, project_identity in projects:
                connection.execute(
                    text(
                        """
                        INSERT INTO projects(
                            project_id, project_identity, name, status, artifact_root_ref
                        ) VALUES (
                            :project_id, :project_identity, 'Project', 'active', 'artifact-root'
                        )
                        """
                    ),
                    {"project_id": project_id, "project_identity": project_identity},
                )

            def insert_registration(
                registration_id: str,
                project_id: str,
                root_path_identity: str,
                status: str = "active",
            ) -> None:
                connection.execute(
                    text(
                        """
                        INSERT INTO repository_registrations(
                            repository_registration_id, project_id, repository_type,
                            canonical_root_path, root_path_identity, status
                        ) VALUES (
                            :registration_id, :project_id, 'local_git', '/workspace/repository',
                            :root_path_identity, :status
                        )
                        """
                    ),
                    {
                        "registration_id": registration_id,
                        "project_id": project_id,
                        "root_path_identity": root_path_identity,
                        "status": status,
                    },
                )

            project_one, project_two = (project[0] for project in projects)
            for index, status in enumerate(
                ("active", "unavailable", "invalid_git_repository")
            ):
                root_path_identity = f"{index:064x}"
                first_id = f"00000000-0000-4000-8000-{index + 1:012d}"
                second_id = f"00000000-0000-4000-8000-{index + 11:012d}"
                insert_registration(first_id, project_one, root_path_identity, status)
                with pytest.raises(IntegrityError), connection.begin_nested():
                    insert_registration(second_id, project_one, root_path_identity)
                connection.execute(
                    text(
                        """
                        UPDATE repository_registrations SET status = 'revoked'
                        WHERE repository_registration_id = :registration_id
                        """
                    ),
                    {"registration_id": first_id},
                )
                insert_registration(second_id, project_one, root_path_identity)
                insert_registration(
                    f"00000000-0000-4000-8000-{index + 21:012d}",
                    project_two,
                    root_path_identity,
                )

            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO repository_registrations(
                            repository_registration_id, project_id, repository_type,
                            canonical_root_path, root_path_identity, status
                        ) VALUES (
                            '00000000-0000-4000-8000-000000000041',
                            :project_id, 'other', '/workspace/repository', :root, 'active'
                        )
                        """
                    ),
                    {"project_id": project_one, "root": "c" * 64},
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO repository_registrations(
                            repository_registration_id, project_id, repository_type,
                            canonical_root_path, root_path_identity, status, row_version
                        ) VALUES (
                            '00000000-0000-4000-8000-000000000042',
                            :project_id, 'local_git', '/workspace/repository', :root, 'active', 0
                        )
                        """
                    ),
                    {"project_id": project_one, "root": "d" * 64},
                )
            owned_id = "00000000-0000-4000-8000-000000000043"
            insert_registration(owned_id, project_one, "e" * 64)
            connection.execute(
                text(
                    """
                    UPDATE projects SET primary_repository_registration_id = :registration_id
                    WHERE project_id = :project_id
                    """
                ),
                {"registration_id": owned_id, "project_id": project_one},
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        """
                        UPDATE projects SET primary_repository_registration_id = :registration_id
                        WHERE project_id = :project_id
                        """
                    ),
                    {"registration_id": owned_id, "project_id": project_two},
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM projects WHERE project_id = :project_id"),
                    {"project_id": project_one},
                )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.destructive
async def test_task9_migration_upgrade_downgrade_upgrade_round_trip(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    config = _alembic_config(isolated_database_url)
    engine = create_engine(sync_url)
    tables = (
        "repository_snapshots",
        "repository_snapshot_inspection_bundles",
        "repository_snapshot_inspection_bundle_entries",
        "repository_snapshot_freshness",
    )
    try:
        command.upgrade(config, "0006_repository_snapshots")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0006_repository_snapshots"
            for table in tables:
                assert connection.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"public.{table}"},
                ) == table

        command.downgrade(config, "0005_repository_registrations")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_repository_registrations"
            assert connection.scalar(
                text("SELECT to_regclass('public.repository_registrations')")
            ) == "repository_registrations"
            assert connection.scalar(
                text("SELECT to_regclass('public.projects')")
            ) == "projects"
            for table in tables:
                assert connection.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"public.{table}"},
                ) is None

        command.upgrade(config, "0006_repository_snapshots")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0006_repository_snapshots"
            for table in tables:
                assert connection.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"public.{table}"},
                ) == table
    finally:
        engine.dispose()
