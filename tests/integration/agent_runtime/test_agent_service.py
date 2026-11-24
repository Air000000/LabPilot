from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select, text
from tests.integration.context.test_repository_snapshot import _seed_capture_service
from tests.integration.db_helpers import upgrade_database_to_head
from tests.integration.experiments.test_proposal_service import (
    PROJECT_ID,
    _git,
    _payload,
    _proposal_system,
)
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.provider import ModelRequest, ModelResponse, ProviderError
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import AgentApplicationService, AgentRuntimeError
from labpilot.agent_runtime.types import AgentBudget, AgentRunDetails
from labpilot.api.result_aware_agent_adapter import SqlResultAwareAgentAdapter
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import CaptureRepositorySnapshot
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.models import BatchRow
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.experiments.proposal_models import ProposalRevisionRow
from labpilot.results_validation.models import BatchSummaryRevisionRow
from labpilot.results_validation.summary_service import BatchSummaryService
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
) -> Callable[[AgentRunDetails], ToolApplicationService]:
    def factory(run: AgentRunDetails) -> ToolApplicationService:
        repository_snapshot_id = run.repository_snapshot_id
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

    async def create_follow_up_agent_revision(self, **kwargs):
        self.calls += 1
        result = await self.service.create_follow_up_agent_revision(**kwargs)
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
            adoption_column = connection.execute(
                text(
                    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='agent_runs' "
                    "AND column_name='source_adoption_id'"
                )
            ).one()
            assert tuple(adoption_column) == ("uuid", "YES", None)
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
        "source_adoption_id",  # Nullable 0017 extension; existing Core columns remain.
        "source_patch_context",  # Nullable 0018 draft opt-in; no backfill.
        "history_context_json",
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
        "output_feedback_json",
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
            run: AgentRunDetails,
        ) -> ToolApplicationService:
            repository_snapshot_id = run.repository_snapshot_id
            factory_calls.append(repository_snapshot_id)
            definitions = list(repository_tool_definitions(source, repository_snapshot_id))
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
            tool_service_factory=_single_snapshot_tool_factory(system.snapshot_id, tools),
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
            tool_service_factory=_single_snapshot_tool_factory(system.snapshot_id, tools),
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
            tool_service_factory=_single_snapshot_tool_factory(system.snapshot_id, tools),
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
            tool_service_factory=_single_snapshot_tool_factory(system.snapshot_id, tools),
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


@pytest.mark.asyncio
async def test_follow_up_proposal_crash_replay_keeps_same_r2(
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
        outcomes = TrialExecutionOutcomeService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        await BatchSummaryService(
            system.database.session, trial_execution_outcome_service=outcomes
        ).refresh_summary(batch_id)
        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
            summary = await session.scalar(
                select(BatchSummaryRevisionRow)
                .where(BatchSummaryRevisionRow.batch_id == batch_id)
                .order_by(BatchSummaryRevisionRow.revision_number.desc())
            )
            source_revision = await session.scalar(
                select(ProposalRevisionRow).order_by(ProposalRevisionRow.revision_number)
            )
        assert batch is not None and summary is not None and source_revision is not None

        source = RepositorySnapshotSourceService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            content_store=ContentAddressedStore(tmp_path / "content"),
        )
        repository = SqlAgentRepository(system.database.session)
        result_adapter = SqlResultAwareAgentAdapter(
            system.database.session, agent_repository=repository
        )
        provider = ScriptedProvider(
            [{"kind": "propose", "proposal": dict(source_revision.payload_json)}]
        )
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
            tool_service_factory=_single_snapshot_tool_factory(system.snapshot_id, tools),
            proposal_service=crashing,
            data_egress_policy="local_only",
            follow_up_run_store=result_adapter,
            result_context_source=result_adapter,
        )
        run = await service.create_follow_up_run(
            context=_context(),
            project_id=batch.project_id,
            source_batch_summary_revision_id=summary.batch_summary_revision_id,
            repository_snapshot_id=system.snapshot_id,
            goal="propose the next revision",
            budget=_budget(),
        )

        with pytest.raises(RuntimeError, match="crash after committed"):
            await service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        after_crash = await service.get_run(run.agent_run_id)
        assert after_crash.produced_proposal_revision_id is None
        assert after_crash.status == "running"

        async with system.database.session() as session:
            revisions_after_crash = (
                await session.scalars(
                    select(ProposalRevisionRow).order_by(ProposalRevisionRow.revision_number)
                )
            ).all()
        assert len(revisions_after_crash) == 2
        assert revisions_after_crash[1].experiment_id == revisions_after_crash[0].experiment_id
        assert revisions_after_crash[1].revision_number == 2

        completed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=after_crash.row_version,
        )

    assert completed.status == "completed"
    assert completed.termination_reason == "proposal_created"
    assert completed.produced_proposal_revision_id == revisions_after_crash[1].proposal_revision_id
    assert len(provider.calls) == 1
    assert str(summary.batch_summary_revision_id) in str(provider.calls[0].messages)
    async with system.database.session() as session:
        revisions_after_replay = (
            await session.scalars(
                select(ProposalRevisionRow).order_by(ProposalRevisionRow.revision_number)
            )
        ).all()
    assert [revision.revision_number for revision in revisions_after_replay] == [1, 2]


