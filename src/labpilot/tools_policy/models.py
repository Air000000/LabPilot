"""SQLAlchemy rows for persisted Tool calls and exact approvals."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from labpilot.db.base import Base


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_tool_calls_project_id_projects",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("tool_call_id", name="pk_tool_calls"),
        CheckConstraint(
            "risk_level IN ('green','yellow','red')",
            name="ck_tool_calls_risk_level",
        ),
        CheckConstraint(
            "side_effect_class IN ('read_only','side_effecting')",
            name="ck_tool_calls_side_effect_class",
        ),
        CheckConstraint(
            "status IN ('requested','executed','failed','denied')",
            name="ck_tool_calls_status",
        ),
    )

    tool_call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    side_effect_class: Mapped[str] = mapped_column(String(20), nullable=False)
    arguments_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolApprovalRow(Base):
    __tablename__ = "tool_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.tool_call_id"],
            name="fk_tool_approvals_tool_call_id_tool_calls",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("tool_approval_id", name="pk_tool_approvals"),
        UniqueConstraint("tool_call_id", name="uq_tool_approvals_tool_call_id"),
        CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_tool_approvals_decision",
        ),
    )

    tool_approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tool_call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
