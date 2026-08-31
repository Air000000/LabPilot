"""add Project core schema

Revision ID: 0004_projects
Revises: 0003_command_receipts
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_projects"
down_revision: str | None = "0003_command_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_identity", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "primary_repository_registration_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("artifact_root_ref", sa.String(length=1000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_project_status",
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name="ck_project_row_version_positive",
        ),
        sa.PrimaryKeyConstraint("project_id", name="pk_projects"),
        sa.UniqueConstraint("project_identity", name="uq_project_identity"),
    )


def downgrade() -> None:
    op.drop_table("projects")
