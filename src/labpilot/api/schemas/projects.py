from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    artifact_root_ref: str


class ArchiveProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_row_version: int = Field(gt=0)


class ProjectCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    project_identity: str
    committed_state: str
    replayed: bool


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    project_id: UUID
    project_identity: str
    name: str
    description: str | None
    status: str
    primary_repository_registration_id: UUID | None
    artifact_root_ref: str
    created_at: datetime
    updated_at: datetime
    row_version: int
