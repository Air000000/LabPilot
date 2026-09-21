from __future__ import annotations

import importlib
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.provider import ModelRequest, ModelResponse
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import AgentApplicationService
from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.api.result_aware_agent_adapter import SqlResultAwareAgentAdapter
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.models import BatchRow
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.proposal_models import (
    DesignDecisionRow,
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
    ProposalVerificationRow,
)
from labpilot.results_validation.models import BatchSummaryRevisionRow
from labpilot.tools_policy.repository import SqlToolRepository
from labpilot.tools_policy.repository_tools import repository_tool_definitions
from labpilot.tools_policy.service import ToolApplicationService

pytestmark = pytest.mark.integration

_BUDGET = {
    "max_steps": 4,
    "max_model_calls": 4,
    "max_wall_clock_seconds": 60,
    "max_tokens": 100,
}


class ScriptedProvider:
    provider_name = "scripted"
    egress = "local"

    def __init__(self, script: list[object]) -> None:
        self.script = script
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(payload=self.script.pop(0), usage={"total_tokens": 1})


def _app(system: object, provider: ScriptedProvider):
    database = system.database  # type: ignore[attr-defined]
    source = RepositorySnapshotSourceService(
        database.session,
        git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
        content_store=ContentAddressedStore(system.repository.parent / "content"),  # type: ignore[attr-defined]
    )
    agent_repository = SqlAgentRepository(database.session)
    result_aware_agent = SqlResultAwareAgentAdapter(
        database.session, agent_repository=agent_repository
    )

    def tool_service_factory(repository_snapshot_id: UUID) -> ToolApplicationService:
        return ToolApplicationService(
            repository_tool_definitions(source, repository_snapshot_id),
            SqlToolRepository(database.session),
        )

    agent_runs = AgentApplicationService(
        repository=agent_repository,
        repository_source=source,
        provider=provider,
        model_name="scripted-model",
        context_compiler=ContextCompiler(),
        tool_service_factory=tool_service_factory,
        proposal_service=system.service,  # type: ignore[attr-defined]
        data_egress_policy="local_only",
        follow_up_run_store=result_aware_agent,
        result_context_source=result_aware_agent,
    )
    return create_app(
        services=ApplicationServices(
            projects=ProjectApplicationService(database.session),
            repository_source=source,
            proposals=system.service,  # type: ignore[attr-defined]
            agent_runs=agent_runs,
        ),
        actor_provider=ServerConfiguredActorProvider(
            installation_identity="result-aware-api", actor_id="operator"
        ),
    )


async def _summary_id(system: object, batch_id: UUID) -> tuple[UUID, UUID]:
    database = system.database  # type: ignore[attr-defined]
    summary_module = importlib.import_module("labpilot.results_validation.summary_service")
    outcomes = TrialExecutionOutcomeService(
        database.session,
        version_semantics=VersionExecutionSemanticsAdapter(database.session),
    )
    await summary_module.BatchSummaryService(
        database.session, trial_execution_outcome_service=outcomes
    ).refresh_summary(batch_id)
    async with database.session() as session:
        batch = await session.get(BatchRow, batch_id)
        summary = await session.scalar(
            select(BatchSummaryRevisionRow)
            .where(BatchSummaryRevisionRow.batch_id == batch_id)
            .order_by(BatchSummaryRevisionRow.revision_number.desc())
        )
    assert summary is not None and batch is not None
    return summary.batch_summary_revision_id, batch.project_id


async def _counts(system: object) -> tuple[int, int, int, int]:
    database = system.database  # type: ignore[attr-defined]
    async with database.session() as session:
        values = []
        for row in (ProposalRevisionRow, ExperimentVersionRow, BatchRow, ExperimentRow):
            value = await session.scalar(select(func.count()).select_from(row))
            assert value is not None
            values.append(value)
    return tuple(values)  # type: ignore[return-value]


async def _governance_counts(system: object) -> tuple[int, int]:
    database = system.database  # type: ignore[attr-defined]
    async with database.session() as session:
        values = []
        for row in (ProposalVerificationRow, DesignDecisionRow):
            value = await session.scalar(select(func.count()).select_from(row))
            assert value is not None
            values.append(value)
    return tuple(values)  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_follow_up_api_binds_exact_summary_context_and_stops_without_writes(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        summary_id, project_id = await _summary_id(system, batch_id)
        provider = ScriptedProvider(
            [{"kind": "stop", "reason_code": "goal_satisfied", "summary": "done"}]
        )
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, provider)),
            base_url="http://test",
        ) as client:
            created = await client.post(
                f"/projects/{project_id}/agent-runs/follow-up",
                json={
                    "source_batch_summary_revision_id": str(summary_id),
                    "repository_snapshot_id": str(system.snapshot_id),
                    "goal": "Interpret this completed batch.",
                    "budget": _BUDGET,
                },
            )
            assert created.status_code == 201
            run = created.json()
            assert run["status"] == "ready"
            assert run["source_batch_summary_revision_id"] == str(summary_id)
            assert run["repository_snapshot_id"] == str(system.snapshot_id)
            counts_before = await _counts(system)

            advanced = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )

    assert advanced.status_code == 200
    assert advanced.json()["status"] == "completed"
    assert advanced.json()["termination_reason"] == "goal_satisfied"
    assert str(summary_id) in str(provider.calls[0].messages)
    assert await _counts(system) == counts_before


async def _source_proposal(system: object) -> dict[str, object]:
    database = system.database  # type: ignore[attr-defined]
    async with database.session() as session:
        revision = await session.scalar(select(ProposalRevisionRow))
    assert revision is not None
    return dict(revision.payload_json)


@pytest.mark.asyncio
async def test_follow_up_api_propose_creates_same_experiment_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        summary_id, project_id = await _summary_id(system, batch_id)
        provider = ScriptedProvider(
            [{"kind": "propose", "proposal": await _source_proposal(system)}]
        )
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, provider)),
            base_url="http://test",
        ) as client:
            created = await client.post(
                f"/projects/{project_id}/agent-runs/follow-up",
                json={
                    "source_batch_summary_revision_id": str(summary_id),
                    "repository_snapshot_id": str(system.snapshot_id),
                    "goal": "Propose a next step.",
                    "budget": _BUDGET,
                },
            )
            assert created.status_code == 201
            run = created.json()
            counts_before = await _counts(system)
            governance_before = await _governance_counts(system)

            advanced = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )

    assert advanced.status_code == 200
    assert advanced.json()["status"] == "completed"
    assert advanced.json()["termination_reason"] == "proposal_created"
    counts_after = await _counts(system)
    assert counts_after == (counts_before[0] + 1, *counts_before[1:])
    async with system.database.session() as session:  # type: ignore[attr-defined]
        revisions = (
            await session.scalars(
                select(ProposalRevisionRow).order_by(ProposalRevisionRow.revision_number)
            )
        ).all()
    assert len(revisions) == 2
    assert revisions[1].experiment_id == revisions[0].experiment_id
    assert revisions[1].revision_number == revisions[0].revision_number + 1
    assert await _governance_counts(system) == governance_before
