import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from tests.integration.context.test_repository_snapshot import _seed_capture_service
from tests.integration.context.test_repository_source import _git
from tests.integration.context.test_source_candidates import counts
from tests.integration.db.test_migrations import _alembic_config
from tests.integration.experiments.test_proposal_service import _payload
from tests.unit.context.test_source_candidates import BASE, CLIPPED, patch

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import RepositoryRegistrationRow, SourceAdoptionRow
from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot
from labpilot.context.source_adoption import AdoptionError, AdoptSource, SourceAdoptionService
from labpilot.context.source_candidates import CandidateError, SourceCandidateService
from labpilot.db.idempotency import IdempotencyKeyConflict
from labpilot.experiments.proposal_models import ExperimentRow
from labpilot.experiments.proposal_service import ProposalApplicationService
from labpilot.experiments.proposal_verifier import ProposalVerifier

pytestmark = pytest.mark.integration
CONTEXT = CommandContext("test", CommandActor("user", "human"), "adoption-test")


async def commit(repository):
    await _git(repository, "add", ".")
    await _git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "manual source commit",
    )


async def capture(system, key):
    outcome = await system.capture.capture_repository_snapshot(
        context=CONTEXT,
        idempotency_key=key,
        command=CaptureRepositorySnapshot(system.registration_id),
    )
    return UUID(outcome.envelope.primary_result_id)


