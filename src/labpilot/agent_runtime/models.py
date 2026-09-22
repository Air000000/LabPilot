"""SQLAlchemy rows for bounded Agent runs and immutable steps."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from labpilot.db.base import Base


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name="fk_agent_runs_project_id_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["produced_proposal_revision_id"],
            ["experiment_proposal_revisions.proposal_revision_id"],
            name="fk_agent_runs_produced_proposal_revision_id_proposal_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_batch_summary_revision_id"],
            ["batch_summary_revisions.batch_summary_revision_id"],
            name="fk_agent_runs_source_summary_revision",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("agent_run_id", name="pk_agent_runs"),
        CheckConstraint(
            "status IN ('ready','running','awaiting_human','completed',"
            "'budget_exhausted','failed')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "egress_policy IN ('local_only','remote_provider_allowed')",
            name="ck_agent_runs_egress_policy",
        ),
        CheckConstraint("row_version > 0", name="ck_agent_runs_row_version_positive"),
    )

    agent_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    repository_snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    egress_policy: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_model_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_wall_clock_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_model_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    next_step_ordinal: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    produced_proposal_revision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    source_batch_summary_revision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class AgentStepRow(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.agent_run_id"],
            name="fk_agent_steps_agent_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.tool_call_id"],
            name="fk_agent_steps_tool_call_id_tool_calls",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("agent_step_id", name="pk_agent_steps"),
        UniqueConstraint("agent_run_id", "ordinal", name="uq_agent_steps_run_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_agent_steps_ordinal_positive"),
        CheckConstraint(
            "step_kind IN ('model_call','human_input')",
            name="ck_agent_steps_step_kind",
        ),
        CheckConstraint(
            "outcome IN ('reserved','succeeded','failed')",
            name="ck_agent_steps_outcome",
        ),
    )

    agent_step_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    agent_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    step_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    tool_call_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_toolset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    semantic_parameters_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    output_schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    token_usage_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    decision_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    human_input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
