from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BatchControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_row_version: int = Field(gt=0)


class BatchCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    committed_state: str
    replayed: bool


class BatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    batch_id: UUID
    project_id: UUID
    experiment_version_id: UUID
    start_requested_at: datetime | None
    cancel_requested_at: datetime | None
    row_version: int
    created_at: datetime
    presentation_status: str
    trial_count: int
    trial_ids: tuple[UUID, ...]


class TrialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    trial_id: UUID
    batch_id: UUID
    trial_ordinal: int
    trial_identity: str
    resolved_parameters: dict[str, object]
    seed: int
    created_at: datetime
    presentation_status: str
    attempt_count: int
    attempt_ids: tuple[UUID, ...]


class AttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    attempt_id: UUID
    trial_id: UUID
    batch_id: UUID
    attempt_number: int
    status: str
    supervisor_execution_identity: str
    created_at: datetime
    prepared_at: datetime | None
    launch_intent_committed_at: datetime | None
    physical_started_at: datetime | None
    physical_ended_at: datetime | None
    terminal_at: datetime | None
    terminal_kind: str | None
    exit_code: int | None
    termination_signal: int | None
    supervisor_result: str | None
    unknown_since: datetime | None
    attention_required_at: datetime | None
    execution_issue_code: str | None
    observed_environment: dict[str, object] | None
