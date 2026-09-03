from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArtifactResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    artifact_id: UUID
    artifact_name: str
    relative_path: str
    content_sha256: str
    byte_size: int
    media_type: str
    availability: str


class MetricObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    metric_observation_id: UUID
    artifact_id: UUID
    metric_name: str
    split: str | None
    numeric_value: str
    extractor_version: str


class ValidationReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    validation_report_id: UUID
    validation_protocol_hash: str
    extraction_profile_version: str
    validator_version: str
    verdict: str
    reasons: list[object]
    warnings: list[object]


class AttemptResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    attempt_id: UUID
    artifacts: tuple[ArtifactResultResponse, ...]
    metrics: tuple[MetricObservationResponse, ...]
    validation_reports: tuple[ValidationReportResponse, ...]


class TrialResultSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    attempt_id: UUID
    validation_report_id: UUID


class TrialResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    trial_id: UUID
    selection: TrialResultSelectionResponse | None


class BatchSummaryRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    revision_id: UUID
    revision_number: int
    presentation_status: str
    summary_protocol_version: str
    included: list[object]
    excluded: list[object]
    warnings: list[object]
    aggregates: dict[str, object]


class BatchSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    batch_id: UUID
    latest_summary: BatchSummaryRevisionResponse | None
