from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.agent_runtime.models import AgentRunRow
from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.db.session import Database
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.project_service import ProjectApplicationService


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ops_finds_recent_run_without_remembering_ids(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id, run_id = uuid4(), uuid4()
    try:
        async with database.session() as session:
            session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="a" * 64,
                    name="Demo",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root:demo",
                )
            )
            await session.flush()
            session.add(
                AgentRunRow(
                    agent_run_id=run_id,
                    project_id=project_id,
                    repository_snapshot_id=uuid4(),
                    goal="CPU <audit> experiment",
                    status="awaiting_human",
                    termination_reason=None,
                    egress_policy="remote_provider_allowed",
                    provider_name="deepseek_chat",
                    model_name="deepseek-v4-flash",
                    max_steps=6,
                    max_model_calls=4,
                    max_wall_clock_seconds=90,
                    max_tokens=24000,
                    total_model_calls=3,
                    total_tokens=100,
                    next_step_ordinal=4,
                    produced_proposal_revision_id=None,
                    created_at=datetime.now(UTC),
                    started_at=datetime.now(UTC),
                    completed_at=None,
                    row_version=1,
                    source_batch_summary_revision_id=None,
                )
            )
            await session.commit()
        app = create_app(
            services=ApplicationServices(
                projects=ProjectApplicationService(database.session),
                ops_sessions=database.session,
            ),
            actor_provider=ServerConfiguredActorProvider(
                installation_identity="test", actor_id="operator"
            ),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ops")
            operator = await client.get("/operator")
            missing_summary = await client.get(f"/ops/summary-revisions/{uuid4()}")
        assert response.status_code == 200
        assert operator.status_code == 200
        assert missing_summary.status_code == 404
        assert 'href="/ops"' in operator.text
        assert "CPU &lt;audit&gt; experiment" in response.text
        assert "阶段: 等待人工回答" in response.text
        assert "CPU <audit> experiment" not in response.text
        assert f"/agent-runs/{run_id}/trace" in response.text
        assert f"/operator?run={run_id}" in response.text
        assert "/operator" in response.text
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(AgentRunRow)) == 1
    finally:
        await database.dispose()
