from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.provider import ModelRequest, ModelResponse, ProviderError
from labpilot.agent_runtime.service import AgentApplicationService, AgentRuntimeError
from labpilot.agent_runtime.types import AgentBudget, AgentRunDetails, AgentStepDetails
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.tools_policy.types import ToolCallDetails, ToolDefinition


class MemoryAgentRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, AgentRunDetails] = {}
        self.steps: dict[UUID, list[AgentStepDetails]] = {}
        self.active = False

    def _begin(self) -> None:
        assert self.active is False
        self.active = True

    def _end(self) -> None:
        self.active = False

    async def add_run(self, run: AgentRunDetails) -> AgentRunDetails:
        self._begin()
        self.runs[run.agent_run_id] = run
        self.steps[run.agent_run_id] = []
        self._end()
        return run

    async def get_run(self, agent_run_id: UUID) -> AgentRunDetails | None:
        self._begin()
        result = self.runs.get(agent_run_id)
        self._end()
        return result

    async def list_steps(self, agent_run_id: UUID) -> tuple[AgentStepDetails, ...]:
        self._begin()
        result = tuple(self.steps[agent_run_id])
        self._end()
        return result

    async def reserve_model_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        step: AgentStepDetails,
    ) -> AgentRunDetails:
        self._begin()
        run = self.runs[agent_run_id]
        if run.row_version != expected_row_version:
            self._end()
            raise AgentRuntimeError("row_version_conflict")
        assert step.outcome == "reserved"
        self.steps[agent_run_id].append(step)
        run = replace(
            run,
            status="running",
            started_at=run.started_at or step.started_at,
            next_step_ordinal=run.next_step_ordinal + 1,
            row_version=run.row_version + 1,
        )
        self.runs[agent_run_id] = run
        self._end()
        return run

    async def finalize_model_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        agent_step_id: UUID,
        outcome: str,
        token_usage: Mapping[str, object],
        decision_kind: str | None,
        decision_json: Mapping[str, object] | None,
        total_tokens: int,
        run_status: str,
        termination_reason: str | None,
        completed_at: datetime,
    ) -> AgentRunDetails:
        self._begin()
        run = self.runs[agent_run_id]
        if run.row_version != expected_row_version:
            self._end()
            raise AgentRuntimeError("row_version_conflict")
        index = next(
            index
            for index, item in enumerate(self.steps[agent_run_id])
            if item.agent_step_id == agent_step_id
        )
        step = self.steps[agent_run_id][index]
        self.steps[agent_run_id][index] = replace(
            step,
            outcome=outcome,
            completed_at=completed_at,
            token_usage=dict(token_usage),
            decision_kind=decision_kind,
            decision_json=None if decision_json is None else dict(decision_json),
        )
        terminal_at = (
            completed_at if run_status in {"failed", "completed", "budget_exhausted"} else None
        )
        run = replace(
            run,
            status=run_status,
            termination_reason=termination_reason,
            total_model_calls=run.total_model_calls + 1,
            total_tokens=run.total_tokens + total_tokens,
            completed_at=terminal_at,
            row_version=run.row_version + 1,
        )
        self.runs[agent_run_id] = run
        self._end()
        return run

    async def link_tool_call(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        agent_step_id: UUID,
        tool_call_id: UUID,
    ) -> AgentRunDetails:
        self._begin()
        run = self.runs[agent_run_id]
        if run.row_version != expected_row_version:
            self._end()
            raise AgentRuntimeError("row_version_conflict")
        index = next(
            index
            for index, item in enumerate(self.steps[agent_run_id])
            if item.agent_step_id == agent_step_id
        )
        self.steps[agent_run_id][index] = replace(
            self.steps[agent_run_id][index], tool_call_id=tool_call_id
        )
        run = replace(run, row_version=run.row_version + 1)
        self.runs[agent_run_id] = run
        self._end()
        return run

    async def add_human_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        step: AgentStepDetails,
    ) -> AgentRunDetails:
        self._begin()
        run = self.runs[agent_run_id]
        if run.row_version != expected_row_version:
            self._end()
            raise AgentRuntimeError("row_version_conflict")
        if run.next_step_ordinal > run.max_steps:
            run = replace(
                run,
                status="budget_exhausted",
                termination_reason="max_steps",
                completed_at=step.started_at,
                row_version=run.row_version + 1,
            )
            self.runs[agent_run_id] = run
            self._end()
            return run
        self.steps[agent_run_id].append(step)
        run = replace(
            run,
            status="ready",
            next_step_ordinal=run.next_step_ordinal + 1,
            row_version=run.row_version + 1,
        )
        self.runs[agent_run_id] = run
        self._end()
        return run

    async def update_run_state(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        status: str,
        termination_reason: str | None,
        produced_proposal_revision_id: UUID | None = None,
    ) -> AgentRunDetails:
        self._begin()
        run = self.runs[agent_run_id]
        if run.row_version != expected_row_version:
            self._end()
            raise AgentRuntimeError("row_version_conflict")
        run = replace(
            run,
            status=status,
            termination_reason=termination_reason,
            produced_proposal_revision_id=produced_proposal_revision_id,
            completed_at=(
                datetime.now(UTC) if status in {"completed", "failed", "budget_exhausted"} else None
            ),
            row_version=run.row_version + 1,
        )
        self.runs[agent_run_id] = run
        self._end()
        return run

    def inject_reserved(self, agent_run_id: UUID) -> AgentRunDetails:
        run = self.runs[agent_run_id]
        step = AgentStepDetails(
            agent_step_id=uuid4(),
            agent_run_id=agent_run_id,
            ordinal=run.next_step_ordinal,
            step_kind="model_call",
            tool_call_id=None,
            provider_name=run.provider_name,
            model_name=run.model_name,
            canonical_request_hash="a" * 64,
            bound_toolset_hash="b" * 64,
            semantic_parameters={},
            output_schema_hash="c" * 64,
            started_at=datetime.now(UTC),
            completed_at=None,
            outcome="reserved",
            token_usage=None,
            decision_kind=None,
            decision_json=None,
            human_input_text=None,
        )
        self.steps[agent_run_id].append(step)
        run = replace(
            run,
            status="running",
            next_step_ordinal=run.next_step_ordinal + 1,
            row_version=run.row_version + 1,
        )
        self.runs[agent_run_id] = run
        return run


