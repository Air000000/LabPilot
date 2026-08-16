from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.common.commands import CommandActor, CommandContext
from labpilot.db.session import Database
from labpilot.experiments.models import ProjectRow
from labpilot.tools_policy.repository import SqlToolRepository
from labpilot.tools_policy.service import ToolApplicationService
from labpilot.tools_policy.types import ToolDefinition

pytestmark = pytest.mark.integration


def _context() -> CommandContext:
    return CommandContext(
        installation_identity="integration-installation",
        actor=CommandActor(actor_type="human", actor_id="operator"),
        interaction_correlation_id="tool-persistence-request",
    )


def test_tool_tables_match_the_frozen_0008_contract(isolated_database_url: str) -> None:
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(isolated_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0009_execution_preparation"
            )
            tool_call_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='tool_calls'"
                    )
                ).scalars()
            )
            approval_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='tool_approvals'"
                    )
                ).scalars()
            )
            constraints = set(
                connection.execute(
                    text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE table_schema='public' "
                        "AND table_name IN ('tool_calls','tool_approvals')"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    assert tool_call_columns == {
        "tool_call_id",
        "project_id",
        "tool_name",
        "tool_version",
        "risk_level",
        "side_effect_class",
        "arguments_json",
        "arguments_hash",
        "status",
        "result_json",
        "result_hash",
        "error_code",
        "requested_at",
        "completed_at",
    }
    assert approval_columns == {
        "tool_approval_id",
        "tool_call_id",
        "decision",
        "arguments_hash",
        "actor_type",
        "actor_id",
        "decided_at",
        "expires_at",
    }
    assert {
        "pk_tool_calls",
        "fk_tool_calls_project_id_projects",
        "ck_tool_calls_risk_level",
        "ck_tool_calls_side_effect_class",
        "ck_tool_calls_status",
        "pk_tool_approvals",
        "fk_tool_approvals_tool_call_id_tool_calls",
        "uq_tool_approvals_tool_call_id",
        "ck_tool_approvals_decision",
    } <= constraints


@pytest.mark.asyncio
async def test_tool_call_and_exact_approval_persist_across_repository_instances(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    project_id = uuid4()
    try:
        async with database.session() as session, session.begin():
            session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity="a" * 64,
                    name="Tool persistence",
                    description=None,
                    status="active",
                    primary_repository_registration_id=None,
                    artifact_root_ref="artifact-root",
                )
            )

        async def executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
            return {"written": arguments["path"]}

        definition = ToolDefinition(
            name="synthetic.write",
            version="v1",
            risk="yellow",
            side_effect="side_effecting",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            approval_required=True,
            executor=executor,
        )
        service = ToolApplicationService((definition,), SqlToolRepository(database.session))
        call = await service.request_call(
            context=_context(),
            project_id=project_id,
            definition_name="synthetic.write",
            arguments={"path": "result.json"},
        )
        await service.approve_call(
            context=_context(),
            tool_call_id=call.tool_call_id,
            exact_arguments_hash=call.arguments_hash,
        )
        await service.execute_call(context=_context(), tool_call_id=call.tool_call_id)

        repository = SqlToolRepository(database.session)
        persisted_call = await repository.get_call(call.tool_call_id)
        persisted_approval = await repository.get_approval(call.tool_call_id)
    finally:
        await database.dispose()

    assert persisted_call is not None
    assert persisted_call.status == "executed"
    assert persisted_call.arguments == {"path": "result.json"}
    assert persisted_call.result == {"written": "result.json"}
    assert persisted_approval is not None
    assert persisted_approval.arguments_hash == persisted_call.arguments_hash
