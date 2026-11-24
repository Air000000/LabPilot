"""Bounded output feedback on existing Steps; preserve legacy NULLs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_agent_output_feedback"
down_revision: str | None = "0014_agent_history_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_steps", sa.Column("output_feedback_json", postgresql.JSONB(), nullable=True)
    )
    op.create_check_constraint(
        "ck_agent_steps_output_feedback_object",
        "agent_steps",
        "output_feedback_json IS NULL OR jsonb_typeof(output_feedback_json) = 'object'",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM agent_steps WHERE output_feedback_json IS NOT NULL)")
    ):
        raise RuntimeError("output feedback exists; refusing destructive downgrade")
    op.drop_constraint("ck_agent_steps_output_feedback_object", "agent_steps", type_="check")
    op.drop_column("agent_steps", "output_feedback_json")
