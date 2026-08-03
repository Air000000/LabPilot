"""SQLAlchemy models owned by the context domain."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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
