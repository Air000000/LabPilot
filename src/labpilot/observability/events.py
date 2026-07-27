from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.db.uow import UnitOfWork
from labpilot.observability.models import (
    AggregateEventCursorRow,
    OutboxRecordRow,
    RuntimeEventRow,
)
from labpilot.observability.payloads import (
    PreparedRuntimeEventPayload,
    prepare_runtime_event_payload,
)

_FOUNDATION_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    project_id: UUID
    aggregate_type: str
    aggregate_id: str
    event_type: str
    event_schema_version: int
    payload: PreparedRuntimeEventPayload
    occurred_at: datetime | None
    interaction_correlation_id: str | None
    workflow_correlation_id: str | None
    causation_id: UUID | None
    actor_type: str
    actor_id: str | None
    visibility_class: str
    topic: str
    partition_key: str

    @classmethod
    def for_test(cls, aggregate_id: str) -> "RuntimeEventEnvelope":
        return cls(
            project_id=_FOUNDATION_PROJECT_ID,
            aggregate_type="foundation_probe",
            aggregate_id=aggregate_id,
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload=prepare_runtime_event_payload(
                event_type="foundation.probe.created",
                event_schema_version=1,
                payload={"aggregate_id": aggregate_id},
            ),
            occurred_at=None,
            interaction_correlation_id=None,
            workflow_correlation_id=None,
            causation_id=None,
            actor_type="system",
            actor_id=None,
            visibility_class="internal",
            topic="runtime-events",
            partition_key=f"foundation_probe:{aggregate_id}",
        )


async def _allocate_sequence(
    uow: UnitOfWork,
    aggregate_type: str,
    aggregate_id: str,
) -> int:
    await uow.session.execute(
        insert(AggregateEventCursorRow)
        .values(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            last_sequence=0,
        )
        .on_conflict_do_nothing(
            index_elements=[
                AggregateEventCursorRow.aggregate_type,
                AggregateEventCursorRow.aggregate_id,
            ]
        )
    )
    cursor = await uow.session.scalar(
        select(AggregateEventCursorRow)
        .where(
            AggregateEventCursorRow.aggregate_type == aggregate_type,
            AggregateEventCursorRow.aggregate_id == aggregate_id,
        )
        .with_for_update()
    )
    assert cursor is not None
    cursor.last_sequence += 1
    await uow.session.flush()
    return cursor.last_sequence


async def append_runtime_event(
    uow: UnitOfWork,
    envelope: RuntimeEventEnvelope,
) -> RuntimeEventRow:
    if envelope.payload.event_type != envelope.event_type:
        raise CanonicalizationError("prepared payload event_type mismatch")
    if envelope.payload.event_schema_version != envelope.event_schema_version:
        raise CanonicalizationError("prepared payload schema version mismatch")
    payload_json = envelope.payload.persisted_json()
    db_now = await uow.session.scalar(select(func.clock_timestamp()))
    assert db_now is not None
    sequence = await _allocate_sequence(
        uow,
        envelope.aggregate_type,
        envelope.aggregate_id,
    )
    event_id = uuid4()
    event = RuntimeEventRow(
        event_id=event_id,
        project_id=envelope.project_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        aggregate_sequence=sequence,
        event_type=envelope.event_type,
        event_schema_version=envelope.event_schema_version,
        payload_json=payload_json,
        payload_hash=envelope.payload.payload_hash,
        occurred_at=envelope.occurred_at or db_now,
        interaction_correlation_id=envelope.interaction_correlation_id,
        workflow_correlation_id=envelope.workflow_correlation_id,
        causation_id=envelope.causation_id,
        actor_type=envelope.actor_type,
        actor_id=envelope.actor_id,
        visibility_class=envelope.visibility_class,
    )
    uow.session.add(event)
    await uow.session.flush()
    outbox = OutboxRecordRow(
        outbox_id=uuid4(),
        event_id=event_id,
        topic=envelope.topic,
        partition_key=envelope.partition_key,
        payload_ref=f"runtime-event:{event_id}",
        status="pending",
        available_at=db_now,
        attempt_count=0,
        lease_generation=0,
    )
    uow.session.add(outbox)
    await uow.session.flush()
    return event
