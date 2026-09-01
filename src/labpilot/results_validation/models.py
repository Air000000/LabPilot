from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from labpilot.db.base import Base


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_artifacts_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("artifact_id", name="pk_artifacts"),
        UniqueConstraint("attempt_id", "artifact_name", name="uq_artifacts_attempt_name"),
        UniqueConstraint("attempt_id", "relative_path", name="uq_artifacts_attempt_path"),
        CheckConstraint("byte_size >= 0", name="ck_artifacts_byte_size_nonnegative"),
    )

    artifact_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class MetricObservationRow(Base):
    __tablename__ = "metric_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.artifact_id"],
            name="fk_metric_observations_artifact_id_artifacts",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "metric_observation_id", name="pk_metric_observations"
        ),
        UniqueConstraint(
            "artifact_id",
            "metric_name",
            "extractor_version",
            name="uq_metric_observations_artifact_metric_extractor",
        ),
    )

    metric_observation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True
    )
    artifact_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    split: Mapped[str | None] = mapped_column(String(200), nullable=True)
    numeric_value: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
    )
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class ValidationReportRow(Base):
    __tablename__ = "validation_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_validation_reports_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "validation_report_id", name="pk_validation_reports"
        ),
        UniqueConstraint(
            "attempt_id",
            "validation_protocol_hash",
            "extraction_profile_version",
            "validator_version",
            name="uq_validation_reports_semantic_key",
        ),
        CheckConstraint(
            "verdict IN ('valid','invalid','inconclusive','error')",
            name="ck_validation_reports_verdict",
        ),
    )

    validation_report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True
    )
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    validation_protocol_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    extraction_profile_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    validator_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_snapshot_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    warning_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class TrialResultSelectionRow(Base):
    __tablename__ = "trial_result_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trial_id"],
            ["trials.trial_id"],
            name="fk_trial_result_selections_trial_id_trials",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.attempt_id"],
            name="fk_trial_result_selections_attempt_id_attempts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["validation_report_id"],
            ["validation_reports.validation_report_id"],
            name="fk_trial_result_selections_report_id_reports",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("trial_id", name="pk_trial_result_selections"),
        UniqueConstraint(
            "validation_report_id",
            name="uq_trial_result_selections_validation_report",
        ),
    )

    trial_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    validation_report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class BatchSummaryRevisionRow(Base):
    __tablename__ = "batch_summary_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id"],
            ["batches.batch_id"],
            name="fk_batch_summary_revisions_batch_id_batches",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "batch_summary_revision_id", name="pk_batch_summary_revisions"
        ),
        UniqueConstraint(
            "batch_id",
            "revision_number",
            name="uq_batch_summary_revisions_batch_number",
        ),
        CheckConstraint(
            "revision_number > 0", name="ck_batch_summary_revisions_number_positive"
        ),
    )

    batch_summary_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True
    )
    batch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary_protocol_version: Mapped[str] = mapped_column(String(100), nullable=False)
    included_result_refs_json: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False
    )
    excluded_reasons_json: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    warning_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    aggregates_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