class CrashAfterSuccessfulFinalizeRepository(MemoryAgentRepository):
    def __init__(self) -> None:
        super().__init__()
        self._crash_on_next_success = True

    async def finalize_model_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        agent_step_id: UUID,
        outcome: str,
        token_usage: Mapping[str, object],
        decision_kind: str | None,
        decision_json: Mapping[str, object] | None,
        total_tokens: int,
        run_status: str,
        termination_reason: str | None,
        completed_at: datetime,
    ) -> AgentRunDetails:
        result = await super().finalize_model_step(
            agent_run_id,
            expected_row_version=expected_row_version,
            agent_step_id=agent_step_id,
            outcome=outcome,
            token_usage=token_usage,
            decision_kind=decision_kind,
            decision_json=decision_json,
            total_tokens=total_tokens,
            run_status=run_status,
            termination_reason=termination_reason,
            completed_at=completed_at,
        )
        if outcome == "succeeded" and self._crash_on_next_success:
            self._crash_on_next_success = False
            raise RuntimeError("crash after committed model-step finalization")
        return result


class SnapshotSource:
    def __init__(self, descriptor: RepositorySourceDescriptor) -> None:
        self.descriptor = descriptor

    async def describe(self, repository_snapshot_id: UUID) -> RepositorySourceDescriptor:
        if repository_snapshot_id != self.descriptor.repository_snapshot_id:
            raise ValueError("snapshot_not_found")
        return self.descriptor


class SnapshotCatalog:
    def __init__(self, *descriptors: RepositorySourceDescriptor) -> None:
        self.descriptors = {
            descriptor.repository_snapshot_id: descriptor for descriptor in descriptors
        }

    async def describe(self, repository_snapshot_id: UUID) -> RepositorySourceDescriptor:
        return self.descriptors[repository_snapshot_id]


class ScriptedProvider:
    provider_name = "scripted"
    egress = "local"

    def __init__(self, script: list[object], before_call=None) -> None:
        self.script = script
        self.before_call = before_call
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self.before_call is not None:
            self.before_call(request)
        self.calls.append(request)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        payload, usage = item if isinstance(item, tuple) else (item, {})
        return ModelResponse(payload=payload, usage=usage)


class DeadlineProbeProvider:
    provider_name = "deadline-probe"
    egress = "local"

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []
        self.cancelled = asyncio.Event()
        self._release = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        try:
            await asyncio.wait_for(self._release.wait(), timeout=1.25)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        except TimeoutError:
            pass
        return ModelResponse(payload={"kind": "stop", "reason_code": "too_late"}, usage={})


