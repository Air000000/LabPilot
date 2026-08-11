from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from tests.integration.db_helpers import upgrade_database_to_head
from tests.integration.experiments.test_proposal_service import (
    PROJECT_ID,
    _payload,
    _proposal_system,
)

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.provider import ModelRequest, ModelResponse, ProviderError
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import AgentApplicationService, AgentRuntimeError
from labpilot.agent_runtime.types import AgentBudget
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.tools_policy.repository import SqlToolRepository
from labpilot.tools_policy.repository_tools import repository_tool_definitions
from labpilot.tools_policy.service import ToolApplicationService

pytestmark = pytest.mark.integration


def _context() -> CommandContext:
    return CommandContext(
        installation_identity="integration-installation",
        actor=CommandActor(actor_type="human", actor_id="operator"),
        interaction_correlation_id="agent-integration-request",
    )


def _budget(**changes: int) -> AgentBudget:
    values = {
        "max_steps": 10,
        "max_model_calls": 10,
        "max_wall_clock_seconds": 600,
        "max_tokens": 1000,
    }
    values.update(changes)
    return AgentBudget(**values)


class TrackingSessions:
    def __init__(self, database) -> None:
        self.database = database
        self.active = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator:
        async with self.database.session() as session:
            self.active += 1
            try:
                yield session
            finally:
                self.active -= 1


class ScriptedProvider:
    provider_name = "scripted"
    egress = "local"

    def __init__(self, script: list[object], before_call=None) -> None:
        self.script = script
        self.before_call = before_call
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self.before_call is not None:
            await self.before_call(request)
        self.calls.append(request)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ModelResponse(payload=item, usage={"total_tokens": 2})


@dataclass
class CrashAfterCreateProposalService:
    service: object
    calls: int = 0

    async def create_agent_revision(self, **kwargs):
        self.calls += 1
        result = await self.service.create_agent_revision(**kwargs)
        if self.calls == 1:
            raise RuntimeError("crash after committed ProposalRevision")
        return result


def test_agent_tables_match_the_frozen_0008_contract(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(isolated_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            run_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='agent_runs'"
                    )
                ).scalars()
            )
            step_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='agent_steps'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    assert run_columns == {
        "agent_run_id",
        "project_id",
        "repository_snapshot_id",
        "goal",
        "status",
        "termination_reason",
        "egress_policy",
        "provider_name",
        "model_name",
        "max_steps",
        "max_model_calls",
        "max_wall_clock_seconds",
        "max_tokens",
        "total_model_calls",
        "total_tokens",
        "next_step_ordinal",
        "produced_proposal_revision_id",
        "created_at",
        "started_at",
        "completed_at",
        "row_version",
    }
    assert step_columns == {
        "agent_step_id",
        "agent_run_id",
        "ordinal",
        "step_kind",
        "tool_call_id",
        "provider_name",
        "model_name",
        "canonical_request_hash",
        "bound_toolset_hash",
        "semantic_parameters_json",
        "output_schema_hash",
        "started_at",
        "completed_at",
        "outcome",
        "token_usage_json",
        "decision_kind",
        "decision_json",
        "human_input_text",
    }


@pytest.mark.asyncio
async def test_reservation_commits_before_provider_and_failure_is_persisted(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        tracker = TrackingSessions(system.database)

        async def before_call(request: ModelRequest) -> None:
            assert tracker.active == 0
            async with system.database.session() as session:
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM agent_steps WHERE outcome='reserved'")
                    )
                    == 1
                )

        provider = ScriptedProvider([ProviderError("provider_timeout")], before_call)
        tools = ToolApplicationService(
            repository_tool_definitions(source, system.snapshot_id),
            SqlToolRepository(system.database.session),
        )
        service = AgentApplicationService(
            repository=SqlAgentRepository(tracker.session),
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service=tools,
            proposal_service=system.service,
            data_egress_policy="local_only",
        )
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="goal",
            budget=_budget(),
        )

        failed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

        assert failed.status == "failed"
        assert failed.termination_reason == "provider_timeout"
        steps = await SqlAgentRepository(tracker.session).list_steps(run.agent_run_id)
        assert steps[0].outcome == "failed"
        assert tracker.active == 0


@pytest.mark.asyncio
async def test_tool_executor_is_outside_uow_and_bound_hash_is_independent(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        tracker = TrackingSessions(system.database)
        definitions = list(repository_tool_definitions(source, system.snapshot_id))
        read_index = next(
            index
            for index, definition in enumerate(definitions)
            if definition.name == "repository.read_text"
        )
        original = definitions[read_index].executor

        async def guarded_executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
            assert tracker.active == 0
            return await original(arguments)

        definitions[read_index] = replace(definitions[read_index], executor=guarded_executor)
        tools = ToolApplicationService(
            tuple(definitions), SqlToolRepository(system.database.session)
        )
        provider = ScriptedProvider(
            [
                {
                    "kind": "tool_call",
                    "tool_name": "repository.read_text",
                    "arguments": {"relative_path": "README.md", "start_line": 1, "end_line": 20},
                },
                {"kind": "stop", "reason_code": "done"},
            ]
        )
        repository = SqlAgentRepository(tracker.session)
        service = AgentApplicationService(
            repository=repository,
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service=tools,
            proposal_service=system.service,
            data_egress_policy="local_only",
        )
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="goal",
            budget=_budget(),
        )

        completed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

        assert completed.status == "completed"
        steps = await repository.list_steps(run.agent_run_id)
        assert steps[0].bound_toolset_hash is not None
        assert steps[0].canonical_request_hash is not None
        assert steps[0].bound_toolset_hash != steps[0].canonical_request_hash
        assert tracker.active == 0


@pytest.mark.asyncio
async def test_proposal_crash_retry_does_not_duplicate_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        provider = ScriptedProvider([{"kind": "propose", "proposal": _payload(system)}])
        repository = SqlAgentRepository(system.database.session)
        crashing = CrashAfterCreateProposalService(system.service)
        service = AgentApplicationService(
            repository=repository,
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service=ToolApplicationService(
                repository_tool_definitions(source, system.snapshot_id),
                SqlToolRepository(system.database.session),
            ),
            proposal_service=crashing,
            data_egress_policy="local_only",
        )
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="goal",
            budget=_budget(),
        )
        with pytest.raises(RuntimeError, match="crash after committed"):
            await service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        after_crash = await service.get_run(run.agent_run_id)

        completed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=after_crash.row_version,
        )

        async with system.database.session() as session:
            count = await session.scalar(
                text(
                    "SELECT count(*) FROM experiment_proposal_revisions "
                    "WHERE origin_agent_step_id IS NOT NULL"
                )
            )
        assert completed.status == "completed"
        assert count == 1
        assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_same_version_has_one_model_call_and_one_conflict(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def before_call(request: ModelRequest) -> None:
            started.set()
            await release.wait()

        provider = ScriptedProvider([{"kind": "stop", "reason_code": "done"}], before_call)
        service = AgentApplicationService(
            repository=SqlAgentRepository(system.database.session),
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service=ToolApplicationService(
                repository_tool_definitions(source, system.snapshot_id),
                SqlToolRepository(system.database.session),
            ),
            proposal_service=system.service,
            data_egress_policy="local_only",
        )
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="goal",
            budget=_budget(),
        )
        winner = asyncio.create_task(
            service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        )
        await started.wait()
        with pytest.raises(AgentRuntimeError, match="row_version_conflict"):
            await service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        release.set()
        completed = await winner

        assert completed.status == "completed"
        assert len(provider.calls) == 1
