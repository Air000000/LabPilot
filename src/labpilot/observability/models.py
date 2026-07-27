from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from labpilot.db.base import Base


class AggregateEventCursorRow(Base):
    __tablename__ = "aggregate_event_cursors"

    aggregate_type: Mapped[str] = mapped_column(String(100), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RuntimeEventRow(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_sequence",
            name="uq_runtime_event_aggregate_sequence",
        ),
        Index("ix_runtime_event_interaction_correlation", "interaction_correlation_id"),
        Index("ix_runtime_event_workflow_correlation", "workflow_correlation_id"),
    )

    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    aggregate_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    interaction_correlation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workflow_correlation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    visibility_class: Mapped[str] = mapped_column(String(50), nullable=False)


class OutboxRecordRow(Base):
    __tablename__ = "outbox_records"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_event_id"),
        Index("ix_outbox_claim", "status", "available_at"),
    )

    outbox_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runtime_events.event_id"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    partition_key: Mapped[str] = mapped_column(String(300), nullable=False)
    payload_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