class MemoryToolService:
    def __init__(
        self,
        repository: MemoryAgentRepository,
        snapshot_id: UUID | None = None,
    ) -> None:
        self.repository = repository
        self.snapshot_id = snapshot_id
        self.executions = 0
        self.calls: dict[UUID, ToolCallDetails] = {}
        self.events: list[str] = []

        async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
            return arguments

        self._definitions = (
            ToolDefinition(
                name="repository.read_text",
                version="v1",
                risk="green",
                side_effect="read_only",
                input_schema={
                    "type": "object",
                    "properties": {"relative_path": {"type": "string"}},
                    "required": ["relative_path"],
                    "additionalProperties": False,
                },
                approval_required=False,
                executor=executor,
            ),
        )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        self.events.append("definitions")
        return self._definitions

    async def request_call(
        self, *, context, project_id, definition_name, arguments
    ) -> ToolCallDetails:
        self.events.append("request_call")
        call = ToolCallDetails(
            tool_call_id=uuid4(),
            project_id=project_id,
            tool_name=definition_name,
            tool_version="v1",
            risk="green",
            side_effect="read_only",
            arguments=MappingProxyType(dict(arguments)),
            arguments_hash="d" * 64,
            status="requested",
            result=None,
            result_hash=None,
            error_code=None,
            requested_at=datetime.now(UTC),
            completed_at=None,
        )
        self.calls[call.tool_call_id] = call
        return call

    async def execute_call(self, *, context, tool_call_id) -> ToolCallDetails:
        assert self.repository.active is False
        self.events.append("execute_call")
        self.executions += 1
        call = replace(
            self.calls[tool_call_id],
            status="executed",
            result={"text": "explicit tool result"},
            result_hash="e" * 64,
            completed_at=datetime.now(UTC),
        )
        self.calls[tool_call_id] = call
        return call

    async def get_call(self, tool_call_id: UUID) -> ToolCallDetails:
        self.events.append("get_call")
        return self.calls[tool_call_id]


class RecordingToolFactory:
    def __init__(self, repository: MemoryAgentRepository) -> None:
        self.repository = repository
        self.calls: list[UUID] = []
        self.services: list[MemoryToolService] = []

    def __call__(self, repository_snapshot_id: UUID) -> MemoryToolService:
        self.calls.append(repository_snapshot_id)
        service = MemoryToolService(self.repository, repository_snapshot_id)
        self.services.append(service)
        return service


@dataclass(frozen=True)
class Revision:
    proposal_revision_id: UUID


class MemoryProposalService:
    def __init__(self, repository: MemoryAgentRepository) -> None:
        self.repository = repository
        self.calls: list[UUID] = []
        self.revisions: dict[UUID, Revision] = {}
        self.crash_after_first_create = False

    async def create_agent_revision(
        self, *, context, project_id, origin_agent_step_id, payload
    ) -> Revision:
        steps = await self.repository.list_steps(
            next(
                run_id
                for run_id, steps in self.repository.steps.items()
                if any(step.agent_step_id == origin_agent_step_id for step in steps)
            )
        )
        assert (
            next(step for step in steps if step.agent_step_id == origin_agent_step_id).outcome
            == "succeeded"
        )
        self.calls.append(origin_agent_step_id)
        revision = self.revisions.setdefault(origin_agent_step_id, Revision(uuid4()))
        if self.crash_after_first_create and len(self.calls) == 1:
            raise RuntimeError("simulated materialization crash")
        return revision


def _context() -> CommandContext:
    return CommandContext(
        installation_identity="test-installation",
        actor=CommandActor(actor_type="human", actor_id="operator"),
        interaction_correlation_id="agent-request-1",
    )


