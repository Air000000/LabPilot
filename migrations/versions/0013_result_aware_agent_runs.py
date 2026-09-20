"""add result-aware AgentRun provenance

Revision ID: 0013_result_aware_agent_runs
Revises: 0012_version_identity_nonunique
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_result_aware_agent_runs"
down_revision: str | None = "0012_version_identity_nonunique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "source_batch_summary_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_agent_runs_source_summary_revision_id_batch_summary_revisions",
        "agent_runs",
        "batch_summary_revisions",
        ["source_batch_summary_revision_id"],
        ["batch_summary_revision_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_runs_source_summary_revision_id_batch_summary_revisions",
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_column("agent_runs", "source_batch_summary_revision_id")
