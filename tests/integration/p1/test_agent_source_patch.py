import asyncio
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from tests.integration.api.test_result_aware_agent_api import (
    _BUDGET,
    ScriptedProvider,
    _app,
    _counts,
    _summary_id,
)
from tests.integration.db.test_migrations import _alembic_config
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt
from tests.unit.context.test_source_candidates import BASE, CLIPPED, patch

from labpilot.agent_runtime.models import AgentRunRow
from labpilot.api.source_patch_adapter import export_agent_patch
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import RepositoryRegistrationRow
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.source_candidates import CandidateError, SourceCandidateService

pytestmark = pytest.mark.integration
PATCH = patch(CLIPPED)


@pytest_asyncio.fixture
async def draft(isolated_database_url, tmp_path, request):
    train = getattr(request, "param", BASE)
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        train_source=train,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
    ) as (system, batch, trial, attempt, workspace):
        await _process(system.database, attempt)
        summary, project = await _summary_id(system, batch)
        provider = ScriptedProvider([{"kind": "source_patch", "patch": PATCH.decode()}])
        app = _app(system, provider)
        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(
                system=system,
                provider=provider,
                client=client,
                project=project,
                source=source,
                summary=summary,
                url=isolated_database_url,
                tmp=tmp_path,
                body=dict(
                    repository_snapshot_id=str(system.snapshot_id),
                    goal="Draft clipping; no execution.",
                    budget=_BUDGET,
                    source_patch=True,
                ),
            )


async def create(d, *, follow_up=False, body=None):
    data = dict(d.body if body is None else body)
    suffix = "/follow-up" if follow_up else ""
    if follow_up:
        data["source_batch_summary_revision_id"] = str(d.summary)
    return await d.client.post(f"/projects/{d.project}/agent-runs" + suffix, json=data)


