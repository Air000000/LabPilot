from datetime import datetime
from math import isfinite
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from labpilot.experiments.proposal_service import (
    DesignDecisionDetails,
    ExperimentVersionDetails,
    ProposalRevisionDetails,
    ProposalVerificationDetails,
)
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
)
from labpilot.experiments.proposal_verifier import ProposalVerificationResult


class DatasetReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_type: Literal["local_path", "uri"]
    path_or_uri: str
    stable_identity: str | None
    reproducibility_level: Literal["strict", "best_effort"]


class EnvironmentExpectationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python_profile: str
    framework_requirements: list[str]
    device_requirement: str | None
    cuda_requirement: str | None


class MetricDeclarationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source_artifact: str
    split: str | None


class ArtifactDeclarationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    relative_path: str
    required: bool


class ValidationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["artifact_exists", "metric_finite"]
    subject: str


class ProposalPayloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    experiment_type: Literal[
        "reproduction",
        "comparison",
        "ablation",
        "parameter_sweep",
        "stability",
    ]
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    train_entrypoint: str
    eval_entrypoint: str | None
    workdir: str
    parameter_matrix: dict[str, list[str | int | float | bool | None]]
    seeds: list[int]
    dataset_reference: DatasetReferenceRequest
    environment_expectation: EnvironmentExpectationRequest
    metrics: list[MetricDeclarationRequest]
    artifacts: list[ArtifactDeclarationRequest]
    validation_rules: list[ValidationRuleRequest]
    timeout_seconds: int
    max_retries: int

    @field_validator("parameter_matrix")
    @classmethod
    def require_finite_parameter_values(
        cls,
        value: dict[str, list[str | int | float | bool | None]],
    ) -> dict[str, list[str | int | float | bool | None]]:
        if any(
            type(item) is float and not isfinite(item)
            for values in value.values()
            for item in values
        ):
            raise ValueError("parameter values must be finite")
        return value

    def to_domain(self) -> ProposalPayload:
        return ProposalPayload(
            goal=self.goal,
            experiment_type=self.experiment_type,
            repository_snapshot_id=self.repository_snapshot_id,
            repository_snapshot_identity=self.repository_snapshot_identity,
            train_entrypoint=self.train_entrypoint,
            eval_entrypoint=self.eval_entrypoint,
            workdir=self.workdir,
            parameter_matrix={key: tuple(values) for key, values in self.parameter_matrix.items()},
            seeds=tuple(self.seeds),
            dataset_reference=DatasetReference(
                reference_type=self.dataset_reference.reference_type,
                path_or_uri=self.dataset_reference.path_or_uri,
                stable_identity=self.dataset_reference.stable_identity,
                reproducibility_level=self.dataset_reference.reproducibility_level,
            ),
            environment_expectation=EnvironmentExpectation(
                python_profile=self.environment_expectation.python_profile,
                framework_requirements=tuple(self.environment_expectation.framework_requirements),
                device_requirement=self.environment_expectation.device_requirement,
                cuda_requirement=self.environment_expectation.cuda_requirement,
            ),
            metrics=tuple(
                MetricDeclaration(item.name, item.source_artifact, item.split)
                for item in self.metrics
            ),
            artifacts=tuple(
                ArtifactDeclaration(item.name, item.relative_path, item.required)
                for item in self.artifacts
            ),
            validation_rules=tuple(
                ValidationRule(item.kind, item.subject) for item in self.validation_rules
            ),
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
        )


class CreateProposalRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: ProposalPayloadRequest


class ApproveProposalRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_verification_id: UUID


class RejectProposalRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    proposal_verification_id: UUID | None = None


class ProposalRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_revision_id: UUID
    experiment_id: UUID
    revision_number: int
    created_at: datetime

    @classmethod
    def from_details(cls, details: ProposalRevisionDetails) -> "ProposalRevisionResponse":
        return cls(
            proposal_revision_id=details.proposal_revision_id,
            experiment_id=details.experiment_id,
            revision_number=details.revision_number,
            created_at=details.created_at,
        )


class VerificationFindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["blocking", "warning"]
    field: str | None
    message: str


class VerificationResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    passed: bool
    findings: list[VerificationFindingResponse]

    @classmethod
    def from_result(cls, result: ProposalVerificationResult) -> "VerificationResultResponse":
        return cls(
            profile=result.profile,
            passed=result.passed,
            findings=[
                VerificationFindingResponse(
                    code=finding.code,
                    severity=finding.severity,
                    field=finding.field,
                    message=finding.message,
                )
                for finding in result.findings
            ],
        )


class ProposalVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_verification_id: UUID
    proposal_revision_id: UUID
    result: VerificationResultResponse
    created_at: datetime

    @classmethod
    def from_details(cls, details: ProposalVerificationDetails) -> "ProposalVerificationResponse":
        return cls(
            proposal_verification_id=details.proposal_verification_id,
            proposal_revision_id=details.proposal_revision_id,
            result=VerificationResultResponse.from_result(details.result),
            created_at=details.created_at,
        )


class DesignDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design_decision_id: UUID
    proposal_revision_id: UUID
    decision: str
    reason: str | None
    decided_at: datetime

    @classmethod
    def from_details(cls, details: DesignDecisionDetails) -> "DesignDecisionResponse":
        return cls(
            design_decision_id=details.design_decision_id,
            proposal_revision_id=details.proposal_revision_id,
            decision=details.decision,
            reason=details.reason,
            decided_at=details.decided_at,
        )


class ExperimentVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_version_id: UUID
    experiment_id: UUID
    version_number: int
    proposal_revision_id: UUID
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    created_at: datetime

    @classmethod
    def from_details(cls, details: ExperimentVersionDetails) -> "ExperimentVersionResponse":
        return cls(
            experiment_version_id=details.experiment_version_id,
            experiment_id=details.experiment_id,
            version_number=details.version_number,
            proposal_revision_id=details.proposal_revision_id,
            repository_snapshot_id=details.repository_snapshot_id,
            repository_snapshot_identity=details.repository_snapshot_identity,
            created_at=details.created_at,
        )
