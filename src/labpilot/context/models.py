"""SQLAlchemy models owned by the context domain."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from labpilot.db.base import Base


class RepositoryRegistrationRow(Base):
    __tablename__ = "repository_registrations"
    __table_args__ = (
        PrimaryKeyConstraint(
            "repository_registration_id",
            name="pk_repository_registrations",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_repository_registrations_project_id_projects",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "repository_registration_id",
            name="uq_repository_registrations_project_registration",
        ),
        CheckConstraint(
            "repository_type = 'local_git'",
            name="ck_repository_registrations_repository_type",
        ),
        CheckConstraint(
            "status IN ('active', 'unavailable', 'invalid_git_repository', 'revoked')",
            name="ck_repository_registrations_status",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_repository_registrations_row_version_positive",
        ),
        Index(
            "uq_repository_registration_live_root",
            "project_id",
            "root_path_identity",
            unique=True,
            postgresql_where=text("status <> 'revoked'"),
        ),
    )

    repository_registration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    repository_type: Mapped[str] = mapped_column(String(30), nullable=False)
    canonical_root_path: Mapped[str] = mapped_column(Text, nullable=False)
    root_path_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_origin_sanitized: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_branch: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    last_access_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )


class RepositorySnapshotRow(Base):
    __tablename__ = "repository_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint(
            "repository_snapshot_id",
            name="pk_repository_snapshots",
        ),
        ForeignKeyConstraint(
            ["repository_registration_id"],
            ["repository_registrations.repository_registration_id"],
            name="fk_repository_snapshots_registration_id_registrations",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "repository_registration_id",
            "snapshot_sequence",
            name="uq_repository_snapshots_registration_sequence",
        ),
        UniqueConstraint(
            "repository_registration_id",
            "repository_snapshot_identity",
            name="uq_repository_snapshots_registration_identity",
        ),
        CheckConstraint(
            "inspector_compatibility_version > 0",
            name="ck_repository_snapshots_inspector_version_positive",
        ),
        CheckConstraint(
            "NOT is_clean OR ("
            "dirty_worktree_manifest_ref IS NULL AND "
            "dirty_worktree_manifest_hash IS NULL AND "
            "untracked_manifest_ref IS NULL AND "
            "untracked_manifest_hash IS NULL)",
            name="ck_repository_snapshots_clean_no_manifests",
        ),
    )

    repository_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    repository_registration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    snapshot_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canonical_root_path: Mapped[str] = mapped_column(Text, nullable=False)
    head_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    current_branch: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_detached_head: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_clean: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tracked_tree_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    dirty_worktree_manifest_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    dirty_worktree_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    untracked_manifest_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    untracked_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repository_state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_snapshot_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    submodule_presence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lfs_pointer_presence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inspector_compatibility_version: Mapped[int] = mapped_column(nullable=False)


class RepositorySnapshotInspectionBundleRow(Base):
    __tablename__ = "repository_snapshot_inspection_bundles"
    __table_args__ = (
        PrimaryKeyConstraint(
            "inspection_bundle_id",
            name="pk_repository_snapshot_inspection_bundles",
        ),
        ForeignKeyConstraint(
            ["repository_snapshot_id"],
            ["repository_snapshots.repository_snapshot_id"],
            name="fk_snapshot_inspection_bundles_snapshot_id_snapshots",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "repository_snapshot_id",
            name="uq_snapshot_inspection_bundles_snapshot_id",
        ),
        UniqueConstraint(
            "inspection_bundle_identity",
            name="uq_snapshot_inspection_bundles_identity",
        ),
        CheckConstraint(
            "bundle_schema_version > 0",
            name="ck_snapshot_inspection_bundles_schema_version_positive",
        ),
    )

    inspection_bundle_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    repository_snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    base_commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_schema_version: Mapped[int] = mapped_column(nullable=False)
    capture_tool_version: Mapped[str] = mapped_column(String(100), nullable=False)
    entries_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    inspection_bundle_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RepositorySnapshotInspectionBundleEntryRow(Base):
    __tablename__ = "repository_snapshot_inspection_bundle_entries"
    __table_args__ = (
        PrimaryKeyConstraint(
            "inspection_bundle_entry_id",
            name="pk_snapshot_inspection_bundle_entries",
        ),
        ForeignKeyConstraint(
            ["inspection_bundle_id"],
            ["repository_snapshot_inspection_bundles.inspection_bundle_id"],
            name="fk_snapshot_bundle_entries_bundle_id_bundles",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "inspection_bundle_id",
            "stable_ordinal",
            name="uq_snapshot_bundle_entries_bundle_ordinal",
        ),
        UniqueConstraint(
            "inspection_bundle_id",
            "entry_identity",
            name="uq_snapshot_bundle_entries_bundle_identity",
        ),
        CheckConstraint(
            "entry_kind IN ('tracked_overlay', 'untracked_file', 'deletion', 'mode_change')",
            name="ck_snapshot_bundle_entries_kind",
        ),
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_snapshot_bundle_entries_size_nonnegative",
        ),
        CheckConstraint(
            "stable_ordinal >= 0",
            name="ck_snapshot_bundle_entries_ordinal_nonnegative",
        ),
        CheckConstraint(
            "(entry_kind IN ('deletion', 'mode_change') AND "
            "content_ref IS NULL AND content_hash IS NULL) OR "
            "(entry_kind IN ('tracked_overlay', 'untracked_file') AND "
            "content_ref IS NOT NULL AND content_hash IS NOT NULL)",
            name="ck_snapshot_bundle_entries_content_semantics",
        ),
    )

    inspection_bundle_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    inspection_bundle_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    repository_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_repository_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    file_type: Mapped[str] = mapped_column(String(30), nullable=False)
    file_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    head_blob_oid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_blob_oid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stable_ordinal: Mapped[int] = mapped_column(nullable=False)
    entry_identity: Mapped[str] = mapped_column(String(64), nullable=False)


class RepositorySnapshotFreshnessRow(Base):
    __tablename__ = "repository_snapshot_freshness"
    __table_args__ = (
        PrimaryKeyConstraint(
            "repository_snapshot_id",
            name="pk_repository_snapshot_freshness",
        ),
        ForeignKeyConstraint(
            ["repository_snapshot_id"],
            ["repository_snapshots.repository_snapshot_id"],
            name="fk_snapshot_freshness_snapshot_id_snapshots",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('current', 'stale', 'unverifiable', 'invalid')",
            name="ck_repository_snapshot_freshness_status",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_repository_snapshot_freshness_row_version_positive",
        ),
    )

    repository_snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_probe_identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probe_version: Mapped[str] = mapped_column(String(100), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