async def advance(d, run):
    response = await d.client.post(
        f"/agent-runs/{run['agent_run_id']}/advance",
        json={"expected_row_version": run["row_version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def counts(d):
    return await _counts(d.system)


@pytest.mark.parametrize("follow_up", [False, True])
async def test_bound_draft_to_read_only_export_then_explicit_static_candidate(draft, follow_up):
    d = draft
    before = await counts(d)
    created = await create(d, follow_up=follow_up)
    assert created.status_code == 201, created.text
    run = await advance(d, created.json())
    assert run["termination_reason"] == "source_patch_drafted"
    assert run["produced_proposal_revision_id"] is None
    assert run["source_patch_context"]["train_py_sha256"] == sha256(BASE).hexdigest()
    exported = await export_agent_patch(d.system.database.session, UUID(run["agent_run_id"]))
    assert exported == PATCH
    trace = (await d.client.get(f"/agent-runs/{run['agent_run_id']}/trace")).json()
    assert trace["source_patch_context"] == run["source_patch_context"]
    assert trace["steps"][-1]["decision_json"]["patch"].encode() == PATCH
    request = json.loads(d.provider.calls[0].messages[1]["content"])
    assert request["source_patch"]["train_py"] == BASE.decode()
    if follow_up:
        assert request["result_context"]["source_batch_summary_revision_id"] == str(d.summary)
    # Explicit human handoff, not an effect of generation or export.
    candidates = SourceCandidateService(d.source, d.tmp / "candidates")
    ref = await candidates.submit(
        project_id=d.project,
        snapshot_id=d.system.snapshot_id,
        snapshot_identity=d.system.snapshot_identity,
        patch=exported,
        rationale="Human chooses to inspect clipping.",
        actor_id="human",
    )
    package = await candidates.prepare(ref)
    view = await candidates.inspect(package)
    assert view["report"]["syntax"] == "passed"
    assert view["report"]["runtime_smoke"] == view["report"]["scientific_validation"] == "not_run"
    assert await counts(d) == before and len(d.provider.calls) == 1
    assert (d.system.repository / "train.py").read_bytes() == BASE


async def test_cli_export_uses_exact_bytes_and_never_initializes_candidate_directory(draft):
    d = draft
    r = await advance(d, (await create(d)).json())
    before = await counts(d)
    root = Path(__file__).resolve().parents[3]
    candidate_root = d.tmp / "must-not-exist"
    env = os.environ | {
        "LABPILOT_DATABASE_URL": d.url,
        "LABPILOT_ENVIRONMENT": "test",
        "LABPILOT_INSTALLATION_IDENTITY": "draft-cli-test",
        "LABPILOT_LOCAL_ACTOR_ID": "human",
        "LABPILOT_REPOSITORY_ALLOWED_ROOTS": json.dumps([str(d.tmp)]),
        "LABPILOT_REPOSITORY_CONTENT_STORE_ROOT": str(candidate_root),
    }
    for run_id, expected in [(r["agent_run_id"], 0), (str(uuid4()), 1)]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(root / "scripts/source_candidate.py"),
            "export-agent-patch",
            "--agent-run-id",
            run_id,
            env=env,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 15)
        assert proc.returncode == expected, stderr.decode()
        assert stdout == (PATCH if expected == 0 else b"")
        if expected:
            assert json.loads(stderr)["detail"] == "source_patch_not_ready"
    assert not candidate_root.exists() and await counts(d) == before


@pytest.mark.parametrize("draft", [BASE + b"\n" * 501, BASE + b"#" + b"x" * 32768], indirect=True)
async def test_oversized_source_is_rejected_without_run_or_provider(draft):
    d = draft
    response = await create(d)
    assert response.status_code == 409, response.text
    async with d.system.database.session() as session:
        assert (await session.execute(select(AgentRunRow))).scalars().all() == []
    assert not d.provider.calls


async def test_registration_revoked_after_create_fails_without_model_call(draft):
    d = draft
    r = (await create(d)).json()
    registration = (await d.source.describe(d.system.snapshot_id)).repository_registration_id
    async with d.system.database.session() as session:
        row = await session.get(RepositoryRegistrationRow, registration)
        row.status = "revoked"
        await session.commit()
    finished = await advance(d, r)
    assert finished["status"] == "failed"
    assert finished["termination_reason"] == "source_patch_source_not_active"
    assert not d.provider.calls
    assert (await d.client.get(f"/agent-runs/{r['agent_run_id']}/trace")).status_code == 200


async def test_worktree_change_does_not_become_new_source(draft):
    d = draft
    r = (await create(d)).json()
    (d.system.repository / "train.py").write_bytes(CLIPPED)
    finished = await advance(d, r)
    assert finished["termination_reason"] == "source_patch_source_changed"
    assert not d.provider.calls


@pytest.mark.parametrize(
    "replacement",
    [
        b"diff --git a/other.py b/other.py\n--- a/other.py\n+++ b/other.py\n",
        patch(BASE.replace(b"return 0.92", b"return 1.0")),
    ],
)
async def test_unchecked_draft_is_not_a_static_success(draft, replacement):
    d = draft
    d.provider.script = [{"kind": "source_patch", "patch": replacement.decode()}]
    r = await advance(d, (await create(d)).json())
    exported = await export_agent_patch(d.system.database.session, UUID(r["agent_run_id"]))
    assert exported == replacement and len(d.provider.calls) == 1
    candidates = SourceCandidateService(d.source, d.tmp / "candidates")
    with pytest.raises(CandidateError):
        ref = await candidates.submit(
            project_id=d.project,
            snapshot_id=d.system.snapshot_id,
            snapshot_identity=d.system.snapshot_identity,
            patch=exported,
            rationale="Review invalid draft.",
            actor_id="human",
        )
        await candidates.prepare(ref)


async def test_adoption_cannot_be_combined_with_source_patch_mode(draft):
    d = draft
    response = await create(d, follow_up=True, body=d.body | {"source_adoption_id": str(uuid4())})
    assert response.status_code == 409, response.text
    assert not d.provider.calls


async def test_migration_column_and_refusal_to_remove_persisted_patch_runs(draft):
    d = draft
    engine = __import__("sqlalchemy").create_engine(d.url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name='agent_runs' AND column_name='source_patch_context'"
                )
            ).one()
            assert tuple(row) == ("jsonb", "YES", None)
        r = await advance(d, (await create(d)).json())
        with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
            alembic_command.downgrade(_alembic_config(d.url), "0017_adopted_source_follow_up")
        assert await export_agent_patch(d.system.database.session, UUID(r["agent_run_id"])) == PATCH
    finally:
        engine.dispose()


async def test_null_legacy_run_survives_downgrade_and_upgrade_unchanged(draft):
    d = draft
    created = await create(d, body=d.body | {"source_patch": False})
    assert created.status_code == 201
    run_id = UUID(created.json()["agent_run_id"])
    engine = __import__("sqlalchemy").create_engine(d.url.replace("+asyncpg", "+psycopg"))
    query = text(
        "SELECT to_jsonb(t) - 'source_patch_context' FROM agent_runs t WHERE agent_run_id=:id"
    )
    try:
        with engine.connect() as connection:
            before = connection.scalar(query, {"id": run_id})
            assert (
                connection.scalar(
                    text("SELECT source_patch_context FROM agent_runs WHERE agent_run_id=:id"),
                    {"id": run_id},
                )
                is None
            )
        alembic_command.downgrade(_alembic_config(d.url), "0017_adopted_source_follow_up")
        alembic_command.upgrade(_alembic_config(d.url), "head")
        with engine.connect() as connection:
            assert connection.scalar(query, {"id": run_id}) == before
        trace = (await d.client.get(f"/agent-runs/{run_id}/trace")).json()
        assert trace["source_patch_context"] is None
        assert trace["steps"] == [] and not d.provider.calls
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", [{}, {"profile": "unknown"}])
async def test_invalid_persisted_context_fails_without_hiding_failure_or_trace(draft, value):
    d = draft
    r = (await create(d)).json()
    async with d.system.database.session() as session:
        row = await session.get(AgentRunRow, UUID(r["agent_run_id"]))
        row.source_patch_context = value
        await session.commit()
    done = await advance(d, r)
    assert (
        done["status"] == "failed" and done["termination_reason"] == "source_patch_context_invalid"
    )
    trace = await d.client.get(f"/agent-runs/{r['agent_run_id']}/trace")
    assert trace.status_code == 200 and not d.provider.calls
    assert trace.json()["source_patch_context"] == {"status": "invalid"}


async def test_changed_frozen_file_hash_fails_without_reading_worktree_as_new_source(draft):
    d = draft
    r = (await create(d)).json()
    async with d.system.database.session() as session:
        row = await session.get(AgentRunRow, UUID(r["agent_run_id"]))
        row.source_patch_context = row.source_patch_context | {"train_py_sha256": "0" * 64}
        await session.commit()
    done = await advance(d, r)
    assert done["termination_reason"] == "source_patch_source_changed" and not d.provider.calls


async def test_jsonb_patch_context_rejects_nonobject_at_database_boundary(draft):
    from sqlalchemy.exc import IntegrityError

    d = draft
    r = (await create(d)).json()
    async with d.system.database.session() as session:
        row = await session.get(AgentRunRow, UUID(r["agent_run_id"]))
        row.source_patch_context = []
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
    assert not d.provider.calls
