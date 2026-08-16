from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.coordinator.execution_adapters import (
    RepositorySnapshotMaterializationAdapter,
    VersionExecutionSemanticsAdapter,
)
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.repository import build_preparing_attempt
from labpilot.execution.types import AttemptStatus, trial_identity
from labpilot.experiments.proposal_models import ExperimentVersionRow, ProposalRevisionRow
from labpilot.experiments.proposal_types import proposal_payload_hash
from tests.integration.experiments.test_proposal_service import (
    PROJECT_ID,
    _agent_revision,
    _context,
    _payload,
    _proposal_system,
)


@dataclass(slots=True)
class _PreparedSystem:
    database: object
    attempt_id: UUID
    batch_id: UUID
    workspace: Path
    service: object


@asynccontextmanager
async def _prepared_system(
    isolated_database_url: str,
    tmp_path: Path,
) -> AsyncIterator[_PreparedSystem]:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        proposal = await _agent_revision(system, payload=_payload(system))
        verification = await system.service.verify_revision(
            context=_context(), proposal_revision_id=proposal.proposal_revision_id
        )
        version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=proposal.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        batch_id, trial_id, attempt_id = uuid4(), uuid4(), uuid4()
        parameters = {"learning_rate": 0.1}
        identity = trial_identity(
            experiment_version_identity=version.experiment_version_identity,
            resolved_parameters=parameters,
            seed=7,
        )
        workspace_root = tmp_path / "attempts"
        workspace_root.mkdir()
        now = datetime.now(UTC)
        attempt = build_preparing_attempt(
            attempt_id=attempt_id,
            trial_id=trial_id,
            attempt_number=1,
            installation_identity="installation-1",
            attempt_workspace_root=workspace_root,
            created_at=now,
        )
        async with system.database.session() as session:
            async with session.begin():
                session.add(
                    BatchRow(
                        batch_id=batch_id,
                        project_id=PROJECT_ID,
                        experiment_version_id=version.experiment_version_id,
                        start_requested_at=now,
                        cancel_requested_at=None,
                        row_version=1,
                        created_at=now,
                    )
                )
                await session.flush()
                session.add(
                    TrialRow(
                        trial_id=trial_id,
                        batch_id=batch_id,
                        trial_ordinal=1,
                        trial_identity=identity,
                        resolved_parameters_json=parameters,
                        seed=7,
                        created_at=now,
                    )
                )
                await session.flush()
                session.add(attempt)

        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        from labpilot.execution.preparation_service import AttemptPreparationService

        service = AttemptPreparationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
            snapshot_materialization=RepositorySnapshotMaterializationAdapter(source),
        )
        yield _PreparedSystem(
            system.database,
            attempt_id,
            batch_id,
            Path(attempt.workspace_path),
            service,
        )


async def _attempt(system: _PreparedSystem) -> AttemptRow:
    async with system.database.session() as session:  # type: ignore[attr-defined]
        row = await session.get(AttemptRow, system.attempt_id)
        assert row is not None
        return row


@pytest.mark.integration
async def test_preparation_materializes_exact_source_and_freezes_full_semantics(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]
        row = await _attempt(system)

        assert row.status == AttemptStatus.LAUNCH_READY.value
        assert row.prepared_at is not None and row.terminal_at is None
        assert row.source_manifest_sha256 and row.frozen_execution_spec_hash
        assert row.source_manifest_json is not None
        assert [entry["relative_path"] for entry in row.source_manifest_json["entries"]] == [
            "README.md", "eval.py", "train.py"
        ]
        assert row.frozen_execution_spec_json is not None
        assert row.frozen_execution_spec_json["argv"][-1] == "{output_dir}"
        assert row.frozen_execution_spec_json["framework_requirements"] == ["torch==2.7.0"]
        assert row.frozen_execution_spec_json["environment_expectation_hash"]
        assert (system.workspace / "source" / "train.py").read_text() == "print('train')\n"
        assert (system.workspace / "source").stat().st_mode & 0o222 == 0
        assert (system.workspace / "runtime" / "output").stat().st_mode & 0o220 == 0o220


