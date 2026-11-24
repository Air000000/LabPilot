"""Explicit roles for one human-adopted source transition; no execution authority."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Ref = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
MAX_SOURCE_BINDING_BYTES = 48 * 1024
MAX_SOURCE_DIFF_BYTES = 32 * 1024


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceIdentity(_Record):
    repository_snapshot_id: UUID
    repository_snapshot_identity: Digest


class SourceChangeEvidence(_Record):
    diff_ref: Ref
    report_ref: Ref
    rationale: str
    diff: str
    static_report: dict[str, JsonValue]


class SourceBinding(_Record):
    source_adoption_id: UUID
    adoption_identity: Digest
    project_id: UUID
    experiment_id: UUID
    source_batch_summary_revision_id: UUID
    result_source: SourceIdentity
    proposal_target: SourceIdentity
    package_ref: Ref
    evidence: SourceChangeEvidence | None = None