@pytest_asyncio.fixture
async def system(isolated_database_url, tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    await _git(repository, "init", "-q", "-b", "main")
    (repository / "train.py").write_bytes(BASE)
    (repository / "eval.py").write_text("raise RuntimeError('never execute')\n")
    await commit(repository)
    database, app, registration = await _seed_capture_service(
        isolated_database_url, tmp_path, repository
    )
    source = RepositorySnapshotSourceService(
        database.session,
        git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
        content_store=ContentAddressedStore(tmp_path / "content"),
    )
    result = SimpleNamespace(
        database=database,
        capture=app,
        registration_id=registration,
        source=source,
        repository=repository,
        tmp=tmp_path,
        database_url=isolated_database_url,
    )
    try:
        result.snapshot_id = await capture(result, "s1")
        descriptor = await source.describe(result.snapshot_id)
        result.snapshot_identity = descriptor.repository_snapshot_identity
        result.project_id = descriptor.project_id
        result.candidates = SourceCandidateService(source, tmp_path / "candidates")
        result.package = await package(result)
        result.adoptions = SourceAdoptionService(database.session, result.candidates)
        yield result
    finally:
        await database.dispose()


async def package(system, rationale="clipping"):
    request = await system.candidates.submit(
        project_id=system.project_id,
        snapshot_id=system.snapshot_id,
        snapshot_identity=system.snapshot_identity,
        patch=patch(CLIPPED),
        rationale=rationale,
        actor_id="human",
    )
    return await system.candidates.prepare(request)


async def target(system):
    (system.repository / "train.py").write_bytes(CLIPPED)
    await commit(system.repository)
    snapshot = await capture(system, "s2")
    descriptor = await system.source.describe(snapshot)
    return AdoptSource(
        project_id=system.project_id,
        package_ref=system.package,
        target_snapshot_id=snapshot,
        target_snapshot_identity=descriptor.repository_snapshot_identity,
    )


async def adopt(system, cmd, key="adopt", context=CONTEXT):
    return await system.adoptions.adopt(context=context, idempotency_key=key, command=cmd)


async def test_manual_adoption_then_existing_manual_verification_approval(system):
    facts = ProposalRepositoryFactAdapter(system.source)
    service = ProposalApplicationService(
        system.database.session, repository_facts=facts, verifier=ProposalVerifier(facts)
    )
    experiment_id = uuid4()
    async with system.database.session() as session:
        session.add(ExperimentRow(experiment_id=experiment_id, project_id=system.project_id))
        await session.commit()
    first = await service.create_manual_revision(
        context=CONTEXT, experiment_id=experiment_id, payload=_payload(system)
    )
    v = await service.verify_revision(
        context=CONTEXT, proposal_revision_id=first.proposal_revision_id
    )
    v1 = await service.approve_revision(
        context=CONTEXT,
        proposal_revision_id=first.proposal_revision_id,
        verification_id=v.proposal_verification_id,
    )
    cmd = await target(system)
    # Old capture key is a replay of S1, never a request to find latest.
    assert await capture(system, "s1") == system.snapshot_id
    before = await counts(system.database)
    result = await adopt(system, cmd)
    after = await counts(system.database)
    allowed = {
        "source_adoptions",
        "runtime_events",
        "aggregate_event_cursors",
        "outbox_records",
        "command_receipts",
    }
    assert all(after[t] == n for t, n in before.items() if t not in allowed)
    assert after["source_adoptions"] == 1
    async with system.database.session() as session:
        row = await session.get(SourceAdoptionRow, UUID(result.envelope.primary_result_id))
        assert row.base_snapshot_id == system.snapshot_id
        assert row.target_snapshot_id == cmd.target_snapshot_id
        assert row.package_ref == system.package and row.actor_id == "human"
    second = await service.create_manual_revision(
        context=CONTEXT,
        experiment_id=experiment_id,
        payload=replace(
            _payload(system),
            repository_snapshot_id=cmd.target_snapshot_id,
            repository_snapshot_identity=cmd.target_snapshot_identity,
        ),
    )
    assert (await counts(system.database))["experiment_versions"] == 1
    verified = await service.verify_revision(
        context=CONTEXT, proposal_revision_id=second.proposal_revision_id
    )
    v2 = await service.approve_revision(
        context=CONTEXT,
        proposal_revision_id=second.proposal_revision_id,
        verification_id=verified.proposal_verification_id,
    )
    assert v2.experiment_id == v1.experiment_id == experiment_id
    assert v2.repository_snapshot_id == cmd.target_snapshot_id
    assert (
        await service.get_version(v1.experiment_version_id)
    ).repository_snapshot_id == system.snapshot_id
    await system.source.materialize_exact_source(
        repository_snapshot_id=system.snapshot_id,
        expected_repository_snapshot_identity=system.snapshot_identity,
        destination=system.tmp / "old-v1",
    )
    assert (system.tmp / "old-v1/train.py").read_bytes() == BASE


async def test_concurrent_reuse_conflict_and_historical_receipt_replay(system, monkeypatch):
    cmd = await target(system)
    first, second = await asyncio.gather(adopt(system, cmd), adopt(system, cmd))
    assert first.envelope == second.envelope
    reuse = await adopt(system, cmd, "new-key")
    assert reuse.envelope == first.envelope
    assert (await counts(system.database))["source_adoptions"] == 1
    with pytest.raises(IdempotencyKeyConflict):
        await adopt(system, cmd.model_copy(update={"target_snapshot_identity": "f" * 64}))
    another = await package(system, "different accepted rationale")
    with pytest.raises(AdoptionError, match="adoption_target_conflict"):
        await adopt(system, cmd.model_copy(update={"package_ref": another}), "conflict")
    import labpilot.context.source_candidates as candidates_module

    old = candidates_module._checker(system.candidates.root)
    monkeypatch.setattr(
        candidates_module,
        "_checker",
        lambda root: old.model_copy(update={"python_version": "future"}),
    )
    assert (await adopt(system, cmd)).replayed
    with pytest.raises(CandidateError, match="checker_version_mismatch"):
        await adopt(system, cmd, "new-checker-key")
    monkeypatch.undo()
    (system.repository / "untracked").write_text("dirty")
    assert (await adopt(system, cmd)).replayed
    with pytest.raises(AdoptionError, match="adoption_source_not_ready"):
        await adopt(system, cmd, "new-dirty-key")


@pytest.mark.parametrize(
    "case,reason",
    [
        ("project", "adoption_project_mismatch"),
        ("identity", "adoption_snapshot_mismatch"),
        ("old-s1", "adoption_snapshot_mismatch"),
        ("actor", "adoption_user_required"),
        ("dirty", "adoption_source_not_ready"),
        ("revoked", "adoption_registration_mismatch"),
        ("parent", "adoption_parent_mismatch"),
        ("extra", "adoption_content_mismatch"),
        ("mode", "adoption_content_mismatch"),
        ("merge", "adoption_parent_mismatch"),
    ],
)
async def test_reject_wrong_source_without_domain_writes(system, case, reason):
    if case == "parent":
        (system.repository / "intermediate").write_text("not the candidate")
        await commit(system.repository)
    if case == "extra":
        (system.repository / "eval.py").write_text("different protected source")
    if case == "mode":
        (system.repository / "train.py").chmod(0o755)
    cmd = await target(system)
    context = CONTEXT
    if case == "project":
        cmd = cmd.model_copy(update={"project_id": uuid4()})
    elif case == "identity":
        cmd = cmd.model_copy(update={"target_snapshot_identity": "f" * 64})
    elif case == "old-s1":
        cmd = cmd.model_copy(
            update={
                "target_snapshot_id": system.snapshot_id,
                "target_snapshot_identity": system.snapshot_identity,
            }
        )
    elif case == "actor":
        context = replace(CONTEXT, actor=CommandActor("agent", "agent"))
    elif case == "dirty":
        (system.repository / "dirty").write_text("untracked")
    elif case == "revoked":
        async with system.database.session() as session:
            reg = await session.get(RepositoryRegistrationRow, system.registration_id)
            reg.status = "revoked"
            await session.commit()
    elif case == "merge":
        base = await system.source.describe(system.snapshot_id)
        target_desc = await system.source.describe(cmd.target_snapshot_id)
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit-tree",
            target_desc.head_commit + "^{tree}",
            "-p",
            base.head_commit,
            "-p",
            target_desc.head_commit,
            "-m",
            "merge",
            cwd=system.repository,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0
        await _git(system.repository, "update-ref", "HEAD", stdout.decode().strip())
        sid = await capture(system, "merge")
        desc = await system.source.describe(sid)
        cmd = cmd.model_copy(
            update={
                "target_snapshot_id": sid,
                "target_snapshot_identity": desc.repository_snapshot_identity,
            }
        )
    before = await counts(system.database)
    with pytest.raises(AdoptionError, match=reason):
        await adopt(system, cmd, context=context)
    assert await counts(system.database) == before


async def test_transaction_rollback_and_downgrade_protects_facts(system, monkeypatch):
    cmd = await target(system)
    before = await counts(system.database)
    import labpilot.context.source_adoption as module

    async def fail(*args, **kwargs):
        raise RuntimeError("injected before transaction commit")

    monkeypatch.setattr(module, "append_runtime_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await adopt(system, cmd)
    assert await counts(system.database) == before
    monkeypatch.undo()
    await adopt(system, cmd)
    with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
        alembic_command.downgrade(
            _alembic_config(system.database_url), "0015_agent_output_feedback"
        )


async def test_git_parent_ignores_shallow_boundary_and_readonly_permissions(system):
    cmd = await target(system)
    desc = await system.source.describe(cmd.target_snapshot_id)
    (system.repository / ".git/shallow").write_text(desc.head_commit + "\n")
    (system.repository / "train.py").chmod(0o444)
    result = await adopt(system, cmd)
    assert result.envelope.committed_state == "recorded"
