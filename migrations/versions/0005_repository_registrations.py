"""add repository registration persistence

Revision ID: 0005_repository_registrations
Revises: 0004_projects
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_repository_registrations"
down_revision: str | None = "0004_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repository_registrations",
        sa.Column(
            "repository_registration_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_type", sa.String(length=30), nullable=False),
        sa.Column("canonical_root_path", sa.Text(), nullable=False),
        sa.Column("root_path_identity", sa.String(length=64), nullable=False),
        sa.Column("remote_origin_sanitized", sa.Text(), nullable=True),
        sa.Column("default_branch", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("last_access_check_at", sa.DateTime(timezone=True), nullable=True),
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
            "repository_type = 'local_git'",
            name="ck_repository_registrations_repository_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'unavailable', 'invalid_git_repository', 'revoked')",
            name="ck_repository_registrations_status",
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name="ck_repository_registrations_row_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_repository_registrations_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "repository_registration_id",
            name="pk_repository_registrations",
        ),
        sa.UniqueConstraint(
            "project_id",
            "repository_registration_id",
            name="uq_repository_registrations_project_registration",
        ),
    )
    op.create_index(
        "uq_repository_registration_live_root",
        "repository_registrations",
        ["project_id", "root_path_identity"],
        unique=True,
        postgresql_where=sa.text("status <> 'revoked'"),
    )
    op.create_foreign_key(
        "fk_projects_primary_repository_registration",
        "projects",
        "repository_registrations",
        ["project_id", "primary_repository_registration_id"],
        ["project_id", "repository_registration_id"],
    )
    op.create_index(
        "uq_projects_primary_repository_registration",
        "projects",
        ["primary_repository_registration_id"],
        unique=True,
        postgresql_where=sa.text("primary_repository_registration_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_projects_primary_repository_registration",
        table_name="projects",
    )
    op.drop_constraint(
        "fk_projects_primary_repository_registration",
        "projects",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_repository_registration_live_root",
        table_name="repository_registrations",
    )
    op.drop_table("repository_registrations")
