import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from tests.integration.db_helpers import upgrade_database_to_head


def _snapshot_values(
    snapshot_id: str,
    registration_id: str,
    **changes: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "snapshot_id": snapshot_id,
        "registration_id": registration_id,
        "sequence": 1,
        "identity": "a" * 64,
        "is_clean": True,
        "dirty_ref": None,
        "dirty_hash": None,
        "untracked_ref": None,
        "untracked_hash": None,
        "inspector_version": 1,
    }
    values.update(changes)
    return values


def _insert_snapshot(connection, values: dict[str, object]) -> None:
    connection.execute(
        text(
            """
            INSERT INTO repository_snapshots(
                repository_snapshot_id, repository_registration_id, snapshot_sequence,
                captured_at, canonical_root_path, head_commit, current_branch,
                is_detached_head, is_clean, tracked_tree_identity,
                dirty_worktree_manifest_ref, dirty_worktree_manifest_hash,
                untracked_manifest_ref, untracked_manifest_hash, repository_state_digest,
                repository_snapshot_identity, submodule_presence, lfs_pointer_presence,
                inspector_compatibility_version
            ) VALUES (
                :snapshot_id, :registration_id, :sequence, now(), '/repository',
                :identity, 'main', false, :is_clean, :identity, :dirty_ref, :dirty_hash,
                :untracked_ref, :untracked_hash, :identity, :identity, false, false,
                :inspector_version
            )
            """
        ),
        values,
    )