def _descriptor(project_id: UUID | None = None) -> RepositorySourceDescriptor:
    return RepositorySourceDescriptor(
        repository_snapshot_id=uuid4(),
        repository_snapshot_identity="a" * 64,
        repository_registration_id=uuid4(),
        project_id=project_id or uuid4(),
        head_commit="b" * 40,
        current_branch="feature/test",
        is_detached_head=False,
        is_clean=True,
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


def _proposal(snapshot: RepositorySourceDescriptor) -> dict[str, object]:
    return {
        "goal": "goal",
        "experiment_type": "comparison",
        "repository_snapshot_id": str(snapshot.repository_snapshot_id),
        "repository_snapshot_identity": snapshot.repository_snapshot_identity,
        "train_entrypoint": "train.py",
        "eval_entrypoint": None,
        "workdir": ".",
        "parameter_matrix": {"optimizer": ["adam"]},
        "seeds": [1],
        "dataset_reference": {
            "reference_type": "uri",
            "path_or_uri": "s3://dataset",
            "stable_identity": "v1",
            "reproducibility_level": "strict",
        },
        "environment_expectation": {
            "python_profile": "3.12",
            "framework_requirements": [],
            "device_requirement": None,
            "cuda_requirement": None,
        },
        "metrics": [],
        "artifacts": [],
        "validation_rules": [],
        "timeout_seconds": 60,
        "max_retries": 0,
    }


def _service(
    repository: MemoryAgentRepository,
    descriptor: RepositorySourceDescriptor,
    provider: ScriptedProvider,
    *,
    egress_policy: str = "local_only",
) -> tuple[AgentApplicationService, MemoryToolService, MemoryProposalService]:
    tools = MemoryToolService(repository)
    proposals = MemoryProposalService(repository)

    def tool_service_factory(repository_snapshot_id: UUID) -> MemoryToolService:
        assert repository_snapshot_id == descriptor.repository_snapshot_id
        return tools

    return (
        AgentApplicationService(
            repository=repository,
            repository_source=SnapshotSource(descriptor),
            provider=provider,
            model_name="test-model",
            context_compiler=ContextCompiler(),
            tool_service_factory=tool_service_factory,
            proposal_service=proposals,
            data_egress_policy=egress_policy,
        ),
        tools,
        proposals,
    )


def _factory_service(
    repository: MemoryAgentRepository,
    repository_source: SnapshotSource | SnapshotCatalog,
    provider: ScriptedProvider,
    factory: RecordingToolFactory,
) -> AgentApplicationService:
    return AgentApplicationService(
        repository=repository,
        repository_source=repository_source,
        provider=provider,
        model_name="test-model",
        context_compiler=ContextCompiler(),
        tool_service_factory=factory,
        proposal_service=MemoryProposalService(repository),
        data_egress_policy="local_only",
    )


@pytest.mark.asyncio
async def test_get_trace_returns_persisted_state_without_side_effects() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            (
                {
                    "kind": "stop",
                    "reason_code": "done",
                    "summary": "persisted summary",
                },
                {"input_tokens": 10, "output_tokens": 5},
            )
        ]
    )
    factory = RecordingToolFactory(repository)
    service = _factory_service(repository, SnapshotSource(descriptor), provider, factory)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    persisted_run = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )
    persisted_steps = await repository.list_steps(run.agent_run_id)
    provider_calls_before = len(provider.calls)
    tool_factory_calls_before = len(factory.calls)
    tool_requests_before = sum(
        tools.events.count("request_call") for tools in factory.services
    )
    tool_executions_before = sum(tools.executions for tools in factory.services)

    trace = await service.get_trace(run.agent_run_id)

    assert trace.run == persisted_run
    assert trace.steps == persisted_steps
    assert trace.steps[0].ordinal == 1
    assert trace.steps[0].decision_kind == "stop"
    assert trace.steps[0].decision_json == {
        "kind": "stop",
        "reason_code": "done",
        "summary": "persisted summary",
    }
    assert trace.steps[0].token_usage == {"input_tokens": 10, "output_tokens": 5}
    assert len(provider.calls) == provider_calls_before
    assert len(factory.calls) == tool_factory_calls_before
    assert sum(tools.events.count("request_call") for tools in factory.services) == (
        tool_requests_before
    )
    assert sum(tools.executions for tools in factory.services) == tool_executions_before


@pytest.mark.asyncio
async def test_get_trace_rejects_missing_run() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    factory = RecordingToolFactory(repository)
    service = _factory_service(repository, SnapshotSource(descriptor), provider, factory)

    with pytest.raises(AgentRuntimeError) as error:
        await service.get_trace(uuid4())

    assert error.value.reason_code == "agent_run_not_found"


