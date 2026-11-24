from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from labpilot.agent_runtime.result_history import HistoryContext
from labpilot.agent_runtime.source_binding import SourceBinding
from labpilot.agent_runtime.types import (
    AgentBudget,
    AgentRunDetails,
    AgentRunTraceDetails,
    AgentStepDetails,
    OutputFeedback,
    read_feedback,
)


class AgentBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(ge=1, le=20)
    max_model_calls: int = Field(ge=1, le=20)
    max_wall_clock_seconds: int = Field(ge=1, le=600)
    max_tokens: int = Field(ge=1, le=200_000)

    def to_domain(self) -> AgentBudget:
        return AgentBudget(**self.model_dump())


class CreateAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_patch: StrictBool = False
    repository_snapshot_id: UUID
    goal: str
    budget: AgentBudgetRequest


class CreateFollowUpAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_adoption_id: UUID | None = None
    source_patch: StrictBool = False

    source_batch_summary_revision_id: UUID
    repository_snapshot_id: UUID
    goal: str
    budget: AgentBudgetRequest


class AdvanceAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_row_version: int = Field(gt=0)


class HumanInputRequest(AdvanceAgentRunRequest):
    text: str


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: UUID
    project_id: UUID
    repository_snapshot_id: UUID
    status: str
    termination_reason: str | None
    produced_proposal_revision_id: UUID | None
    source_batch_summary_revision_id: UUID | None
    source_adoption_id: UUID | None = None
    source_patch_context: dict[str, JsonValue] | None = None
    created_at: datetime
    row_version: int

    @classmethod
    def from_details(cls, details: AgentRunDetails) -> "AgentRunResponse":
        return cls(
            agent_run_id=details.agent_run_id,
            project_id=details.project_id,
            repository_snapshot_id=details.repository_snapshot_id,
            status=details.status,
            termination_reason=details.termination_reason,
            produced_proposal_revision_id=details.produced_proposal_revision_id,
            source_batch_summary_revision_id=details.source_batch_summary_revision_id,
            source_adoption_id=details.source_adoption_id,
            source_patch_context=_patch_context(details),
            created_at=details.created_at,
            row_version=details.row_version,
        )


class AgentStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_step_id: UUID
    ordinal: int
    step_kind: str
    outcome: str
    tool_call_id: UUID | None
    provider_name: str | None
    model_name: str | None
    started_at: datetime
    completed_at: datetime | None
    decision_kind: str | None
    decision_json: dict[str, JsonValue] | None
    token_usage: dict[str, JsonValue] | None
    human_input_text: str | None
    output_feedback: OutputFeedback | None = None

    @classmethod
    def from_details(cls, details: AgentStepDetails) -> "AgentStepResponse":
        return cls(
            agent_step_id=details.agent_step_id,
            ordinal=details.ordinal,
            step_kind=details.step_kind,
            outcome=details.outcome,
            tool_call_id=details.tool_call_id,
            provider_name=details.provider_name,
            model_name=details.model_name,
            started_at=details.started_at,
            completed_at=details.completed_at,
            decision_kind=details.decision_kind,
            decision_json=(None if details.decision_json is None else dict(details.decision_json)),
            token_usage=None if details.token_usage is None else dict(details.token_usage),
            human_input_text=details.human_input_text,
            output_feedback=read_feedback(details.output_feedback),
        )


class AgentRunTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: UUID
    project_id: UUID
    repository_snapshot_id: UUID
    source_batch_summary_revision_id: UUID | None
    source_adoption_id: UUID | None = None
    source_patch_context: dict[str, JsonValue] | None = None
    goal: str
    status: str
    termination_reason: str | None
    provider_name: str
    model_name: str
    total_model_calls: int
    total_tokens: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    row_version: int
    steps: list[AgentStepResponse]
    history_context: HistoryContext | None = None
    source_binding: SourceBinding | None = None

    @classmethod
    def from_details(cls, details: AgentRunTraceDetails) -> "AgentRunTraceResponse":
        run = details.run
        return cls(
            agent_run_id=run.agent_run_id,
            project_id=run.project_id,
            repository_snapshot_id=run.repository_snapshot_id,
            source_batch_summary_revision_id=run.source_batch_summary_revision_id,
            source_adoption_id=run.source_adoption_id,
            source_patch_context=_patch_context(run),
            source_binding=details.source_binding,
            goal=run.goal,
            status=run.status,
            termination_reason=run.termination_reason,
            provider_name=run.provider_name,
            model_name=run.model_name,
            total_model_calls=run.total_model_calls,
            total_tokens=run.total_tokens,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            row_version=run.row_version,
            steps=[AgentStepResponse.from_details(step) for step in details.steps],
            history_context=(
                None
                if run.history_context is None
                else HistoryContext.model_validate(run.history_context)
            ),
        )


def _patch_context(run: AgentRunDetails) -> dict[str, JsonValue] | None:
    if run.source_patch_context is None:
        return None
    from labpilot.agent_runtime.source_patch import SourcePatchContext

    try:
        return SourcePatchContext.model_validate(dict(run.source_patch_context)).model_dump(
            mode="json"
        )
    except (ValueError, TypeError):
        # Preserve a readable failed Run without exposing invalid metadata or hiding its mode.
        return {"status": "invalid"}
