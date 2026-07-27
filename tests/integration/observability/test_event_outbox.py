from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.hashing import namespaced_sha256
from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow
from labpilot.observability.outbox import claim_outbox_records
from labpilot.observability.payloads import prepare_runtime_event_payload


@pytest.mark.integration
@pytest.mark.asyncio
async def test_event_and_outbox_are_committed_atomically(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    occurred_at = datetime.now(UTC) - timedelta(seconds=1)
    try:
        async with UnitOfWork(database.session) as uow:
            event = await append_runtime_event(
                uow,
                RuntimeEventEnvelope(
                    project_id=uuid4(),
                    aggregate_type="foundation_probe",
                    aggregate_id="probe-1",
                    event_type="foundation.probe.created",
                    event_schema_version=1,
                    payload=prepare_runtime_event_payload(
                        event_type="foundation.probe.created",
                        event_schema_version=1,
                        payload={"aggregate_id": "probe-1", "value": 1},
                    ),
                    occurred_at=occurred_at,
                    interaction_correlation_id="interaction-1",
                    workflow_correlation_id=None,
                    causation_id=None,
                    actor_type="system",
                    actor_id=None,
                    visibility_class="internal",
                    topic="runtime-events",
                    partition_key="foundation_probe:probe-1",
                ),
            )
            await uow.commit()

        async with database.session() as session:
            stored_event = await session.scalar(
                select(RuntimeEventRow).where(RuntimeEventRow.event_id == event.event_id)
            )
            stored_outbox = await session.scalar(
                select(OutboxRecordRow).where(OutboxRecordRow.event_id == event.event_id)
            )
        assert stored_event is not None
        assert stored_outbox is not None
        assert stored_event.payload_hash == namespaced_sha256(
            "labpilot-runtime-event-payload:v1",
            {"aggregate_id": "probe-1", "value": 1},
        )
        assert stored_event.occurred_at == occurred_at
        assert stored_event.recorded_at > occurred_at
        assert stored_event.created_at is not None
        assert stored_event.project_id is not None
        assert stored_outbox.event_id == stored_event.event_id
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prepared_payload_cannot_be_used_for_a_different_schema(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    envelope = RuntimeEventEnvelope.for_test("schema-mismatch")
    mismatched = RuntimeEventEnvelope(
        project_id=envelope.project_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        event_type="another.event",
        event_schema_version=99,
        payload=envelope.payload,
        occurred_at=envelope.occurred_at,
        interaction_correlation_id=None,
        workflow_correlation_id=None,
        causation_id=None,
        actor_type="system",
        actor_id=None,
        visibility_class="internal",
        topic="runtime-events",
        partition_key="foundation_probe:schema-mismatch",
    )
    try:
        with pytest.raises(CanonicalizationError, match="event_type mismatch"):
            async with UnitOfWork(database.session) as uow:
                await append_runtime_event(uow, mismatched)
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rollback_persists_neither_event_nor_outbox(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    event_id = None
    try:
        with pytest.raises(RuntimeError, match="force rollback"):
            async with UnitOfWork(database.session) as uow:
                event = await append_runtime_event(
                    uow,
                    RuntimeEventEnvelope.for_test("probe-rollback"),
                )
                event_id = event.event_id
                raise RuntimeError("force rollback")

        assert event_id is not None
        async with database.session() as session:
            assert await session.scalar(
                select(RuntimeEventRow).where(RuntimeEventRow.event_id == event_id)
            ) is None
            assert await session.scalar(
                select(OutboxRecordRow).where(OutboxRecordRow.event_id == event_id)
            ) is None
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outbox_redelivery_reuses_the_existing_event_id(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            event = await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("probe-redelivery"),
            )
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            first = await claim_outbox_records(
                uow,
                owner="publisher-1",
                limit=1,
                lease_seconds=0,
            )
            await uow.commit()
        async with UnitOfWork(database.session) as uow:
            second = await claim_outbox_records(
                uow,
                owner="publisher-2",
                limit=1,
                lease_seconds=30,
            )
            await uow.commit()

        assert first[0].event_id == event.event_id
        assert second[0].event_id == event.event_id
    finally:
        await database.dispose()
