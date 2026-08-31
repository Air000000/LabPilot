from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from labpilot.db.base import Base


class BatchRow(Base):
    __tablename__ = "batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_batches_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["experiment_version_id"],
            ["experiment_versions.experiment_version_id"],
            name="fk_batches_experiment_version_id_versions",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("batch_id", name="pk_batches"),
        CheckConstraint("row_version > 0", name="ck_batches_row_version_positive"),
    )

    batch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    experiment_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    start_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class TrialRow(Base):
    __tablename__ = "trials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id"],
            ["batches.batch_id"],
            name="fk_trials_batch_id_batches",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("trial_id", name="pk_trials"),
        UniqueConstraint(
            "batch_id", "trial_identity", name="uq_trials_batch_identity"
        ),
        UniqueConstraint(
            "batch_id", "trial_ordinal", name="uq_trials_batch_ordinal"
        ),
        CheckConstraint(
            "trial_ordinal > 0", name="ck_trials_ordinal_positive"
        ),
        CheckConstraint("seed >= 0", name="ck_trials_seed_nonnegative"),
    )

    trial_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trial_ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trial_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_parameters_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class AttemptRow(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trial_id"],
            ["trials.trial_id"],
            name="fk_attempts_trial_id_trials",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_attempts_active_trial",
            "trial_id",
            unique=True,
            postgresql_where=text("terminal_at IS NULL"),
        ),
        PrimaryKeyConstraint("attempt_id", name="pk_attempts"),
        UniqueConstraint(
            "supervisor_execution_identity", name="uq_attempts_supervisor_identity"
        ),
        UniqueConstraint(
            "trial_id", "attempt_number", name="uq_attempts_trial_number"
        ),
        CheckConstraint(
            "attempt_number > 0", name="ck_attempts_number_positive"
        ),
        CheckConstraint(
            "status IN ('preparing','launch_ready','preparation_failed',"
            "'cancelled_pre_launch','launch_pending','running','unknown',"
            "'identity_conflict','terminal')",
            name="ck_attempts_status",
        ),
        CheckConstraint(
            "(frozen_launch_spec_json IS NULL) = (execution_spec_hash IS NULL)",
            name="ck_attempts_physical_identity_pair",
        ),
        CheckConstraint(
            "status <> 'identity_conflict' OR terminal_at IS NULL",
            name="ck_attempts_physical_identity_conflict_nonterminal",
        ),
        CheckConstraint(
            "(observed_environment_json IS NULL) = "
            "(observed_environment_recorded_at IS NULL)",
            name="ck_attempts_physical_observed_environment_pair",
        ),
        CheckConstraint(
            "(status = 'terminal') = (terminal_kind IS NOT NULL)",
            name="ck_attempts_physical_terminal_kind",
        ),
        CheckConstraint(
            "(terminal_kind = 'process_exited' AND exit_code IS NOT NULL "
            "AND termination_signal IS NULL) OR "
            "(terminal_kind = 'process_signaled' AND exit_code IS NULL "
            "AND termination_signal IS NOT NULL) OR "
            "(terminal_kind IN ('process_terminal_unclassified','launch_not_accepted') "
            "AND exit_code IS NULL AND termination_signal IS NULL) OR "
            "terminal_kind IS NULL",
            name="ck_attempts_physical_terminal_causality",
        ),
    )

    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    trial_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    supervisor_execution_identity: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_execution_spec_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    frozen_execution_spec_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    frozen_launch_spec_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    execution_spec_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    launch_intent_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_environment_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    observed_environment_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    physical_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    physical_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    termination_signal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supervisor_result: Mapped[str | None] = mapped_column(String(100), nullable=True)
    terminal_kind: Mapped[str | None] = mapped_column(String(60), nullable=True)
    unknown_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attention_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_issue_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    supervisor_invocation_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    physical_boot_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_manifest_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    source_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    preparation_failure_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
