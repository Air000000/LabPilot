from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from labpilot.common.canonical_json import CanonicalValue
from labpilot.common.hashing import namespaced_sha256

type ParameterValue = str | int | float | bool | None
type ExperimentType = Literal[
    "reproduction",
    "comparison",
    "ablation",
    "parameter_sweep",
    "stability",
]
type ReproducibilityLevel = Literal["strict", "best_effort"]
type DatasetReferenceType = Literal["local_path", "uri"]
type ValidationRuleKind = Literal[
    "artifact_exists",
    "metric_finite",
    "metric_gte",
    "metric_lte",
]

_PROPOSAL_PAYLOAD_NAMESPACE = "labpilot-proposal-payload:v1"
_DATASET_REFERENCE_NAMESPACE = "labpilot-dataset-reference:v1"
_ENVIRONMENT_EXPECTATION_NAMESPACE = "labpilot-environment-expectation:v1"
_VALIDATION_PROTOCOL_NAMESPACE = "labpilot-validation-protocol:v1"
_EXPERIMENT_VERSION_NAMESPACE = "labpilot-experiment-version:v1"


def _require_safe_relative_path(value: str, *, field: str, allow_root: bool) -> None:
    if allow_root and value == ".":
        return
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{field} must be a safe repository-relative path")


@dataclass(frozen=True, slots=True)
class DatasetReference:
    reference_type: DatasetReferenceType
    path_or_uri: str
    stable_identity: str | None
    reproducibility_level: ReproducibilityLevel

    def __post_init__(self) -> None:
        if self.reproducibility_level == "strict" and not self.stable_identity:
            raise ValueError("strict DatasetReference requires stable_identity")


@dataclass(frozen=True, slots=True)
class EnvironmentExpectation:
    python_profile: str
    framework_requirements: tuple[str, ...]
    device_requirement: str | None
    cuda_requirement: str | None


@dataclass(frozen=True, slots=True)
class ArtifactDeclaration:
    name: str
    relative_path: str
    required: bool

    def __post_init__(self) -> None:
        _require_safe_relative_path(
            self.relative_path,
            field="relative_path",
            allow_root=False,
        )


@dataclass(frozen=True, slots=True)
class MetricDeclaration:
    name: str
    source_artifact: str
    split: str | None


@dataclass(frozen=True, slots=True)
class ValidationRule:
    kind: ValidationRuleKind
    subject: str
    threshold: Decimal | None = None

    def __post_init__(self) -> None:
        if self.kind in {"artifact_exists", "metric_finite"}:
            if self.threshold is not None:
                raise ValueError("threshold is only valid for metric_gte and metric_lte")
        elif self.kind in {"metric_gte", "metric_lte"}:
            if type(self.threshold) is not Decimal or not self.threshold.is_finite():
                raise ValueError("threshold must be a finite Decimal")
        else:
            raise ValueError("unknown validation rule kind")


@dataclass(frozen=True, slots=True)
class ProposalPayload:
    goal: str
    experiment_type: ExperimentType
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    train_entrypoint: str
    eval_entrypoint: str | None
    workdir: str
    parameter_matrix: Mapping[str, tuple[ParameterValue, ...]]
    seeds: tuple[int, ...]
    dataset_reference: DatasetReference
    environment_expectation: EnvironmentExpectation
    metrics: tuple[MetricDeclaration, ...]
    artifacts: tuple[ArtifactDeclaration, ...]
    validation_rules: tuple[ValidationRule, ...]
    timeout_seconds: int
    max_retries: int
    train_argv_template: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameter_matrix",
            MappingProxyType(
                {key: tuple(values) for key, values in self.parameter_matrix.items()}
            ),
        )
        _require_safe_relative_path(self.workdir, field="workdir", allow_root=True)


def _parameter_value(value: ParameterValue) -> CanonicalValue:
    if value is None or type(value) in {str, int, bool}:
        return value  # type: ignore[return-value]
    if type(value) is float and isfinite(value):
        return Decimal(str(value))
    raise ValueError("parameter values must be finite scalar values")


