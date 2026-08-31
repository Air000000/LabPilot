from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import cast

from pydantic import TypeAdapter

from labpilot.agent_runtime.provider import ModelRequest
from labpilot.agent_runtime.types import AgentDecision
from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.common.hashing import namespaced_sha256
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.tools_policy.types import ToolDefinition, bound_toolset_hash

_OUTPUT_SCHEMA_NAMESPACE = "labpilot-agent-output-schema:v1"
_MODEL_REQUEST_NAMESPACE = "labpilot-model-request:v1"
_MAX_GOAL_BYTES = 4000
_MAX_TOOL_COUNT = 32
_MAX_TOOL_SCHEMA_BYTES = 32 * 1024
_MAX_PRIOR_RESULTS = 20
_MAX_TOOL_RESULT_BYTES = 64 * 1024
_MAX_HUMAN_INPUT_BYTES = 4000
_MAX_MESSAGE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class PriorToolResult:
    tool_name: str
    result: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CompiledContext:
    request: ModelRequest
    output_schema_hash: str
    canonical_request_hash: str


class ContextCompilationError(ValueError):
    pass


class ContextCompiler:
    def compile(
        self,
        *,
        provider_name: str,
        model: str,
        goal: str,
        snapshot: RepositorySourceDescriptor,
        definitions: tuple[ToolDefinition, ...],
        prior_tool_results: tuple[PriorToolResult, ...],
        prior_human_inputs: tuple[str, ...],
    ) -> CompiledContext:
        _require_utf8(goal, field="goal", max_bytes=_MAX_GOAL_BYTES, non_blank=True)
        if len(definitions) > _MAX_TOOL_COUNT:
            raise ContextCompilationError("too_many_tools")
        if len(prior_tool_results) > _MAX_PRIOR_RESULTS:
            raise ContextCompilationError("too_many_tool_results")

        output_schema = cast(dict[str, object], TypeAdapter(AgentDecision).json_schema())
        output_schema_value = _canonical_value(output_schema)
        output_schema_hash = namespaced_sha256(_OUTPUT_SCHEMA_NAMESPACE, output_schema_value)
        tools = [_tool_contract(definition) for definition in definitions]
        system_value: CanonicalValue = {
            "instruction": (
                "Return exactly one JSON object matching agent_decision_schema. "
                "Do not include rationale, reasoning, or chain_of_thought."
            ),
            "agent_decision_schema": output_schema_value,
            "tools": tools,
        }
        prior_results: list[CanonicalValue] = []
        for item in prior_tool_results:
            value: CanonicalValue = {
                "tool_name": item.tool_name,
                "result": _canonical_value(item.result),
            }
            if len(canonical_json_bytes(value)) > _MAX_TOOL_RESULT_BYTES:
                raise ContextCompilationError("tool_result_too_large")
            prior_results.append(value)
        human_inputs: list[CanonicalValue] = []
        for value in prior_human_inputs:
            _require_utf8(
                value,
                field="human_input",
                max_bytes=_MAX_HUMAN_INPUT_BYTES,
                non_blank=True,
            )
            human_inputs.append(value)
        user_value: CanonicalValue = {
            "goal": goal,
            "snapshot": {
                "repository_snapshot_id": str(snapshot.repository_snapshot_id),
                "repository_snapshot_identity": snapshot.repository_snapshot_identity,
                "repository_registration_id": str(snapshot.repository_registration_id),
                "project_id": str(snapshot.project_id),
                "head_commit": snapshot.head_commit,
                "current_branch": snapshot.current_branch,
                "is_detached_head": snapshot.is_detached_head,
                "is_clean": snapshot.is_clean,
            },
            "prior_tool_results": prior_results,
            "prior_human_inputs": human_inputs,
        }
        messages = (
            {"role": "system", "content": _render(system_value)},
            {"role": "user", "content": _render(user_value)},
        )
        message_bytes = len(messages[0]["content"].encode("utf-8")) + len(
            messages[1]["content"].encode("utf-8")
        )
        if message_bytes > _MAX_MESSAGE_BYTES:
            raise ContextCompilationError("compiled_messages_too_large")
        request = ModelRequest(
            model=model,
            messages=messages,
            semantic_parameters={},
            output_schema=output_schema,
            bound_toolset_hash=bound_toolset_hash(definitions),
        )
        return CompiledContext(
            request=request,
            output_schema_hash=output_schema_hash,
            canonical_request_hash=canonical_request_hash(
                provider_name=provider_name,
                request=request,
                output_schema_hash=output_schema_hash,
            ),
        )


def canonical_request_hash(
    *, provider_name: str, request: ModelRequest, output_schema_hash: str
) -> str:
    return namespaced_sha256(
        _MODEL_REQUEST_NAMESPACE,
        {
            "provider_name": provider_name,
            "model": request.model,
            "messages": _canonical_value(request.messages),
            "semantic_parameters": _canonical_value(request.semantic_parameters),
            "output_schema_hash": output_schema_hash,
            "bound_toolset_hash": request.bound_toolset_hash,
        },
    )


def _tool_contract(definition: ToolDefinition) -> CanonicalValue:
    value: CanonicalValue = {
        "name": definition.name,
        "version": definition.version,
        "risk": definition.risk,
        "side_effect": definition.side_effect,
        "approval_required": definition.approval_required,
        "input_schema": _canonical_value(definition.input_schema),
    }
    if len(canonical_json_bytes(value)) > _MAX_TOOL_SCHEMA_BYTES:
        raise ContextCompilationError("tool_schema_too_large")
    return value


def _render(value: CanonicalValue) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _require_utf8(value: str, *, field: str, max_bytes: int, non_blank: bool) -> None:
    if non_blank and not value.strip():
        raise ContextCompilationError(f"{field}_blank")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContextCompilationError(f"{field}_invalid_utf8") from exc
    if size > max_bytes:
        raise ContextCompilationError(f"{field}_too_large")


def _canonical_value(value: object) -> CanonicalValue:
    if value is None or type(value) in {str, int, bool, Decimal}:
        return cast(CanonicalValue, value)
    if type(value) is float and isfinite(value):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    raise ContextCompilationError(f"non_json_value:{type(value).__name__}")
