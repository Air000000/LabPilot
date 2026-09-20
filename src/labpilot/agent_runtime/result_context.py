from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from labpilot.experiments.proposal_types import ProposalPayload


class ResultTrialCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_id: UUID
    trial_ordinal: int = Field(gt=0)
    resolved_parameters: dict[str, JsonValue]
    seed: int


class ResultContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: UUID
    source_experiment_version_id: UUID
    source_version_number: int = Field(gt=0)
    source_proposal_revision_id: UUID
    source_batch_id: UUID
    source_batch_summary_revision_id: UUID
    source_summary_revision_number: int = Field(gt=0)
    presentation_status: Literal["complete", "complete_with_invalid_results"]
    source_proposal: ProposalPayload
    trials: tuple[ResultTrialCondition, ...]
    included_results: tuple[dict[str, JsonValue], ...]
    excluded_results: tuple[dict[str, JsonValue], ...]
    warnings: tuple[dict[str, JsonValue], ...]
    aggregates: dict[str, JsonValue]
