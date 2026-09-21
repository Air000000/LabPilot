from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from tests.integration.context.test_repository_snapshot import _seed_capture_service
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot
from labpilot.db.session import Database
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
    dataset_reference_hash,
    environment_expectation_hash,
    experiment_version_identity,
    validation_protocol_hash,
)
from labpilot.experiments.proposal_verifier import ProposalVerifier

PROJECT_ID = UUID("10000000-0000-4000-8000-000000000020")


def _context(*, actor_id: str = "air", actor_type: str = "local_user") -> CommandContext:
    return CommandContext(
        installation_identity="installation-1",
        actor=CommandActor(actor_type=actor_type, actor_id=actor_id),
        interaction_correlation_id="proposal-service-test",
    )


async def _git(repository: Path, *arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/git",
        *arguments,
        cwd=repository,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", "replace")


@dataclass(slots=True)
class _ProposalSystem:
    database: Database
    service: Any
    repository: Path
    snapshot_id: UUID
    snapshot_identity: str


@asynccontextmanager
async def _proposal_system(
    isolated_database_url: str,
    tmp_path: Path,
) -> AsyncIterator[_ProposalSystem]:
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init", "-q", "-b", "main")
    (repository / "train.py").write_text("print('train')\n", encoding="utf-8")
    (repository / "eval.py").write_text("print('eval')\n", encoding="utf-8")
    (repository / "README.md").write_text("proposal fixture\n", encoding="utf-8")
    await _git(repository, "add", "train.py", "eval.py", "README.md")
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
    database, capture_service, registration_id = await _seed_capture_service(
        isolated_database_url,
        tmp_path,
        repository,
    )
    try:
        captured = await capture_service.capture_repository_snapshot(
            context=_context(),
            idempotency_key=f"capture-{registration_id}",
            command=CaptureRepositorySnapshot(registration_id),
        )
        snapshot_id = UUID(captured.envelope.primary_result_id)
        source = RepositorySnapshotSourceService(
            database.session,
            git_adapter=GitAdapter(
                git_executable=Path("/usr/bin/git"),
                timeout_seconds=2,
            ),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        adapter = ProposalRepositoryFactAdapter(source)
        descriptor = await source.describe(snapshot_id)
        from labpilot.experiments.proposal_service import ProposalApplicationService

        yield _ProposalSystem(
            database=database,
            service=ProposalApplicationService(
                database.session,
                repository_facts=adapter,
                verifier=ProposalVerifier(adapter),
            ),
            repository=repository,
            snapshot_id=snapshot_id,
            snapshot_identity=descriptor.repository_snapshot_identity,
        )
    finally:
        await database.dispose()


def _payload(system: _ProposalSystem, **changes: object) -> ProposalPayload:
    payload = ProposalPayload(
        goal="Reproduce the baseline",
        experiment_type="reproduction",
        repository_snapshot_id=system.snapshot_id,
        repository_snapshot_identity=system.snapshot_identity,
        train_entrypoint="train.py",
        eval_entrypoint="eval.py",
        workdir=".",
        parameter_matrix={"learning_rate": (0.1,)},
        seeds=(7,),
        dataset_reference=DatasetReference(
            reference_type="uri",
            path_or_uri="s3://datasets/example",
            stable_identity="dataset:v1",
            reproducibility_level="strict",
        ),
        environment_expectation=EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=("torch==2.7.0",),
            device_requirement="cuda",
            cuda_requirement="12.8",
        ),
        metrics=(MetricDeclaration("accuracy", "model", "test"),),
        artifacts=(ArtifactDeclaration("model", "out/model.pt", True),),
        validation_rules=(
            ValidationRule("artifact_exists", "model"),
            ValidationRule("metric_finite", "accuracy"),
        ),
        timeout_seconds=3600,
        max_retries=1,
        train_argv_template=(
            "--learning-rate",
            "{parameter:learning_rate}",
            "--seed",
            "{seed}",
            "--dataset",
            "{dataset}",
            "--output-dir",
            "{output_dir}",
        ),
    )
    return replace(payload, **changes)


async def _agent_revision(
    system: _ProposalSystem,
    *,
    payload: ProposalPayload | None = None,
    origin_agent_step_id: UUID | None = None,
):
    return await system.service.create_agent_revision(
        context=_context(),
        project_id=PROJECT_ID,
        origin_agent_step_id=origin_agent_step_id or uuid4(),
        payload=payload or _payload(system),
    )


async def _table_count(database: Database, table_name: str) -> int:
    async with database.session() as session:
        return int(await session.scalar(text(f"SELECT count(*) FROM {table_name}")))


def _synchronize_concurrent_origin_inserts(
    system: _ProposalSystem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = system.service._repository.get_revision_by_origin_step
    call_counts: dict[asyncio.Task[Any], int] = {}
    barrier = asyncio.Barrier(2)

    async def synchronized_lookup(
        session: Any,
        origin_agent_step_id: UUID,
    ) -> Any:
        result = await original(session, origin_agent_step_id)
        task = asyncio.current_task()
        assert task is not None
        call_counts[task] = call_counts.get(task, 0) + 1
        if call_counts[task] == 2:
            assert result is None
            await barrier.wait()
        return result

    monkeypatch.setattr(
        system.service._repository,
        "get_revision_by_origin_step",
        synchronized_lookup,
    )


def _synchronize_concurrent_verification_inserts(
    system: _ProposalSystem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = system.service._repository.get_verification_by_key
    barrier = asyncio.Barrier(2)

    async def synchronized_lookup(
        session: Any,
        *,
        proposal_revision_id: UUID,
        verifier_profile: str,
        proposal_payload_hash: str,
    ) -> Any:
        result = await original(
            session,
            proposal_revision_id=proposal_revision_id,
            verifier_profile=verifier_profile,
            proposal_payload_hash=proposal_payload_hash,
        )
        if result is None:
            await barrier.wait()
        return result

    monkeypatch.setattr(
        system.service._repository,
        "get_verification_by_key",
        synchronized_lookup,
    )


@pytest.mark.integration
async def test_migration_0007_creates_exactly_five_experiment_tables(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with database.session() as session:
            tables = set(
                (
                    await session.execute(
                        text(
                            """
                            SELECT table_name
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name LIKE 'experiment%'
                            """
                        )
                    )
                ).scalars()
            )
            snapshot_foreign_keys = int(
                await session.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM information_schema.constraint_column_usage
                        WHERE table_schema = 'public'
                          AND table_name = 'repository_snapshots'
                          AND column_name = 'repository_snapshot_id'
                          AND constraint_name LIKE 'fk_experiment%'
                        """
                    )
                )
            )
    finally:
        await database.dispose()

    assert tables == {
        "experiments",
        "experiment_proposal_revisions",
        "experiment_proposal_verifications",
        "experiment_design_decisions",
        "experiment_versions",
    }
    assert snapshot_foreign_keys == 0


@pytest.mark.integration
async def test_revision_is_immutable_manual_edits_increment_and_agent_replay_is_exact(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_models import ProposalRevisionRow
    from labpilot.experiments.proposal_service import ProposalInvariantViolation

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        origin = uuid4()
        first = await _agent_revision(system, origin_agent_step_id=origin)
        replay = await _agent_revision(system, origin_agent_step_id=origin)
        assert replay.proposal_revision_id == first.proposal_revision_id

        with pytest.raises(ProposalInvariantViolation):
            await system.service.create_agent_revision(
                context=_context(),
                project_id=UUID("10000000-0000-4000-8000-000000000099"),
                origin_agent_step_id=origin,
                payload=_payload(system),
            )

        with pytest.raises(ProposalInvariantViolation):
            await _agent_revision(
                system,
                origin_agent_step_id=origin,
                payload=_payload(system, goal="Changed semantics"),
            )

        second = await system.service.create_manual_revision(
            context=_context(),
            experiment_id=first.experiment_id,
            payload=_payload(system, goal="Manual edit"),
        )
        third = await system.service.create_manual_revision(
            context=_context(),
            experiment_id=first.experiment_id,
            payload=_payload(system, goal="Second manual edit"),
        )

        async with system.database.session() as session:
            stored_first = await session.get(
                ProposalRevisionRow,
                first.proposal_revision_id,
            )
            assert stored_first is not None
            assert stored_first.payload_json["goal"] == "Reproduce the baseline"
        assert (first.revision_number, second.revision_number, third.revision_number) == (
            1,
            2,
            3,
        )
        assert not hasattr(system.service, "update_revision")


@pytest.mark.integration
async def test_concurrent_same_payload_agent_replay_returns_one_revision(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        origin_agent_step_id = uuid4()
        payload = _payload(system)
        _synchronize_concurrent_origin_inserts(system, monkeypatch)

        revisions = await asyncio.gather(
            _agent_revision(
                system,
                origin_agent_step_id=origin_agent_step_id,
                payload=payload,
            ),
            _agent_revision(
                system,
                origin_agent_step_id=origin_agent_step_id,
                payload=payload,
            ),
        )

        assert len({revision.proposal_revision_id for revision in revisions}) == 1
        assert await _table_count(system.database, "experiments") == 1
        assert await _table_count(
            system.database,
            "experiment_proposal_revisions",
        ) == 1


@pytest.mark.integration
async def test_concurrent_changed_payload_agent_replay_returns_domain_conflict(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labpilot.experiments.proposal_service import ProposalInvariantViolation

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        origin_agent_step_id = uuid4()
        _synchronize_concurrent_origin_inserts(system, monkeypatch)

        outcomes = await asyncio.gather(
            _agent_revision(
                system,
                origin_agent_step_id=origin_agent_step_id,
                payload=_payload(system, goal="First semantics"),
            ),
            _agent_revision(
                system,
                origin_agent_step_id=origin_agent_step_id,
                payload=_payload(system, goal="Second semantics"),
            ),
            return_exceptions=True,
        )

        successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ProposalInvariantViolation)
        assert await _table_count(system.database, "experiments") == 1
        assert await _table_count(
            system.database,
            "experiment_proposal_revisions",
        ) == 1


@pytest.mark.integration
async def test_revision_creation_rejects_project_snapshot_ownership_mismatch(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalInvariantViolation

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        with pytest.raises(ProposalInvariantViolation):
            await system.service.create_agent_revision(
                context=_context(),
                project_id=UUID("10000000-0000-4000-8000-000000000099"),
                origin_agent_step_id=uuid4(),
                payload=_payload(system),
            )


@pytest.mark.integration
async def test_get_verification_returns_persisted_read_without_reverification(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labpilot.experiments.proposal_service import ProposalNotFound

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        persisted = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        before = await _table_count(system.database, "experiment_proposal_verifications")

        async def explode_verify(*, project_id: UUID, payload: ProposalPayload) -> None:
            raise AssertionError("persisted verification read must not run verifier")

        with monkeypatch.context() as patch:
            patch.setattr(system.service._verifier, "verify", explode_verify)
            fetched = await system.service.get_verification(
                persisted.proposal_verification_id
            )

        after = await _table_count(system.database, "experiment_proposal_verifications")
        assert fetched.proposal_verification_id == persisted.proposal_verification_id
        assert fetched.proposal_revision_id == persisted.proposal_revision_id
        assert fetched.proposal_payload_hash == persisted.proposal_payload_hash
        assert fetched.result == persisted.result
        assert (before, after) == (1, 1)

        with pytest.raises(ProposalNotFound):
            await system.service.get_verification(uuid4())


@pytest.mark.integration
async def test_verification_is_immutable_and_reused_by_frozen_semantic_key(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        first = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        second = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )

        assert first.proposal_verification_id == second.proposal_verification_id
        assert first.proposal_payload_hash == revision.payload_hash
        assert first.result.passed is True
        assert await _table_count(
            system.database,
            "experiment_proposal_verifications",
        ) == 1


@pytest.mark.integration
async def test_concurrent_verification_reuses_one_semantic_key_row(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        _synchronize_concurrent_verification_inserts(system, monkeypatch)

        verifications = await asyncio.gather(
            system.service.verify_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
            ),
            system.service.verify_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
            ),
        )

        assert len(
            {verification.proposal_verification_id for verification in verifications}
        ) == 1
        assert await _table_count(
            system.database,
            "experiment_proposal_verifications",
        ) == 1


@pytest.mark.integration
async def test_live_change_rejects_approval_then_restore_reuses_verification_and_version(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_models import (
        DesignDecisionRow,
        ExperimentVersionRow,
    )
    from labpilot.experiments.proposal_service import (
        ProposalApprovalNotReady,
        ProposalDecisionConflict,
    )

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        (system.repository / "train.py").write_text("changed\n", encoding="utf-8")

        reused = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        assert reused.proposal_verification_id == verification.proposal_verification_id
        with pytest.raises(ProposalApprovalNotReady):
            await system.service.approve_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
        assert await _table_count(system.database, "experiment_design_decisions") == 0
        assert await _table_count(system.database, "experiment_versions") == 0

        await _git(system.repository, "checkout", "--", "train.py")
        version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        replay = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        assert replay.experiment_version_id == version.experiment_version_id
        with pytest.raises(ProposalDecisionConflict):
            await system.service.approve_revision(
                context=_context(actor_id="different-actor"),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )

        expected_dataset_hash = dataset_reference_hash(revision.payload.dataset_reference)
        expected_environment_hash = environment_expectation_hash(
            revision.payload.environment_expectation
        )
        expected_validation_hash = validation_protocol_hash(
            revision.payload.validation_rules
        )
        expected_identity = experiment_version_identity(
            experiment_id=revision.experiment_id,
            proposal_payload_hash_value=revision.payload_hash,
            repository_snapshot_identity=revision.payload.repository_snapshot_identity,
            dataset_reference_hash_value=expected_dataset_hash,
            environment_expectation_hash_value=expected_environment_hash,
            validation_protocol_hash_value=expected_validation_hash,
        )
        async with system.database.session() as session:
            stored_version = await session.get(
                ExperimentVersionRow,
                version.experiment_version_id,
            )
            decision = await session.get(
                DesignDecisionRow,
                version.design_decision_id,
            )
        assert stored_version is not None and decision is not None
        assert stored_version.experiment_version_identity == expected_identity
        assert stored_version.proposal_payload_hash == revision.payload_hash
        assert stored_version.repository_snapshot_id == system.snapshot_id
        assert stored_version.repository_snapshot_identity == system.snapshot_identity
        assert stored_version.dataset_reference_hash == expected_dataset_hash
        assert stored_version.environment_expectation_hash == expected_environment_hash
        assert stored_version.validation_protocol_hash == expected_validation_hash
        assert decision.proposal_verification_id == verification.proposal_verification_id
        assert decision.proposal_payload_hash == revision.payload_hash
        assert decision.actor_type == "local_user"
        assert decision.actor_id == "air"
        assert decision.decided_at is not None
        assert not hasattr(system.service, "update_version")


@pytest.mark.integration
async def test_distinct_revisions_with_same_semantics_get_distinct_versions(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        payload = _payload(system)
        first_revision = await _agent_revision(system, payload=payload)
        second_revision = await system.service.create_manual_revision(
            context=_context(),
            experiment_id=first_revision.experiment_id,
            payload=payload,
        )
        first_verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=first_revision.proposal_revision_id,
        )
        second_verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=second_revision.proposal_revision_id,
        )
        first_version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=first_revision.proposal_revision_id,
            verification_id=first_verification.proposal_verification_id,
        )
        second_version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=second_revision.proposal_revision_id,
            verification_id=second_verification.proposal_verification_id,
        )
        replayed = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=second_revision.proposal_revision_id,
            verification_id=second_verification.proposal_verification_id,
        )

        assert first_revision.proposal_revision_id != second_revision.proposal_revision_id
        assert first_version.experiment_version_id != second_version.experiment_version_id
        assert first_version.design_decision_id != second_version.design_decision_id
        assert first_version.proposal_revision_id == first_revision.proposal_revision_id
        assert second_version.proposal_revision_id == second_revision.proposal_revision_id
        assert (first_version.version_number, second_version.version_number) == (1, 2)
        assert (
            first_version.experiment_version_identity
            == second_version.experiment_version_identity
        )
        assert replayed.experiment_version_id == second_version.experiment_version_id
        assert await _table_count(system.database, "experiment_design_decisions") == 2
        assert await _table_count(system.database, "experiment_versions") == 2


@pytest.mark.integration
async def test_failed_verification_cannot_approve(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalVerificationConflict

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(
            system,
            payload=_payload(system, train_entrypoint="missing.py"),
        )
        verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        assert verification.result.passed is False

        with pytest.raises(ProposalVerificationConflict):
            await system.service.approve_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
        assert await _table_count(system.database, "experiment_design_decisions") == 0
        assert await _table_count(system.database, "experiment_versions") == 0


@pytest.mark.integration
async def test_rejected_decision_never_materializes_a_version(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalDecisionConflict

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )
        decision = await system.service.reject_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
            reason="Human rejected the design",
        )
        assert decision.decision == "rejected"
        assert decision.proposal_verification_id == verification.proposal_verification_id
        assert await _table_count(system.database, "experiment_versions") == 0

        with pytest.raises(ProposalDecisionConflict):
            await system.service.approve_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
        assert await _table_count(system.database, "experiment_versions") == 0


@pytest.mark.integration
async def test_unverified_local_user_may_reject_without_verification(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)

        decision = await system.service.reject_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=None,
            reason="Human rejected before verification",
        )

        assert decision.decision == "rejected"
        assert decision.proposal_verification_id is None
        assert await _table_count(system.database, "experiment_design_decisions") == 1
        assert await _table_count(system.database, "experiment_versions") == 0


@pytest.mark.integration
async def test_null_rejection_conflicts_after_verification_exists(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalVerificationConflict

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )

        with pytest.raises(ProposalVerificationConflict):
            await system.service.reject_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=None,
                reason="Do not omit the existing verification",
            )

        assert await _table_count(system.database, "experiment_design_decisions") == 0
        assert await _table_count(system.database, "experiment_versions") == 0


@pytest.mark.integration
async def test_non_local_user_cannot_reject_without_verification(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalInvariantViolation

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)

        with pytest.raises(ProposalInvariantViolation):
            await system.service.reject_revision(
                context=_context(actor_type="agent"),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=None,
                reason="Agent attempted an unverified rejection",
            )

        assert await _table_count(system.database, "experiment_design_decisions") == 0
        assert await _table_count(system.database, "experiment_versions") == 0


@pytest.mark.integration
async def test_semantically_mismatched_verification_cannot_reuse_decision_or_version(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalVerificationConflict

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        first = await _agent_revision(system)
        second = await system.service.create_manual_revision(
            context=_context(),
            experiment_id=first.experiment_id,
            payload=_payload(system, goal="Different semantics"),
        )
        first_verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=first.proposal_revision_id,
        )
        await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=second.proposal_revision_id,
        )

        with pytest.raises(ProposalVerificationConflict):
            await system.service.approve_revision(
                context=_context(),
                proposal_revision_id=second.proposal_revision_id,
                verification_id=first_verification.proposal_verification_id,
            )
        assert await _table_count(system.database, "experiment_design_decisions") == 0
        assert await _table_count(system.database, "experiment_versions") == 0


@pytest.mark.integration
async def test_concurrent_distinct_approvals_serialize_to_consecutive_versions(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        first = await _agent_revision(system)
        second = await system.service.create_manual_revision(
            context=_context(),
            experiment_id=first.experiment_id,
            payload=_payload(system, goal="Different approved semantics"),
        )
        first_verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=first.proposal_revision_id,
        )
        second_verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=second.proposal_revision_id,
        )

        versions = await asyncio.gather(
            system.service.approve_revision(
                context=_context(),
                proposal_revision_id=first.proposal_revision_id,
                verification_id=first_verification.proposal_verification_id,
            ),
            system.service.approve_revision(
                context=_context(),
                proposal_revision_id=second.proposal_revision_id,
                verification_id=second_verification.proposal_verification_id,
            ),
        )

        assert {version.version_number for version in versions} == {1, 2}
        assert len({version.experiment_version_id for version in versions}) == 2
        assert len({version.experiment_version_identity for version in versions}) == 2
        assert await _table_count(system.database, "experiment_design_decisions") == 2
        assert await _table_count(system.database, "experiment_versions") == 2


@pytest.mark.integration
async def test_version_insert_failure_rolls_back_staged_decision(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        revision = await _agent_revision(system)
        verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
        )

        async def fail_version_insert(*args: object, **kwargs: object) -> None:
            raise RuntimeError("forced version insert failure")

        monkeypatch.setattr(system.service._repository, "add_version", fail_version_insert)
        with pytest.raises(RuntimeError, match="forced version insert failure"):
            await system.service.approve_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )

        assert await _table_count(system.database, "experiment_design_decisions") == 0
        assert await _table_count(system.database, "experiment_versions") == 0



@pytest.mark.integration
async def test_threshold_validation_rule_persists_and_reloads_canonically(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from labpilot.experiments.proposal_models import ProposalRevisionRow

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        payload = _payload(
            system,
            validation_rules=(
                ValidationRule("artifact_exists", "model"),
                ValidationRule("metric_finite", "accuracy"),
                ValidationRule("metric_gte", "accuracy", Decimal("0.80")),
            ),
        )
        revision = await _agent_revision(system, payload=payload)
        reloaded = await system.service.get_revision(revision.proposal_revision_id)
        async with system.database.session() as session:
            row = await session.get(ProposalRevisionRow, revision.proposal_revision_id)

    assert row is not None
    assert row.payload_json["validation_rules"][-1]["threshold"] == "0.8"
    assert "threshold" not in row.payload_json["validation_rules"][0]
    assert reloaded.payload.validation_rules[-1].threshold == Decimal("0.8")


async def _follow_up_revision(
    system: _ProposalSystem,
    *,
    experiment_id: UUID,
    origin_agent_step_id: UUID | None = None,
    payload: ProposalPayload | None = None,
    project_id: UUID = PROJECT_ID,
):
    return await system.service.create_follow_up_agent_revision(
        context=_context(),
        project_id=project_id,
        experiment_id=experiment_id,
        origin_agent_step_id=origin_agent_step_id or uuid4(),
        payload=payload or _payload(system),
    )


@pytest.mark.integration
async def test_follow_up_agent_revisions_reuse_experiment_and_sequence(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        initial = await _agent_revision(system)
        experiments_before = await _table_count(system.database, "experiments")
        second = await _follow_up_revision(system, experiment_id=initial.experiment_id)
        third = await _follow_up_revision(system, experiment_id=initial.experiment_id)

    assert initial.revision_number == 1
    assert (second.experiment_id, second.revision_number) == (initial.experiment_id, 2)
    assert (third.experiment_id, third.revision_number) == (initial.experiment_id, 3)
    assert await _table_count(system.database, "experiments") == experiments_before


@pytest.mark.integration
async def test_follow_up_agent_revision_replay_is_exact_and_experiment_bound(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalInvariantViolation

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        initial = await _agent_revision(system)
        origin = uuid4()
        first = await _follow_up_revision(
            system,
            experiment_id=initial.experiment_id,
            origin_agent_step_id=origin,
        )
        replay = await _follow_up_revision(
            system,
            experiment_id=initial.experiment_id,
            origin_agent_step_id=origin,
        )
        with pytest.raises(ProposalInvariantViolation):
            await _follow_up_revision(
                system,
                experiment_id=initial.experiment_id,
                origin_agent_step_id=origin,
                payload=_payload(system, goal="Changed semantics"),
            )
        other = await _agent_revision(system)
        with pytest.raises(ProposalInvariantViolation):
            await _follow_up_revision(
                system,
                experiment_id=other.experiment_id,
                origin_agent_step_id=origin,
            )

    assert replay.proposal_revision_id == first.proposal_revision_id
    assert replay.revision_number == 2


@pytest.mark.integration
async def test_follow_up_agent_revision_rejects_wrong_project_without_write(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    from labpilot.experiments.proposal_service import ProposalInvariantViolation

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        initial = await _agent_revision(system)
        revisions_before = await _table_count(
            system.database, "experiment_proposal_revisions"
        )
        with pytest.raises(ProposalInvariantViolation):
            await _follow_up_revision(
                system,
                experiment_id=initial.experiment_id,
                project_id=UUID("10000000-0000-4000-8000-000000000099"),
            )

    assert await _table_count(
        system.database, "experiment_proposal_revisions"
    ) == revisions_before


@pytest.mark.integration
async def test_concurrent_distinct_follow_up_agent_revisions_are_consecutive(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        initial = await _agent_revision(system)
        revisions = await asyncio.gather(
            _follow_up_revision(system, experiment_id=initial.experiment_id),
            _follow_up_revision(system, experiment_id=initial.experiment_id),
        )

    assert {revision.experiment_id for revision in revisions} == {initial.experiment_id}
    assert {revision.revision_number for revision in revisions} == {2, 3}
    assert await _table_count(system.database, "experiments") == 1


@pytest.mark.integration
async def test_concurrent_same_follow_up_origin_recovers_one_winner(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        initial = await _agent_revision(system)
        origin = uuid4()
        _synchronize_concurrent_origin_inserts(system, monkeypatch)
        revisions = await asyncio.gather(
            _follow_up_revision(
                system,
                experiment_id=initial.experiment_id,
                origin_agent_step_id=origin,
            ),
            _follow_up_revision(
                system,
                experiment_id=initial.experiment_id,
                origin_agent_step_id=origin,
            ),
        )

    assert len({revision.proposal_revision_id for revision in revisions}) == 1
    assert {revision.revision_number for revision in revisions} == {2}
    assert await _table_count(
        system.database, "experiment_proposal_revisions"
    ) == 2
