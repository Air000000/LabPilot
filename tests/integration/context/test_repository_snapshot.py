import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, create_engine, text
from sqlalchemy.exc import IntegrityError
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.context.models import RepositorySnapshotInspectionBundleEntryRow


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


async def _run_git(repository: Path, *arguments: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/git",
        *arguments,
        cwd=repository,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", "replace")
    return stdout.decode("utf-8").strip()


async def _seed_capture_service(isolated_database_url: str, tmp_path: Path, repository: Path):
    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.git_adapter import GitAdapter
    from labpilot.context.models import RepositoryRegistrationRow
    from labpilot.context.repository_identities import repository_registration_identity
    from labpilot.context.repository_service import RepositoryApplicationService
    from labpilot.db.session import Database
    from labpilot.db.uow import UnitOfWork
    from labpilot.experiments.models import ProjectRow

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("10000000-0000-4000-8000-000000000020")
    registration_id = UUID("20000000-0000-4000-8000-000000000020")
    canonical_root = str(repository.resolve())
    async with UnitOfWork(database.session) as uow:
        project = ProjectRow(
            project_id=project_id,
            project_identity="p" * 64,
            name="Project",
            description=None,
            status="active",
            primary_repository_registration_id=None,
            artifact_root_ref="artifact",
            row_version=1,
        )
        uow.session.add(project)
        await uow.session.flush()
        uow.session.add(
            RepositoryRegistrationRow(
                repository_registration_id=registration_id,
                project_id=project_id,
                repository_type="local_git",
                canonical_root_path=canonical_root,
                root_path_identity=repository_registration_identity(
                    canonical_root_path=canonical_root,
                    repository_type="local_git",
                ),
                remote_origin_sanitized=None,
                default_branch="main",
                status="active",
                last_access_check_at=None,
                row_version=1,
            )
        )
        await uow.session.flush()
        project.primary_repository_registration_id = registration_id
        await uow.commit()
    return (
        database,
        RepositoryApplicationService(
            database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            allowed_roots=(tmp_path,),
            content_store=ContentAddressedStore(tmp_path / "content"),
        ),
        registration_id,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "entry_kind",
        "file_type",
        "file_mode",
        "content_ref",
        "content_hash",
        "size_bytes",
        "must_succeed",
    ),
    (
        ("tracked_overlay", "submodule", "160000", None, None, 0, True),
        ("tracked_overlay", "submodule", "160000", "content/ref", "a" * 64, 1, False),
        ("untracked_file", "submodule", "160000", "content/ref", "a" * 64, 1, False),
        ("tracked_overlay", "regular", "100644", "content/ref", "a" * 64, 1, True),
        ("tracked_overlay", "symlink", "120000", "content/ref", "a" * 64, 1, True),
        ("untracked_file", "regular", "100644", "content/ref", "a" * 64, 1, True),
        ("untracked_file", "symlink", "120000", "content/ref", "a" * 64, 1, True),
        ("tracked_overlay", "regular", "100644", None, None, 0, False),
        ("tracked_overlay", "symlink", "120000", None, None, 0, False),
        ("untracked_file", "regular", "100644", None, None, 0, False),
        ("untracked_file", "symlink", "120000", None, None, 0, False),
    ),
)
async def test_bundle_entry_content_semantics_match_frozen_gitlink_contract(
    isolated_database_url: str,
    entry_kind: str,
    file_type: str,
    file_mode: str,
    content_ref: str | None,
    content_hash: str | None,
    size_bytes: int,
    must_succeed: bool,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            project_id = "10000000-0000-4000-8000-000000000001"
            registration_id = "20000000-0000-4000-8000-000000000001"
            snapshot_id = "30000000-0000-4000-8000-000000000001"
            bundle_id = "40000000-0000-4000-8000-000000000001"
            connection.execute(
                text(
                    """
                    INSERT INTO projects(
                        project_id, project_identity, name, status, artifact_root_ref
                    ) VALUES (:project_id, :identity, 'Project', 'active', 'artifact-root')
                    """
                ),
                {"project_id": project_id, "identity": "p" * 64},
            )
            _seed_registration(connection, project_id, registration_id, "r")
            _insert_snapshot(connection, _snapshot_values(snapshot_id, registration_id))
            connection.execute(
                text(
                    """
                    INSERT INTO repository_snapshot_inspection_bundles(
                        inspection_bundle_id, repository_snapshot_id, base_commit_sha,
                        bundle_schema_version, capture_tool_version, entries_manifest_hash,
                        inspection_bundle_identity, created_at
                    ) VALUES (
                        :bundle_id, :snapshot_id, :identity, 1, 'v1', :identity,
                        :identity, now()
                    )
                    """
                ),
                {"bundle_id": bundle_id, "snapshot_id": snapshot_id, "identity": "b" * 64},
            )
            statement = text(
                """
                INSERT INTO repository_snapshot_inspection_bundle_entries(
                    inspection_bundle_entry_id, inspection_bundle_id, repository_path,
                    original_repository_path, entry_kind, file_type, file_mode, head_blob_oid,
                    index_blob_oid, content_ref, content_hash, size_bytes, stable_ordinal,
                    entry_identity
                ) VALUES (
                    '50000000-0000-4000-8000-000000000001', :bundle_id, 'entry', NULL,
                    :entry_kind, :file_type, :file_mode, :head_oid, :index_oid, :content_ref,
                    :content_hash, :size_bytes, 0, :entry_identity
                )
                """
            )
            values = {
                "bundle_id": bundle_id,
                "entry_kind": entry_kind,
                "file_type": file_type,
                "file_mode": file_mode,
                "head_oid": None if entry_kind == "untracked_file" else "c" * 40,
                "index_oid": None if entry_kind == "untracked_file" else "d" * 40,
                "content_ref": content_ref,
                "content_hash": content_hash,
                "size_bytes": size_bytes,
                "entry_identity": "e" * 64,
            }
            if must_succeed:
                connection.execute(statement, values)
            else:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(statement, values)
    finally:
        engine.dispose()


def test_bundle_entry_orm_content_constraint_matches_frozen_gitlink_contract() -> None:
    constraint = next(
        item
        for item in RepositorySnapshotInspectionBundleEntryRow.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_snapshot_bundle_entries_content_semantics"
    )
    actual = " ".join(str(constraint.sqltext).split())
    expected = " ".join(
        """
        (entry_kind IN ('deletion', 'mode_change') AND
        content_ref IS NULL AND content_hash IS NULL) OR
        (entry_kind = 'tracked_overlay' AND file_type = 'submodule' AND
        content_ref IS NULL AND content_hash IS NULL) OR
        (entry_kind IN ('tracked_overlay', 'untracked_file') AND
        file_type IN ('regular', 'symlink') AND
        content_ref IS NOT NULL AND content_hash IS NOT NULL)
        """.split()
    )
    assert actual == expected


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


@pytest.mark.integration
async def test_capture_clean_snapshot_persists_snapshot_freshness_and_receipt(
    isolated_database_url: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.content_store import ContentAddressedStore
    from labpilot.context.git_adapter import GitAdapter
    from labpilot.context.models import (
        RepositoryRegistrationRow,
        RepositorySnapshotFreshnessRow,
        RepositorySnapshotInspectionBundleEntryRow,
        RepositorySnapshotInspectionBundleRow,
        RepositorySnapshotRow,
    )
    from labpilot.context.repository_identities import repository_registration_identity
    from labpilot.context.repository_service import (
        RepositoryApplicationService,
        RepositoryRegistrationConflict,
    )
    from labpilot.context.repository_types import (
        CaptureRepositorySnapshot,
        RepositorySnapshotCaptureConflict,
        RepositorySnapshotInvariantViolation,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.db.session import Database
    from labpilot.db.uow import UnitOfWork
    from labpilot.experiments.models import ProjectRow
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = UUID("10000000-0000-4000-8000-000000000010")
    registration_id = UUID("20000000-0000-4000-8000-000000000010")
    repository = Path(tmp_path) / "repository"
    repository.mkdir()

    async def run_git(*arguments: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/git",
            *arguments,
            cwd=repository,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode("utf-8", "replace")

    await run_git("init", "-q", "-b", "main")
    (repository / "README.md").write_text("base", encoding="utf-8")
    await run_git("add", "README.md")
    await run_git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    try:
        async with UnitOfWork(database.session) as uow:
            project = ProjectRow(
                project_id=project_id,
                project_identity="p" * 64,
                name="Project",
                description=None,
                status="active",
                primary_repository_registration_id=None,
                artifact_root_ref="artifact",
                row_version=1,
            )
            uow.session.add(project)
            await uow.session.flush()
            uow.session.add(
                RepositoryRegistrationRow(
                    repository_registration_id=registration_id,
                    project_id=project_id,
                    repository_type="local_git",
                    canonical_root_path=str(repository),
                    root_path_identity=repository_registration_identity(
                        canonical_root_path=str(repository),
                        repository_type="local_git",
                    ),
                    remote_origin_sanitized=None,
                    default_branch="main",
                    status="active",
                    last_access_check_at=None,
                    row_version=1,
                )
            )
            await uow.session.flush()
            project.primary_repository_registration_id = registration_id
            await uow.commit()
        service = RepositoryApplicationService(
            database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            allowed_roots=(Path(tmp_path),),
            content_store=ContentAddressedStore(Path(tmp_path) / "content"),
        )
        initial_probe = await service._git_adapter.probe(repository)

        outcome = await service.capture_repository_snapshot(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user"),
                interaction_correlation_id="capture",
            ),
            idempotency_key="capture-clean",
            command=CaptureRepositorySnapshot(registration_id),
        )

        assert outcome.replayed is False
        assert outcome.envelope.outcome_kind == "repository_snapshot_created"
        removed_repository = Path(tmp_path) / "removed-repository"
        repository.rename(removed_repository)
        replay = await service.capture_repository_snapshot(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user"),
                interaction_correlation_id="capture",
            ),
            idempotency_key="capture-clean",
            command=CaptureRepositorySnapshot(registration_id),
        )
        assert replay.replayed is True
        assert replay.envelope.primary_result_id == outcome.envelope.primary_result_id

        removed_repository.rename(repository)
        clean_reused = await service.capture_repository_snapshot(
            context=CommandContext(
                installation_identity="installation",
                actor=CommandActor(actor_type="user", actor_id="user"),
                interaction_correlation_id="capture",
            ),
            idempotency_key="capture-clean-reuse",
            command=CaptureRepositorySnapshot(registration_id),
        )
        assert clean_reused.replayed is False
        assert clean_reused.envelope.outcome_kind == "repository_snapshot_reused"
        assert clean_reused.envelope.primary_result_id == outcome.envelope.primary_result_id

        (repository / "README.md").write_text("dirty", encoding="utf-8")
        outcomes = await asyncio.gather(
            service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-dirty",
                command=CaptureRepositorySnapshot(registration_id),
            ),
            service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-dirty-reuse",
                command=CaptureRepositorySnapshot(registration_id),
            ),
        )
        dirty = next(
            outcome
            for outcome in outcomes
            if outcome.envelope.outcome_kind == "repository_snapshot_created"
        )
        reused = next(
            outcome
            for outcome in outcomes
            if outcome.envelope.outcome_kind == "repository_snapshot_reused"
        )
        assert dirty.replayed is False
        assert reused.replayed is False
        assert reused.envelope.primary_result_id == dirty.envelope.primary_result_id
        async with UnitOfWork(database.session) as uow:
            bundle = await uow.session.scalar(
                select(RepositorySnapshotInspectionBundleRow).where(
                    RepositorySnapshotInspectionBundleRow.repository_snapshot_id
                    == UUID(dirty.envelope.primary_result_id)
                )
            )
            assert bundle is not None
            assert bundle.inspection_bundle_identity != "0" * 64
            bundle.inspection_bundle_identity = "0" * 64
            await uow.commit()
        with pytest.raises(RepositorySnapshotInvariantViolation):
            await service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-dirty-invariant",
                command=CaptureRepositorySnapshot(registration_id),
            )

        original_capture = service._git_adapter.capture_snapshot

        async def fail_capture(*arguments: object, **keywords: object) -> object:
            raise RepositorySnapshotCaptureConflict("test_capture_failure")

        monkeypatch.setattr(service._git_adapter, "capture_snapshot", fail_capture)
        with pytest.raises(RepositorySnapshotCaptureConflict, match="test_capture_failure"):
            await service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-failure",
                command=CaptureRepositorySnapshot(registration_id),
            )

        async def capture_then_change_root(*arguments: object, **keywords: object) -> object:
            observation = await original_capture(*arguments, **keywords)
            async with UnitOfWork(database.session) as uow:
                registration = await uow.session.get(RepositoryRegistrationRow, registration_id)
                assert registration is not None
                registration.canonical_root_path = str(repository / "changed")
                await uow.commit()
            return observation

        monkeypatch.setattr(service._git_adapter, "capture_snapshot", capture_then_change_root)
        with pytest.raises(RepositoryRegistrationConflict, match="registration root changed"):
            await service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-root-race",
                command=CaptureRepositorySnapshot(registration_id),
            )
        async with UnitOfWork(database.session) as uow:
            registration = await uow.session.get(RepositoryRegistrationRow, registration_id)
            assert registration is not None
            registration.canonical_root_path = str(repository)
            await uow.commit()

        async def capture_then_revoke(*arguments: object, **keywords: object) -> object:
            observation = await original_capture(*arguments, **keywords)
            async with UnitOfWork(database.session) as uow:
                registration = await uow.session.get(RepositoryRegistrationRow, registration_id)
                assert registration is not None
                registration.status = "revoked"
                await uow.commit()
            return observation

        monkeypatch.setattr(service._git_adapter, "capture_snapshot", capture_then_revoke)
        with pytest.raises(RepositoryRegistrationConflict, match="registration is not active"):
            await service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-revocation-race",
                command=CaptureRepositorySnapshot(registration_id),
            )
        async with database.session() as session:
            snapshots = list(
                (
                    await session.scalars(
                        select(RepositorySnapshotRow).order_by(
                            RepositorySnapshotRow.snapshot_sequence
                        )
                    )
                ).all()
            )
            assert len(snapshots) == 2
            clean_snapshot, dirty_snapshot = snapshots
            assert (clean_snapshot.snapshot_sequence, dirty_snapshot.snapshot_sequence) == (1, 2)
            assert clean_snapshot.repository_snapshot_id == UUID(outcome.envelope.primary_result_id)
            assert clean_snapshot.canonical_root_path == initial_probe.canonical_root_path
            assert clean_snapshot.head_commit == initial_probe.head_commit
            assert clean_snapshot.tracked_tree_identity == initial_probe.tracked_tree_identity
            assert clean_snapshot.current_branch == initial_probe.current_branch
            assert clean_snapshot.is_clean is True
            assert clean_snapshot.dirty_worktree_manifest_ref is None
            assert clean_snapshot.untracked_manifest_ref is None
            freshness = await session.get(
                RepositorySnapshotFreshnessRow,
                clean_snapshot.repository_snapshot_id,
            )
            assert freshness is not None
            assert freshness.status == "current"
            assert freshness.reason_code is None
            assert freshness.current_probe_identity == clean_snapshot.repository_state_digest
            assert freshness.probe_version == initial_probe.observation_profile_version
            assert freshness.row_version == 1
            assert (
                await session.scalar(
                    select(func.count()).select_from(RepositorySnapshotFreshnessRow)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RepositorySnapshotInspectionBundleRow)
                    .where(
                        RepositorySnapshotInspectionBundleRow.repository_snapshot_id
                        == clean_snapshot.repository_snapshot_id
                    )
                )
                == 0
            )
            bundle = await session.scalar(
                select(RepositorySnapshotInspectionBundleRow).where(
                    RepositorySnapshotInspectionBundleRow.repository_snapshot_id
                    == dirty_snapshot.repository_snapshot_id
                )
            )
            assert bundle is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RepositorySnapshotInspectionBundleEntryRow)
                    .where(
                        RepositorySnapshotInspectionBundleEntryRow.inspection_bundle_id
                        == bundle.inspection_bundle_id
                    )
                )
                > 0
            )
            assert await session.scalar(select(func.count()).select_from(CommandReceiptRow)) == 4
            events = list((await session.scalars(select(RuntimeEventRow))).all())
            outboxes = list((await session.scalars(select(OutboxRecordRow))).all())
            assert len(events) == len(outboxes) == 2
            snapshots_by_id = {snapshot.repository_snapshot_id: snapshot for snapshot in snapshots}
            outboxes_by_event = {outbox.event_id: outbox for outbox in outboxes}
            for event in events:
                snapshot = snapshots_by_id[UUID(event.aggregate_id)]
                assert event.aggregate_type == "repository_snapshot"
                assert event.event_type == "repository.snapshot.captured"
                assert event.aggregate_sequence == 1
                assert event.payload_json == {
                    "repository_snapshot_id": str(snapshot.repository_snapshot_id),
                    "repository_registration_id": str(registration_id),
                    "repository_snapshot_identity": snapshot.repository_snapshot_identity,
                    "repository_state_digest": snapshot.repository_state_digest,
                    "is_clean": snapshot.is_clean,
                    "snapshot_sequence": snapshot.snapshot_sequence,
                }
                outbox = outboxes_by_event[event.event_id]
                assert outbox.topic == "runtime-events"
                assert outbox.partition_key == (
                    f"repository_snapshot:{snapshot.repository_snapshot_id}"
                )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_snapshot_reuse_rejects_persisted_entry_identity_mismatch(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.models import (
        RepositorySnapshotFreshnessRow,
        RepositorySnapshotInspectionBundleEntryRow,
        RepositorySnapshotInspectionBundleRow,
        RepositorySnapshotRow,
    )
    from labpilot.context.repository_types import (
        CaptureRepositorySnapshot,
        RepositorySnapshotInvariantViolation,
    )
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.db.uow import UnitOfWork
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    repository = tmp_path / "repository"
    repository.mkdir()
    await _run_git(repository, "init", "-q", "-b", "main")
    tracked = repository / "README.md"
    tracked.write_bytes(b"base")
    await _run_git(repository, "add", "README.md")
    await _run_git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    tracked.write_bytes(b"dirty")
    database, service, registration_id = await _seed_capture_service(
        isolated_database_url,
        tmp_path,
        repository,
    )
    context = CommandContext(
        installation_identity="installation",
        actor=CommandActor(actor_type="user", actor_id="user"),
        interaction_correlation_id="capture",
    )
    try:
        dirty = await service.capture_repository_snapshot(
            context=context,
            idempotency_key="capture-dirty",
            command=CaptureRepositorySnapshot(registration_id),
        )
        async with UnitOfWork(database.session) as uow:
            bundle = await uow.session.scalar(
                select(RepositorySnapshotInspectionBundleRow).where(
                    RepositorySnapshotInspectionBundleRow.repository_snapshot_id
                    == UUID(dirty.envelope.primary_result_id)
                )
            )
            assert bundle is not None
            entry = await uow.session.scalar(
                select(RepositorySnapshotInspectionBundleEntryRow)
                .where(
                    RepositorySnapshotInspectionBundleEntryRow.inspection_bundle_id
                    == bundle.inspection_bundle_id
                )
                .order_by(RepositorySnapshotInspectionBundleEntryRow.stable_ordinal)
                .limit(1)
            )
            assert entry is not None
            entry.size_bytes += 1
            await uow.commit()

        async def persistence_counts() -> tuple[int, ...]:
            counts: list[int] = []
            async with database.session() as session:
                for model in (
                    RepositorySnapshotRow,
                    RepositorySnapshotFreshnessRow,
                    RepositorySnapshotInspectionBundleRow,
                    RepositorySnapshotInspectionBundleEntryRow,
                    CommandReceiptRow,
                    RuntimeEventRow,
                    OutboxRecordRow,
                ):
                    value = await session.scalar(select(func.count()).select_from(model))
                    assert value is not None
                    counts.append(value)
            return tuple(counts)

        before_counts = await persistence_counts()
        with pytest.raises(
            RepositorySnapshotInvariantViolation,
            match="bundle_entry_identity_mismatch",
        ):
            await service.capture_repository_snapshot(
                context=context,
                idempotency_key="capture-dirty-entry-invariant",
                command=CaptureRepositorySnapshot(registration_id),
            )
        assert await persistence_counts() == before_counts
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_capture_snapshot_revalidates_locked_registration_identity(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.models import (
        RepositoryRegistrationRow,
        RepositorySnapshotFreshnessRow,
        RepositorySnapshotInspectionBundleEntryRow,
        RepositorySnapshotInspectionBundleRow,
        RepositorySnapshotRow,
    )
    from labpilot.context.repository_service import RepositoryRegistrationConflict
    from labpilot.context.repository_types import CaptureRepositorySnapshot
    from labpilot.db.idempotency_models import CommandReceiptRow
    from labpilot.db.uow import UnitOfWork
    from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow

    repository = tmp_path / "repository"
    repository.mkdir()
    await _run_git(repository, "init", "-q", "-b", "main")
    (repository / "README.md").write_text("base", encoding="utf-8")
    await _run_git(repository, "add", "README.md")
    await _run_git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    database, service, registration_id = await _seed_capture_service(
        isolated_database_url,
        tmp_path,
        repository,
    )
    original_capture = service._git_adapter.capture_snapshot

    async def capture_then_corrupt_identity(*arguments: object, **keywords: object):
        observation = await original_capture(*arguments, **keywords)
        async with UnitOfWork(database.session) as uow:
            registration = await uow.session.get(RepositoryRegistrationRow, registration_id)
            assert registration is not None
            registration.root_path_identity = "f" * 64
            await uow.commit()
        return observation

    monkeypatch.setattr(service._git_adapter, "capture_snapshot", capture_then_corrupt_identity)
    try:
        with pytest.raises(RepositoryRegistrationConflict, match="registration identity"):
            await service.capture_repository_snapshot(
                context=CommandContext(
                    installation_identity="installation",
                    actor=CommandActor(actor_type="user", actor_id="user"),
                    interaction_correlation_id="capture",
                ),
                idempotency_key="capture-registration-identity-race",
                command=CaptureRepositorySnapshot(registration_id),
            )

        async with database.session() as session:
            for model in (
                RepositorySnapshotRow,
                RepositorySnapshotInspectionBundleRow,
                RepositorySnapshotInspectionBundleEntryRow,
                RepositorySnapshotFreshnessRow,
                RuntimeEventRow,
                OutboxRecordRow,
                CommandReceiptRow,
            ):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_capture_dirty_submodule_persists_metadata_only_entry(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from labpilot.common.commands import CommandActor, CommandContext
    from labpilot.context.models import (
        RepositorySnapshotInspectionBundleEntryRow,
        RepositorySnapshotInspectionBundleRow,
        RepositorySnapshotRow,
    )
    from labpilot.context.repository_types import CaptureRepositorySnapshot

    source = tmp_path / "source"
    source.mkdir()
    await _run_git(source, "init", "-q", "-b", "main")
    (source / "source.txt").write_bytes(b"first")
    await _run_git(source, "add", "source.txt")
    await _run_git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "first",
    )
    first_oid = await _run_git(source, "rev-parse", "HEAD")

    repository = tmp_path / "repository"
    repository.mkdir()
    await _run_git(repository, "init", "-q", "-b", "main")
    await _run_git(
        repository,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(source),
        "module",
    )
    await _run_git(repository, "add", "-A")
    await _run_git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )

    (source / "source.txt").write_bytes(b"second")
    await _run_git(source, "add", "source.txt")
    await _run_git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "second",
    )
    second_oid = await _run_git(source, "rev-parse", "HEAD")
    module = repository / "module"
    await _run_git(module, "fetch", "-q", "origin")
    await _run_git(module, "checkout", "-q", second_oid)
    await _run_git(repository, "add", "module")

    database, service, registration_id = await _seed_capture_service(
        isolated_database_url,
        tmp_path,
        repository,
    )
    context = CommandContext(
        installation_identity="installation",
        actor=CommandActor(actor_type="user", actor_id="user"),
        interaction_correlation_id="capture",
    )
    try:
        dirty = await service.capture_repository_snapshot(
            context=context,
            idempotency_key="capture-dirty-submodule",
            command=CaptureRepositorySnapshot(registration_id),
        )
        async with database.session() as session:
            snapshot = await session.get(
                RepositorySnapshotRow,
                UUID(dirty.envelope.primary_result_id),
            )
            assert snapshot is not None
            assert snapshot.submodule_presence is True
            bundle = await session.scalar(
                select(RepositorySnapshotInspectionBundleRow).where(
                    RepositorySnapshotInspectionBundleRow.repository_snapshot_id
                    == snapshot.repository_snapshot_id
                )
            )
            assert bundle is not None
            entry = await session.scalar(
                select(RepositorySnapshotInspectionBundleEntryRow).where(
                    RepositorySnapshotInspectionBundleEntryRow.inspection_bundle_id
                    == bundle.inspection_bundle_id,
                    RepositorySnapshotInspectionBundleEntryRow.repository_path == "module",
                )
            )
            assert entry is not None
            assert (entry.entry_kind, entry.file_type, entry.file_mode) == (
                "tracked_overlay",
                "submodule",
                "160000",
            )
            assert entry.content_ref is None
            assert entry.content_hash is None
            assert entry.size_bytes == 0
            assert entry.head_blob_oid == first_oid
            assert entry.index_blob_oid == second_oid

        await _run_git(repository, "reset", "--hard", "-q", "HEAD")
        await _run_git(repository, "submodule", "update", "-q", "--init")
        await _run_git(repository, "rm", "-q", "-f", "module")
        deleted = await service.capture_repository_snapshot(
            context=context,
            idempotency_key="capture-deleted-submodule",
            command=CaptureRepositorySnapshot(registration_id),
        )
        async with database.session() as session:
            snapshot = await session.get(
                RepositorySnapshotRow,
                UUID(deleted.envelope.primary_result_id),
            )
            assert snapshot is not None
            assert snapshot.submodule_presence is False
    finally:
        await database.dispose()
