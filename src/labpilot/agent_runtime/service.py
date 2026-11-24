from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from itertools import product
from math import prod
from typing import Literal, Protocol, cast, get_args
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from pydantic_core.core_schema import ErrorType

from labpilot.agent_runtime.context_compiler import (
    ContextCompilationError,
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
    ResultContext,
    ResultContextSource,
)
from labpilot.agent_runtime.result_history import validate_history_binding
from labpilot.agent_runtime.source_patch import (
    SourcePatchContext,
    SourcePatchLoader,
)
from labpilot.agent_runtime.types import (
    TEXT_ERROR_CODES,
    AgentBudget,
    AgentDecision,
    AgentRunDetails,
    AgentRunStatus,
    AgentRuntimeError,
    AgentRunTraceDetails,
    AgentStepDetails,
    HumanInputDecision,
    OutputFeedback,
    OutputValidationError,
    ProposeDecision,
    SourcePatchAgentDecision,
    SourcePatchDecision,
    StopDecision,
    ToolCallDecision,
    feedback_json,
    read_feedback,
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


AgentToolServiceFactory = Callable[[AgentRunDetails], AgentToolService]


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
        source_patch_loader: SourcePatchLoader | None = None,
    ) -> None:
        self._source_patch_loader = source_patch_loader
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
        source_patch: bool = False,
    ) -> AgentRunDetails:
        _require_text(goal, "goal", 4000)
        descriptor = await self._repository_source.describe(repository_snapshot_id)
        if descriptor.repository_snapshot_id != repository_snapshot_id:
            raise AgentRuntimeError("snapshot_binding_mismatch")
        if descriptor.project_id != project_id:
            raise AgentRuntimeError("snapshot_project_mismatch")
        patch_context = await self._prepare_patch_context(
            context, project_id, repository_snapshot_id, source_patch
        )
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
            source_patch_context=patch_context,
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
        source_adoption_id: UUID | None = None,
        repository_snapshot_id: UUID,
        goal: str,
        budget: AgentBudget,
        source_patch: bool = False,
    ) -> AgentRunDetails:
        _require_text(goal, "goal", 4000)
        if source_adoption_id is not None and (
            context.actor.actor_type not in {"user", "local_user"} or context.actor.actor_id is None
        ):
            raise AgentRuntimeError("source_adoption_user_required")
        if self._follow_up_run_store is None or self._result_context_source is None:
            raise AgentRuntimeError("follow_up_runtime_unconfigured")
        if source_patch and source_adoption_id is not None:
            raise AgentRuntimeError("source_patch_adoption_conflict")
        patch_context = await self._prepare_patch_context(
            context, project_id, repository_snapshot_id, source_patch
        )
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
            source_patch_context=patch_context,
            source_batch_summary_revision_id=source_batch_summary_revision_id,
            source_adoption_id=source_adoption_id,
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
        if steps and _is_pending_proposal(steps[-1], run) and run.source_patch_context is not None:
            return await self._repository.update_run_state(
                agent_run_id,
                expected_row_version=run.row_version,
                status="failed",
                termination_reason="source_patch_context_invalid",
            )
        if steps and _is_pending_proposal(steps[-1], run):
            return await self._materialize_proposal(context, run, steps[-1])
        try:
            assert_provider_egress(run.egress_policy, self._provider)
        except ProviderPolicyError as exc:
            raise AgentRuntimeError(exc.reason_code) from None
        result_context = None
        if run.source_batch_summary_revision_id is not None:
            assert self._result_context_source is not None
            result_context = await self._result_context_source.load(
                run.source_batch_summary_revision_id
            )
        history_context = validate_history_binding(run, result_context)
        trial_facts = (
            history_context.trial_facts.source
            if history_context is not None and history_context.trial_facts is not None
            else None
        )
        tools = self._tool_service_factory(run)

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
            source_binding = None
            if run.source_adoption_id is not None:
                assert self._result_context_source is not None
                try:
                    source_binding = await self._result_context_source.load_source_binding(
                        run, for_model=True
                    )
                    if source_binding.source_adoption_id != run.source_adoption_id:
                        raise AgentRuntimeError("source_adoption_binding_invalid")
                except AgentRuntimeError as exc:
                    return await self._repository.update_run_state(
                        run.agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason=exc.reason_code,
                    )
            patch_input = None
            if run.source_patch_context is not None:
                try:
                    pinned = SourcePatchContext.model_validate(dict(run.source_patch_context))
                    if self._source_patch_loader is None or run.source_adoption_id is not None:
                        raise AgentRuntimeError("source_patch_context_invalid")
                    patch_input = await self._source_patch_loader(
                        run.project_id, run.repository_snapshot_id, pinned
                    )
                    if patch_input.context != pinned:
                        raise AgentRuntimeError("source_patch_context_invalid")
                except (AgentRuntimeError, ValueError, TypeError) as exc:
                    return await self._repository.update_run_state(
                        agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason=exc.reason_code
                        if isinstance(exc, AgentRuntimeError)
                        else "source_patch_context_invalid",
                    )
            descriptor = await self._repository_source.describe(run.repository_snapshot_id)
            decision_adapter = (
                TypeAdapter(SourcePatchAgentDecision)
                if patch_input is not None
                else self._decision_adapter
            )
            prior_results, human_inputs = await self._history(agent_run_id, tools)
            steps = await self._repository.list_steps(agent_run_id)
            try:
                source = _correction_source(steps)
            except AgentRuntimeError as exc:
                return await self._repository.update_run_state(
                    agent_run_id,
                    expected_row_version=run.row_version,
                    status="failed",
                    termination_reason=exc.reason_code,
                )
            parameters = (
                {"max_tokens": min(8192, run.max_tokens - run.total_tokens)}
                if self._provider.provider_name == "deepseek_chat"
                else {}
            )
            if source is not None and (
                source.provider_name != run.provider_name
                or source.model_name != run.model_name
                or self._provider.provider_name != run.provider_name
                or self._model_name != run.model_name
                or getattr(self._provider, "model_name", self._model_name) != run.model_name
            ):
                return await self._repository.update_run_state(
                    agent_run_id,
                    expected_row_version=run.row_version,
                    status="failed",
                    termination_reason="correction_context_changed",
                )
            try:
                compiled = self._compiler.compile(
                    provider_name=run.provider_name,
                    model=run.model_name,
                    goal=run.goal,
                    snapshot=descriptor,
                    definitions=tools.definitions,
                    prior_tool_results=prior_results,
                    prior_human_inputs=human_inputs,
                    result_context=result_context,
                    trial_facts=trial_facts,
                    source_binding=source_binding,
                    source_patch=patch_input,
                    semantic_parameters=parameters
                    if source is None
                    else source.semantic_parameters,
                )
                if source is not None:
                    if compiled.canonical_request_hash != source.canonical_request_hash:
                        raise ContextCompilationError("correction_context_changed")
                    source_feedback = read_feedback(source.output_feedback)
                    assert source_feedback is not None
                    compiled = self._compiler.compile(
                        provider_name=run.provider_name,
                        model=run.model_name,
                        goal=run.goal,
                        snapshot=descriptor,
                        definitions=tools.definitions,
                        prior_tool_results=prior_results,
                        prior_human_inputs=human_inputs,
                        result_context=result_context,
                        trial_facts=trial_facts,
                        source_binding=source_binding,
                        source_patch=patch_input,
                        semantic_parameters=parameters,
                        output_feedback=feedback_json(source_feedback),
                    )
            except ContextCompilationError:
                if source is None and patch_input is not None:
                    return await self._repository.update_run_state(
                        agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason="source_patch_context_invalid",
                    )
                if source is None and source_binding is not None:
                    return await self._repository.update_run_state(
                        agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason="source_adoption_context_invalid",
                    )
                if source is None:
                    raise
                return await self._repository.update_run_state(
                    agent_run_id,
                    expected_row_version=run.row_version,
                    status="failed",
                    termination_reason="correction_context_changed",
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
                output_feedback=(
                    None
                    if source is None
                    else feedback_json(
                        OutputFeedback(
                            category="correction",
                            code="correction_reserved",
                            error_count=0,
                            correction_of_step_id=source.agent_step_id,
                        )
                    )
                ),
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
                usage = exc.usage if isinstance(exc, ProviderError) else {}
                reason = exc.reason_code if isinstance(exc, ProviderError) else "provider_failure"
                feedback = None
                if isinstance(exc, ProviderError) and reason in {
                    "provider_invalid_json",
                    "provider_output_not_object",
                }:
                    feedback = OutputFeedback(
                        category="provider_invalid_output",
                        code=cast(
                            Literal["provider_invalid_json", "provider_output_not_object"], reason
                        ),
                        error_count=0,
                        correction_allowed=exc.response_completed and _known_usage(usage),
                    )
                run = await self._record_output_failure(context, run, step, usage, reason, feedback)
                if run.status == "ready":
                    continue
                return run
            feedback = None
            try:
                decision = decision_adapter.validate_python(response.payload)
            except ValidationError as exc:
                errors = exc.errors(include_input=False, include_context=False, include_url=False)
                decision = None
                if (
                    response.response_completed
                    and _known_usage(response.usage)
                    and _summary_only(errors)
                ):
                    try:
                        decision = decision_adapter.validate_python(
                            {**response.payload, "summary": None}
                        )
                    except ValidationError:
                        pass
                safe_errors = tuple(
                    OutputValidationError(
                        loc=tuple(cast(list[str], item["loc"])),
                        type=cast(str, item["type"]),
                    )
                    for item in _safe_validation_errors(exc)
                )
                feedback = OutputFeedback(
                    category="summary_omitted" if decision is not None else "invalid_decision",
                    code="summary_omitted" if decision is not None else "invalid_agent_decision",
                    error_count=exc.error_count(),
                    errors=safe_errors,
                    correction_allowed=(
                        decision is None
                        and response.response_completed
                        and _known_usage(response.usage)
                        and len(errors) <= 8
                        and all(_correctable_error(item) for item in errors)
                    ),
                )
                if decision is None:
                    logger.warning(
                        "agent_decision_validation_failed run=%s step=%s error_count=%s errors=%s",
                        run.agent_run_id,
                        step.agent_step_id,
                        exc.error_count(),
                        _safe_validation_errors(exc),
                        extra={
                            "event": "agent_decision_validation_failed",
                            "request_id": context.interaction_correlation_id,
                            "project_id": str(run.project_id),
                            "agent_run_id": str(run.agent_run_id),
                            "agent_step_id": str(step.agent_step_id),
                            "reason_code": "invalid_agent_decision",
                            "validation_error_count": exc.error_count(),
                            "validation_errors": _safe_validation_errors(exc),
                        },
                    )
                    run = await self._record_output_failure(
                        context, run, step, response.usage, "invalid_agent_decision", feedback
                    )
                    if run.status == "ready":
                        continue
                    return run
            assert decision is not None
            decision_json = cast(
                dict[str, object],
                decision.model_dump(
                    mode="json",
                    warnings="none",
                    fallback=_serialization_fallback,
                ),
            )
            total_tokens = _token_count(response.usage)
            final_status: AgentRunStatus = "running"
            final_termination_reason: str | None = None
            budget = _response_budget_reason(run, response.usage)
            if budget is not None:
                final_status = "budget_exhausted"
                final_termination_reason = budget
            elif isinstance(decision, HumanInputDecision):
                final_status = "awaiting_human"
            elif isinstance(decision, SourcePatchDecision):
                final_status = "completed"
                final_termination_reason = "source_patch_drafted"
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
                output_feedback=None if feedback is None else feedback_json(feedback),
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
            assert isinstance(decision, StopDecision | SourcePatchDecision)
            return run

    async def _prepare_patch_context(
        self, context: CommandContext, project_id: UUID, snapshot_id: UUID, enabled: bool
    ) -> Mapping[str, object] | None:
        if type(enabled) is not bool:
            raise AgentRuntimeError("source_patch_context_invalid")
        if not enabled:
            return None
        if context.actor.actor_type not in {"user", "local_user"} or not context.actor.actor_id:
            raise AgentRuntimeError("source_patch_user_required")
        if self._source_patch_loader is None:
            raise AgentRuntimeError("source_patch_unconfigured")
        evidence = await self._source_patch_loader(project_id, snapshot_id, None)
        return evidence.context.model_dump(mode="json")

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
        binding = None
        if run.source_adoption_id is not None:
            if self._result_context_source is None:
                raise AgentRuntimeError("follow_up_runtime_unconfigured")
            binding = await self._result_context_source.load_source_binding(run, for_model=False)
        return AgentRunTraceDetails(run=run, steps=steps, source_binding=binding)

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

    async def _record_output_failure(
        self,
        context: CommandContext,
        run: AgentRunDetails,
        step: AgentStepDetails,
        usage: Mapping[str, object],
        reason: str,
        feedback: OutputFeedback | None,
    ) -> AgentRunDetails:
        budget = _response_budget_reason(run, usage)
        if budget is None and feedback is not None:
            if run.next_step_ordinal - 1 >= run.max_steps:
                budget = "max_steps"
            elif run.total_model_calls + 1 >= run.max_model_calls:
                budget = "max_model_calls"
        steps = await self._repository.list_steps(run.agent_run_id)
        consumed = any(
            item is not None and item.correction_of_step_id is not None
            for item in (read_feedback(step.output_feedback) for step in steps)
        )
        retry = (
            feedback is not None and feedback.correction_allowed and not consumed and budget is None
        )
        result = await self._repository.finalize_model_step(
            run.agent_run_id,
            expected_row_version=run.row_version,
            agent_step_id=step.agent_step_id,
            outcome="failed",
            token_usage=dict(usage),
            decision_kind=None,
            decision_json=None,
            total_tokens=_token_count(usage),
            run_status="budget_exhausted" if budget else "ready" if retry else "failed",
            termination_reason=budget or (None if retry else reason),
            completed_at=datetime.now(UTC),
            output_feedback=None if feedback is None else feedback_json(feedback),
        )
        _log("agent_model_step_failed", context, result, reason)
        return result

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
            binding = None
            if run.source_adoption_id is not None:
                try:
                    binding = await self._result_context_source.load_source_binding(
                        run, for_model=False
                    )
                except AgentRuntimeError as exc:
                    return await self._repository.update_run_state(
                        run.agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason=exc.reason_code,
                    )
                if (
                    binding.source_adoption_id != run.source_adoption_id
                    or binding.proposal_target.repository_snapshot_id
                    != decision.proposal.repository_snapshot_id
                    or binding.proposal_target.repository_snapshot_identity
                    != decision.proposal.repository_snapshot_identity
                ):
                    return await self._repository.update_run_state(
                        run.agent_run_id,
                        expected_row_version=run.row_version,
                        status="failed",
                        termination_reason="follow_up_proposal_snapshot_mismatch",
                    )
            if binding is None and _repeats_only_invalid_trials(decision.proposal, result_context):
                return await self._repository.update_run_state(
                    run.agent_run_id,
                    expected_row_version=run.row_version,
                    status="failed",
                    termination_reason="follow_up_repeats_invalid_trials",
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


def _known_usage(usage: Mapping[str, object]) -> bool:
    for key in ("total_tokens", "prompt_tokens", "completion_tokens"):
        if key in usage and (type(usage[key]) is not int or cast(int, usage[key]) < 0):
            return False
    components = "prompt_tokens" in usage and "completion_tokens" in usage
    if components and "total_tokens" in usage:
        if usage["total_tokens"] != cast(int, usage["prompt_tokens"]) + cast(
            int, usage["completion_tokens"]
        ):
            return False
    return "total_tokens" in usage or components


def _summary_error(item: Mapping[str, object]) -> bool:
    loc = tuple(cast(tuple[object, ...], item["loc"]))
    return (
        len(loc) == 2
        and loc[0] in {"stop", "tool_call", "propose", "request_human_input", "source_patch"}
        and loc[1] == "summary"
        and item["type"]
        in {"string_type", "string_too_long", "text_invalid_utf8", "text_too_large"}
    )


def _summary_only(errors: Sequence[Mapping[str, object]]) -> bool:
    return bool(errors) and all(_summary_error(item) for item in errors)


def _correctable_error(item: Mapping[str, object]) -> bool:
    loc = tuple(cast(tuple[object, ...], item["loc"]))
    code = item["type"]
    if not loc:
        return code in {"union_tag_not_found", "union_tag_invalid"}
    if _summary_error(item):
        return True
    if loc in {
        ("tool_call", "tool_name"),
        ("request_human_input", "question"),
        ("source_patch", "patch"),
    }:
        return code in {"missing", "string_type", *TEXT_ERROR_CODES}
    if loc == ("propose", "proposal"):
        return code in {"missing", "dataclass_type"}
    if loc == ("tool_call", "arguments") or loc == ("propose", "proposal", "parameter_matrix"):
        return code in {"missing", "dict_type"}
    if loc == ("stop", "reason_code"):
        return code in {"missing", "string_type", "string_pattern_mismatch"}
    if loc == ("propose", "proposal", "seeds"):
        return code in {"missing", "tuple_type"}
    if len(loc) == 4 and loc[:3] == ("propose", "proposal", "seeds") and type(loc[3]) is int:
        return code in {"int_type", "int_parsing"}
    if loc in {("propose", "proposal", "timeout_seconds"), ("propose", "proposal", "max_retries")}:
        return code in {"missing", "int_type", "int_parsing"}
    return False


def _correction_source(steps: tuple[AgentStepDetails, ...]) -> AgentStepDetails | None:
    feedbacks = [read_feedback(step.output_feedback) for step in steps]
    if not steps or steps[-1].outcome != "failed":
        return None
    source, feedback = steps[-1], feedbacks[-1]
    if (
        feedback is None
        or not feedback.correction_allowed
        or feedback.category not in {"invalid_decision", "provider_invalid_output"}
        or source.step_kind != "model_call"
        or any(item and item.correction_of_step_id is not None for item in feedbacks)
    ):
        raise AgentRuntimeError("output_feedback_invalid")
    return source


def _is_pending_proposal(step: AgentStepDetails, run: AgentRunDetails) -> bool:
    return (
        step.outcome == "succeeded"
        and step.decision_kind == "propose"
        and run.produced_proposal_revision_id is None
    )


def _repeats_only_invalid_trials(proposal: ProposalPayload, context: ResultContext) -> bool:
    invalid_ids = {
        item["trial_id"]
        for item in context.excluded_results
        if item.get("exclusion_code") == "VALIDATION_INVALID"
    }
    invalid = [trial for trial in context.trials if str(trial.trial_id) in invalid_ids]
    names = tuple(proposal.parameter_matrix)
    # Repeated matrix values do not introduce new experimental conditions.
    values = tuple(tuple(dict.fromkeys(items)) for items in proposal.parameter_matrix.values())
    if not invalid or len(proposal.seeds) * prod(map(len, values)) > len(invalid):
        return False
    return all(
        any(
            trial.seed == seed
            and trial.resolved_parameters == dict(zip(names, combination, strict=True))
            for trial in invalid
        )
        for seed in proposal.seeds
        for combination in product(*values)
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


def _response_budget_reason(run: AgentRunDetails, usage: Mapping[str, object]) -> str | None:
    if _remaining_wall_clock_seconds(run) <= 0:
        return "max_wall_clock_seconds"
    if run.total_tokens + _token_count(usage) >= run.max_tokens:
        return "max_tokens"
    return None


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


def _safe_validation_errors(error: ValidationError) -> list[dict[str, object]]:
    # Only code-defined names are safe; model-supplied keys and custom errors may contain secrets.
    schema = TypeAdapter(AgentDecision).json_schema()
    fields = {"tool_call", "request_human_input", "propose", "stop", "source_patch", "patch"}
    fields.update({"reasoning", "rationale", "chain_of_thought"})
    for definition in schema.get("$defs", {}).values():
        fields.update(definition.get("properties", {}))
    error_types = (*get_args(ErrorType), *TEXT_ERROR_CODES)
    # ponytail: bound diagnostic output only; raw response and exception context remain unrecorded.
    return [
        {
            "loc": [
                "[]" if isinstance(part, int) else part if part in fields else "*"
                for part in detail["loc"][:8]
            ],
            "type": detail["type"] if detail["type"] in error_types else "other",
        }
        for detail in error.errors(include_input=False, include_context=False, include_url=False)[
            :8
        ]
    ]


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