def _seed_registration(connection, project_id: str, registration_id: str, root: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO repository_registrations(
                repository_registration_id, project_id, repository_type,
                canonical_root_path, root_path_identity, status
            ) VALUES (:registration_id, :project_id, 'local_git', '/repository', :root, 'active')
            """
        ),
        {
            "project_id": project_id,
            "registration_id": registration_id,
            "root": root * 64,
        },
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("manifest_column", "invalid_value"),
    (
        ("dirty_worktree_manifest_ref", "dirty-ref"),
        ("dirty_worktree_manifest_hash", "a" * 64),
        ("untracked_manifest_ref", "untracked-ref"),
        ("untracked_manifest_hash", "b" * 64),
    ),
)
async def test_repository_snapshot_schema_enforces_history_and_bundle_constraints(
    isolated_database_url: str,
    manifest_column: str,
    invalid_value: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            project_id = "10000000-0000-4000-8000-000000000001"
            registration_one = "20000000-0000-4000-8000-000000000001"
            registration_two = "20000000-0000-4000-8000-000000000002"
            snapshot_one = "30000000-0000-4000-8000-000000000001"
            snapshot_two = "30000000-0000-4000-8000-000000000002"
            connection.execute(
                text(
                    """
                    INSERT INTO projects(
                        project_id, project_identity, name, status, artifact_root_ref
                    )
                    VALUES (:project_id, :identity, 'Project', 'active', 'artifact-root')
                    """
                ),
                {"project_id": project_id, "identity": "p" * 64},
            )
            _seed_registration(connection, project_id, registration_one, "r")
            _seed_registration(connection, project_id, registration_two, "s")
            _insert_snapshot(connection, _snapshot_values(snapshot_one, registration_one))
            manifest_parameter_keys = {
                "dirty_worktree_manifest_ref": "dirty_ref",
                "dirty_worktree_manifest_hash": "dirty_hash",
                "untracked_manifest_ref": "untracked_ref",
                "untracked_manifest_hash": "untracked_hash",
            }

            for changes in (
                {"snapshot_id": snapshot_two, "identity": "b" * 64},
                {"snapshot_id": snapshot_two, "sequence": 2},
                {
                    "snapshot_id": snapshot_two,
                    "sequence": 2,
                    "identity": "c" * 64,
                    manifest_parameter_keys[manifest_column]: invalid_value,
                },
                {
                    "snapshot_id": snapshot_two,
                    "sequence": 2,
                    "identity": "c" * 64,
                    "inspector_version": 0,
                },
            ):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    _insert_snapshot(
                        connection,
                        _snapshot_values(registration_id=registration_one, **changes),
                    )
            _insert_snapshot(connection, _snapshot_values(snapshot_two, registration_two))

            bundle_id = "40000000-0000-4000-8000-000000000001"
            bundle_values = {
                "bundle_id": bundle_id,
                "snapshot_id": snapshot_one,
                "identity": "d" * 64,
                "schema_version": 1,
            }
            bundle_sql = text(
                """
                INSERT INTO repository_snapshot_inspection_bundles(
                    inspection_bundle_id, repository_snapshot_id, base_commit_sha,
                    bundle_schema_version, capture_tool_version, entries_manifest_hash,
                    inspection_bundle_identity, created_at
                ) VALUES (
                    :bundle_id, :snapshot_id, :identity, :schema_version, 'v1', :identity,
                    :identity, now()
                )
                """
            )
            connection.execute(bundle_sql, bundle_values)
            for changes in (
                {"bundle_id": "40000000-0000-4000-8000-000000000002"},
                {
                    "bundle_id": "40000000-0000-4000-8000-000000000002",
                    "snapshot_id": snapshot_two,
                    "identity": "e" * 64,
                    "schema_version": 0,
                },
            ):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(bundle_sql, bundle_values | changes)

            entry_sql = text(
                """
                INSERT INTO repository_snapshot_inspection_bundle_entries(
                    inspection_bundle_entry_id, inspection_bundle_id, repository_path,
                    original_repository_path, entry_kind, file_type, file_mode, head_blob_oid,
                    index_blob_oid, content_ref, content_hash, size_bytes, stable_ordinal,
                    entry_identity
                ) VALUES (
                    :entry_id, :bundle_id, 'file.py', NULL, :kind, 'regular', '100644', NULL,
                    NULL, :content_ref, :content_hash, :size_bytes, :ordinal, :identity
                )
                """
            )
            entry_values = {
                "entry_id": "50000000-0000-4000-8000-000000000001",
                "bundle_id": bundle_id,
                "kind": "tracked_overlay",
                "content_ref": "content/ref",
                "content_hash": "f" * 64,
                "size_bytes": 1,
                "ordinal": 0,
                "identity": "g" * 64,
            }
            connection.execute(entry_sql, entry_values)
            for changes in (
                {"entry_id": "50000000-0000-4000-8000-000000000002"},
                {
                    "entry_id": "50000000-0000-4000-8000-000000000002",
                    "ordinal": 1,
                },
                {
                    "entry_id": "50000000-0000-4000-8000-000000000002",
                    "ordinal": 1,
                    "identity": "h" * 64,
                    "kind": "other",
                },
                {
                    "entry_id": "50000000-0000-4000-8000-000000000002",
                    "ordinal": 1,
                    "identity": "h" * 64,
                    "size_bytes": -1,
                },
                {
                    "entry_id": "50000000-0000-4000-8000-000000000002",
                    "ordinal": -1,
                    "identity": "h" * 64,
                },
                {
                    "entry_id": "50000000-0000-4000-8000-000000000002",
                    "ordinal": 1,
                    "identity": "h" * 64,
                    "content_hash": None,
                },
                {
                    "entry_id": "50000000-0000-4000-8000-000000000002",
                    "ordinal": 1,
                    "identity": "h" * 64,
                    "kind": "deletion",
                },
            ):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(entry_sql, entry_values | changes)

            columns = set(
                connection.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'repository_snapshots'
                        """
                    )
                ).scalars()
            )
            assert "inspection_bundle_id" not in columns
            for table, column, identifier in (
                ("repository_registrations", "repository_registration_id", registration_one),
                ("repository_snapshots", "repository_snapshot_id", snapshot_one),
                ("repository_snapshot_inspection_bundles", "inspection_bundle_id", bundle_id),
            ):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE {column} = :identifier"),
                        {"identifier": identifier},
                    )
    finally:
        engine.dispose()
