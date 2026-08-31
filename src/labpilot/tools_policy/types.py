from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Literal, cast
from uuid import UUID

from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.common.hashing import namespaced_sha256

ToolRisk = Literal["green", "yellow", "red"]
ToolSideEffect = Literal["read_only", "side_effecting"]
ToolCallStatus = Literal["requested", "executed", "failed", "denied"]
ToolApprovalDecision = Literal["approved", "rejected"]
ToolExecutor = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]

_ARGUMENTS_NAMESPACE = "labpilot-tool-arguments:v1"
_RESULT_NAMESPACE = "labpilot-tool-result:v1"
_TOOLSET_NAMESPACE = "labpilot-bound-toolset:v1"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: str
    risk: ToolRisk
    side_effect: ToolSideEffect
    input_schema: Mapping[str, object]
    approval_required: bool
    executor: ToolExecutor


@dataclass(frozen=True, slots=True)
class ToolCallDetails:
    tool_call_id: UUID
    project_id: UUID
    tool_name: str
    tool_version: str
    risk: ToolRisk
    side_effect: ToolSideEffect
    arguments: Mapping[str, object]
    arguments_hash: str
    status: ToolCallStatus
    result: Mapping[str, object] | None
    result_hash: str | None
    error_code: str | None
    requested_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ToolApprovalDetails:
    tool_approval_id: UUID
    tool_call_id: UUID
    decision: ToolApprovalDecision
    arguments_hash: str
    actor_type: str
    actor_id: str
    decided_at: datetime
    expires_at: datetime | None


def _canonical_value(value: object) -> CanonicalValue:
    if value is None or type(value) in {str, int, bool, Decimal}:
        return cast(CanonicalValue, value)
    if type(value) is float and isfinite(value):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    raise ValueError(f"value is not canonical JSON: {type(value).__name__}")


def _canonical_schema_value(value: object, *, keyword: str | None = None) -> CanonicalValue:
    if keyword == "enum" and isinstance(value, list | tuple):
        items = [_canonical_value(item) for item in value]
        items.sort(key=canonical_json_bytes)
        return items
    if keyword in {"required", "type"} and isinstance(value, list | tuple):
        items = [_canonical_value(item) for item in value]
        items.sort(key=canonical_json_bytes)
        return items
    if keyword in {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
    } and isinstance(value, Mapping):
        return {str(key): _canonical_schema_value(item) for key, item in value.items()}
    if keyword in {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "items",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "unevaluatedItems",
    }:
        return _canonical_schema_value(value)
    if keyword in {"allOf", "anyOf", "oneOf", "prefixItems"} and isinstance(
        value, list | tuple
    ):
        return [_canonical_schema_value(item) for item in value]
    if keyword is not None:
        return _canonical_value(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_schema_value(item, keyword=str(key))
            for key, item in value.items()
        }
    return _canonical_value(value)


def arguments_hash(arguments: Mapping[str, object]) -> str:
    return namespaced_sha256(_ARGUMENTS_NAMESPACE, _canonical_value(arguments))


def tool_result_hash(result: Mapping[str, object]) -> str:
    return namespaced_sha256(_RESULT_NAMESPACE, _canonical_value(result))


def bound_toolset_hash(definitions: tuple[ToolDefinition, ...]) -> str:
    payload: list[CanonicalValue] = []
    for definition in sorted(definitions, key=lambda item: (item.name, item.version)):
        payload.append(
            {
                "name": definition.name,
                "version": definition.version,
                "risk": definition.risk,
                "side_effect": definition.side_effect,
                "approval_required": definition.approval_required,
                "input_schema": _canonical_schema_value(definition.input_schema),
            }
        )
    return namespaced_sha256(_TOOLSET_NAMESPACE, payload)
