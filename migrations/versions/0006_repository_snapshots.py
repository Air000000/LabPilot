"""add repository snapshot persistence

Revision ID: 0006_repository_snapshots
Revises: 0005_repository_registrations
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_repository_snapshots"
down_revision: str | None = "0005_repository_registrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repository_snapshots",
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_registration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_sequence", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_root_path", sa.Text(), nullable=False),
        sa.Column("head_commit", sa.String(length=64), nullable=False),
        sa.Column("current_branch", sa.String(length=300), nullable=True),
        sa.Column("is_detached_head", sa.Boolean(), nullable=False),
        sa.Column("is_clean", sa.Boolean(), nullable=False),
        sa.Column("tracked_tree_identity", sa.String(length=64), nullable=False),
        sa.Column("dirty_worktree_manifest_ref", sa.String(length=300), nullable=True),
        sa.Column("dirty_worktree_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("untracked_manifest_ref", sa.String(length=300), nullable=True),
        sa.Column("untracked_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("repository_state_digest", sa.String(length=64), nullable=False),
        sa.Column("repository_snapshot_identity", sa.String(length=64), nullable=False),
        sa.Column("submodule_presence", sa.Boolean(), nullable=False),
        sa.Column("lfs_pointer_presence", sa.Boolean(), nullable=False),
        sa.Column("inspector_compatibility_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "inspector_compatibility_version > 0",
            name="ck_repository_snapshots_inspector_version_positive",
        ),
        sa.CheckConstraint(
            "NOT is_clean OR ("
            "dirty_worktree_manifest_ref IS NULL AND "
            "dirty_worktree_manifest_hash IS NULL AND "
            "untracked_manifest_ref IS NULL AND "
            "untracked_manifest_hash IS NULL)",
            name="ck_repository_snapshots_clean_no_manifests",
        ),
        sa.ForeignKeyConstraint(
            ["repository_registration_id"],
            ["repository_registrations.repository_registration_id"],
            name="fk_repository_snapshots_registration_id_registrations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "repository_snapshot_id",
            name="pk_repository_snapshots",
        ),
        sa.UniqueConstraint(
            "repository_registration_id",
            "snapshot_sequence",
            name="uq_repository_snapshots_registration_sequence",
        ),
        sa.UniqueConstraint(
            "repository_registration_id",
            "repository_snapshot_identity",
            name="uq_repository_snapshots_registration_identity",
        ),
    )
    op.create_table(
        "repository_snapshot_inspection_bundles",
        sa.Column("inspection_bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_commit_sha", sa.String(length=64), nullable=False),
        sa.Column("bundle_schema_version", sa.Integer(), nullable=False),
        sa.Column("capture_tool_version", sa.String(length=100), nullable=False),
        sa.Column("entries_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("inspection_bundle_identity", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "bundle_schema_version > 0",
            name="ck_snapshot_inspection_bundles_schema_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["repository_snapshot_id"],
            ["repository_snapshots.repository_snapshot_id"],
            name="fk_snapshot_inspection_bundles_snapshot_id_snapshots",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "inspection_bundle_id",
            name="pk_repository_snapshot_inspection_bundles",
        ),
        sa.UniqueConstraint(
            "repository_snapshot_id",
            name="uq_snapshot_inspection_bundles_snapshot_id",
        ),
        sa.UniqueConstraint(
            "inspection_bundle_identity",
            name="uq_snapshot_inspection_bundles_identity",
        ),
    )
    op.create_table(
        "repository_snapshot_inspection_bundle_entries",
        sa.Column(
            "inspection_bundle_entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("inspection_bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_path", sa.Text(), nullable=False),
        sa.Column("original_repository_path", sa.Text(), nullable=True),
        sa.Column("entry_kind", sa.String(length=30), nullable=False),
        sa.Column("file_type", sa.String(length=30), nullable=False),
        sa.Column("file_mode", sa.String(length=20), nullable=False),
        sa.Column("head_blob_oid", sa.String(length=64), nullable=True),
        sa.Column("index_blob_oid", sa.String(length=64), nullable=True),
        sa.Column("content_ref", sa.String(length=300), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("stable_ordinal", sa.Integer(), nullable=False),
        sa.Column("entry_identity", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "entry_kind IN ('tracked_overlay', 'untracked_file', 'deletion', 'mode_change')",
            name="ck_snapshot_bundle_entries_kind",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name="ck_snapshot_bundle_entries_size_nonnegative",
        ),
        sa.CheckConstraint(
            "stable_ordinal >= 0",
            name="ck_snapshot_bundle_entries_ordinal_nonnegative",
        ),
        sa.CheckConstraint(
            "(entry_kind IN ('deletion', 'mode_change') AND "
            "content_ref IS NULL AND content_hash IS NULL) OR "
            "(entry_kind = 'tracked_overlay' AND file_type = 'submodule' AND "
            "content_ref IS NULL AND content_hash IS NULL) OR "
            "(entry_kind IN ('tracked_overlay', 'untracked_file') AND "
            "file_type IN ('regular', 'symlink') AND "
            "content_ref IS NOT NULL AND content_hash IS NOT NULL)",
            name="ck_snapshot_bundle_entries_content_semantics",
        ),
        sa.ForeignKeyConstraint(
            ["inspection_bundle_id"],
            ["repository_snapshot_inspection_bundles.inspection_bundle_id"],
            name="fk_snapshot_bundle_entries_bundle_id_bundles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "inspection_bundle_entry_id",
            name="pk_snapshot_inspection_bundle_entries",
        ),
        sa.UniqueConstraint(
            "inspection_bundle_id",
            "stable_ordinal",
            name="uq_snapshot_bundle_entries_bundle_ordinal",
        ),
        sa.UniqueConstraint(
            "inspection_bundle_id",
            "entry_identity",
            name="uq_snapshot_bundle_entries_bundle_identity",
        ),
    )
    op.create_table(
        "repository_snapshot_freshness",
        sa.Column("repository_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("current_probe_identity", sa.String(length=64), nullable=True),
        sa.Column("probe_version", sa.String(length=100), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('current', 'stale', 'unverifiable', 'invalid')",
            name="ck_repository_snapshot_freshness_status",
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name="ck_repository_snapshot_freshness_row_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["repository_snapshot_id"],
            ["repository_snapshots.repository_snapshot_id"],
            name="fk_snapshot_freshness_snapshot_id_snapshots",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "repository_snapshot_id",
            name="pk_repository_snapshot_freshness",
        ),
    )


def downgrade() -> None:
    op.drop_table("repository_snapshot_freshness")
    op.drop_table("repository_snapshot_inspection_bundle_entries")
    op.drop_table("repository_snapshot_inspection_bundles")
    op.drop_table("repository_snapshots")
