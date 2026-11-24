import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.integration.api.test_result_aware_agent_api import (
    _BUDGET,
    ScriptedProvider,
    _app,
    _source_proposal,
    _summary_id,
)
from tests.integration.context.test_repository_source import _git
from tests.integration.db.test_migrations import _alembic_config
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt
from tests.unit.context.test_source_candidates import BASE, CLIPPED, patch

from labpilot.agent_runtime.models import AgentRunRow
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import RepositoryRegistrationRow, SourceAdoptionRow
from labpilot.context.repository_service import RepositoryApplicationService
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot
from labpilot.context.source_adoption import AdoptSource, SourceAdoptionService
from labpilot.context.source_candidates import SourceCandidateService
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.proposal_models import ExperimentVersionRow, ProposalRevisionRow

pytestmark = pytest.mark.integration
CONTEXT = CommandContext("fixture", CommandActor("user", "human"), "cross-source-test")


@pytest_asyncio.fixture
async def adopted(isolated_database_url, tmp_path):
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        train_source=BASE,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
    ) as (system, batch, trial, attempt, workspace):
        await _process(system.database, attempt)
        summary, project = await _summary_id(system, batch)
        git = GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2)
        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=git,
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        candidates = SourceCandidateService(source, tmp_path / "content/source-candidates")
        request = await candidates.submit(
            project_id=project,
            snapshot_id=system.snapshot_id,
            snapshot_identity=system.snapshot_identity,
            patch=patch(CLIPPED),
            rationale="Compare same parameters on clipped target; static only.",
            actor_id="human",
        )
        package = await candidates.prepare(request)
        (system.repository / "train.py").write_bytes(CLIPPED)
        await _git(system.repository, "add", "train.py")
        await _git(
            system.repository,
            "-c",
            "user.name=Human",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Human accepted clipping",
        )
        registration = UUID(
            str((await source.describe(system.snapshot_id)).repository_registration_id)
        )
        capture = RepositoryApplicationService(
            system.database.session,
            git_adapter=git,
            allowed_roots=(tmp_path,),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        captured = await capture.capture_repository_snapshot(
            context=CONTEXT,
            idempotency_key="capture-s2",
            command=CaptureRepositorySnapshot(registration),
        )
        target = await source.describe(UUID(captured.envelope.primary_result_id))
        adoption = await SourceAdoptionService(system.database.session, candidates).adopt(
            context=CONTEXT,
            idempotency_key="adopt-s2",
            command=AdoptSource(
                project_id=project,
                package_ref=package,
                target_snapshot_id=target.repository_snapshot_id,
                target_snapshot_identity=target.repository_snapshot_identity,
            ),
        )
        provider = ScriptedProvider([{"kind": "stop", "reason_code": "done"}])
        app = _app(system, provider, candidate_store=candidates.store)
        body = dict(
            source_batch_summary_revision_id=str(summary),
            repository_snapshot_id=str(target.repository_snapshot_id),
            source_adoption_id=adoption.envelope.primary_result_id,
            goal="Design a control on adopted S2.",
            budget=_BUDGET,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(
                system=system,
                source=source,
                candidates=candidates,
                client=client,
                app=app,
                provider=provider,
                body=body,
                project=project,
                target=target,
                registration=registration,
                adoption=UUID(adoption.envelope.primary_result_id),
                database_url=isolated_database_url,
            )


async def create(a, body=None):
    return await a.client.post(f"/projects/{a.project}/agent-runs/follow-up", json=body or a.body)


async def advance(a, run):
    response = await a.client.post(
        f"/agent-runs/{run['agent_run_id']}/advance",
        json={"expected_row_version": run["row_version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def count(a, row):
    async with a.system.database.session() as session:
        return await session.scalar(select(func.count()).select_from(row))


async def target_proposal(a):
    payload = await _source_proposal(a.system)
    payload.update(
        repository_snapshot_id=str(a.target.repository_snapshot_id),
        repository_snapshot_identity=a.target.repository_snapshot_identity,
    )
    return payload


async def test_two_source_proposal_same_invalid_parameters_needs_explicit_approval(adopted):
    a = adopted
    a.provider.script = [{"kind": "propose", "proposal": await target_proposal(a)}]
    response = await create(a)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["source_adoption_id"] == str(a.adoption)
    finished = await advance(a, run)
    assert finished["termination_reason"] == "proposal_created"
    assert await count(a, ExperimentVersionRow) == 1
    r2 = await a.system.service.get_revision(UUID(finished["produced_proposal_revision_id"]))
    async with a.system.database.session() as session:
        v1 = await session.scalar(select(ExperimentVersionRow))
    assert r2.experiment_id == v1.experiment_id
    assert r2.payload.repository_snapshot_id == a.target.repository_snapshot_id
    verified = await a.system.service.verify_revision(
        context=CONTEXT, proposal_revision_id=r2.proposal_revision_id
    )
    v2 = await a.system.service.approve_revision(
        context=CONTEXT,
        proposal_revision_id=r2.proposal_revision_id,
        verification_id=verified.proposal_verification_id,
    )
    assert v2.experiment_id == v1.experiment_id and v2.version_number == 2
    assert v2.repository_snapshot_id == a.target.repository_snapshot_id
    user = json.loads(a.provider.calls[0].messages[1]["content"])
    assert user["result_context"]["source_proposal"]["repository_snapshot_id"] == str(
        a.system.snapshot_id
    )
    assert user["source_binding"]["proposal_target"]["repository_snapshot_id"] == str(
        a.target.repository_snapshot_id
    )
    assert user["source_binding"]["evidence"]["static_report"]["scientific_validation"] == "not_run"
    assert user["result_context"]["excluded_results"]
    trace = (await a.client.get(f"/agent-runs/{run['agent_run_id']}/trace")).json()
    assert trace["source_binding"]["result_source"]["repository_snapshot_id"] == str(
        a.system.snapshot_id
    )


async def test_tools_read_target_stop_and_old_workspace_is_not_required(adopted):
    a = adopted
    workspaces = list(a.candidates.root.glob("candidate-*"))
    assert workspaces
    for p in workspaces:
        assert p.parent == a.candidates.root
        shutil.rmtree(p)
    a.provider.script = [
        {
            "kind": "tool_call",
            "tool_name": "repository.read_text",
            "arguments": {"relative_path": "train.py", "start_line": 1, "end_line": 30},
        },
        {"kind": "stop", "reason_code": "done"},
    ]
    run = (await create(a)).json()
    finished = await advance(a, run)
    assert finished["status"] == "completed"
    user = json.loads(a.provider.calls[1].messages[1]["content"])
    assert "gradient = max" in user["prior_tool_results"][0]["result"]["text"]
    assert await count(a, ProposalRevisionRow) == 1


@pytest.mark.parametrize(
    "case",
    [
        "no_adoption",
        "wrong_adoption",
        "wrong_target",
        "wrong_identity",
        "wrong_project",
        "revoked",
        "missing_cas",
        "oversize_diff",
    ],
)
async def test_invalid_binding_creates_no_run_or_provider_call(adopted, case):
    a = adopted
    body = dict(a.body)
    if case == "no_adoption":
        body.pop("source_adoption_id")
    if case == "wrong_adoption":
        body["source_adoption_id"] = str(uuid4())
    if case == "wrong_target":
        body["repository_snapshot_id"] = str(a.system.snapshot_id)
    if case in {"wrong_identity", "wrong_project", "revoked"}:
        async with a.system.database.session() as session:
            if case == "revoked":
                row = await session.get(RepositoryRegistrationRow, a.registration)
                row.status = "revoked"
            else:
                row = await session.get(SourceAdoptionRow, a.adoption)
                if case == "wrong_identity":
                    row.adoption_identity = "f" * 64
                else:
                    project = ProjectRow(
                        project_id=uuid4(),
                        project_identity=uuid4().hex * 2,
                        name="other",
                        status="active",
                        artifact_root_ref="test",
                        row_version=1,
                    )
                    session.add(project)
                    await session.flush()
                    row.project_id = project.project_id
            await session.commit()
    if case in {"missing_cas", "oversize_diff"}:
        row = None
        async with a.system.database.session() as session:
            row = await session.get(SourceAdoptionRow, a.adoption)
        package = json.loads(await a.candidates.store.read_bytes(row.package_ref))
        ref = row.package_ref if case == "missing_cas" else package["diff_ref"]
        path = a.candidates.store._blob_path(ref[7:])
        if case == "missing_cas":
            path.unlink()
        else:
            path.write_bytes(b"x" * (32 * 1024 + 1))
    response = await create(a, body)
    assert response.status_code in {400, 409, 422}, response.text
    assert await count(a, AgentRunRow) == 0
    assert a.provider.calls == []


@pytest.mark.parametrize("case", ["old_source", "bad_identity", "other_source"])
async def test_proposal_target_guard_remains(adopted, case):
    a = adopted
    proposal = await target_proposal(a)
    if case == "old_source":
        proposal["repository_snapshot_id"] = str(a.system.snapshot_id)
    elif case == "bad_identity":
        proposal["repository_snapshot_identity"] = "f" * 64
    else:
        proposal["repository_snapshot_id"] = str(uuid4())
    a.provider.script = [{"kind": "propose", "proposal": proposal}]
    run = (await create(a)).json()
    finished = await advance(a, run)
    assert finished["termination_reason"] == "follow_up_proposal_snapshot_mismatch"
    assert await count(a, ProposalRevisionRow) == 1


async def test_missing_evidence_after_creation_fails_without_model_call_and_trace_stays_readable(
    adopted,
):
    a = adopted
    run = (await create(a)).json()
    async with a.system.database.session() as session:
        row = await session.get(SourceAdoptionRow, a.adoption)
    a.candidates.store._blob_path(row.package_ref[7:]).unlink()
    failed = await advance(a, run)
    assert (
        failed["status"] == "failed"
        and failed["termination_reason"] == "source_adoption_evidence_invalid"
    )
    assert not a.provider.calls
    trace = await a.client.get(f"/agent-runs/{run['agent_run_id']}/trace")
    assert trace.status_code == 200


async def test_human_input_resume_budget_and_non_destructive_migration(adopted):
    a = adopted
    a.provider.script = [
        {"kind": "request_human_input", "question": "Confirm the control condition?"},
        {"kind": "stop", "reason_code": "done"},
    ]
    run = (await create(a)).json()
    waiting = await advance(a, run)
    assert waiting["status"] == "awaiting_human"
    with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
        alembic_command.downgrade(_alembic_config(a.database_url), "0016_source_adoptions")
    response = await a.client.post(
        f"/agent-runs/{run['agent_run_id']}/human-input",
        json={
            "expected_row_version": waiting["row_version"],
            "text": "Use the same seed and parameters on S2.",
        },
    )
    assert response.status_code == 200, response.text
    finished = await advance(a, response.json())
    assert finished["status"] == "completed"
    body = dict(a.body, budget=dict(_BUDGET, max_model_calls=1))
    a.provider.script = [
        {"kind": "tool_call", "tool_name": "repository.list_tree", "arguments": {}}
    ]
    exhausted = await advance(a, (await create(a, body)).json())
    assert exhausted["termination_reason"] == "max_model_calls"


async def test_persisted_proposal_replay_needs_no_cas_or_new_call(adopted, monkeypatch):
    a = adopted
    a.provider.script = [{"kind": "propose", "proposal": await target_proposal(a)}]
    run = (await create(a)).json()
    service = a.app.state.application_services.agent_runs
    real = service._proposals.create_follow_up_agent_revision

    async def crash(**kwargs):
        raise RuntimeError("injected after decision persistence")

    monkeypatch.setattr(service._proposals, "create_follow_up_agent_revision", crash)
    with pytest.raises(RuntimeError, match="injected"):
        await advance(a, run)
    monkeypatch.setattr(service._proposals, "create_follow_up_agent_revision", real)
    async with a.system.database.session() as session:
        row = await session.get(SourceAdoptionRow, a.adoption)
    a.candidates.store._blob_path(row.package_ref[7:]).unlink()
    current = (await a.client.get(f"/agent-runs/{run['agent_run_id']}")).json()
    completed = await advance(a, current)
    assert completed["termination_reason"] == "proposal_created"
    assert len(a.provider.calls) == 1 and await count(a, ProposalRevisionRow) == 2


@pytest.mark.parametrize("change", ["archive", "revoke", "clear_primary"])
async def test_changed_authority_fails_before_a_new_model_call(adopted, change):
    a = adopted
    run = (await create(a)).json()
    async with a.system.database.session() as session:
        if change == "revoke":
            row = await session.get(RepositoryRegistrationRow, a.registration)
            row.status = "revoked"
        else:
            row = await session.get(ProjectRow, a.project)
            if change == "archive":
                row.status = "archived"
            else:
                row.primary_repository_registration_id = None
        await session.commit()
    failed = await advance(a, run)
    assert failed["termination_reason"] == "source_adoption_not_active"
    assert not a.provider.calls
    # The old fact remains readable and does not regain execution authority.
    assert (await a.client.get(f"/agent-runs/{run['agent_run_id']}/trace")).status_code == 200


async def test_reserved_model_outcome_unknown_takes_precedence_over_missing_evidence(
    adopted, monkeypatch
):
    a = adopted
    run = (await create(a)).json()

    class SimulatedProcessDeath(BaseException):
        pass

    async def crash(request):
        a.provider.calls.append(request)
        raise SimulatedProcessDeath("injected death during model call")

    monkeypatch.setattr(a.provider, "complete", crash)
    with pytest.raises(SimulatedProcessDeath, match="injected"):
        await advance(a, run)
    async with a.system.database.session() as session:
        row = await session.get(SourceAdoptionRow, a.adoption)
    a.candidates.store._blob_path(row.package_ref[7:]).unlink()
    current = (await a.client.get(f"/agent-runs/{run['agent_run_id']}")).json()
    failed = await advance(a, current)
    assert failed["termination_reason"] == "model_call_outcome_unknown"
    assert len(a.provider.calls) == 1


async def test_null_legacy_run_roundtrip_does_not_adopt_latest_source(adopted):
    from sqlalchemy import create_engine, text

    a = adopted
    body = dict(a.body, source_adoption_id=None, repository_snapshot_id=str(a.system.snapshot_id))
    run = (await create(a, body)).json()
    assert run["source_adoption_id"] is None
    config = _alembic_config(a.database_url)
    alembic_command.downgrade(config, "0016_source_adoptions")
    engine = create_engine(a.database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM agent_runs")) == 1
            assert connection.scalar(text("SELECT count(*) FROM source_adoptions")) == 1
        alembic_command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT source_adoption_id IS NULL FROM agent_runs")) is True
            )
    finally:
        engine.dispose()
    current = (await a.client.get(f"/agent-runs/{run['agent_run_id']}")).json()
    assert current["repository_snapshot_id"] == str(a.system.snapshot_id)
    assert current["source_adoption_id"] is None
    done = await advance(a, current)
    assert done["status"] == "completed"
    user = json.loads(a.provider.calls[0].messages[1]["content"])
    assert "source_binding" not in user


@pytest.mark.parametrize("old_source", [False, True])
async def test_proposal_correction_preserves_cross_source_guard(adopted, monkeypatch, old_source):
    a = adopted
    complete = a.provider.complete

    async def completed_response(request):
        return replace(await complete(request), response_completed=True)

    monkeypatch.setattr(a.provider, "complete", completed_response)
    proposal = await target_proposal(a)
    if old_source:
        proposal["repository_snapshot_id"] = str(a.system.snapshot_id)
    a.provider.script = [{"kind": "propose"}, {"kind": "propose", "proposal": proposal}]
    run = (await create(a)).json()
    finished = await advance(a, run)
    expected = "follow_up_proposal_snapshot_mismatch" if old_source else "proposal_created"
    assert finished["termination_reason"] == expected and len(a.provider.calls) == 2
    assert await count(a, ProposalRevisionRow) == (1 if old_source else 2)
    assert await count(a, ExperimentVersionRow) == 1
    trace = (await a.client.get(f"/agent-runs/{run['agent_run_id']}/trace")).json()
    first, second = trace["steps"]
    assert first["outcome"] == "failed" and first["decision_json"] is None
    assert second["output_feedback"]["correction_of_step_id"] == first["agent_step_id"]
    assert trace["source_binding"]["result_source"]["repository_snapshot_id"] == str(
        a.system.snapshot_id
    )
    user = json.loads(a.provider.calls[1].messages[1]["content"])
    assert user["source_binding"]["proposal_target"]["repository_snapshot_id"] == str(
        a.target.repository_snapshot_id
    )
    if not old_source:
        revision = await a.system.service.get_revision(
            UUID(finished["produced_proposal_revision_id"])
        )
        verified = await a.system.service.verify_revision(
            context=CONTEXT, proposal_revision_id=revision.proposal_revision_id
        )
        assert verified.result.passed