@pytest.mark.asyncio
async def test_create_run_does_not_resolve_tool_factory() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    factory = RecordingToolFactory(repository)
    service = _factory_service(repository, SnapshotSource(descriptor), provider, factory)

    await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    assert factory.calls == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_advance_resolves_tool_factory_from_persisted_snapshot() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([{"kind": "stop", "reason_code": "done"}])
    factory = RecordingToolFactory(repository)
    service = _factory_service(repository, SnapshotSource(descriptor), provider, factory)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert factory.calls == [descriptor.repository_snapshot_id]


@pytest.mark.asyncio
async def test_one_agent_service_isolates_two_run_snapshots() -> None:
    project_id = uuid4()
    first = replace(
        _descriptor(project_id),
        repository_snapshot_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    second = replace(
        _descriptor(project_id),
        repository_snapshot_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    )
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {"kind": "stop", "reason_code": "first_done"},
            {"kind": "stop", "reason_code": "second_done"},
        ]
    )
    factory = RecordingToolFactory(repository)
    service = _factory_service(
        repository, SnapshotCatalog(first, second), provider, factory
    )
    first_run = await service.create_run(
        context=_context(),
        project_id=project_id,
        repository_snapshot_id=first.repository_snapshot_id,
        goal="first",
        budget=_budget(),
    )
    second_run = await service.create_run(
        context=_context(),
        project_id=project_id,
        repository_snapshot_id=second.repository_snapshot_id,
        goal="second",
        budget=_budget(),
    )

    await service.advance_run(
        context=_context(),
        agent_run_id=first_run.agent_run_id,
        expected_row_version=first_run.row_version,
    )
    await service.advance_run(
        context=_context(),
        agent_run_id=second_run.agent_run_id,
        expected_row_version=second_run.row_version,
    )

    assert factory.calls == [
        first.repository_snapshot_id,
        second.repository_snapshot_id,
    ]


@pytest.mark.asyncio
async def test_one_advance_reuses_same_snapshot_bound_tool_service() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {"relative_path": "train.py"},
            },
            {"kind": "stop", "reason_code": "done"},
        ]
    )
    factory = RecordingToolFactory(repository)
    service = _factory_service(repository, SnapshotSource(descriptor), provider, factory)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert factory.calls == [descriptor.repository_snapshot_id]
    assert len(factory.services) == 1
    assert factory.services[0].events.count("definitions") == 2
    assert factory.services[0].events.count("request_call") == 1
    assert factory.services[0].events.count("execute_call") == 1
    assert factory.services[0].events.count("get_call") == 1


@pytest.mark.asyncio
async def test_tool_arguments_cannot_override_persisted_snapshot_binding() -> None:
    project_id = uuid4()
    persisted = _descriptor(project_id)
    attempted = _descriptor(project_id)
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {
                    "relative_path": "train.py",
                    "repository_snapshot_id": str(attempted.repository_snapshot_id),
                },
            },
            {"kind": "stop", "reason_code": "done"},
        ]
    )
    factory = RecordingToolFactory(repository)
    service = _factory_service(
        repository, SnapshotCatalog(persisted, attempted), provider, factory
    )
    run = await service.create_run(
        context=_context(),
        project_id=project_id,
        repository_snapshot_id=persisted.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert factory.calls == [persisted.repository_snapshot_id]
    assert all(
        tools.snapshot_id == persisted.repository_snapshot_id
        for tools in factory.services
    )
    assert factory.services[0].events.count("request_call") == 1
    assert factory.services[0].events.count("execute_call") == 1


@pytest.mark.asyncio
async def test_later_advance_resolves_factory_again_from_persisted_snapshot() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {"kind": "request_human_input", "question": "Which metric?"},
            {"kind": "stop", "reason_code": "done"},
        ]
    )
    factory = RecordingToolFactory(repository)
    service = _factory_service(repository, SnapshotSource(descriptor), provider, factory)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )
    paused = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )
    resumed = await service.submit_human_input(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=paused.row_version,
        text="accuracy",
    )

    await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=resumed.row_version,
    )

    assert factory.calls == [
        descriptor.repository_snapshot_id,
        descriptor.repository_snapshot_id,
    ]
    assert factory.services[0] is not factory.services[1]


@pytest.mark.asyncio
async def test_create_run_validates_snapshot_and_never_invokes_provider() -> None:
    project_id = uuid4()
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    service, _, _ = _service(repository, descriptor, provider)

    with pytest.raises(AgentRuntimeError, match="snapshot_project_mismatch"):
        await service.create_run(
            context=_context(),
            project_id=project_id,
            repository_snapshot_id=descriptor.repository_snapshot_id,
            goal="goal",
            budget=_budget(),
        )

    assert provider.calls == []
    assert repository.runs == {}