@pytest.mark.integration
async def test_interruption_after_publish_resumes_same_attempt_without_rewriting_source(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        original = system.service._persist_prepared  # type: ignore[attr-defined]

        async def interrupt(*args: object, **kwargs: object) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(system.service, "_persist_prepared", interrupt)
        with pytest.raises(asyncio.CancelledError):
            await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]
        assert (await _attempt(system)).status == AttemptStatus.PREPARING.value
        source_mtime = (system.workspace / "source" / "train.py").stat().st_mtime_ns

        monkeypatch.setattr(system.service, "_persist_prepared", original)
        await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]

        assert (await _attempt(system)).status == AttemptStatus.LAUNCH_READY.value
        assert (system.workspace / "source" / "train.py").stat().st_mtime_ns == source_mtime


@pytest.mark.integration
async def test_staging_only_is_deleted_and_rebuilt(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        staging = Path(f"{system.workspace}.staging")
        staging.mkdir()
        (staging / "stale").write_text("not authoritative\n")

        await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]

        assert (await _attempt(system)).status == AttemptStatus.LAUNCH_READY.value
        assert not staging.exists()
        assert not (system.workspace / "stale").exists()


@pytest.mark.integration
async def test_final_workspace_tamper_marks_preparation_failed(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        async def interrupt(*args: object, **kwargs: object) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(system.service, "_persist_prepared", interrupt)
        with pytest.raises(asyncio.CancelledError):
            await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]
        source = system.workspace / "source"
        source.chmod(0o755)
        train = source / "train.py"
        train.chmod(0o644)
        train.write_text("tampered\n")

        monkeypatch.undo()
        await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]

        row = await _attempt(system)
        assert row.status == AttemptStatus.PREPARATION_FAILED.value
        assert row.preparation_failure_code == "SOURCE_TAMPERED"
        assert row.terminal_at is not None


@pytest.mark.integration
async def test_cancel_is_revalidated_before_launch_ready(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        original = system.service._snapshot_materialization  # type: ignore[attr-defined]

        class _CancelAfterMaterialization:
            async def materialize_exact_source(self, **kwargs: object):
                manifest = await original.materialize_exact_source(**kwargs)
                async with system.database.session() as session:  # type: ignore[attr-defined]
                    async with session.begin():
                        batch = await session.get(BatchRow, system.batch_id)
                        assert batch is not None
                        batch.cancel_requested_at = datetime.now(UTC)
                return manifest

        system.service._snapshot_materialization = _CancelAfterMaterialization()  # type: ignore[attr-defined]
        await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]

        row = await _attempt(system)
        assert row.status == AttemptStatus.CANCELLED_PRE_LAUNCH.value
        assert row.terminal_at is not None


@pytest.mark.integration
async def test_legacy_version_without_argv_semantics_fails_preparation(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        async with system.database.session() as session:  # type: ignore[attr-defined]
            async with session.begin():
                batch = await session.get(BatchRow, system.batch_id)
                assert batch is not None
                version = await session.get(
                    ExperimentVersionRow, batch.experiment_version_id
                )
                assert version is not None
                revision = await session.get(
                    ProposalRevisionRow, version.proposal_revision_id
                )
                assert revision is not None
                legacy_json = dict(revision.payload_json)
                legacy_json.pop("train_argv_template")
                legacy_hash = proposal_payload_hash(
                    replace(_payload_for_revision(revision), train_argv_template=None)
                )
                revision.payload_json = legacy_json
                revision.payload_hash = legacy_hash
                version.proposal_payload_hash = legacy_hash

        await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]

        row = await _attempt(system)
        assert row.status == AttemptStatus.PREPARATION_FAILED.value
        assert row.preparation_failure_code == "EXECUTION_SEMANTICS_UNSUPPORTED"


def _payload_for_revision(revision: ProposalRevisionRow):
    from labpilot.experiments.proposal_service import _payload_from_json

    return _payload_from_json(revision.payload_json)
