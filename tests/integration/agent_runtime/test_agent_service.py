from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from tests.integration.context.test_repository_snapshot import _seed_capture_service
from tests.integration.db_helpers import upgrade_database_to_head
from tests.integration.experiments.test_proposal_service import (
    PROJECT_ID,
    _git,
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
from labpilot.context.repository_types import CaptureRepositorySnapshot
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


def _single_snapshot_tool_factory(
    expected_snapshot_id: UUID,
    tools: ToolApplicationService,
) -> Callable[[UUID], ToolApplicationService]:
    def factory(repository_snapshot_id: UUID) -> ToolApplicationService:
        assert repository_snapshot_id == expected_snapshot_id
        return tools

    return factory


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
        "source_batch_summary_revision_id",
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
async def test_one_agent_service_reads_two_persisted_snapshots_without_cross_binding(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    git_repository = tmp_path / "two-snapshot-repository"
    git_repository.mkdir()
    marker = git_repository / "marker.txt"
    await _git(git_repository, "init", "-q", "-b", "main")
    marker.write_text("marker-A\n", encoding="utf-8")
    await _git(git_repository, "add", "marker.txt")
    await _git(
        git_repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "snapshot A",
    )
    database, capture_service, registration_id = await _seed_capture_service(
        isolated_database_url,
        tmp_path,
        git_repository,
    )
    try:
        captured_a = await capture_service.capture_repository_snapshot(
            context=_context(),
            idempotency_key="capture-snapshot-a",
            command=CaptureRepositorySnapshot(registration_id),
        )
        snapshot_a = UUID(captured_a.envelope.primary_result_id)
        marker.write_text("marker-B\n", encoding="utf-8")
        await _git(git_repository, "add", "marker.txt")
        await _git(
            git_repository,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "snapshot B",
        )
        captured_b = await capture_service.capture_repository_snapshot(
            context=_context(),
            idempotency_key="capture-snapshot-b",
            command=CaptureRepositorySnapshot(registration_id),
        )
        snapshot_b = UUID(captured_b.envelope.primary_result_id)
        assert snapshot_a != snapshot_b

        tracker = TrackingSessions(database)
        source = RepositorySnapshotSourceService(
            database.session,
            git_adapter=GitAdapter(
                git_executable=Path("/usr/bin/git"),
                timeout_seconds=2,
            ),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        shared_tool_repository = SqlToolRepository(tracker.session)
        factory_calls: list[UUID] = []

        def tool_service_factory(
            repository_snapshot_id: UUID,
        ) -> ToolApplicationService:
            factory_calls.append(repository_snapshot_id)
            definitions = list(
                repository_tool_definitions(source, repository_snapshot_id)
            )
            read_index = next(
                index
                for index, definition in enumerate(definitions)
                if definition.name == "repository.read_text"
            )
            original = definitions[read_index].executor

            async def guarded_executor(
                arguments: Mapping[str, object],
            ) -> Mapping[str, object]:
                assert tracker.active == 0
                return await original(arguments)

            definitions[read_index] = replace(
                definitions[read_index],
                executor=guarded_executor,
            )
            return ToolApplicationService(
                tuple(definitions),
                shared_tool_repository,
            )

        provider = ScriptedProvider(
            [
                {
                    "kind": "tool_call",
                    "tool_name": "repository.read_text",
                    "arguments": {
                        "relative_path": "marker.txt",
                        "start_line": 1,
                        "end_line": 10,
                    },
                },
                {"kind": "stop", "reason_code": "snapshot_a_done"},
                {
                    "kind": "tool_call",
                    "tool_name": "repository.read_text",
                    "arguments": {
                        "relative_path": "marker.txt",
                        "start_line": 1,
                        "end_line": 10,
                    },
                },
                {"kind": "stop", "reason_code": "snapshot_b_done"},
            ]
        )
        agent_repository = SqlAgentRepository(tracker.session)
        service = AgentApplicationService(
            repository=agent_repository,
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service_factory=tool_service_factory,
            proposal_service=object(),
            data_egress_policy="local_only",
        )
        run_a = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=snapshot_a,
            goal="read snapshot A",
            budget=_budget(),
        )
        run_b = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=snapshot_b,
            goal="read snapshot B",
            budget=_budget(),
        )

        await service.advance_run(
            context=_context(),
            agent_run_id=run_a.agent_run_id,
            expected_row_version=run_a.row_version,
        )
        await service.advance_run(
            context=_context(),
            agent_run_id=run_b.agent_run_id,
            expected_row_version=run_b.row_version,
        )

        steps_a = await agent_repository.list_steps(run_a.agent_run_id)
        steps_b = await agent_repository.list_steps(run_b.agent_run_id)
        assert steps_a[0].tool_call_id is not None
        assert steps_b[0].tool_call_id is not None
        call_a = await shared_tool_repository.get_call(steps_a[0].tool_call_id)
        call_b = await shared_tool_repository.get_call(steps_b[0].tool_call_id)
        assert call_a is not None and call_a.result is not None
        assert call_b is not None and call_b.result is not None
        assert call_a.result["text"] == "marker-A\n"
        assert call_b.result["text"] == "marker-B\n"
        assert factory_calls == [snapshot_a, snapshot_b]
        async with database.session() as session:
            assert await session.scalar(text("SELECT count(*) FROM tool_calls")) == 2
        assert tracker.active == 0
    finally:
        await database.dispose()


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
            tool_service_factory=_single_snapshot_tool_factory(
                system.snapshot_id, tools
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
            tool_service_factory=_single_snapshot_tool_factory(
                system.snapshot_id, tools
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
        tools = ToolApplicationService(
            repository_tool_definitions(source, system.snapshot_id),
            SqlToolRepository(system.database.session),
        )
        service = AgentApplicationService(
            repository=repository,
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service_factory=_single_snapshot_tool_factory(
                system.snapshot_id, tools
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
        tools = ToolApplicationService(
            repository_tool_definitions(source, system.snapshot_id),
            SqlToolRepository(system.database.session),
        )
        service = AgentApplicationService(
            repository=SqlAgentRepository(system.database.session),
            repository_source=source,
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service_factory=_single_snapshot_tool_factory(
                system.snapshot_id, tools
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
