from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from labpilot.agent_runtime.context_compiler import (
    ContextCompiler,
    PriorToolResult,
)
from labpilot.agent_runtime.provider import (
    DataEgressPolicy,
    ModelProvider,
    ProviderError,
    ProviderPolicyError,
    assert_provider_egress,
)
from labpilot.agent_runtime.repository import AgentRepository
from labpilot.agent_runtime.result_context import (
    FollowUpRunStore,
    ResultContextSource,
)
from labpilot.agent_runtime.types import (
    AgentBudget,
    AgentDecision,
    AgentRunDetails,
    AgentRunStatus,
    AgentRuntimeError,
    AgentRunTraceDetails,
    AgentStepDetails,
    HumanInputDecision,
    ProposeDecision,
    StopDecision,
    ToolCallDecision,
)
from labpilot.common.commands import CommandContext
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.experiments.proposal_types import ProposalPayload
from labpilot.tools_policy.types import ToolCallDetails, ToolDefinition

logger = logging.getLogger(__name__)


class RepositoryDescriptorSource(Protocol):
    async def describe(self, repository_snapshot_id: UUID) -> RepositorySourceDescriptor: ...


class AgentToolService(Protocol):
    definitions: tuple[ToolDefinition, ...]

    async def request_call(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        definition_name: str,
        arguments: Mapping[str, object],
    ) -> ToolCallDetails: ...

    async def execute_call(
        self, *, context: CommandContext, tool_call_id: UUID
    ) -> ToolCallDetails: ...

    async def get_call(self, tool_call_id: UUID) -> ToolCallDetails: ...


AgentToolServiceFactory = Callable[[UUID], AgentToolService]


class ProposalRevisionResult(Protocol):
    proposal_revision_id: UUID


