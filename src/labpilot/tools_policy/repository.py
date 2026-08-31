from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import select

from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.tools_policy.models import ToolApprovalRow, ToolCallRow
from labpilot.tools_policy.types import (
    ToolApprovalDecision,
    ToolApprovalDetails,
    ToolCallDetails,
    ToolCallStatus,
    ToolRisk,
    ToolSideEffect,
)


class ToolRepository(Protocol):
    async def add_call(self, call: ToolCallDetails) -> ToolCallDetails: ...

    async def get_call(self, tool_call_id: UUID) -> ToolCallDetails | None: ...

    async def add_approval(self, approval: ToolApprovalDetails) -> ToolApprovalDetails: ...

    async def get_approval(self, tool_call_id: UUID) -> ToolApprovalDetails | None: ...

    async def finalize_call(
        self,
        tool_call_id: UUID,
        *,
        status: ToolCallStatus,
        result: dict[str, object] | None,
        result_hash: str | None,
        error_code: str | None,
        completed_at: datetime,
    ) -> ToolCallDetails: ...


class SqlToolRepository:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory

    async def add_call(self, call: ToolCallDetails) -> ToolCallDetails:
        row = ToolCallRow(
            tool_call_id=call.tool_call_id,
            project_id=call.project_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            risk_level=call.risk,
            side_effect_class=call.side_effect,
            arguments_json=dict(call.arguments),
            arguments_hash=call.arguments_hash,
            status=call.status,
            result_json=None,
            result_hash=None,
            error_code=None,
            requested_at=call.requested_at,
            completed_at=None,
        )
        async with UnitOfWork(self._session_context_factory) as uow:
            uow.session.add(row)
            await uow.session.flush()
            await uow.commit()
        return _call_details(row)

    async def get_call(self, tool_call_id: UUID) -> ToolCallDetails | None:
        async with self._session_context_factory() as session:
            row = await session.get(ToolCallRow, tool_call_id)
        return None if row is None else _call_details(row)

    async def add_approval(self, approval: ToolApprovalDetails) -> ToolApprovalDetails:
        row = ToolApprovalRow(
            tool_approval_id=approval.tool_approval_id,
            tool_call_id=approval.tool_call_id,
            decision=approval.decision,
            arguments_hash=approval.arguments_hash,
            actor_type=approval.actor_type,
            actor_id=approval.actor_id,
            decided_at=approval.decided_at,
            expires_at=approval.expires_at,
        )
        async with UnitOfWork(self._session_context_factory) as uow:
            uow.session.add(row)
            await uow.session.flush()
            await uow.commit()
        return _approval_details(row)

    async def get_approval(self, tool_call_id: UUID) -> ToolApprovalDetails | None:
        async with self._session_context_factory() as session:
            result = await session.execute(
                select(ToolApprovalRow).where(ToolApprovalRow.tool_call_id == tool_call_id)
            )
            row = result.scalar_one_or_none()
        return None if row is None else _approval_details(row)

    async def finalize_call(
        self,
        tool_call_id: UUID,
        *,
        status: ToolCallStatus,
        result: dict[str, object] | None,
        result_hash: str | None,
        error_code: str | None,
        completed_at: datetime,
    ) -> ToolCallDetails:
        async with UnitOfWork(self._session_context_factory) as uow:
            selected = await uow.session.execute(
                select(ToolCallRow)
                .where(ToolCallRow.tool_call_id == tool_call_id)
                .with_for_update()
            )
            row = selected.scalar_one_or_none()
            if row is None:
                raise LookupError("tool_call_not_found")
            row.status = status
            row.result_json = result
            row.result_hash = result_hash
            row.error_code = error_code
            row.completed_at = completed_at
            await uow.session.flush()
            details = _call_details(row)
            await uow.commit()
        return details


def _call_details(row: ToolCallRow) -> ToolCallDetails:
    return ToolCallDetails(
        tool_call_id=row.tool_call_id,
        project_id=row.project_id,
        tool_name=row.tool_name,
        tool_version=row.tool_version,
        risk=cast(ToolRisk, row.risk_level),
        side_effect=cast(ToolSideEffect, row.side_effect_class),
        arguments=dict(row.arguments_json),
        arguments_hash=row.arguments_hash,
        status=cast(ToolCallStatus, row.status),
        result=None if row.result_json is None else dict(row.result_json),
        result_hash=row.result_hash,
        error_code=row.error_code,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
    )


def _approval_details(row: ToolApprovalRow) -> ToolApprovalDetails:
    return ToolApprovalDetails(
        tool_approval_id=row.tool_approval_id,
        tool_call_id=row.tool_call_id,
        decision=cast(ToolApprovalDecision, row.decision),
        arguments_hash=row.arguments_hash,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        decided_at=row.decided_at,
        expires_at=row.expires_at,
    )
