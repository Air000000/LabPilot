from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from tests.integration.context.test_repository_snapshot import _seed_capture_service

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import (
    RepositoryRegistrationRow,
    RepositorySnapshotFreshnessRow,
    RepositorySnapshotRow,
)
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot
from labpilot.db.uow import UnitOfWork


async def _git(repository: Path, *arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/git", *arguments, cwd=repository, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", "replace")


async def _capture(service, registration_id: UUID) -> UUID:
    outcome = await service.capture_repository_snapshot(
        context=CommandContext(
            installation_identity="installation",
            actor=CommandActor(actor_type="user", actor_id="user"),
            interaction_correlation_id="source-test",
        ),
        idempotency_key=f"source-{registration_id}",
        command=CaptureRepositorySnapshot(registration_id),
    )
    return UUID(outcome.envelope.primary_result_id)


async def _new_source(isolated_database_url: str, tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init", "-q", "-b", "main")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    await _git(repository, "add", "tracked.txt")
    await _git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    database, app, registration_id = await _seed_capture_service(
        isolated_database_url, tmp_path, repository
    )
    snapshot_id = await _capture(app, registration_id)
    source = RepositorySnapshotSourceService(
        database.session,
        git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )
    return database, repository, registration_id, snapshot_id, source


@pytest.mark.integration
async def test_approval_readiness_uses_live_probe_without_mutating_freshness(
    isolated_database_url: str, tmp_path: Path
) -> None:
    database, repository, registration_id, snapshot_id, source = await _new_source(
        isolated_database_url, tmp_path
    )
    try:
        async with database.session() as session:
            freshness = await session.get(RepositorySnapshotFreshnessRow, snapshot_id)
            assert freshness is not None
            before = (
                freshness.status,
                freshness.reason_code,
                freshness.current_probe_identity,
                freshness.checked_at,
                freshness.row_version,
            )

        assert (await source.check_approval_readiness(snapshot_id)).status == "ready"
        async with database.session() as session:
            freshness = await session.get(RepositorySnapshotFreshnessRow, snapshot_id)
            assert freshness is not None
            assert (
                freshness.status,
                freshness.reason_code,
                freshness.current_probe_identity,
                freshness.checked_at,
                freshness.row_version,
            ) == before

        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
        assert (await source.check_approval_readiness(snapshot_id)).status == "repository_changed"
        await _git(repository, "checkout", "--", "tracked.txt")
        (repository / "untracked.txt").write_text("new\n", encoding="utf-8")
        assert (await source.check_approval_readiness(snapshot_id)).status == "repository_changed"
        (repository / "untracked.txt").unlink()
        (repository / "staged.txt").write_text("stage\n", encoding="utf-8")
        await _git(repository, "add", "staged.txt")
        assert (await source.check_approval_readiness(snapshot_id)).status == "repository_changed"
        await _git(repository, "reset", "--hard", "HEAD")
        await _git(repository, "checkout", "-q", "-b", "other")
        assert (await source.check_approval_readiness(snapshot_id)).status == "repository_changed"
        await _git(repository, "checkout", "-q", "main")

        async with UnitOfWork(database.session) as uow:
            snapshot = await uow.session.get(RepositorySnapshotRow, snapshot_id)
            assert snapshot is not None
            snapshot.is_clean = False
            await uow.commit()
        assert (await source.check_approval_readiness(snapshot_id)).status == "snapshot_dirty"
        async with UnitOfWork(database.session) as uow:
            snapshot = await uow.session.get(RepositorySnapshotRow, snapshot_id)
            registration = await uow.session.get(RepositoryRegistrationRow, registration_id)
            assert snapshot is not None and registration is not None
            snapshot.is_clean = True
            registration.status = "revoked"
            await uow.commit()
        assert (
            await source.check_approval_readiness(snapshot_id)
        ).status == "registration_not_active"
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_approval_readiness_reports_missing_snapshot_and_repository(
    isolated_database_url: str, tmp_path: Path
) -> None:
    database, repository, _, snapshot_id, source = await _new_source(
        isolated_database_url, tmp_path
    )
    try:
        missing = UUID("10000000-0000-4000-8000-000000000099")
        assert (await source.check_approval_readiness(missing)).status == "snapshot_not_found"
        repository.rename(tmp_path / "gone")
        assert (
            await source.check_approval_readiness(snapshot_id)
        ).status == "repository_unavailable"
    finally:
        await database.dispose()
