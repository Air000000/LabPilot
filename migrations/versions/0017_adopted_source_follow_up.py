"""Pin an optional human source adoption on existing Follow-up Runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_adopted_source_follow_up"
down_revision: str | None = "0016_source_adoptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("source_adoption_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_runs_source_adoption",
        "agent_runs",
        "source_adoptions",
        ["source_adoption_id"],
        ["source_adoption_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_agent_runs_adoption_requires_summary",
        "agent_runs",
        "source_adoption_id IS NULL OR source_batch_summary_revision_id IS NOT NULL",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM agent_runs WHERE source_adoption_id IS NOT NULL)")
    ):
        raise RuntimeError("adoption-bound Runs exist; refusing destructive downgrade")
    op.drop_constraint("ck_agent_runs_adoption_requires_summary", "agent_runs", type_="check")
    op.drop_constraint("fk_agent_runs_source_adoption", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "source_adoption_id")
