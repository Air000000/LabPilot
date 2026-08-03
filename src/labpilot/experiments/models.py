"""SQLAlchemy models owned by the experiments domain."""

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


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "primary_repository_registration_id"],
            [
                "repository_registrations.project_id",
                "repository_registrations.repository_registration_id",
            ],
            name="fk_projects_primary_repository_registration",
        ),
        Index(
            "uq_projects_primary_repository_registration",
            "primary_repository_registration_id",
            unique=True,
            postgresql_where=text("primary_repository_registration_id IS NOT NULL"),
        ),
        PrimaryKeyConstraint("project_id", name="pk_projects"),
        UniqueConstraint("project_identity", name="uq_project_identity"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_project_status",
        ),
        CheckConstraint(
            "row_version > 0",
            name="ck_project_row_version_positive",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    project_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    primary_repository_registration_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    artifact_root_ref: Mapped[str] = mapped_column(String(1000), nullable=False)
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