@pytest.mark.asyncio
async def test_model_step_is_reserved_with_stable_hashes_before_provider_call() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()

    def before_call(request: ModelRequest) -> None:
        assert repository.active is False
        step = next(iter(repository.steps.values()))[-1]
        assert step.outcome == "reserved"
        assert step.canonical_request_hash is not None
        assert step.bound_toolset_hash == request.bound_toolset_hash
        assert step.semantic_parameters == {}
        assert step.output_schema_hash is not None

    provider = ScriptedProvider(
        [{"kind": "stop", "reason_code": "done"}, {"kind": "stop", "reason_code": "done"}],
        before_call,
    )
    service, _, _ = _service(repository, descriptor, provider)
    hashes = []
    for _ in range(2):
        run = await service.create_run(
            context=_context(),
            project_id=descriptor.project_id,
            repository_snapshot_id=descriptor.repository_snapshot_id,
            goal="same goal",
            budget=_budget(),
        )
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
        hashes.append(repository.steps[run.agent_run_id][0].canonical_request_hash)

    assert hashes[0] == hashes[1]


@pytest.mark.asyncio
async def test_remote_provider_is_blocked_before_call() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    provider.egress = "remote"
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    with pytest.raises(AgentRuntimeError, match="remote_provider_blocked"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

    assert provider.calls == []
    assert repository.steps[run.agent_run_id] == []


@pytest.mark.asyncio
async def test_unknown_reserved_model_outcome_is_failed_closed_without_retry() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )
    reserved_run = repository.inject_reserved(run.agent_run_id)

    failed = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=reserved_run.row_version,
    )

    assert failed.status == "failed"
    assert failed.termination_reason == "model_call_outcome_unknown"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_reserved_outcome_recovery_does_not_require_remote_egress() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    provider.egress = "remote"
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )
    reserved_run = repository.inject_reserved(run.agent_run_id)

    failed = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=reserved_run.row_version,
    )

    assert failed.termination_reason == "model_call_outcome_unknown"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_human_decision_state_is_committed_with_finalization() -> None:
    descriptor = _descriptor()
    repository = CrashAfterSuccessfulFinalizeRepository()
    provider = ScriptedProvider(
        [
            (
                {"kind": "request_human_input", "question": "Which metric?"},
                {"total_tokens": 999},
            ),
            {"kind": "stop", "reason_code": "unexpected_second_call"},
        ]
    )
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    with pytest.raises(RuntimeError, match="crash after committed"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

    persisted = await service.get_run(run.agent_run_id)
    steps = await repository.list_steps(run.agent_run_id)
    assert persisted.status == "awaiting_human"
    assert steps[-1].outcome == "succeeded"
    assert steps[-1].decision_kind == "request_human_input"

    with pytest.raises(AgentRuntimeError, match="human_input_required"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=persisted.row_version,
        )

    assert len(provider.calls) == 1
    assert len(await repository.list_steps(run.agent_run_id)) == 1


@pytest.mark.asyncio
async def test_stop_decision_state_is_committed_with_finalization() -> None:
    descriptor = _descriptor()
    repository = CrashAfterSuccessfulFinalizeRepository()
    provider = ScriptedProvider(
        [
            (
                {"kind": "stop", "reason_code": "goal_satisfied"},
                {"total_tokens": 999},
            ),
            {"kind": "stop", "reason_code": "unexpected_second_call"},
        ]
    )
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    with pytest.raises(RuntimeError, match="crash after committed"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

    persisted = await service.get_run(run.agent_run_id)
    steps = await repository.list_steps(run.agent_run_id)
    assert persisted.status == "completed"
    assert persisted.termination_reason == "goal_satisfied"
    assert steps[-1].outcome == "succeeded"
    assert steps[-1].decision_kind == "stop"

    with pytest.raises(AgentRuntimeError, match="agent_run_terminal"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=persisted.row_version,
        )

    assert len(provider.calls) == 1
    assert len(await repository.list_steps(run.agent_run_id)) == 1


@pytest.mark.parametrize(
    ("decision", "decision_kind", "payload_field", "payload_value"),
    [
        (
            {"kind": "request_human_input", "question": "Which metric?"},
            "request_human_input",
            "question",
            "Which metric?",
        ),
        (
            {"kind": "stop", "reason_code": "goal_satisfied"},
            "stop",
            "reason_code",
            "goal_satisfied",
        ),
    ],
    ids=["human", "stop"],
)
@pytest.mark.asyncio
async def test_token_exhaustion_is_atomic_with_human_and_stop_decisions(
    decision: dict[str, object],
    decision_kind: str,
    payload_field: str,
    payload_value: str,
) -> None:
    descriptor = _descriptor()
    repository = CrashAfterSuccessfulFinalizeRepository()
    provider = ScriptedProvider(
        [
            (decision, {"total_tokens": 1000}),
            {"kind": "stop", "reason_code": "unexpected_second_call"},
        ]
    )
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(max_tokens=1000),
    )

    with pytest.raises(RuntimeError, match="crash after committed"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

    finalized = await service.get_run(run.agent_run_id)
    steps = await repository.list_steps(run.agent_run_id)
    assert finalized.status == "budget_exhausted"
    assert finalized.termination_reason == "max_tokens"
    assert finalized.total_tokens == 1000
    assert steps[-1].outcome == "succeeded"
    assert steps[-1].token_usage == {"total_tokens": 1000}
    assert steps[-1].decision_kind == decision_kind
    assert steps[-1].decision_json is not None
    assert steps[-1].decision_json[payload_field] == payload_value

    with pytest.raises(AgentRuntimeError, match="agent_run_terminal"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=finalized.row_version,
        )

    assert len(provider.calls) == 1
    assert len(await repository.list_steps(run.agent_run_id)) == 1


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_reason", "retry_error"),
    [
        (
            {"kind": "request_human_input", "question": "Which metric?"},
            "awaiting_human",
            None,
            "human_input_required",
        ),
        (
            {"kind": "stop", "reason_code": "goal_satisfied"},
            "completed",
            "goal_satisfied",
            "agent_run_terminal",
        ),
    ],
    ids=["human", "stop"],
)
@pytest.mark.asyncio
async def test_human_and_stop_states_apply_below_token_budget(
    decision: dict[str, object],
    expected_status: str,
    expected_reason: str | None,
    retry_error: str,
) -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([(decision, {"total_tokens": 999})])
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(max_tokens=1000),
    )

    finalized = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert finalized.status == expected_status
    assert finalized.termination_reason == expected_reason
    with pytest.raises(AgentRuntimeError, match=retry_error):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=finalized.row_version,
        )
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_tool_call_executes_read_only_tool_outside_repository_and_continues() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {"relative_path": "train.py"},
            },
            {"kind": "stop", "reason_code": "done"},
        ]
    )
    service, tools, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    completed = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert completed.status == "completed"
    assert tools.executions == 1
    assert len(provider.calls) == 2
    assert repository.steps[run.agent_run_id][0].tool_call_id is not None
    assert "explicit tool result" in str(provider.calls[1].messages)


