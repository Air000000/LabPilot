"""add execution preparation ledger

Revision ID: 0009_execution_preparation
Revises: 0008_agent_tool_runtime
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_execution_preparation"
down_revision: str | None = "0008_agent_tool_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "experiment_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("start_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "row_version > 0", name="ck_batches_row_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_batches_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_version_id"],
            ["experiment_versions.experiment_version_id"],
            name="fk_batches_experiment_version_id_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("batch_id", name="pk_batches"),
    )
    op.create_table(
        "trials",
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("trial_identity", sa.String(length=64), nullable=False),
        sa.Column(
            "resolved_parameters_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trial_ordinal > 0", name="ck_trials_ordinal_positive"
        ),
        sa.CheckConstraint("seed >= 0", name="ck_trials_seed_nonnegative"),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.batch_id"],
            name="fk_trials_batch_id_batches",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("trial_id", name="pk_trials"),
        sa.UniqueConstraint(
            "batch_id", "trial_identity", name="uq_trials_batch_identity"
        ),
        sa.UniqueConstraint(
            "batch_id", "trial_ordinal", name="uq_trials_batch_ordinal"
        ),
    )
    op.create_table(
        "attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column(
            "supervisor_execution_identity", sa.String(length=200), nullable=False
        ),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column(
            "frozen_execution_spec_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("frozen_execution_spec_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "source_manifest_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("source_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("preparation_failure_code", sa.String(length=100), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_number > 0", name="ck_attempts_number_positive"
        ),
        sa.CheckConstraint(
            "status IN ('preparing','launch_ready','preparation_failed',"
            "'cancelled_pre_launch','launch_pending','running','unknown',"
            "'identity_conflict','terminal')",
            name="ck_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["trial_id"],
            ["trials.trial_id"],
            name="fk_attempts_trial_id_trials",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_attempts"),
        sa.UniqueConstraint(
            "supervisor_execution_identity", name="uq_attempts_supervisor_identity"
        ),
        sa.UniqueConstraint(
            "trial_id", "attempt_number", name="uq_attempts_trial_number"
        ),
    )
    op.create_index(
        "uq_attempts_active_trial",
        "attempts",
        ["trial_id"],
        unique=True,
        postgresql_where=sa.text("terminal_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_attempts_active_trial", table_name="attempts")
    op.drop_table("attempts")
    op.drop_table("trials")
    op.drop_table("batches")
