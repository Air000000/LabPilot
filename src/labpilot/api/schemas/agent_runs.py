from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from labpilot.agent_runtime.types import AgentBudget, AgentRunDetails


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
            created_at=details.created_at,
            row_version=details.row_version,
        )
