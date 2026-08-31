from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.context.repository_source import RepositoryTextSlice
from labpilot.tools_policy.repository_tools import repository_tool_definitions
from labpilot.tools_policy.service import ToolApplicationService, ToolPolicyError
from labpilot.tools_policy.types import (
    ToolApprovalDetails,
    ToolCallDetails,
    ToolDefinition,
    bound_toolset_hash,
)


class MemoryToolRepository:
    def __init__(self) -> None:
        self.calls: dict[UUID, ToolCallDetails] = {}
        self.approvals: dict[UUID, ToolApprovalDetails] = {}

    async def add_call(self, call: ToolCallDetails) -> ToolCallDetails:
        self.calls[call.tool_call_id] = call
        return call

    async def get_call(self, tool_call_id: UUID) -> ToolCallDetails | None:
        return self.calls.get(tool_call_id)

    async def add_approval(self, approval: ToolApprovalDetails) -> ToolApprovalDetails:
        self.approvals[approval.tool_call_id] = approval
        return approval

    async def get_approval(self, tool_call_id: UUID) -> ToolApprovalDetails | None:
        return self.approvals.get(tool_call_id)

    async def finalize_call(
        self,
        tool_call_id: UUID,
        *,
        status: str,
        result: Mapping[str, object] | None,
        result_hash: str | None,
        error_code: str | None,
        completed_at: datetime,
    ) -> ToolCallDetails:
        updated = replace(
            self.calls[tool_call_id],
            status=status,
            result=result,
            result_hash=result_hash,
            error_code=error_code,
            completed_at=completed_at,
        )
        self.calls[tool_call_id] = updated
        return updated


@pytest.fixture
def context() -> CommandContext:
    return CommandContext(
        installation_identity="test-installation",
        actor=CommandActor(actor_type="human", actor_id="operator"),
        interaction_correlation_id="request-1",
    )


