"""add append-only result plane tables

Revision ID: 0011_result_plane
Revises: 0010_attempt_physical_execution
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_result_plane"
down_revision: str | None = "0010_attempt_physical_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_name", sa.String(length=200), nullable=False),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_artifacts_byte_size_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_artifacts_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_artifacts"),
        sa.UniqueConstraint(
            "attempt_id", "artifact_name", name="uq_artifacts_attempt_name"
        ),
        sa.UniqueConstraint(
            "attempt_id", "relative_path", name="uq_artifacts_attempt_path"
        ),
    )
    op.create_table(
        "metric_observations",
        sa.Column(
            "metric_observation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_name", sa.String(length=200), nullable=False),
        sa.Column("split", sa.String(length=200), nullable=True),
        sa.Column("numeric_value", sa.Numeric(), nullable=False),
        sa.Column("extractor_version", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.artifact_id"],
            name="fk_metric_observations_artifact_id_artifacts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "metric_observation_id", name="pk_metric_observations"
        ),
        sa.UniqueConstraint(
            "artifact_id",
            "metric_name",
            "extractor_version",
            name="uq_metric_observations_artifact_metric_extractor",
        ),
    )
    op.create_table(
        "validation_reports",
        sa.Column(
            "validation_report_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("validation_protocol_hash", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "extraction_profile_version", sa.String(length=100), nullable=False
        ),
        sa.Column("validator_version", sa.String(length=100), nullable=False),
        sa.Column(
            "input_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column(
            "reason_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "warning_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('valid','invalid','inconclusive','error')",
            name="ck_validation_reports_verdict",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_validation_reports_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "validation_report_id", name="pk_validation_reports"
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "validation_protocol_hash",
            "extraction_profile_version",
            "validator_version",
            name="uq_validation_reports_semantic_key",
        ),
    )
    op.create_table(
        "trial_result_selections",
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "validation_report_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "selected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trial_id"],
            ["trials.trial_id"],
            name="fk_trial_result_selections_trial_id_trials",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_trial_result_selections_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["validation_report_id"],
            ["validation_reports.validation_report_id"],
            name="fk_trial_result_selections_report_id_reports",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("trial_id", name="pk_trial_result_selections"),
        sa.UniqueConstraint(
            "validation_report_id",
            name="uq_trial_result_selections_validation_report",
        ),
    )
    op.create_table(
        "batch_summary_revisions",
        sa.Column(
            "batch_summary_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.BigInteger(), nullable=False),
        sa.Column(
            "summary_protocol_version", sa.String(length=100), nullable=False
        ),
        sa.Column(
            "included_result_refs_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "excluded_reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "warning_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "aggregates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_batch_summary_revisions_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.batch_id"],
            name="fk_batch_summary_revisions_batch_id_batches",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "batch_summary_revision_id", name="pk_batch_summary_revisions"
        ),
        sa.UniqueConstraint(
            "batch_id",
            "revision_number",
            name="uq_batch_summary_revisions_batch_number",
        ),
    )


def downgrade() -> None:
    op.drop_table("batch_summary_revisions")
    op.drop_table("trial_result_selections")
    op.drop_table("validation_reports")
    op.drop_table("metric_observations")
    op.drop_table("artifacts")
