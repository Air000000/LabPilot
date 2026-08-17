"""add durable physical attempt execution

Revision ID: 0010_attempt_physical_execution
Revises: 0009_execution_preparation
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_attempt_physical_execution"
down_revision: str | None = "0009_execution_preparation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column(
            "frozen_launch_spec_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("execution_spec_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("launch_intent_committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed_environment_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "observed_environment_recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("physical_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("physical_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("termination_signal", sa.Integer(), nullable=True),
        sa.Column("supervisor_result", sa.String(length=100), nullable=True),
        sa.Column("terminal_kind", sa.String(length=60), nullable=True),
        sa.Column("unknown_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attention_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_issue_code", sa.String(length=100), nullable=True),
        sa.Column("supervisor_invocation_id", sa.String(length=200), nullable=True),
        sa.Column("physical_boot_id", sa.String(length=200), nullable=True),
    )
    for column in columns:
        op.add_column("attempts", column)
    op.create_check_constraint(
        "ck_attempts_physical_identity_pair",
        "attempts",
        "(frozen_launch_spec_json IS NULL) = (execution_spec_hash IS NULL)",
    )
    op.create_check_constraint(
        "ck_attempts_physical_identity_conflict_nonterminal",
        "attempts",
        "status <> 'identity_conflict' OR terminal_at IS NULL",
    )
    op.create_check_constraint(
        "ck_attempts_physical_observed_environment_pair",
        "attempts",
        "(observed_environment_json IS NULL) = "
        "(observed_environment_recorded_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_attempts_physical_terminal_kind",
        "attempts",
        "(status = 'terminal') = (terminal_kind IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_attempts_physical_terminal_causality",
        "attempts",
        "(terminal_kind = 'process_exited' AND exit_code IS NOT NULL "
        "AND termination_signal IS NULL) OR "
        "(terminal_kind = 'process_signaled' AND exit_code IS NULL "
        "AND termination_signal IS NOT NULL) OR "
        "(terminal_kind IN ('process_terminal_unclassified','launch_not_accepted') "
        "AND exit_code IS NULL AND termination_signal IS NULL) OR "
        "terminal_kind IS NULL",
    )


def downgrade() -> None:
    for name in (
        "ck_attempts_physical_terminal_causality",
        "ck_attempts_physical_terminal_kind",
        "ck_attempts_physical_observed_environment_pair",
        "ck_attempts_physical_identity_conflict_nonterminal",
        "ck_attempts_physical_identity_pair",
    ):
        op.drop_constraint(name, "attempts", type_="check")
    for name in (
        "physical_boot_id",
        "supervisor_invocation_id",
        "execution_issue_code",
        "attention_required_at",
        "unknown_since",
        "terminal_kind",
        "supervisor_result",
        "termination_signal",
        "exit_code",
        "physical_ended_at",
        "physical_started_at",
        "observed_environment_recorded_at",
        "observed_environment_json",
        "launch_intent_committed_at",
        "execution_spec_hash",
        "frozen_launch_spec_json",
    ):
        op.drop_column("attempts", name)
