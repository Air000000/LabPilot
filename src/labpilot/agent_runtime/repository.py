from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import select

from labpilot.agent_runtime.models import AgentRunRow, AgentStepRow
from labpilot.agent_runtime.types import (
    AgentRunDetails,
    AgentRunStatus,
    AgentRuntimeError,
    AgentStepDetails,
    AgentStepKind,
    AgentStepOutcome,
)
from labpilot.db.uow import SessionContextFactory, UnitOfWork


class AgentRepository(Protocol):
    async def add_run(self, run: AgentRunDetails) -> AgentRunDetails: ...

    async def get_run(self, agent_run_id: UUID) -> AgentRunDetails | None: ...

    async def list_steps(self, agent_run_id: UUID) -> tuple[AgentStepDetails, ...]: ...

    async def reserve_model_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        step: AgentStepDetails,
    ) -> AgentRunDetails: ...

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
        run_status: AgentRunStatus,
        termination_reason: str | None,
        completed_at: datetime,
    ) -> AgentRunDetails: ...

    async def link_tool_call(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        agent_step_id: UUID,
        tool_call_id: UUID,
    ) -> AgentRunDetails: ...

    async def add_human_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        step: AgentStepDetails,
    ) -> AgentRunDetails: ...

    async def update_run_state(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        status: AgentRunStatus,
        termination_reason: str | None,
        produced_proposal_revision_id: UUID | None = None,
    ) -> AgentRunDetails: ...


class SqlAgentRepository:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory

    async def add_run(self, run: AgentRunDetails) -> AgentRunDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            details = await self.add_run_in_uow(uow, run)
            await uow.commit()
        return details

    async def add_run_in_uow(self, uow: UnitOfWork, run: AgentRunDetails) -> AgentRunDetails:
        row = _run_row(run)
        uow.session.add(row)
        await uow.session.flush()
        return _run_details(row)

    async def get_run(self, agent_run_id: UUID) -> AgentRunDetails | None:
        async with self._session_context_factory() as session:
            row = await session.get(AgentRunRow, agent_run_id)
        return None if row is None else _run_details(row)

    async def list_steps(self, agent_run_id: UUID) -> tuple[AgentStepDetails, ...]:
        async with self._session_context_factory() as session:
            result = await session.execute(
                select(AgentStepRow)
                .where(AgentStepRow.agent_run_id == agent_run_id)
                .order_by(AgentStepRow.ordinal)
            )
            rows = result.scalars().all()
        return tuple(_step_details(row) for row in rows)

    async def reserve_model_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        step: AgentStepDetails,
    ) -> AgentRunDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            run = await _locked_run(uow, agent_run_id, expected_row_version)
            uow.session.add(_step_row(step))
            run.status = "running"
            run.started_at = run.started_at or step.started_at
            run.next_step_ordinal += 1
            run.row_version += 1
            await uow.session.flush()
            details = _run_details(run)
            await uow.commit()
        return details

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
        run_status: AgentRunStatus,
        termination_reason: str | None,
        completed_at: datetime,
    ) -> AgentRunDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            run = await _locked_run(uow, agent_run_id, expected_row_version)
            selected = await uow.session.execute(
                select(AgentStepRow)
                .where(
                    AgentStepRow.agent_step_id == agent_step_id,
                    AgentStepRow.agent_run_id == agent_run_id,
                )
                .with_for_update()
            )
            step = selected.scalar_one_or_none()
            if step is None:
                raise AgentRuntimeError("agent_step_not_found")
            step.outcome = outcome
            step.completed_at = completed_at
            step.token_usage_json = dict(token_usage)
            step.decision_kind = decision_kind
            step.decision_json = None if decision_json is None else dict(decision_json)
            run.status = run_status
            run.termination_reason = termination_reason
            run.total_model_calls += 1
            run.total_tokens += total_tokens
            run.completed_at = (
                completed_at if run_status in {"completed", "budget_exhausted", "failed"} else None
            )
            run.row_version += 1
            await uow.session.flush()
            details = _run_details(run)
            await uow.commit()
        return details

    async def link_tool_call(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        agent_step_id: UUID,
        tool_call_id: UUID,
    ) -> AgentRunDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            run = await _locked_run(uow, agent_run_id, expected_row_version)
            selected = await uow.session.execute(
                select(AgentStepRow)
                .where(
                    AgentStepRow.agent_step_id == agent_step_id,
                    AgentStepRow.agent_run_id == agent_run_id,
                )
                .with_for_update()
            )
            step = selected.scalar_one_or_none()
            if step is None:
                raise AgentRuntimeError("agent_step_not_found")
            step.tool_call_id = tool_call_id
            run.row_version += 1
            await uow.session.flush()
            details = _run_details(run)
            await uow.commit()
        return details

    async def add_human_step(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        step: AgentStepDetails,
    ) -> AgentRunDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            run = await _locked_run(uow, agent_run_id, expected_row_version)
            if run.next_step_ordinal > run.max_steps:
                run.status = "budget_exhausted"
                run.termination_reason = "max_steps"
                run.completed_at = step.started_at
                run.row_version += 1
                await uow.session.flush()
                details = _run_details(run)
                await uow.commit()
                return details
            uow.session.add(_step_row(step))
            run.status = "ready"
            run.next_step_ordinal += 1
            run.row_version += 1
            await uow.session.flush()
            details = _run_details(run)
            await uow.commit()
        return details

    async def update_run_state(
        self,
        agent_run_id: UUID,
        *,
        expected_row_version: int,
        status: AgentRunStatus,
        termination_reason: str | None,
        produced_proposal_revision_id: UUID | None = None,
    ) -> AgentRunDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            run = await _locked_run(uow, agent_run_id, expected_row_version)
            run.status = status
            run.termination_reason = termination_reason
            if produced_proposal_revision_id is not None:
                run.produced_proposal_revision_id = produced_proposal_revision_id
            run.completed_at = (
                datetime.now(run.created_at.tzinfo)
                if status in {"completed", "budget_exhausted", "failed"}
                else None
            )
            run.row_version += 1
            await uow.session.flush()
            details = _run_details(run)
            await uow.commit()
        return details


