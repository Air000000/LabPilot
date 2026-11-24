"""Optional source-patch draft context on bounded Agent runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_agent_source_patch_drafts"
down_revision: str | None = "0017_adopted_source_follow_up"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = (
    "source_patch_context IS NULL OR (jsonb_typeof(source_patch_context) = 'object' "
    "AND source_adoption_id IS NULL AND produced_proposal_revision_id IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("source_patch_context", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint("ck_agent_runs_source_patch_object", "agent_runs", CHECK)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM agent_runs WHERE source_patch_context IS NOT NULL)")
    ):
        raise RuntimeError("source patch Runs exist; refusing destructive downgrade")
    op.drop_constraint("ck_agent_runs_source_patch_object", "agent_runs", type_="check")
    op.drop_column("agent_runs", "source_patch_context")
