"""Record manual adoption of exact static candidates into clean Snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_source_adoptions"
down_revision: str | None = "0015_agent_output_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_adoptions",
        sa.Column("source_adoption_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_ref", sa.String(71), nullable=False),
        sa.Column("adoption_identity", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_snapshot_id"],
            ["repository_snapshots.repository_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_snapshot_id"],
            ["repository_snapshots.repository_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("target_snapshot_id", name="uq_source_adoptions_target"),
        sa.CheckConstraint(
            "base_snapshot_id <> target_snapshot_id", name="ck_source_adoptions_new"
        ),
        sa.CheckConstraint(
            "adoption_identity ~ '^[0-9a-f]{64}$'", name="ck_source_adoptions_identity"
        ),
        sa.CheckConstraint(
            "package_ref ~ '^sha256:[0-9a-f]{64}$'", name="ck_source_adoptions_package"
        ),
        sa.CheckConstraint("length(actor_id) > 0", name="ck_source_adoptions_actor"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM source_adoptions)")):
        raise RuntimeError("source adoption facts exist; refusing destructive downgrade")
    op.drop_table("source_adoptions")