def definition(
    executor,
    *,
    name: str = "repository.read_text",
    version: str = "v1",
    approval_required: bool = False,
    schema: Mapping[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version=version,
        risk="green" if not approval_required else "yellow",
        side_effect="read_only" if not approval_required else "side_effecting",
        input_schema=schema
        or {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        approval_required=approval_required,
        executor=executor,
    )


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_before_persistence(context) -> None:
    repository = MemoryToolRepository()
    service = ToolApplicationService((), repository)

    with pytest.raises(ToolPolicyError, match="unknown_tool"):
        await service.request_call(
            context=context,
            project_id=uuid4(),
            definition_name="missing",
            arguments={},
        )

    assert repository.calls == {}


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_before_persistence_or_execution(
    context,
) -> None:
    repository = MemoryToolRepository()
    executed = False

    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal executed
        executed = True
        return arguments

    service = ToolApplicationService((definition(executor),), repository)

    with pytest.raises(ToolPolicyError, match="invalid_arguments"):
        await service.request_call(
            context=context,
            project_id=uuid4(),
            definition_name="repository.read_text",
            arguments={"unexpected": True},
        )

    assert repository.calls == {}
    assert executed is False


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "repository.read_text",
            {"relative_path": "../secret", "start_line": 1, "end_line": 1},
        ),
        ("repository.list_tree", {"prefix": "../../outside"}),
        (
            "repository.path_fact",
            {"relative_path": "C:\\host\\secret"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_repository_paths_are_rejected_before_persistence(
    context, tool_name: str, arguments: Mapping[str, object]
) -> None:
    repository = MemoryToolRepository()
    service = ToolApplicationService(repository_tool_definitions(object(), uuid4()), repository)

    with pytest.raises(ToolPolicyError, match="invalid_arguments"):
        await service.request_call(
            context=context,
            project_id=uuid4(),
            definition_name=tool_name,
            arguments=arguments,
        )

    assert repository.calls == {}


@pytest.mark.asyncio
async def test_argument_hash_is_canonical_and_stable(context) -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    repository = MemoryToolRepository()
    service = ToolApplicationService(
        (
            definition(
                executor,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start": {"type": "integer"},
                    },
                    "required": ["path", "start"],
                    "additionalProperties": False,
                },
            ),
        ),
        repository,
    )

    first = await service.request_call(
        context=context,
        project_id=uuid4(),
        definition_name="repository.read_text",
        arguments={"path": "README.md", "start": 1},
    )
    second = await service.request_call(
        context=context,
        project_id=uuid4(),
        definition_name="repository.read_text",
        arguments={"start": 1, "path": "README.md"},
    )

    assert first.arguments_hash == second.arguments_hash
    assert (
        first.arguments
        == second.arguments
        == {
            "path": "README.md",
            "start": 1,
        }
    )


def test_bound_toolset_hash_tracks_only_canonical_contract() -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    async def other_executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return {"ignored": arguments}

    original = definition(executor)
    same_semantics = definition(
        other_executor,
        schema=MappingProxyType(
            {
                "required": ["path"],
                "additionalProperties": False,
                "properties": MappingProxyType({"path": MappingProxyType({"type": "string"})}),
                "type": "object",
            }
        ),
    )
    version_changed = definition(executor, version="v2")
    schema_changed = definition(
        executor,
        schema={
            "type": "object",
            "properties": {"path": {"type": "string", "maxLength": 100}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    assert bound_toolset_hash((original,)) == bound_toolset_hash((same_semantics,))
    assert bound_toolset_hash((original,)) != bound_toolset_hash((version_changed,))
    assert bound_toolset_hash((original,)) != bound_toolset_hash((schema_changed,))


def test_bound_toolset_hash_ignores_required_order() -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    def tool(required: list[str]) -> ToolDefinition:
        return definition(
            executor,
            schema={
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                },
                "required": required,
                "additionalProperties": False,
            },
        )

    assert bound_toolset_hash((tool(["a", "b"]),)) == bound_toolset_hash(
        (tool(["b", "a"]),)
    )
    assert bound_toolset_hash((tool(["a"]),)) != bound_toolset_hash((tool(["b"]),))


def test_bound_toolset_hash_ignores_union_type_order() -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    def tool(types: list[str]) -> ToolDefinition:
        return definition(
            executor,
            schema={
                "type": "object",
                "properties": {"value": {"type": types}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )

    assert bound_toolset_hash((tool(["string", "null"]),)) == bound_toolset_hash(
        (tool(["null", "string"]),)
    )
    assert bound_toolset_hash((tool(["string"]),)) != bound_toolset_hash(
        (tool(["string", "null"]),)
    )


def test_bound_toolset_hash_ignores_enum_order() -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    def tool(values: list[object]) -> ToolDefinition:
        return definition(
            executor,
            schema={
                "type": "object",
                "properties": {"value": {"enum": values}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )

    values: list[object] = ["x", 1, None, {"nested": [1, 2]}]
    assert bound_toolset_hash((tool(values),)) == bound_toolset_hash(
        (tool(list(reversed(values))),)
    )
    assert bound_toolset_hash((tool(values),)) != bound_toolset_hash(
        (tool(["x", 2, None, {"nested": [1, 2]}]),)
    )


def test_bound_toolset_hash_preserves_other_array_order() -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    def tool(default: list[str]) -> ToolDefinition:
        return definition(
            executor,
            schema={
                "type": "object",
                "properties": {
                    "value": {"type": "array", "default": default},
                },
                "additionalProperties": False,
            },
        )

    assert bound_toolset_hash((tool(["first", "second"]),)) != bound_toolset_hash(
        (tool(["second", "first"]),)
    )


@pytest.mark.parametrize(
    ("keyword", "first", "second"),
    [
        ("const", {"required": ["a", "b"]}, {"required": ["b", "a"]}),
        (
            "x-metadata",
            {"required": ["a", "b"]},
            {"required": ["b", "a"]},
        ),
        ("default", {"type": ["string", "null"]}, {"type": ["null", "string"]}),
        (
            "enum",
            [{"type": ["string", "null"]}],
            [{"type": ["null", "string"]}],
        ),
    ],
    ids=[
        "const_object",
        "custom_keyword_object",
        "default_object",
        "object_valued_enum",
    ],
)
def test_bound_toolset_hash_preserves_arrays_inside_literal_values(
    keyword: str, first: object, second: object
) -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return arguments

    def tool(value: object) -> ToolDefinition:
        return definition(
            executor,
            schema={
                "type": "object",
                "properties": {"value": {keyword: value}},
                "additionalProperties": False,
            },
        )

    assert bound_toolset_hash((tool(first),)) != bound_toolset_hash((tool(second),))


def test_repository_toolset_hash_is_snapshot_binding_independent() -> None:
    snapshot_a = uuid4()
    snapshot_b = uuid4()

    definitions_a = repository_tool_definitions(object(), snapshot_a)
    definitions_b = repository_tool_definitions(object(), snapshot_b)

    assert snapshot_a != snapshot_b
    assert bound_toolset_hash(definitions_a) == bound_toolset_hash(definitions_b)


@pytest.mark.asyncio
async def test_read_only_tool_executes_without_approval(context) -> None:
    snapshot_id = uuid4()

    class Source:
        async def read_text(
            self,
            repository_snapshot_id: UUID,
            relative_path: str,
            *,
            start_line: int,
            end_line: int,
            max_bytes: int,
        ) -> RepositoryTextSlice:
            assert repository_snapshot_id == snapshot_id
            assert (relative_path, start_line, end_line, max_bytes) == (
                "README.md",
                1,
                10,
                64 * 1024,
            )
            return RepositoryTextSlice(relative_path, start_line, end_line, "bounded text")

    repository = MemoryToolRepository()
    definitions = repository_tool_definitions(Source(), snapshot_id)
    assert {item.name for item in definitions} == {
        "repository.list_tree",
        "repository.read_text",
        "repository.search_text",
        "repository.path_fact",
    }
    assert all(
        item.risk == "green" and item.side_effect == "read_only" and item.approval_required is False
        for item in definitions
    )
    service = ToolApplicationService(definitions, repository)
    call = await service.request_call(
        context=context,
        project_id=uuid4(),
        definition_name="repository.read_text",
        arguments={"relative_path": "README.md", "start_line": 1, "end_line": 10},
    )

    completed = await service.execute_call(context=context, tool_call_id=call.tool_call_id)

    assert completed.status == "executed"
    assert completed.result == {
        "relative_path": "README.md",
        "start_line": 1,
        "end_line": 10,
        "text": "bounded text",
    }
    assert completed.result_hash is not None


@pytest.mark.asyncio
async def test_approval_required_tool_requires_exact_argument_hash(context) -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return {"ok": True}

    repository = MemoryToolRepository()
    service = ToolApplicationService(
        (definition(executor, name="synthetic.write", approval_required=True),),
        repository,
    )
    call = await service.request_call(
        context=context,
        project_id=uuid4(),
        definition_name="synthetic.write",
        arguments={"path": "output.txt"},
    )

    with pytest.raises(ToolPolicyError, match="approval_required"):
        await service.execute_call(context=context, tool_call_id=call.tool_call_id)
    with pytest.raises(ToolPolicyError, match="arguments_hash_mismatch"):
        await service.approve_call(
            context=context,
            tool_call_id=call.tool_call_id,
            exact_arguments_hash="0" * 64,
        )

    approval = await service.approve_call(
        context=context,
        tool_call_id=call.tool_call_id,
        exact_arguments_hash=call.arguments_hash,
    )
    completed = await service.execute_call(context=context, tool_call_id=call.tool_call_id)

    assert approval.decision == "approved"
    assert approval.arguments_hash == call.arguments_hash
    assert completed.status == "executed"


@pytest.mark.asyncio
async def test_rejected_tool_cannot_execute(context) -> None:
    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        pytest.fail("rejected executor must not run")

    repository = MemoryToolRepository()
    service = ToolApplicationService(
        (definition(executor, name="synthetic.write", approval_required=True),),
        repository,
    )
    call = await service.request_call(
        context=context,
        project_id=uuid4(),
        definition_name="synthetic.write",
        arguments={"path": "output.txt"},
    )
    await service.reject_call(
        context=context,
        tool_call_id=call.tool_call_id,
        exact_arguments_hash=call.arguments_hash,
    )

    with pytest.raises(ToolPolicyError, match="approval_rejected"):
        await service.execute_call(context=context, tool_call_id=call.tool_call_id)


@pytest.mark.asyncio
async def test_executor_failure_persists_only_bounded_error_code(context, caplog) -> None:
    secret = "sentinel-tool-secret"

    async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError(f"raw traceback payload {secret}")

    repository = MemoryToolRepository()
    service = ToolApplicationService((definition(executor),), repository)
    call = await service.request_call(
        context=context,
        project_id=uuid4(),
        definition_name="repository.read_text",
        arguments={"path": "README.md"},
    )

    completed = await service.execute_call(context=context, tool_call_id=call.tool_call_id)

    assert completed.status == "failed"
    assert completed.error_code == "tool_execution_failed"
    assert completed.result is None
    assert secret not in repr(completed)
    assert secret not in caplog.text
