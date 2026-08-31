from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from labpilot.db.base import Base


class CommandReceiptRow(Base):
    __tablename__ = "command_receipts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_scope_identity",
            "command_name",
            "command_version",
            "idempotency_key",
            name="uq_command_receipt_idempotency_key",
        ),
        CheckConstraint(
            "command_version > 0",
            name="ck_command_receipt_version_positive",
        ),
        CheckConstraint(
            "result_envelope_schema_version > 0",
            name="ck_command_receipt_envelope_version_positive",
        ),
    )

    command_receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    idempotency_scope_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    command_name: Mapped[str] = mapped_column(String(120), nullable=False)
    command_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    primary_result_type: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_result_id: Mapped[str] = mapped_column(String(200), nullable=False)
    result_envelope_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    result_envelope_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    result_envelope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
