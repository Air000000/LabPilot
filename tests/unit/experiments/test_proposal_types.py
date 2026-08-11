from dataclasses import replace
from inspect import signature
from uuid import UUID

import pytest

from labpilot.experiments import proposal_service
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
    dataset_reference_hash,
    environment_expectation_hash,
    experiment_version_identity,
    proposal_payload_hash,
    validation_protocol_hash,
)

EXPERIMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
SNAPSHOT_ID = UUID("22222222-2222-2222-2222-222222222222")
SNAPSHOT_IDENTITY = "a" * 64


def _dataset(*, stable_identity: str | None = "dataset:v1") -> DatasetReference:
    return DatasetReference(
        reference_type="uri",
        path_or_uri="s3://datasets/example",
        stable_identity=stable_identity,
        reproducibility_level="strict" if stable_identity else "best_effort",
    )


def _environment(*, python_profile: str = "python-3.12") -> EnvironmentExpectation:
    return EnvironmentExpectation(
        python_profile=python_profile,
        framework_requirements=("torch==2.7.0",),
        device_requirement="cuda",
        cuda_requirement="12.8",
    )


def _rules(*, artifact_subject: str = "model") -> tuple[ValidationRule, ...]:
    return (
        ValidationRule(kind="artifact_exists", subject=artifact_subject),
        ValidationRule(kind="metric_finite", subject="accuracy"),
    )


def _payload(
    *,
    parameter_matrix: dict[str, tuple[str | int | float | bool | None, ...]]
    | None = None,
) -> ProposalPayload:
    return ProposalPayload(
        goal="Reproduce the baseline",
        experiment_type="reproduction",
        repository_snapshot_id=SNAPSHOT_ID,
        repository_snapshot_identity=SNAPSHOT_IDENTITY,
        train_entrypoint="train.py",
        eval_entrypoint="eval.py",
        workdir=".",
        parameter_matrix=parameter_matrix
        or {"batch_size": (16, 32), "learning_rate": (0.1,)},
        seeds=(7,),
        dataset_reference=_dataset(),
        environment_expectation=_environment(),
        metrics=(MetricDeclaration(name="accuracy", source_artifact="model", split="test"),),
        artifacts=(
            ArtifactDeclaration(name="model", relative_path="out/model.pt", required=True),
        ),
        validation_rules=_rules(),
        timeout_seconds=3600,
        max_retries=1,
    )


def _version_identity(
    payload: ProposalPayload,
    *,
    dataset: DatasetReference | None = None,
    environment: EnvironmentExpectation | None = None,
    rules: tuple[ValidationRule, ...] | None = None,
) -> str:
    dataset = dataset or payload.dataset_reference
    environment = environment or payload.environment_expectation
    rules = rules or payload.validation_rules
    return experiment_version_identity(
        experiment_id=EXPERIMENT_ID,
        proposal_payload_hash_value=proposal_payload_hash(payload),
        repository_snapshot_identity=payload.repository_snapshot_identity,
        dataset_reference_hash_value=dataset_reference_hash(dataset),
        environment_expectation_hash_value=environment_expectation_hash(environment),
        validation_protocol_hash_value=validation_protocol_hash(rules),
    )


def test_payload_hash_is_independent_of_parameter_mapping_insertion_order() -> None:
    first = _payload(
        parameter_matrix={"batch_size": (16, 32), "learning_rate": (0.1,)},
    )
    second = _payload(
        parameter_matrix={"learning_rate": (0.1,), "batch_size": (16, 32)},
    )

    assert proposal_payload_hash(first) == proposal_payload_hash(second)