async def _locked_run(
    uow: UnitOfWork, agent_run_id: UUID, expected_row_version: int
) -> AgentRunRow:
    selected = await uow.session.execute(
        select(AgentRunRow).where(AgentRunRow.agent_run_id == agent_run_id).with_for_update()
    )
    run = selected.scalar_one_or_none()
    if run is None:
        raise AgentRuntimeError("agent_run_not_found")
    if run.row_version != expected_row_version:
        raise AgentRuntimeError("row_version_conflict")
    return run


def _run_row(run: AgentRunDetails) -> AgentRunRow:
    return AgentRunRow(
        agent_run_id=run.agent_run_id,
        project_id=run.project_id,
        repository_snapshot_id=run.repository_snapshot_id,
        goal=run.goal,
        status=run.status,
        termination_reason=run.termination_reason,
        egress_policy=run.egress_policy,
        provider_name=run.provider_name,
        model_name=run.model_name,
        max_steps=run.max_steps,
        max_model_calls=run.max_model_calls,
        max_wall_clock_seconds=run.max_wall_clock_seconds,
        max_tokens=run.max_tokens,
        total_model_calls=run.total_model_calls,
        total_tokens=run.total_tokens,
        next_step_ordinal=run.next_step_ordinal,
        produced_proposal_revision_id=run.produced_proposal_revision_id,
        source_batch_summary_revision_id=run.source_batch_summary_revision_id,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        row_version=run.row_version,
    )


def _step_row(step: AgentStepDetails) -> AgentStepRow:
    return AgentStepRow(
        agent_step_id=step.agent_step_id,
        agent_run_id=step.agent_run_id,
        ordinal=step.ordinal,
        step_kind=step.step_kind,
        tool_call_id=step.tool_call_id,
        provider_name=step.provider_name,
        model_name=step.model_name,
        canonical_request_hash=step.canonical_request_hash,
        bound_toolset_hash=step.bound_toolset_hash,
        semantic_parameters_json=(
            None if step.semantic_parameters is None else dict(step.semantic_parameters)
        ),
        output_schema_hash=step.output_schema_hash,
        started_at=step.started_at,
        completed_at=step.completed_at,
        outcome=step.outcome,
        token_usage_json=(None if step.token_usage is None else dict(step.token_usage)),
        decision_kind=step.decision_kind,
        decision_json=(None if step.decision_json is None else dict(step.decision_json)),
        human_input_text=step.human_input_text,
    )


def _run_details(row: AgentRunRow) -> AgentRunDetails:
    return AgentRunDetails(
        agent_run_id=row.agent_run_id,
        project_id=row.project_id,
        repository_snapshot_id=row.repository_snapshot_id,
        goal=row.goal,
        status=cast(AgentRunStatus, row.status),
        termination_reason=row.termination_reason,
        egress_policy=cast(Literal["local_only", "remote_provider_allowed"], row.egress_policy),
        provider_name=row.provider_name,
        model_name=row.model_name,
        max_steps=row.max_steps,
        max_model_calls=row.max_model_calls,
        max_wall_clock_seconds=row.max_wall_clock_seconds,
        max_tokens=row.max_tokens,
        total_model_calls=row.total_model_calls,
        total_tokens=row.total_tokens,
        next_step_ordinal=row.next_step_ordinal,
        produced_proposal_revision_id=row.produced_proposal_revision_id,
        created_at=row.created_at,
        source_batch_summary_revision_id=row.source_batch_summary_revision_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        row_version=row.row_version,
    )


def _step_details(row: AgentStepRow) -> AgentStepDetails:
    return AgentStepDetails(
        agent_step_id=row.agent_step_id,
        agent_run_id=row.agent_run_id,
        ordinal=row.ordinal,
        step_kind=cast(AgentStepKind, row.step_kind),
        tool_call_id=row.tool_call_id,
        provider_name=row.provider_name,
        model_name=row.model_name,
        canonical_request_hash=row.canonical_request_hash,
        bound_toolset_hash=row.bound_toolset_hash,
        semantic_parameters=(
            None if row.semantic_parameters_json is None else dict(row.semantic_parameters_json)
        ),
        output_schema_hash=row.output_schema_hash,
        started_at=row.started_at,
        completed_at=row.completed_at,
        outcome=cast(AgentStepOutcome, row.outcome),
        token_usage=(None if row.token_usage_json is None else dict(row.token_usage_json)),
        decision_kind=row.decision_kind,
        decision_json=(None if row.decision_json is None else dict(row.decision_json)),
        human_input_text=row.human_input_text,
    )
