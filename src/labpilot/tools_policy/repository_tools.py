from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast
from uuid import UUID

from labpilot.context.repository_source import (
    RepositoryPathFact,
    RepositorySearchHit,
    RepositoryTextSlice,
)
from labpilot.tools_policy.types import ToolDefinition, ToolExecutor

_SAFE_PATH = r"(?!/)(?!.*\x00)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\)(?![A-Za-z]:).+"


class RepositoryToolSource(Protocol):
    async def list_tree(
        self, repository_snapshot_id: UUID, *, prefix: str | None, max_entries: int
    ) -> tuple[RepositoryPathFact, ...]: ...

    async def read_text(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
        *,
        start_line: int,
        end_line: int,
        max_bytes: int,
    ) -> RepositoryTextSlice: ...

    async def search_text(
        self,
        repository_snapshot_id: UUID,
        query: str,
        *,
        prefix: str | None,
        max_results: int,
        max_file_bytes: int,
    ) -> tuple[RepositorySearchHit, ...]: ...

    async def path_fact(
        self, repository_snapshot_id: UUID, relative_path: str
    ) -> RepositoryPathFact | None: ...


def repository_tool_definitions(
    source: RepositoryToolSource, repository_snapshot_id: UUID
) -> tuple[ToolDefinition, ...]:
    async def list_tree(arguments: Mapping[str, object]) -> Mapping[str, object]:
        entries = await source.list_tree(
            repository_snapshot_id,
            prefix=cast(str | None, arguments.get("prefix")),
            max_entries=500,
        )
        return {
            "entries": [
                {
                    "relative_path": item.relative_path,
                    "file_type": item.file_type,
                    "file_mode": item.file_mode,
                }
                for item in entries
            ]
        }

    async def read_text(arguments: Mapping[str, object]) -> Mapping[str, object]:
        result = await source.read_text(
            repository_snapshot_id,
            cast(str, arguments["relative_path"]),
            start_line=cast(int, arguments["start_line"]),
            end_line=cast(int, arguments["end_line"]),
            max_bytes=64 * 1024,
        )
        return {
            "relative_path": result.relative_path,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "text": result.text,
        }

    async def search_text(arguments: Mapping[str, object]) -> Mapping[str, object]:
        hits = await source.search_text(
            repository_snapshot_id,
            cast(str, arguments["query"]),
            prefix=cast(str | None, arguments.get("prefix")),
            max_results=100,
            max_file_bytes=256 * 1024,
        )
        return {
            "hits": [
                {
                    "relative_path": item.relative_path,
                    "line_number": item.line_number,
                    "line_text": item.line_text,
                }
                for item in hits
            ]
        }

    async def path_fact(arguments: Mapping[str, object]) -> Mapping[str, object]:
        fact = await source.path_fact(repository_snapshot_id, cast(str, arguments["relative_path"]))
        return {
            "fact": None
            if fact is None
            else {
                "relative_path": fact.relative_path,
                "file_type": fact.file_type,
                "file_mode": fact.file_mode,
            }
        }

    path = {"type": "string", "minLength": 1, "maxLength": 1000, "pattern": _SAFE_PATH}
    prefix = {
        "type": ["string", "null"],
        "minLength": 1,
        "maxLength": 1000,
        "pattern": _SAFE_PATH,
    }
    return (
        _definition(
            "repository.list_tree",
            {"prefix": prefix},
            [],
            list_tree,
        ),
        _definition(
            "repository.read_text",
            {
                "relative_path": path,
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            ["relative_path", "start_line", "end_line"],
            read_text,
            extra={"x-labpilot-max-line-span": 500},
        ),
        _definition(
            "repository.search_text",
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "prefix": prefix,
            },
            ["query"],
            search_text,
        ),
        _definition(
            "repository.path_fact",
            {"relative_path": path},
            ["relative_path"],
            path_fact,
        ),
    )


def _definition(
    name: str,
    properties: dict[str, object],
    required: list[str],
    executor: ToolExecutor,
    *,
    extra: dict[str, object] | None = None,
) -> ToolDefinition:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    schema.update(extra or {})
    return ToolDefinition(
        name=name,
        version="v1",
        risk="green",
        side_effect="read_only",
        input_schema=schema,
        approval_required=False,
        executor=executor,
    )
