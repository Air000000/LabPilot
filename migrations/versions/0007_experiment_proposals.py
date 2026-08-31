"""add experiment proposal authority

Revision ID: 0007_experiment_proposals
Revises: 0006_repository_snapshots
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_experiment_proposals"
down_revision: str | None = "0006_repository_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_experiments_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("experiment_id", name="pk_experiments"),
    )
    op.create_table(
        "experiment_proposal_revisions",
        sa.Column("proposal_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.BigInteger(), nullable=False),
        sa.Column("origin_agent_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_snapshot_identity", sa.String(length=64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_actor_type", sa.String(length=30), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_experiment_proposal_revisions_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name="fk_experiment_proposal_revisions_experiment_id_experiments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "proposal_revision_id",
            name="pk_experiment_proposal_revisions",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "revision_number",
            name="uq_experiment_proposal_revisions_experiment_number",
        ),
    )
    op.create_index(
        "uq_experiment_proposal_revisions_origin_agent_step",
        "experiment_proposal_revisions",
        ["origin_agent_step_id"],
        unique=True,
        postgresql_where=sa.text("origin_agent_step_id IS NOT NULL"),
    )
    op.create_table(
        "experiment_proposal_verifications",
        sa.Column(
            "proposal_verification_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("proposal_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("verifier_profile", sa.String(length=100), nullable=False),
        sa.Column("proposal_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name=(
                "fk_proposal_verifications_revision_id_proposal_revisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "proposal_verification_id",
            name="pk_experiment_proposal_verifications",
        ),
        sa.UniqueConstraint(
            "proposal_revision_id",
            "verifier_profile",
            "proposal_payload_hash",
            name="uq_experiment_proposal_verifications_semantic_key",
        ),
    )
    op.create_table(
        "experiment_design_decisions",
        sa.Column("design_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_verification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("proposal_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_snapshot_identity", sa.String(length=64), nullable=False),
        sa.Column("dataset_reference_hash", sa.String(length=64), nullable=False),
        sa.Column("environment_expectation_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_experiment_design_decisions_decision",
        ),
        sa.CheckConstraint(
            "decision = 'rejected' OR proposal_verification_id IS NOT NULL",
            name="ck_experiment_design_decisions_approval_verification",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name="fk_experiment_design_decisions_revision_id_proposal_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_verification_id"],
            ["experiment_proposal_verifications.proposal_verification_id"],
            name="fk_experiment_design_decisions_verification_id_verifications",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "design_decision_id",
            name="pk_experiment_design_decisions",
        ),
        sa.UniqueConstraint(
            "proposal_revision_id",
            name="uq_experiment_design_decisions_proposal_revision",
        ),
    )
    op.create_table(
        "experiment_versions",
        sa.Column("experiment_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("design_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.BigInteger(), nullable=False),
        sa.Column("experiment_version_identity", sa.String(length=64), nullable=False),
        sa.Column("proposal_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_snapshot_identity", sa.String(length=64), nullable=False),
        sa.Column(
            "dataset_reference_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("dataset_reference_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "environment_expectation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("environment_expectation_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "validation_protocol_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("validation_protocol_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_experiment_versions_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name="fk_experiment_versions_experiment_id_experiments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["design_decision_id"],
            ["experiment_design_decisions.design_decision_id"],
            name="fk_experiment_versions_decision_id_design_decisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name="fk_experiment_versions_revision_id_proposal_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "experiment_version_id",
            name="pk_experiment_versions",
        ),
        sa.UniqueConstraint(
            "design_decision_id",
            name="uq_experiment_versions_design_decision",
        ),
        sa.UniqueConstraint(
            "experiment_version_identity",
            name="uq_experiment_versions_identity",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "version_number",
            name="uq_experiment_versions_experiment_number",
        ),
    )


def downgrade() -> None:
    op.drop_table("experiment_versions")
    op.drop_table("experiment_design_decisions")
    op.drop_table("experiment_proposal_verifications")
    op.drop_index(
        "uq_experiment_proposal_revisions_origin_agent_step",
        table_name="experiment_proposal_revisions",
    )
    op.drop_table("experiment_proposal_revisions")
    op.drop_table("experiments")