@pytest.mark.asyncio
async def test_human_input_pauses_and_submission_resumes_with_human_step() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {"kind": "request_human_input", "question": "Which metric?"},
            {"kind": "stop", "reason_code": "done"},
        ]
    )
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )
    paused = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )
    assert paused.status == "awaiting_human"

    resumed = await service.submit_human_input(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=paused.row_version,
        text="accuracy",
    )
    assert resumed.status == "ready"
    assert repository.steps[run.agent_run_id][-1].human_input_text == "accuracy"

    completed = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=resumed.row_version,
    )
    assert completed.status == "completed"
    assert "accuracy" in str(provider.calls[-1].messages)


@pytest.mark.asyncio
async def test_human_input_cannot_exceed_max_steps() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([{"kind": "request_human_input", "question": "Metric?"}])
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(max_steps=1),
    )
    paused = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    exhausted = await service.submit_human_input(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=paused.row_version,
        text="accuracy",
    )

    assert exhausted.status == "budget_exhausted"
    assert exhausted.termination_reason == "max_steps"
    assert exhausted.next_step_ordinal == 2
    assert len(await repository.list_steps(run.agent_run_id)) == 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_propose_is_persisted_before_materialization_and_replays_after_crash() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([{"kind": "propose", "proposal": _proposal(descriptor)}])
    service, _, proposals = _service(repository, descriptor, provider)
    proposals.crash_after_first_create = True
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(),
    )

    with pytest.raises(RuntimeError, match="simulated materialization crash"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
    after_crash = await service.get_run(run.agent_run_id)
    assert repository.steps[run.agent_run_id][-1].decision_kind == "propose"

    completed = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=after_crash.row_version,
    )

    assert completed.status == "completed"
    assert completed.produced_proposal_revision_id is not None
    assert len(provider.calls) == 1
    assert len(proposals.calls) == 2
    assert proposals.calls[0] == proposals.calls[1]


