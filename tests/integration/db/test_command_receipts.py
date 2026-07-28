from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_command_receipts_have_the_required_columns(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with database.session() as session:
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'command_receipts'"
                )
            )
            columns = set(result.scalars())
    finally:
        await database.dispose()

    assert {
        "command_receipt_id",
        "idempotency_scope_identity",
        "command_name",
        "command_version",
        "idempotency_key",
        "normalized_request_hash",
        "outcome_kind",
        "primary_result_type",
        "primary_result_id",
        "result_envelope_schema_version",
        "result_envelope_json",
        "result_envelope_hash",
        "created_at",
    } <= columns


@pytest.mark.integration
@pytest.mark.asyncio
async def test_command_receipt_idempotency_key_is_unique_within_command_scope(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    statement = text(
        """
        INSERT INTO command_receipts (
            command_receipt_id,
            idempotency_scope_identity,
            command_name,
            command_version,
            idempotency_key,
            normalized_request_hash,
            outcome_kind,
            primary_result_type,
            primary_result_id,
            result_envelope_schema_version,
            result_envelope_json,
            result_envelope_hash
        ) VALUES (
            :command_receipt_id,
            :idempotency_scope_identity,
            :command_name,
            :command_version,
            :idempotency_key,
            :normalized_request_hash,
            :outcome_kind,
            :primary_result_type,
            :primary_result_id,
            :result_envelope_schema_version,
            CAST(:result_envelope_json AS JSONB),
            :result_envelope_hash
        )
        """
    )
    values = {
        "idempotency_scope_identity": "a" * 64,
        "command_name": "CreateProject",
        "command_version": 1,
        "idempotency_key": "request-1",
        "normalized_request_hash": "b" * 64,
        "outcome_kind": "project_created",
        "primary_result_type": "project",
        "primary_result_id": "project-1",
        "result_envelope_schema_version": 1,
        "result_envelope_json": "{}",
        "result_envelope_hash": "c" * 64,
    }
    try:
        with pytest.raises(IntegrityError):
            async with UnitOfWork(database.session) as uow:
                await uow.session.execute(
                    statement,
                    values | {"command_receipt_id": uuid4()},
                )
                await uow.session.execute(
                    statement,
                    values | {"command_receipt_id": uuid4()},
                )
                await uow.commit()
    finally:
        await database.dispose()