def _output_runtime(system, repo, provider):
    source = RepositorySnapshotSourceService(
        system.database.session,
        git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
        content_store=ContentAddressedStore(system.repository.parent / "content"),
    )
    tools = ToolApplicationService(
        repository_tool_definitions(source, system.snapshot_id),
        SqlToolRepository(system.database.session),
    )
    return AgentApplicationService(
        repository=repo,
        repository_source=source,
        provider=provider,
        model_name="test-model",
        context_compiler=ContextCompiler(),
        tool_service_factory=_single_snapshot_tool_factory(system.snapshot_id, tools),
        proposal_service=system.service,
        data_egress_policy="local_only",
    )


class CompletedOutputProvider(ScriptedProvider):
    async def complete(self, request):
        return replace(await super().complete(request), response_completed=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("crash", ["none", "feedback", "reserve", "before_feedback"])
async def test_output_correction_sql_recovery_boundaries(isolated_database_url, tmp_path, crash):
    class CrashRepository(SqlAgentRepository):
        triggered = False

        async def finalize_model_step(self, *args, **kwargs):
            if crash == "before_feedback" and kwargs.get("output_feedback") and not self.triggered:
                self.triggered = True
                raise RuntimeError("offline before feedback transaction")
            run = await super().finalize_model_step(*args, **kwargs)
            if crash == "feedback" and run.status == "ready" and not self.triggered:
                self.triggered = True
                raise RuntimeError("offline after feedback commit")
            return run

        async def reserve_model_step(self, *args, **kwargs):
            run = await super().reserve_model_step(*args, **kwargs)
            if crash == "reserve" and kwargs["step"].output_feedback and not self.triggered:
                self.triggered = True
                raise RuntimeError("offline after correction reserve")
            return run

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        repo = CrashRepository(system.database.session)
        provider = CompletedOutputProvider(
            [{"kind": "stop"}, {"kind": "stop", "reason_code": "done"}]
        )
        service = _output_runtime(system, repo, provider)
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="bounded offline correction",
            budget=_budget(),
        )
        if crash != "none":
            with pytest.raises(RuntimeError, match="offline"):
                await service.advance_run(
                    context=_context(),
                    agent_run_id=run.agent_run_id,
                    expected_row_version=run.row_version,
                )
            run = await repo.get_run(run.agent_run_id)
            assert run is not None
            # A fresh service/repository proves no in-memory retry counter is needed.
            repo = SqlAgentRepository(system.database.session)
            service = _output_runtime(system, repo, provider)
        result = await service.advance_run(
            context=_context(), agent_run_id=run.agent_run_id, expected_row_version=run.row_version
        )
        steps = await repo.list_steps(run.agent_run_id)
        if crash in {"reserve", "before_feedback"}:
            assert result.termination_reason == "model_call_outcome_unknown"
            assert len(provider.calls) == 1
        else:
            assert result.status == "completed" and result.total_model_calls == 2
            assert result.total_tokens == 4 and len(provider.calls) == 2
            assert steps[0].decision_json is None and steps[0].output_feedback["correction_allowed"]
            assert steps[1].output_feedback["correction_of_step_id"] == str(steps[0].agent_step_id)
        assert not result.produced_proposal_revision_id


@pytest.mark.asyncio
async def test_duplicate_sql_finalize_keeps_counters_and_trace(isolated_database_url, tmp_path):
    from labpilot.api.schemas.agent_runs import AgentRunTraceResponse

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        repo = SqlAgentRepository(system.database.session)
        provider = CompletedOutputProvider(
            [{"kind": "stop", "reason_code": "done", "summary": "中" * 334}]
        )
        service = _output_runtime(system, repo, provider)
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="display isolation",
            budget=_budget(),
        )
        result = await service.advance_run(
            context=_context(), agent_run_id=run.agent_run_id, expected_row_version=run.row_version
        )
        (step,) = await repo.list_steps(run.agent_run_id)
        trace = AgentRunTraceResponse.from_details(await service.get_trace(run.agent_run_id))
        assert trace.steps[0].output_feedback.category == "summary_omitted"
        assert step.decision_json["summary"] is None
        with pytest.raises(AgentRuntimeError, match="row_version_conflict"):
            await repo.finalize_model_step(
                run.agent_run_id,
                expected_row_version=result.row_version,
                agent_step_id=step.agent_step_id,
                outcome="succeeded",
                token_usage={"total_tokens": 2},
                decision_kind="stop",
                decision_json=step.decision_json,
                total_tokens=2,
                run_status="completed",
                termination_reason="done",
                completed_at=step.completed_at,
            )
        current = await repo.get_run(run.agent_run_id)
        assert current.total_model_calls == 1 and current.total_tokens == 2


