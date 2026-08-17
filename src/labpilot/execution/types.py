from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from pathlib import Path
from uuid import UUID

from labpilot.common.canonical_json import CanonicalValue
from labpilot.common.hashing import namespaced_sha256

type ParameterValue = str | int | float | bool | None

_TRIAL_NAMESPACE = "labpilot-trial:v1"
_INSTALLATION_NAMESPACE = "labpilot-supervisor-installation:v1"
_SOURCE_MANIFEST_NAMESPACE = "labpilot-source-manifest:v1"
_FROZEN_EXECUTION_SPEC_NAMESPACE = "labpilot-frozen-execution-spec:v1"


class AttemptStatus(StrEnum):
    PREPARING = "preparing"
    LAUNCH_READY = "launch_ready"
    PREPARATION_FAILED = "preparation_failed"
    CANCELLED_PRE_LAUNCH = "cancelled_pre_launch"
    LAUNCH_PENDING = "launch_pending"
    RUNNING = "running"
    UNKNOWN = "unknown"
    IDENTITY_CONFLICT = "identity_conflict"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class VersionExecutionSemantics:
    project_id: UUID
    experiment_version_id: UUID
    experiment_version_identity: str
    proposal_revision_id: UUID
    proposal_payload_hash: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    train_entrypoint: str
    workdir: str
    train_argv_template: tuple[str, ...]
    parameter_matrix: dict[str, tuple[ParameterValue, ...]]
    seeds: tuple[int, ...]
    dataset_reference_type: str
    dataset_path_or_uri: str
    dataset_reference_hash: str
    environment_expectation_hash: str
    python_profile: str
    framework_requirements: tuple[str, ...]
    device_requirement: str | None
    cuda_requirement: str | None
    artifact_relative_paths: tuple[str, ...]
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class SourceManifestEntry:
    relative_path: str
    file_type: str
    file_mode: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SourceManifest:
    schema_version: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    entries: tuple[SourceManifestEntry, ...]


@dataclass(frozen=True, slots=True)
class FrozenExecutionSpec:
    schema_version: str
    experiment_version_id: UUID
    experiment_version_identity: str
    trial_identity: str
    train_entrypoint_relative_path: str
    workdir_relative_path: str
    python_profile: str
    argv: tuple[str, ...]
    output_dir_arg_indexes: tuple[int, ...]
    dataset_reference_type: str
    dataset_argument: str
    dataset_reference_hash: str
    environment_expectation_hash: str
    framework_requirements: tuple[str, ...]
    framework_distribution_names: tuple[str, ...]
    device_requirement: str | None
    cuda_requirement: str | None
    timeout_seconds: int

    def __post_init__(self) -> None:
        indexes = self.output_dir_arg_indexes
        if (
            any(
                type(index) is not int or index < 0 or index >= len(self.argv)
                for index in indexes
            )
            or indexes != tuple(sorted(set(indexes)))
        ):
            raise ValueError("output_dir_arg_indexes must be unique ascending argv indexes")


def _parameter_value(value: ParameterValue) -> CanonicalValue:
    if value is None or type(value) in {str, int, bool}:
        return value  # type: ignore[return-value]
    if type(value) is float and isfinite(value):
        return Decimal(str(value))
    raise ValueError("resolved parameters must contain finite scalar values")


def trial_identity(
    *,
    experiment_version_identity: str,
    resolved_parameters: dict[str, ParameterValue],
    seed: int,
) -> str:
    return namespaced_sha256(
        _TRIAL_NAMESPACE,
        {
            "experiment_version_identity": experiment_version_identity,
            "resolved_parameters": {
                key: _parameter_value(value)
                for key, value in resolved_parameters.items()
            },
            "seed": seed,
        },
    )


def installation_digest(installation_identity: str) -> str:
    return namespaced_sha256(
        _INSTALLATION_NAMESPACE,
        {"installation_identity": installation_identity},
    )


def supervisor_execution_identity(
    *,
    installation_identity: str,
    attempt_id: UUID,
) -> str:
    return f"labpilot-v1-{installation_digest(installation_identity)}-{attempt_id.hex}"


def attempt_workspace_path(attempt_workspace_root: Path, attempt_id: UUID) -> str:
    if not attempt_workspace_root.is_absolute():
        raise ValueError("attempt_workspace_root must be absolute")
    return str(attempt_workspace_root / attempt_id.hex)


def source_manifest_value(manifest: SourceManifest) -> dict[str, CanonicalValue]:
    return {
        "schema_version": manifest.schema_version,
        "repository_snapshot_id": str(manifest.repository_snapshot_id),
        "repository_snapshot_identity": manifest.repository_snapshot_identity,
        "entries": [
            {
                "relative_path": entry.relative_path,
                "file_type": entry.file_type,
                "file_mode": entry.file_mode,
                "content_sha256": entry.content_sha256,
            }
            for entry in manifest.entries
        ],
    }


def source_manifest_hash(manifest: SourceManifest) -> str:
    return namespaced_sha256(_SOURCE_MANIFEST_NAMESPACE, source_manifest_value(manifest))


def frozen_execution_spec_value(
    spec: FrozenExecutionSpec,
) -> dict[str, CanonicalValue]:
    return {
        "schema_version": spec.schema_version,
        "experiment_version_id": str(spec.experiment_version_id),
        "experiment_version_identity": spec.experiment_version_identity,
        "trial_identity": spec.trial_identity,
        "train_entrypoint_relative_path": spec.train_entrypoint_relative_path,
        "workdir_relative_path": spec.workdir_relative_path,
        "python_profile": spec.python_profile,
        "argv": list(spec.argv),
        "output_dir_arg_indexes": list(spec.output_dir_arg_indexes),
        "dataset_reference_type": spec.dataset_reference_type,
        "dataset_argument": spec.dataset_argument,
        "dataset_reference_hash": spec.dataset_reference_hash,
        "environment_expectation_hash": spec.environment_expectation_hash,
        "framework_requirements": list(spec.framework_requirements),
        "framework_distribution_names": list(spec.framework_distribution_names),
        "device_requirement": spec.device_requirement,
        "cuda_requirement": spec.cuda_requirement,
        "timeout_seconds": spec.timeout_seconds,
    }


def frozen_execution_spec_hash(spec: FrozenExecutionSpec) -> str:
    return namespaced_sha256(
        _FROZEN_EXECUTION_SPEC_NAMESPACE,
        frozen_execution_spec_value(spec),
    )
