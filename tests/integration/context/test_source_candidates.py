from pathlib import Path

import pytest
from sqlalchemy import func, select
from tests.integration.context.test_repository_snapshot import _seed_capture_service
from tests.integration.context.test_repository_source import _capture, _git
from tests.unit.context.test_source_candidates import BASE, CLIPPED, patch

from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import (
    RepositorySnapshotSourceService,
    RepositorySourceReadError,
)
from labpilot.context.source_candidates import SourceCandidateService
from labpilot.db import model_registry  # noqa: F401
from labpilot.db.base import Base

pytestmark = pytest.mark.integration


async def counts(database):
    async with database.session() as session:
        return {
            table.name: (
                await session.execute(select(func.count()).select_from(table))
            ).scalar_one()
            for table in Base.metadata.tables.values()
        }


async def test_actual_snapshot_static_candidate_does_not_write_domain(
    isolated_database_url: str, tmp_path: Path
):
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init", "-q", "-b", "main")
    (repository / "train.py").write_bytes(BASE)
    # Import-path poisoning must not affect the trusted -I AST worker.
    (repository / "ast.py").write_bytes(b'raise RuntimeError("must never import")\n')
    await _git(repository, "add", ".")
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
    database, capture, registration_id = await _seed_capture_service(
        isolated_database_url, tmp_path, repository
    )
    try:
        snapshot = await _capture(capture, registration_id)
        source = RepositorySnapshotSourceService(
            database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        descriptor = await source.describe(snapshot)
        before = await counts(database)
        candidates = SourceCandidateService(source, tmp_path / "candidates")
        ref = await candidates.submit(
            project_id=descriptor.project_id,
            snapshot_id=snapshot,
            snapshot_identity=descriptor.repository_snapshot_identity,
            patch=patch(CLIPPED),
            rationale="fixed clipping",
            actor_id="user",
        )
        result = await candidates.prepare(ref)
        assert await candidates.prepare(ref) == result
        view = await candidates.inspect(result)
        assert view["report"]["runtime_smoke"] == "not_run"
        assert await counts(database) == before
        assert (repository / "train.py").read_bytes() == BASE
        # Existing source verification still verifies S1 rather than the candidate.
        await source.materialize_exact_source(
            repository_snapshot_id=snapshot,
            expected_repository_snapshot_identity=descriptor.repository_snapshot_identity,
            destination=tmp_path / "unchanged",
        )
        assert (tmp_path / "unchanged/train.py").read_bytes() == BASE
        with pytest.raises(RepositorySourceReadError, match="materialization_entries_limit"):
            await source.materialize_exact_source(
                repository_snapshot_id=snapshot,
                expected_repository_snapshot_identity=descriptor.repository_snapshot_identity,
                destination=tmp_path / "limited",
                max_entries=1,
            )
        assert not (tmp_path / "limited").exists()
        with pytest.raises(RepositorySourceReadError):
            await source.materialize_exact_source(
                repository_snapshot_id=snapshot,
                expected_repository_snapshot_identity=descriptor.repository_snapshot_identity,
                destination=tmp_path / "byte-limited",
                max_total_bytes=1,
            )
        assert await counts(database) == before
    finally:
        await database.dispose()
