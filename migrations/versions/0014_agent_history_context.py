"""Freeze bounded history for new result-aware runs; preserve legacy NULLs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_agent_history_context"
down_revision: str | None = "0013_result_aware_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("history_context_json", postgresql.JSONB(), nullable=True)
    )
    op.create_check_constraint(
        "ck_agent_runs_history_requires_source",
        "agent_runs",
        "history_context_json IS NULL OR (source_batch_summary_revision_id IS NOT NULL "
        "AND jsonb_typeof(history_context_json) = 'object')",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM agent_runs WHERE history_context_json IS NOT NULL)")
    ):
        raise RuntimeError("history evidence exists; refusing destructive downgrade")
    op.drop_constraint("ck_agent_runs_history_requires_source", "agent_runs", type_="check")
    op.drop_column("agent_runs", "history_context_json")
