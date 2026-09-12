"""SQLAlchemy models for immutable experiment proposals and versions."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from labpilot.db.base import Base


class ExperimentRow(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_experiments_project_id_projects",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("experiment_id", name="pk_experiments"),
    )

    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )


class ProposalRevisionRow(Base):
    __tablename__ = "experiment_proposal_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name="fk_experiment_proposal_revisions_experiment_id_experiments",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_experiment_proposal_revisions_origin_agent_step",
            "origin_agent_step_id",
            unique=True,
            postgresql_where=text("origin_agent_step_id IS NOT NULL"),
        ),
        PrimaryKeyConstraint(
            "proposal_revision_id",
            name="pk_experiment_proposal_revisions",
        ),
        UniqueConstraint(
            "experiment_id",
            "revision_number",
            name="uq_experiment_proposal_revisions_experiment_number",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_experiment_proposal_revisions_number_positive",
        ),
    )

    proposal_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    origin_agent_step_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    repository_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    repository_snapshot_identity: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProposalVerificationRow(Base):
    __tablename__ = "experiment_proposal_verifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name=(
                "fk_proposal_verifications_revision_id_proposal_revisions"
            ),
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "proposal_verification_id",
            name="pk_experiment_proposal_verifications",
        ),
        UniqueConstraint(
            "proposal_revision_id",
            "verifier_profile",
            "proposal_payload_hash",
            name="uq_experiment_proposal_verifications_semantic_key",
        ),
    )

    proposal_verification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    proposal_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    verifier_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    proposal_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DesignDecisionRow(Base):
    __tablename__ = "experiment_design_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name="fk_experiment_design_decisions_revision_id_proposal_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["proposal_verification_id"],
            ["experiment_proposal_verifications.proposal_verification_id"],
            name="fk_experiment_design_decisions_verification_id_verifications",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "design_decision_id",
            name="pk_experiment_design_decisions",
        ),
        UniqueConstraint(
            "proposal_revision_id",
            name="uq_experiment_design_decisions_proposal_revision",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_experiment_design_decisions_decision",
        ),
        CheckConstraint(
            "decision = 'rejected' OR proposal_verification_id IS NOT NULL",
            name="ck_experiment_design_decisions_approval_verification",
        ),
    )

    design_decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    proposal_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    proposal_verification_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    proposal_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    repository_snapshot_identity: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    dataset_reference_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_expectation_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExperimentVersionRow(Base):
    __tablename__ = "experiment_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.experiment_id"],
            name="fk_experiment_versions_experiment_id_experiments",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["design_decision_id"],
            ["experiment_design_decisions.design_decision_id"],
            name="fk_experiment_versions_decision_id_design_decisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name="fk_experiment_versions_revision_id_proposal_revisions",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "experiment_version_id",
            name="pk_experiment_versions",
        ),
        UniqueConstraint(
            "design_decision_id",
            name="uq_experiment_versions_design_decision",
        ),
        UniqueConstraint(
            "experiment_id",
            "version_number",
            name="uq_experiment_versions_experiment_number",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_experiment_versions_number_positive",
        ),
    )

    experiment_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    design_decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    experiment_version_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    proposal_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    repository_snapshot_identity: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    dataset_reference_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    dataset_reference_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_expectation_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    environment_expectation_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    validation_protocol_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    validation_protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