@pytest.mark.asyncio
async def test_budget_limit_terminates_without_second_model_call() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {"relative_path": "train.py"},
            },
        ]
    )
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(max_model_calls=1),
    )

    exhausted = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert exhausted.status == "budget_exhausted"
    assert exhausted.termination_reason == "max_model_calls"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    ("budget", "usage", "reason"),
    [
        (_budget(max_steps=1), {}, "max_steps"),
        (_budget(max_tokens=1), {"total_tokens": 1}, "max_tokens"),
    ],
)
@pytest.mark.asyncio
async def test_step_and_token_budgets_exhaust(
    budget: AgentBudget, usage: Mapping[str, object], reason: str
) -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider(
        [
            (
                {
                    "kind": "tool_call",
                    "tool_name": "repository.read_text",
                    "arguments": {"relative_path": "train.py"},
                },
                usage,
            )
        ]
    )
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=budget,
    )

    exhausted = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert exhausted.status == "budget_exhausted"
    assert exhausted.termination_reason == reason


@pytest.mark.asyncio
async def test_wall_clock_budget_exhausts_before_provider_call() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([])
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(max_wall_clock_seconds=1),
    )
    repository.runs[run.agent_run_id] = replace(
        run,
        created_at=datetime.now(UTC) - timedelta(seconds=2),
        started_at=datetime.now(UTC) - timedelta(seconds=2),
    )

    exhausted = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert exhausted.termination_reason == "max_wall_clock_seconds"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_run_wall_clock_deadline_cancels_provider_and_is_not_retried() -> None:
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = DeadlineProbeProvider()
    service, _, _ = _service(repository, descriptor, provider)  # type: ignore[arg-type]
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="goal",
        budget=_budget(max_wall_clock_seconds=1),
    )

    exhausted = await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    steps = await repository.list_steps(run.agent_run_id)
    assert provider.cancelled.is_set()
    assert exhausted.status == "budget_exhausted"
    assert exhausted.termination_reason == "max_wall_clock_seconds"
    assert steps[-1].outcome == "failed"
    assert steps[-1].decision_kind is None

    with pytest.raises(AgentRuntimeError, match="agent_run_terminal"):
        await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=exhausted.row_version,
        )

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_provider_failure_and_invalid_decision_fail_without_actions() -> None:
    descriptor = _descriptor()
    for response, reason in [
        (ProviderError("provider_timeout"), "provider_timeout"),
        (
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {},
                "reasoning": "secret",
            },
            "invalid_agent_decision",
        ),
    ]:
        repository = MemoryAgentRepository()
        provider = ScriptedProvider([response])
        service, tools, proposals = _service(repository, descriptor, provider)
        run = await service.create_run(
            context=_context(),
            project_id=descriptor.project_id,
            repository_snapshot_id=descriptor.repository_snapshot_id,
            goal="goal",
            budget=_budget(),
        )

        failed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )

        assert failed.status == "failed"
        assert failed.termination_reason == reason
        assert tools.executions == 0
        assert proposals.calls == []


@pytest.mark.asyncio
async def test_structured_logs_have_correlations_and_no_raw_content(caplog) -> None:
    caplog.set_level(logging.INFO)
    sentinel = "sentinel-agent-secret"
    descriptor = _descriptor()
    repository = MemoryAgentRepository()
    provider = ScriptedProvider([{"kind": "stop", "reason_code": "done", "summary": sentinel}])
    service, _, _ = _service(repository, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal=f"goal {sentinel}",
        budget=_budget(),
    )

    await service.advance_run(
        context=_context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )

    assert sentinel not in caplog.text
    lifecycle = [record for record in caplog.records if hasattr(record, "agent_run_id")]
    assert lifecycle
    assert all(record.request_id == "agent-request-1" for record in lifecycle)
    assert all(record.project_id == str(descriptor.project_id) for record in lifecycle)
    assert all(record.agent_run_id == str(run.agent_run_id) for record in lifecycle)
