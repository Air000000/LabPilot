from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast
from uuid import UUID, uuid4

from labpilot.common.commands import CommandContext
from labpilot.tools_policy.repository import ToolRepository
from labpilot.tools_policy.types import (
    ToolApprovalDecision,
    ToolApprovalDetails,
    ToolCallDetails,
    ToolDefinition,
    arguments_hash,
    tool_result_hash,
)

logger = logging.getLogger(__name__)


class ToolPolicyError(ValueError):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ToolApplicationService:
    def __init__(
        self,
        definitions: tuple[ToolDefinition, ...],
        repository: ToolRepository,
    ) -> None:
        self.definitions = definitions
        self._definitions = {definition.name: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("duplicate Tool definition name")
        self._repository = repository

    async def request_call(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        definition_name: str,
        arguments: Mapping[str, object],
    ) -> ToolCallDetails:
        definition = self._definitions.get(definition_name)
        if definition is None:
            raise ToolPolicyError("unknown_tool")
        try:
            normalized = _validate_arguments(arguments, definition.input_schema)
            digest = arguments_hash(normalized)
        except (TypeError, ValueError) as exc:
            raise ToolPolicyError("invalid_arguments") from exc
        call = ToolCallDetails(
            tool_call_id=uuid4(),
            project_id=project_id,
            tool_name=definition.name,
            tool_version=definition.version,
            risk=definition.risk,
            side_effect=definition.side_effect,
            arguments=MappingProxyType(normalized),
            arguments_hash=digest,
            status="requested",
            result=None,
            result_hash=None,
            error_code=None,
            requested_at=datetime.now(UTC),
            completed_at=None,
        )
        stored = await self._repository.add_call(call)
        _log("tool_call_requested", context, stored)
        return stored

    async def approve_call(
        self,
        *,
        context: CommandContext,
        tool_call_id: UUID,
        exact_arguments_hash: str,
    ) -> ToolApprovalDetails:
        return await self._decide(
            context=context,
            tool_call_id=tool_call_id,
            exact_arguments_hash=exact_arguments_hash,
            decision="approved",
        )

    async def reject_call(
        self,
        *,
        context: CommandContext,
        tool_call_id: UUID,
        exact_arguments_hash: str,
    ) -> ToolApprovalDetails:
        approval = await self._decide(
            context=context,
            tool_call_id=tool_call_id,
            exact_arguments_hash=exact_arguments_hash,
            decision="rejected",
        )
        await self._repository.finalize_call(
            tool_call_id,
            status="denied",
            result=None,
            result_hash=None,
            error_code="approval_rejected",
            completed_at=approval.decided_at,
        )
        return approval

    async def execute_call(
        self,
        *,
        context: CommandContext,
        tool_call_id: UUID,
    ) -> ToolCallDetails:
        call = await self._require_call(tool_call_id)
        if call.status == "denied":
            raise ToolPolicyError("approval_rejected")
        if call.status != "requested":
            raise ToolPolicyError("tool_call_not_executable")
        definition = self._definitions.get(call.tool_name)
        if definition is None or definition.version != call.tool_version:
            raise ToolPolicyError("tool_definition_changed")
        if definition.approval_required:
            approval = await self._repository.get_approval(tool_call_id)
            if approval is None:
                raise ToolPolicyError("approval_required")
            if approval.decision != "approved":
                raise ToolPolicyError("approval_rejected")
            if approval.arguments_hash != call.arguments_hash:
                raise ToolPolicyError("arguments_hash_mismatch")
        try:
            raw_result = await definition.executor(call.arguments)
            result = _plain_json_object(raw_result)
            digest = tool_result_hash(result)
        except Exception:
            failed = await self._repository.finalize_call(
                tool_call_id,
                status="failed",
                result=None,
                result_hash=None,
                error_code="tool_execution_failed",
                completed_at=datetime.now(UTC),
            )
            _log("tool_call_failed", context, failed, error_code="tool_execution_failed")
            return failed
        completed = await self._repository.finalize_call(
            tool_call_id,
            status="executed",
            result=result,
            result_hash=digest,
            error_code=None,
            completed_at=datetime.now(UTC),
        )
        _log("tool_call_executed", context, completed)
        return completed

    async def get_call(self, tool_call_id: UUID) -> ToolCallDetails:
        return await self._require_call(tool_call_id)

    async def _decide(
        self,
        *,
        context: CommandContext,
        tool_call_id: UUID,
        exact_arguments_hash: str,
        decision: ToolApprovalDecision,
    ) -> ToolApprovalDetails:
        call = await self._require_call(tool_call_id)
        definition = self._definitions.get(call.tool_name)
        if definition is None or not definition.approval_required:
            raise ToolPolicyError("approval_not_required")
        if exact_arguments_hash != call.arguments_hash:
            raise ToolPolicyError("arguments_hash_mismatch")
        approval = ToolApprovalDetails(
            tool_approval_id=uuid4(),
            tool_call_id=tool_call_id,
            decision=decision,
            arguments_hash=exact_arguments_hash,
            actor_type=context.actor.actor_type,
            actor_id=context.actor.actor_id or context.installation_identity,
            decided_at=datetime.now(UTC),
            expires_at=None,
        )
        stored = await self._repository.add_approval(approval)
        _log(f"tool_call_{decision}", context, call)
        return stored

    async def _require_call(self, tool_call_id: UUID) -> ToolCallDetails:
        call = await self._repository.get_call(tool_call_id)
        if call is None:
            raise ToolPolicyError("tool_call_not_found")
        return call


def _plain_json_object(value: Mapping[str, object]) -> dict[str, object]:
    result = {str(key): item for key, item in value.items()}
    arguments_hash(result)
    return result


def _validate_arguments(
    arguments: Mapping[str, object], schema: Mapping[str, object]
) -> dict[str, object]:
    if not isinstance(arguments, Mapping):
        raise TypeError("arguments must be an object")
    result = {str(key): value for key, value in arguments.items()}
    _validate_value(result, schema)
    span = schema.get("x-labpilot-max-line-span")
    if type(span) is int:
        start, end = result.get("start_line"), result.get("end_line")
        if type(start) is not int or type(end) is not int or end < start or end - start + 1 > span:
            raise ValueError("line span exceeds limit")
    return result


def _validate_value(value: object, schema: Mapping[str, object]) -> None:
    allowed_types = schema.get("type")
    if allowed_types is None:
        type_names: tuple[str, ...] | None = None
    elif isinstance(allowed_types, str):
        type_names = (allowed_types,)
    elif isinstance(allowed_types, list) and all(isinstance(item, str) for item in allowed_types):
        type_names = tuple(cast(list[str], allowed_types))
    else:
        raise ValueError("schema type invalid")
    if type_names is not None and not any(_matches_type(value, item) for item in type_names):
        raise ValueError("schema type mismatch")
    if "enum" in schema and value not in cast(list[object], schema["enum"]):
        raise ValueError("schema enum mismatch")
    if isinstance(value, Mapping):
        properties = cast(Mapping[str, Mapping[str, object]], schema.get("properties", {}))
        required = cast(list[str], schema.get("required", []))
        if any(name not in value for name in required):
            raise ValueError("required property missing")
        if schema.get("additionalProperties") is False and any(
            name not in properties for name in value
        ):
            raise ValueError("additional property")
        for name, item in value.items():
            if name in properties:
                _validate_value(item, properties[name])
    if type(value) is str:
        if "minLength" in schema and len(value) < cast(int, schema["minLength"]):
            raise ValueError("string too short")
        if "maxLength" in schema and len(value) > cast(int, schema["maxLength"]):
            raise ValueError("string too long")
        if "pattern" in schema and re.fullmatch(cast(str, schema["pattern"]), value) is None:
            raise ValueError("string pattern mismatch")
    if type(value) is int:
        if "minimum" in schema and value < cast(int, schema["minimum"]):
            raise ValueError("integer below minimum")
        if "maximum" in schema and value > cast(int, schema["maximum"]):
            raise ValueError("integer above maximum")


def _matches_type(value: object, type_name: object) -> bool:
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return type(value) is str
    if type_name == "integer":
        return type(value) is int
    if type_name == "number":
        return type(value) in {int, float}
    if type_name == "boolean":
        return type(value) is bool
    if type_name == "null":
        return value is None
    return False


def _log(
    event: str,
    context: CommandContext,
    call: ToolCallDetails,
    *,
    error_code: str | None = None,
) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "request_id": context.interaction_correlation_id,
            "project_id": str(call.project_id),
            "tool_call_id": str(call.tool_call_id),
            "status": call.status,
            "error_code": error_code,
        },
    )
