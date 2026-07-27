"""add RuntimeEvent and Outbox foundation

Revision ID: 0002_runtime_events_outbox
Revises: 0001_initial
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_runtime_events_outbox"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aggregate_event_cursors",
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=200), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "aggregate_type",
            "aggregate_id",
            name="pk_aggregate_event_cursors",
        ),
    )

    op.create_table(
        "runtime_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=200), nullable=False),
        sa.Column("aggregate_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("event_schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("interaction_correlation_id", sa.String(length=200), nullable=True),
        sa.Column("workflow_correlation_id", sa.String(length=200), nullable=True),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("visibility_class", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_runtime_events"),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_sequence",
            name="uq_runtime_event_aggregate_sequence",
        ),
    )
    op.create_index(
        "ix_runtime_event_interaction_correlation",
        "runtime_events",
        ["interaction_correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_event_workflow_correlation",
        "runtime_events",
        ["workflow_correlation_id"],
        unique=False,
    )

    op.create_table(
        "outbox_records",
        sa.Column("outbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.Column("partition_key", sa.String(length=300), nullable=False),
        sa.Column("payload_ref", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["runtime_events.event_id"],
            name="fk_outbox_event_id_runtime_events",
        ),
        sa.PrimaryKeyConstraint("outbox_id", name="pk_outbox_records"),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox_records",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_claim", table_name="outbox_records")
    op.drop_table("outbox_records")
    op.drop_index("ix_runtime_event_workflow_correlation", table_name="runtime_events")
    op.drop_index("ix_runtime_event_interaction_correlation", table_name="runtime_events")
    op.drop_table("runtime_events")
    op.drop_table("aggregate_event_cursors")
