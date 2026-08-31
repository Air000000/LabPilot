"""add generic command receipt persistence

Revision ID: 0003_command_receipts
Revises: 0002_runtime_events_outbox
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_command_receipts"
down_revision: str | None = "0002_runtime_events_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "command_receipts",
        sa.Column("command_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_scope_identity", sa.String(length=64), nullable=False),
        sa.Column("command_name", sa.String(length=120), nullable=False),
        sa.Column("command_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("normalized_request_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome_kind", sa.String(length=80), nullable=False),
        sa.Column("primary_result_type", sa.String(length=100), nullable=False),
        sa.Column("primary_result_id", sa.String(length=200), nullable=False),
        sa.Column("result_envelope_schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "result_envelope_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("result_envelope_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "command_version > 0",
            name="ck_command_receipt_version_positive",
        ),
        sa.CheckConstraint(
            "result_envelope_schema_version > 0",
            name="ck_command_receipt_envelope_version_positive",
        ),
        sa.PrimaryKeyConstraint(
            "command_receipt_id",
            name="pk_command_receipts",
        ),
        sa.UniqueConstraint(
            "idempotency_scope_identity",
            "command_name",
            "command_version",
            "idempotency_key",
            name="uq_command_receipt_idempotency_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("command_receipts")