@pytest.mark.asyncio
async def test_feedback_migration_refuses_evidence_loss(isolated_database_url, tmp_path):
    from alembic import command
    from tests.integration.db.test_migrations import _alembic_config

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        provider = CompletedOutputProvider(
            [{"kind": "stop"}, {"kind": "stop", "reason_code": "done"}]
        )
        service = _output_runtime(system, SqlAgentRepository(system.database.session), provider)
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="audit feedback",
            budget=_budget(),
        )
        await service.advance_run(
            context=_context(), agent_run_id=run.agent_run_id, expected_row_version=run.row_version
        )
        with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
            command.downgrade(_alembic_config(isolated_database_url), "0014_agent_history_context")
        async with system.database.session() as session:
            assert (
                await session.scalar(text("SELECT version_num FROM alembic_version"))
                == "0018_agent_source_patch_drafts"
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_steps WHERE output_feedback_json IS NOT NULL")
                )
                == 2
            )


@pytest.mark.asyncio
async def test_sql_correction_reservation_rejects_bad_link_and_second_reserve(
    isolated_database_url, tmp_path
):
    from datetime import UTC, datetime
    from uuid import uuid4

    from labpilot.agent_runtime.types import OutputFeedback, feedback_json

    class CommitFeedbackThenCrash(SqlAgentRepository):
        async def finalize_model_step(self, *args, **kwargs):
            run = await super().finalize_model_step(*args, **kwargs)
            if run.status == "ready":
                raise RuntimeError("offline after feedback")
            return run

    async with _proposal_system(isolated_database_url, tmp_path) as system:
        repo = CommitFeedbackThenCrash(system.database.session)
        provider = CompletedOutputProvider([{"kind": "stop"}])
        service = _output_runtime(system, repo, provider)
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="reservation invariants",
            budget=_budget(),
        )
        with pytest.raises(RuntimeError, match="offline"):
            await service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        current = await repo.get_run(run.agent_run_id)
        (source,) = await repo.list_steps(run.agent_run_id)
        reserved = replace(
            source,
            agent_step_id=uuid4(),
            ordinal=current.next_step_ordinal,
            outcome="reserved",
            completed_at=None,
            started_at=datetime.now(UTC),
            token_usage=None,
            output_feedback=feedback_json(
                OutputFeedback(
                    category="correction",
                    code="correction_reserved",
                    error_count=0,
                    correction_of_step_id=uuid4(),
                )
            ),
        )
        with pytest.raises(AgentRuntimeError, match="output_correction_conflict"):
            await repo.reserve_model_step(
                run.agent_run_id, expected_row_version=current.row_version, step=reserved
            )
        assert len(await repo.list_steps(run.agent_run_id)) == 1
        reserved = replace(
            reserved,
            output_feedback=feedback_json(
                OutputFeedback(
                    category="correction",
                    code="correction_reserved",
                    error_count=0,
                    correction_of_step_id=source.agent_step_id,
                )
            ),
        )
        active = await repo.reserve_model_step(
            run.agent_run_id, expected_row_version=current.row_version, step=reserved
        )
        with pytest.raises(AgentRuntimeError, match="row_version_conflict"):
            await repo.reserve_model_step(
                run.agent_run_id, expected_row_version=current.row_version, step=reserved
            )
        another = replace(reserved, agent_step_id=uuid4(), ordinal=active.next_step_ordinal)
        with pytest.raises(AgentRuntimeError, match="output_correction_conflict"):
            await repo.reserve_model_step(
                run.agent_run_id, expected_row_version=active.row_version, step=another
            )
        assert len(await repo.list_steps(run.agent_run_id)) == 2 and len(provider.calls) == 1


@pytest.mark.asyncio
async def test_fresh_version_advance_during_correction_fails_closed_without_third_call(
    isolated_database_url,
    tmp_path,
):
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        repo = SqlAgentRepository(system.database.session)
        started, release = asyncio.Event(), asyncio.Event()

        async def before_call(request):
            if "output_feedback" in request.messages[0]["content"]:
                started.set()
                await release.wait()

        provider = CompletedOutputProvider(
            [{"kind": "stop"}, {"kind": "stop", "reason_code": "done"}], before_call
        )
        service = _output_runtime(system, repo, provider)
        run = await service.create_run(
            context=_context(),
            project_id=PROJECT_ID,
            repository_snapshot_id=system.snapshot_id,
            goal="single writer boundary",
            budget=_budget(),
        )
        task = asyncio.create_task(
            service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        try:
            fresh = await repo.get_run(run.agent_run_id)
            interrupted = await service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=fresh.row_version,
            )
        finally:
            release.set()
        with pytest.raises(AgentRuntimeError, match="row_version_conflict"):
            await task
        assert interrupted.termination_reason == "model_call_outcome_unknown"
        assert len(provider.calls) == 2 and interrupted.total_model_calls == 2
        assert interrupted.total_tokens == 2  # known first response; second cost is unknown.
        first, second = await repo.list_steps(run.agent_run_id)
        assert second.output_feedback["correction_of_step_id"] == str(first.agent_step_id)
        assert second.outcome == "failed" and second.decision_json is None