def _dataset_reference_value(value: DatasetReference) -> dict[str, CanonicalValue]:
    return {
        "reference_type": value.reference_type,
        "path_or_uri": value.path_or_uri,
        "stable_identity": value.stable_identity,
        "reproducibility_level": value.reproducibility_level,
    }


def _environment_expectation_value(
    value: EnvironmentExpectation,
) -> dict[str, CanonicalValue]:
    return {
        "python_profile": value.python_profile,
        "framework_requirements": list(value.framework_requirements),
        "device_requirement": value.device_requirement,
        "cuda_requirement": value.cuda_requirement,
    }


def _validation_protocol_value(
    rules: tuple[ValidationRule, ...],
) -> list[CanonicalValue]:
    values: list[CanonicalValue] = []
    for rule in rules:
        value: dict[str, CanonicalValue] = {"kind": rule.kind, "subject": rule.subject}
        if rule.threshold is not None:
            value["threshold"] = rule.threshold
        values.append(value)
    return values


def _proposal_payload_value(payload: ProposalPayload) -> dict[str, CanonicalValue]:
    value: dict[str, CanonicalValue] = {
        "goal": payload.goal,
        "experiment_type": payload.experiment_type,
        "repository_snapshot_id": str(payload.repository_snapshot_id),
        "repository_snapshot_identity": payload.repository_snapshot_identity,
        "train_entrypoint": payload.train_entrypoint,
        "eval_entrypoint": payload.eval_entrypoint,
        "workdir": payload.workdir,
        "parameter_matrix": {
            key: [_parameter_value(value) for value in values]
            for key, values in payload.parameter_matrix.items()
        },
        "seeds": list(payload.seeds),
        "dataset_reference": _dataset_reference_value(payload.dataset_reference),
        "environment_expectation": _environment_expectation_value(
            payload.environment_expectation
        ),
        "metrics": [
            {
                "name": metric.name,
                "source_artifact": metric.source_artifact,
                "split": metric.split,
            }
            for metric in payload.metrics
        ],
        "artifacts": [
            {
                "name": artifact.name,
                "relative_path": artifact.relative_path,
                "required": artifact.required,
            }
            for artifact in payload.artifacts
        ],
        "validation_rules": _validation_protocol_value(payload.validation_rules),
        "timeout_seconds": payload.timeout_seconds,
        "max_retries": payload.max_retries,
    }
    if payload.train_argv_template is not None:
        value["train_argv_template"] = list(payload.train_argv_template)
    return value


def proposal_payload_hash(payload: ProposalPayload) -> str:
    return namespaced_sha256(_PROPOSAL_PAYLOAD_NAMESPACE, _proposal_payload_value(payload))


def dataset_reference_hash(value: DatasetReference) -> str:
    return namespaced_sha256(
        _DATASET_REFERENCE_NAMESPACE,
        _dataset_reference_value(value),
    )


def environment_expectation_hash(value: EnvironmentExpectation) -> str:
    return namespaced_sha256(
        _ENVIRONMENT_EXPECTATION_NAMESPACE,
        _environment_expectation_value(value),
    )


def validation_protocol_hash(rules: tuple[ValidationRule, ...]) -> str:
    return namespaced_sha256(
        _VALIDATION_PROTOCOL_NAMESPACE,
        _validation_protocol_value(rules),
    )


def experiment_version_identity(
    *,
    experiment_id: UUID,
    proposal_payload_hash_value: str,
    repository_snapshot_identity: str,
    dataset_reference_hash_value: str,
    environment_expectation_hash_value: str,
    validation_protocol_hash_value: str,
) -> str:
    return namespaced_sha256(
        _EXPERIMENT_VERSION_NAMESPACE,
        {
            "experiment_id": str(experiment_id),
            "proposal_payload_hash": proposal_payload_hash_value,
            "repository_snapshot_identity": repository_snapshot_identity,
            "dataset_reference_hash": dataset_reference_hash_value,
            "environment_expectation_hash": environment_expectation_hash_value,
            "validation_protocol_hash": validation_protocol_hash_value,
        },
    )
