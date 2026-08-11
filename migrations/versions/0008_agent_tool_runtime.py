"""add bounded Tool runtime persistence

Revision ID: 0008_agent_tool_runtime
Revises: 0007_experiment_proposals
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_agent_tool_runtime"
down_revision: str | None = "0007_experiment_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("tool_version", sa.String(length=50), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("side_effect_class", sa.String(length=20), nullable=False),
        sa.Column(
            "arguments_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "risk_level IN ('green','yellow','red')",
            name="ck_tool_calls_risk_level",
        ),
        sa.CheckConstraint(
            "side_effect_class IN ('read_only','side_effecting')",
            name="ck_tool_calls_side_effect_class",
        ),
        sa.CheckConstraint(
            "status IN ('requested','executed','failed','denied')",
            name="ck_tool_calls_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_tool_calls_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tool_call_id", name="pk_tool_calls"),
    )
    op.create_table(
        "tool_approvals",
        sa.Column("tool_approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_tool_approvals_decision",
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.tool_call_id"],
            name="fk_tool_approvals_tool_call_id_tool_calls",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tool_approval_id", name="pk_tool_approvals"),
        sa.UniqueConstraint("tool_call_id", name="uq_tool_approvals_tool_call_id"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("termination_reason", sa.String(length=100), nullable=True),
        sa.Column("egress_policy", sa.String(length=30), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("max_wall_clock_seconds", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("total_model_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_step_ordinal", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "produced_proposal_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('ready','running','awaiting_human','completed',"
            "'budget_exhausted','failed')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "egress_policy IN ('local_only','remote_provider_allowed')",
            name="ck_agent_runs_egress_policy",
        ),
        sa.CheckConstraint("row_version > 0", name="ck_agent_runs_row_version_positive"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_agent_runs_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produced_proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name="fk_agent_runs_produced_proposal_revision_id_proposal_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("agent_run_id", name="pk_agent_runs"),
    )
    op.create_table(
        "agent_steps",
        sa.Column("agent_step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("step_kind", sa.String(length=30), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_name", sa.String(length=100), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("canonical_request_hash", sa.String(length=64), nullable=True),
        sa.Column("bound_toolset_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "semantic_parameters_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("output_schema_hash", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("token_usage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decision_kind", sa.String(length=30), nullable=True),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("human_input_text", sa.Text(), nullable=True),
        sa.CheckConstraint("ordinal > 0", name="ck_agent_steps_ordinal_positive"),
        sa.CheckConstraint(
            "step_kind IN ('model_call','human_input')",
            name="ck_agent_steps_step_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('reserved','succeeded','failed')",
            name="ck_agent_steps_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.agent_run_id"],
            name="fk_agent_steps_agent_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.tool_call_id"],
            name="fk_agent_steps_tool_call_id_tool_calls",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("agent_step_id", name="pk_agent_steps"),
        sa.UniqueConstraint("agent_run_id", "ordinal", name="uq_agent_steps_run_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("tool_approvals")
    op.drop_table("tool_calls")