class AgentProposalService(Protocol):
    async def create_agent_revision(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        origin_agent_step_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalRevisionResult: ...

    async def create_follow_up_agent_revision(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        experiment_id: UUID,
        origin_agent_step_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalRevisionResult: ...


class AgentApplicationService:
    def __init__(
        self,
        *,
        repository: AgentRepository,
        repository_source: RepositoryDescriptorSource,
        provider: ModelProvider,
        model_name: str,
        context_compiler: ContextCompiler,
        tool_service_factory: AgentToolServiceFactory,
        proposal_service: AgentProposalService,
        data_egress_policy: DataEgressPolicy,
        follow_up_run_store: FollowUpRunStore | None = None,
        result_context_source: ResultContextSource | None = None,
    ) -> None:
        self._repository = repository
        self._repository_source = repository_source
        self._provider = provider
        self._model_name = model_name
        self._compiler = context_compiler
        self._tool_service_factory = tool_service_factory
        self._proposals = proposal_service
        self._egress_policy = data_egress_policy
        self._follow_up_run_store = follow_up_run_store
        self._result_context_source = result_context_source
        self._decision_adapter: TypeAdapter[AgentDecision] = TypeAdapter(AgentDecision)

    async def create_run(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        repository_snapshot_id: UUID,
        goal: str,
        budget: AgentBudget,
    ) -> AgentRunDetails:
        _require_text(goal, "goal", 4000)
        descriptor = await self._repository_source.describe(repository_snapshot_id)
        if descriptor.repository_snapshot_id != repository_snapshot_id:
            raise AgentRuntimeError("snapshot_binding_mismatch")
        if descriptor.project_id != project_id:
            raise AgentRuntimeError("snapshot_project_mismatch")
        now = datetime.now(UTC)
        run = AgentRunDetails(
            agent_run_id=uuid4(),
            project_id=project_id,
            repository_snapshot_id=repository_snapshot_id,
            goal=goal,
            status="ready",
            termination_reason=None,
            egress_policy=self._egress_policy,
            provider_name=self._provider.provider_name,
            model_name=self._model_name,
            max_steps=budget.max_steps,
            max_model_calls=budget.max_model_calls,
            max_wall_clock_seconds=budget.max_wall_clock_seconds,
            max_tokens=budget.max_tokens,
            total_model_calls=0,
            total_tokens=0,
            next_step_ordinal=1,
            produced_proposal_revision_id=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            row_version=1,
        )
        stored = await self._repository.add_run(run)
        _log("agent_run_created", context, stored)
        return stored

    async def create_follow_up_run(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        source_batch_summary_revision_id: UUID,
        repository_snapshot_id: UUID,
        goal: str,
        budget: AgentBudget,
    ) -> AgentRunDetails:
        _require_text(goal, "goal", 4000)
        if self._follow_up_run_store is None or self._result_context_source is None:
            raise AgentRuntimeError("follow_up_runtime_unconfigured")
        now = datetime.now(UTC)
        run = AgentRunDetails(
            agent_run_id=uuid4(),
            project_id=project_id,
            repository_snapshot_id=repository_snapshot_id,
            goal=goal,
            status="ready",
            termination_reason=None,
            egress_policy=self._egress_policy,
            provider_name=self._provider.provider_name,
            model_name=self._model_name,
            max_steps=budget.max_steps,
            max_model_calls=budget.max_model_calls,
            max_wall_clock_seconds=budget.max_wall_clock_seconds,
            max_tokens=budget.max_tokens,
            total_model_calls=0,
            total_tokens=0,
            next_step_ordinal=1,
            produced_proposal_revision_id=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            row_version=1,
            source_batch_summary_revision_id=source_batch_summary_revision_id,
        )
        stored = await self._follow_up_run_store.create_bound_run(run)
        _log("follow_up_agent_run_created", context, stored)
        return stored

    async def advance_run(
        self,
        *,
        context: CommandContext,
        agent_run_id: UUID,
        expected_row_version: int,
    ) -> AgentRunDetails:
        run = await self._require_run(agent_run_id)
        if run.row_version != expected_row_version:
            raise AgentRuntimeError("row_version_conflict")
        if run.status in {"completed", "budget_exhausted", "failed"}:
            raise AgentRuntimeError("agent_run_terminal")
        if run.status == "awaiting_human":
            raise AgentRuntimeError("human_input_required")
        if run.source_batch_summary_revision_id is not None and self._result_context_source is None:
            raise AgentRuntimeError("follow_up_runtime_unconfigured")
        steps = await self._repository.list_steps(agent_run_id)
        if steps and steps[-1].outcome == "reserved":
            return await self._repository.finalize_model_step(
                agent_run_id,
                expected_row_version=run.row_version,
                agent_step_id=steps[-1].agent_step_id,
                outcome="failed",
                token_usage={},
                decision_kind=None,
                decision_json=None,
                total_tokens=0,
                run_status="failed",
                termination_reason="model_call_outcome_unknown",
                completed_at=datetime.now(UTC),
            )
        if steps and _is_pending_proposal(steps[-1], run):
            return await self._materialize_proposal(context, run, steps[-1])
        tools = self._tool_service_factory(run.repository_snapshot_id)

        while True:
            budget_reason = _budget_reason(run)
            if budget_reason is not None:
                return await self._repository.update_run_state(
                    agent_run_id,
                    expected_row_version=run.row_version,
                    status="budget_exhausted",
                    termination_reason=budget_reason,
                )
            try:
                assert_provider_egress(run.egress_policy, self._provider)
            except ProviderPolicyError as exc:
                raise AgentRuntimeError(exc.reason_code) from None
            descriptor = await self._repository_source.describe(run.repository_snapshot_id)
            result_context = None
            if run.source_batch_summary_revision_id is not None:
                assert self._result_context_source is not None
                result_context = await self._result_context_source.load(
                    run.source_batch_summary_revision_id
                )
            prior_results, human_inputs = await self._history(agent_run_id, tools)
            compiled = self._compiler.compile(
                provider_name=run.provider_name,
                model=run.model_name,
                goal=run.goal,
                snapshot=descriptor,
                definitions=tools.definitions,
                prior_tool_results=prior_results,
                prior_human_inputs=human_inputs,
                result_context=result_context,
            )
            step = AgentStepDetails(
                agent_step_id=uuid4(),
                agent_run_id=agent_run_id,
                ordinal=run.next_step_ordinal,
                step_kind="model_call",
                tool_call_id=None,
                provider_name=run.provider_name,
                model_name=run.model_name,
                canonical_request_hash=compiled.canonical_request_hash,
                bound_toolset_hash=compiled.request.bound_toolset_hash,
                semantic_parameters=dict(compiled.request.semantic_parameters),
                output_schema_hash=compiled.output_schema_hash,
                started_at=datetime.now(UTC),
                completed_at=None,
                outcome="reserved",
                token_usage=None,
                decision_kind=None,
                decision_json=None,
                human_input_text=None,
            )
            run = await self._repository.reserve_model_step(
                agent_run_id,
                expected_row_version=run.row_version,
                step=step,
            )
            _log("agent_model_step_reserved", context, run)
            remaining = _remaining_wall_clock_seconds(run)
            if remaining <= 0:
                return await self._finalize_wall_clock_exhaustion(context, run, step)
            timeout = asyncio.timeout(remaining)
            try:
                async with timeout:
                    response = await self._provider.complete(compiled.request)
            except Exception as exc:
                if isinstance(exc, TimeoutError) and timeout.expired():
                    return await self._finalize_wall_clock_exhaustion(context, run, step)
                reason = exc.reason_code if isinstance(exc, ProviderError) else "provider_failure"
                failed = await self._repository.finalize_model_step(
                    agent_run_id,
                    expected_row_version=run.row_version,
                    agent_step_id=step.agent_step_id,
                    outcome="failed",
                    token_usage={},
                    decision_kind=None,
                    decision_json=None,
                    total_tokens=0,
                    run_status="failed",
                    termination_reason=reason,
                    completed_at=datetime.now(UTC),
                )
                _log("agent_model_step_failed", context, failed, reason)
                return failed
            try:
                decision = self._decision_adapter.validate_python(response.payload)
            except ValidationError:
                failed = await self._repository.finalize_model_step(
                    agent_run_id,
                    expected_row_version=run.row_version,
                    agent_step_id=step.agent_step_id,
                    outcome="failed",
                    token_usage=dict(response.usage),
                    decision_kind=None,
                    decision_json=None,
                    total_tokens=_token_count(response.usage),
                    run_status="failed",
                    termination_reason="invalid_agent_decision",
                    completed_at=datetime.now(UTC),
                )
                _log(
                    "agent_model_step_failed",
                    context,
                    failed,
                    "invalid_agent_decision",
                )
                return failed

            decision_json = cast(
                dict[str, object],
                decision.model_dump(
                    mode="json",
                    warnings="none",
                    fallback=_serialization_fallback,
                ),
            )
            if _remaining_wall_clock_seconds(run) <= 0:
                return await self._finalize_wall_clock_exhaustion(context, run, step)
            total_tokens = _token_count(response.usage)
            final_status: AgentRunStatus = "running"
            final_termination_reason: str | None = None
            if run.total_tokens + total_tokens >= run.max_tokens:
                final_status = "budget_exhausted"
                final_termination_reason = "max_tokens"
            elif isinstance(decision, HumanInputDecision):
                final_status = "awaiting_human"
            elif isinstance(decision, StopDecision):
                final_status = "completed"
                final_termination_reason = decision.reason_code
            run = await self._repository.finalize_model_step(
                agent_run_id,
                expected_row_version=run.row_version,
                agent_step_id=step.agent_step_id,
                outcome="succeeded",
                token_usage=dict(response.usage),
                decision_kind=decision.kind,
                decision_json=decision_json,
                total_tokens=total_tokens,
                run_status=final_status,
                termination_reason=final_termination_reason,
                completed_at=datetime.now(UTC),
            )
            _log("agent_model_step_succeeded", context, run)
            if final_status == "budget_exhausted":
                return run
            if isinstance(decision, ToolCallDecision):
                try:
                    call = await tools.request_call(
                        context=context,
                        project_id=run.project_id,
                        definition_name=decision.tool_name,
                        arguments=decision.arguments,
                    )
                    run = await self._repository.link_tool_call(
                        agent_run_id,
                        expected_row_version=run.row_version,
                        agent_step_id=step.agent_step_id,
                        tool_call_id=call.tool_call_id,
                    )
                    result = await tools.execute_call(
                        context=context, tool_call_id=call.tool_call_id
                    )
                except Exception:
                    return await self._repository.update_run_state(
                        agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason="tool_execution_failed",
                    )
                if result.status != "executed":
                    return await self._repository.update_run_state(
                        agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason=result.error_code or "tool_execution_failed",
                    )
                continue
            if isinstance(decision, HumanInputDecision):
                return run
            if isinstance(decision, ProposeDecision):
                return await self._materialize_proposal(context, run, step, decision)
            assert isinstance(decision, StopDecision)
            return run

    async def submit_human_input(
        self,
        *,
        context: CommandContext,
        agent_run_id: UUID,
        expected_row_version: int,
        text: str,
    ) -> AgentRunDetails:
        _require_text(text, "human_input", 4000)
        run = await self._require_run(agent_run_id)
        if run.row_version != expected_row_version:
            raise AgentRuntimeError("row_version_conflict")
        if run.status != "awaiting_human":
            raise AgentRuntimeError("agent_run_not_awaiting_human")
        now = datetime.now(UTC)
        step = AgentStepDetails(
            agent_step_id=uuid4(),
            agent_run_id=agent_run_id,
            ordinal=run.next_step_ordinal,
            step_kind="human_input",
            tool_call_id=None,
            provider_name=None,
            model_name=None,
            canonical_request_hash=None,
            bound_toolset_hash=None,
            semantic_parameters=None,
            output_schema_hash=None,
            started_at=now,
            completed_at=now,
            outcome="succeeded",
            token_usage=None,
            decision_kind=None,
            decision_json=None,
            human_input_text=text,
        )
        resumed = await self._repository.add_human_step(
            agent_run_id,
            expected_row_version=run.row_version,
            step=step,
        )
        _log("agent_human_input_submitted", context, resumed)
        return resumed

    async def get_run(self, agent_run_id: UUID) -> AgentRunDetails:
        return await self._require_run(agent_run_id)

    async def get_trace(self, agent_run_id: UUID) -> AgentRunTraceDetails:
        run = await self._require_run(agent_run_id)
        steps = await self._repository.list_steps(agent_run_id)
        return AgentRunTraceDetails(run=run, steps=steps)

    async def _history(
        self,
        agent_run_id: UUID,
        tools: AgentToolService,
    ) -> tuple[tuple[PriorToolResult, ...], tuple[str, ...]]:
        results: list[PriorToolResult] = []
        human_inputs: list[str] = []
        for step in await self._repository.list_steps(agent_run_id):
            if step.step_kind == "human_input" and step.human_input_text is not None:
                human_inputs.append(step.human_input_text)
            if step.tool_call_id is not None:
                call = await tools.get_call(step.tool_call_id)
                if call.status == "executed" and call.result is not None:
                    results.append(PriorToolResult(call.tool_name, call.result))
        return tuple(results), tuple(human_inputs)

    async def _finalize_wall_clock_exhaustion(
        self,
        context: CommandContext,
        run: AgentRunDetails,
        step: AgentStepDetails,
    ) -> AgentRunDetails:
        exhausted = await self._repository.finalize_model_step(
            run.agent_run_id,
            expected_row_version=run.row_version,
            agent_step_id=step.agent_step_id,
            outcome="failed",
            token_usage={},
            decision_kind=None,
            decision_json=None,
            total_tokens=0,
            run_status="budget_exhausted",
            termination_reason="max_wall_clock_seconds",
            completed_at=datetime.now(UTC),
        )
        _log("agent_model_step_failed", context, exhausted, "max_wall_clock_seconds")
        return exhausted

    async def _materialize_proposal(
        self,
        context: CommandContext,
        run: AgentRunDetails,
        step: AgentStepDetails,
        decision: ProposeDecision | None = None,
    ) -> AgentRunDetails:
        if decision is None:
            assert step.decision_json is not None
            parsed = self._decision_adapter.validate_python(step.decision_json)
            if not isinstance(parsed, ProposeDecision):
                raise AgentRuntimeError("proposal_replay_invalid")
            decision = parsed
        if run.source_batch_summary_revision_id is None:
            revision = await self._proposals.create_agent_revision(
                context=context,
                project_id=run.project_id,
                origin_agent_step_id=step.agent_step_id,
                payload=decision.proposal,
            )
        else:
            if decision.proposal.repository_snapshot_id != run.repository_snapshot_id:
                return await self._repository.update_run_state(
                    run.agent_run_id,
                    expected_row_version=run.row_version,
                    status="failed",
                    termination_reason="follow_up_proposal_snapshot_mismatch",
                )
            assert self._result_context_source is not None
            result_context = await self._result_context_source.load(
                run.source_batch_summary_revision_id
            )
            revision = await self._proposals.create_follow_up_agent_revision(
                context=context,
                project_id=run.project_id,
                experiment_id=result_context.experiment_id,
                origin_agent_step_id=step.agent_step_id,
                payload=decision.proposal,
            )
        completed = await self._repository.update_run_state(
            run.agent_run_id,
            expected_row_version=run.row_version,
            status="completed",
            termination_reason="proposal_created",
            produced_proposal_revision_id=revision.proposal_revision_id,
        )
        _log("agent_proposal_materialized", context, completed)
        return completed

    async def _require_run(self, agent_run_id: UUID) -> AgentRunDetails:
        run = await self._repository.get_run(agent_run_id)
        if run is None:
            raise AgentRuntimeError("agent_run_not_found")
        return run


def _is_pending_proposal(step: AgentStepDetails, run: AgentRunDetails) -> bool:
    return (
        step.outcome == "succeeded"
        and step.decision_kind == "propose"
        and run.produced_proposal_revision_id is None
    )


def _budget_reason(run: AgentRunDetails) -> str | None:
    if run.next_step_ordinal - 1 >= run.max_steps:
        return "max_steps"
    if run.total_model_calls >= run.max_model_calls:
        return "max_model_calls"
    if run.total_tokens >= run.max_tokens:
        return "max_tokens"
    if run.started_at is not None and _remaining_wall_clock_seconds(run) <= 0:
        return "max_wall_clock_seconds"
    return None


def _remaining_wall_clock_seconds(run: AgentRunDetails) -> float:
    if run.started_at is None:
        return float(run.max_wall_clock_seconds)
    deadline = run.started_at + timedelta(seconds=run.max_wall_clock_seconds)
    return (deadline - datetime.now(UTC)).total_seconds()


def _token_count(usage: Mapping[str, object]) -> int:
    total = usage.get("total_tokens")
    if type(total) is int and total >= 0:
        return total
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return (prompt if type(prompt) is int and prompt >= 0 else 0) + (
        completion if type(completion) is int and completion >= 0 else 0
    )


def _serialization_fallback(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"unsupported decision value: {type(value).__name__}")


def _require_text(value: str, field: str, maximum: int) -> None:
    if not value.strip():
        raise AgentRuntimeError(f"{field}_blank")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise AgentRuntimeError(f"{field}_invalid_utf8") from exc
    if size > maximum:
        raise AgentRuntimeError(f"{field}_too_large")


def _log(
    event: str,
    context: CommandContext,
    run: AgentRunDetails,
    reason: str | None = None,
) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "request_id": context.interaction_correlation_id,
            "project_id": str(run.project_id),
            "agent_run_id": str(run.agent_run_id),
            "status": run.status,
            "reason_code": reason,
        },
    )
