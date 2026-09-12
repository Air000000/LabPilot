"""allow repeated experiment version semantic identities

Revision ID: 0012_version_identity_nonunique
Revises: 0011_result_plane
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_version_identity_nonunique"
down_revision: str | None = "0011_result_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_experiment_versions_identity",
        "experiment_versions",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_experiment_versions_identity",
        "experiment_versions",
        ["experiment_version_identity"],
    )