def test_payload_owns_immutable_parameter_mapping_snapshot() -> None:
    source = {"lr": (1e-3, 1e-4)}
    payload = _payload(parameter_matrix=source)
    before_hash = proposal_payload_hash(payload)
    before_json = proposal_service._payload_to_json(payload)

    source["lr"] = (999.0,)

    assert proposal_payload_hash(payload) == before_hash
    assert proposal_service._payload_to_json(payload) == before_json
    assert tuple(payload.parameter_matrix["lr"]) == (1e-3, 1e-4)
    with pytest.raises(TypeError):
        payload.parameter_matrix["lr"] = (999.0,)  # type: ignore[index]


def test_payload_snapshots_mutable_parameter_value_sequences() -> None:
    values = [1e-3, 1e-4]
    payload = _payload(parameter_matrix={"lr": values})  # type: ignore[dict-item]
    before_hash = proposal_payload_hash(payload)
    before_json = proposal_service._payload_to_json(payload)

    values.append(999.0)

    assert proposal_payload_hash(payload) == before_hash
    assert proposal_service._payload_to_json(payload) == before_json
    assert tuple(payload.parameter_matrix["lr"]) == (1e-3, 1e-4)


def test_version_identity_accepts_only_frozen_semantic_inputs() -> None:
    assert tuple(signature(experiment_version_identity).parameters) == (
        "experiment_id",
        "proposal_payload_hash_value",
        "repository_snapshot_identity",
        "dataset_reference_hash_value",
        "environment_expectation_hash_value",
        "validation_protocol_hash_value",
    )


def test_dataset_semantic_change_changes_version_identity() -> None:
    payload = _payload()
    changed = replace(payload.dataset_reference, stable_identity="dataset:v2")

    assert _version_identity(payload) != _version_identity(payload, dataset=changed)


def test_environment_semantic_change_changes_version_identity() -> None:
    payload = _payload()
    changed = replace(payload.environment_expectation, python_profile="python-3.13")

    assert _version_identity(payload) != _version_identity(payload, environment=changed)


def test_validation_semantic_change_changes_version_identity() -> None:
    payload = _payload()
    changed = _rules(artifact_subject="checkpoint")

    assert _version_identity(payload) != _version_identity(payload, rules=changed)


def test_same_semantic_inputs_produce_same_identities() -> None:
    first = _payload()
    second = _payload()

    assert proposal_payload_hash(first) == proposal_payload_hash(second)
    assert dataset_reference_hash(first.dataset_reference) == dataset_reference_hash(
        second.dataset_reference
    )
    assert environment_expectation_hash(
        first.environment_expectation
    ) == environment_expectation_hash(second.environment_expectation)
    assert validation_protocol_hash(first.validation_rules) == validation_protocol_hash(
        second.validation_rules
    )
    assert _version_identity(first) == _version_identity(second)


@pytest.mark.parametrize(
    "workdir",
    ["/tmp", "../escape", "a/../b", "a\\b", "C:/repo", "bad\x00dir"],
)
def test_proposal_payload_rejects_unsafe_workdir(workdir: str) -> None:
    with pytest.raises(ValueError, match="workdir"):
        replace(_payload(), workdir=workdir)


@pytest.mark.parametrize(
    "relative_path",
    [
        "/tmp/model.pt",
        "../model.pt",
        "out/../../model.pt",
        "out\\model.pt",
        ".",
        "bad\x00path",
    ],
)
def test_artifact_declaration_rejects_unsafe_relative_path(relative_path: str) -> None:
    with pytest.raises(ValueError, match="relative_path"):
        ArtifactDeclaration(name="model", relative_path=relative_path, required=True)


def test_strict_dataset_reference_requires_stable_identity() -> None:
    with pytest.raises(ValueError, match="stable_identity"):
        DatasetReference(
            reference_type="local_path",
            path_or_uri="data/train",
            stable_identity=None,
            reproducibility_level="strict",
        )


def test_best_effort_dataset_reference_accepts_missing_stable_identity() -> None:
    value = DatasetReference(
        reference_type="local_path",
        path_or_uri="data/train",
        stable_identity=None,
        reproducibility_level="best_effort",
    )

    assert value.stable_identity is None
