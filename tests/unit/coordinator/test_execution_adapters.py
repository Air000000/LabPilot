import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from labpilot.execution.types import SourceManifest
from labpilot.experiments.proposal_models import (
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
)
from labpilot.experiments.proposal_service import (
    _dataset_to_json,
    _environment_to_json,
    _payload_to_json,
)
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    ProposalPayload,
    dataset_reference_hash,
    environment_expectation_hash,
    proposal_payload_hash,
)

PROJECT_ID = UUID("10000000-0000-4000-8000-000000000020")
EXPERIMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = UUID("33333333-3333-4333-8333-333333333333")
SNAPSHOT_ID = UUID("44444444-4444-4444-8444-444444444444")


def _payload() -> ProposalPayload:
    return ProposalPayload(
        goal="Reproduce",
        experiment_type="reproduction",
        repository_snapshot_id=SNAPSHOT_ID,
        repository_snapshot_identity="a" * 64,
        train_entrypoint="train.py",
        eval_entrypoint=None,
        workdir=".",
        parameter_matrix={"learning_rate": (0.1,)},
        seeds=(7,),
        dataset_reference=DatasetReference(
            reference_type="uri",
            path_or_uri="s3://dataset/v1",
            stable_identity="dataset:v1",
            reproducibility_level="strict",
        ),
        environment_expectation=EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=("torch==2.7.0", "numpy>=2"),
            device_requirement="cuda",
            cuda_requirement="12.8",
        ),
        metrics=(),
        artifacts=(ArtifactDeclaration("model", "model.pt", True),),
        validation_rules=(),
        timeout_seconds=3600,
        max_retries=1,
        train_argv_template=(
            "--lr",
            "{parameter:learning_rate}",
            "--seed",
            "{seed}",
            "--dataset",
            "{dataset}",
            "--output",
            "{output_dir}",
        ),
    )


class _Session:
    def __init__(self, rows: dict[tuple[type[object], UUID], object]) -> None:
        self.rows = rows

    async def get(self, model: type[object], identity: UUID) -> object | None:
        return self.rows.get((model, identity))


def _authority_rows() -> tuple[dict[tuple[type[object], UUID], object], ProposalPayload]:
    payload = _payload()
    now = datetime(2026, 8, 16, tzinfo=UTC)
    payload_hash = proposal_payload_hash(payload)
    dataset_hash = dataset_reference_hash(payload.dataset_reference)
    environment_hash = environment_expectation_hash(payload.environment_expectation)
    experiment = ExperimentRow(
        experiment_id=EXPERIMENT_ID,
        project_id=PROJECT_ID,
        created_at=now,
    )
    revision = ProposalRevisionRow(
        proposal_revision_id=REVISION_ID,
        experiment_id=EXPERIMENT_ID,
        revision_number=1,
        origin_agent_step_id=None,
        repository_snapshot_id=SNAPSHOT_ID,
        repository_snapshot_identity=payload.repository_snapshot_identity,
        payload_json=_payload_to_json(payload),
        payload_hash=payload_hash,
        created_by_actor_type="local_user",
        created_by_actor_id="air",
        created_at=now,
    )
    version = ExperimentVersionRow(
        experiment_version_id=VERSION_ID,
        experiment_id=EXPERIMENT_ID,
        design_decision_id=UUID("55555555-5555-4555-8555-555555555555"),
        version_number=1,
        experiment_version_identity="b" * 64,
        proposal_revision_id=REVISION_ID,
        proposal_payload_hash=payload_hash,
        repository_snapshot_id=SNAPSHOT_ID,
        repository_snapshot_identity=payload.repository_snapshot_identity,
        dataset_reference_json=_dataset_to_json(payload.dataset_reference),
        dataset_reference_hash=dataset_hash,
        environment_expectation_json=_environment_to_json(
            payload.environment_expectation
        ),
        environment_expectation_hash=environment_hash,
        validation_protocol_json=[],
        validation_protocol_hash="c" * 64,
        created_at=now,
    )
    return {
        (ExperimentRow, EXPERIMENT_ID): experiment,
        (ProposalRevisionRow, REVISION_ID): revision,
        (ExperimentVersionRow, VERSION_ID): version,
    }, payload


def _session_factory(rows: dict[tuple[type[object], UUID], object]):
    @asynccontextmanager
    async def factory() -> AsyncIterator[Any]:
        yield _Session(rows)

    return factory


async def test_version_adapter_returns_full_frozen_execution_semantics() -> None:
    adapters = importlib.import_module("labpilot.coordinator.execution_adapters")
    rows, payload = _authority_rows()
    adapter = adapters.VersionExecutionSemanticsAdapter(_session_factory(rows))

    semantics = await adapter.load(VERSION_ID)

    assert semantics.project_id == PROJECT_ID
    assert semantics.proposal_revision_id == REVISION_ID
    assert semantics.train_argv_template == payload.train_argv_template
    assert semantics.parameter_matrix == {"learning_rate": (0.1,)}
    assert semantics.framework_requirements == ("torch==2.7.0", "numpy>=2")
    assert semantics.device_requirement == "cuda"
    assert semantics.cuda_requirement == "12.8"
    assert semantics.artifact_relative_paths == ("model.pt",)


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("proposal_hash", "PROPOSAL_BINDING_MISMATCH"),
        ("snapshot", "REPOSITORY_SNAPSHOT_BINDING_MISMATCH"),
        ("dataset", "DATASET_REFERENCE_HASH_MISMATCH"),
        ("environment", "ENVIRONMENT_EXPECTATION_HASH_MISMATCH"),
    ],
)
async def test_version_adapter_fails_closed_on_bound_authority_mismatch(
    mutation: str,
    reason_code: str,
) -> None:
    adapters = importlib.import_module("labpilot.coordinator.execution_adapters")
    ports = importlib.import_module("labpilot.execution.ports")
    rows, _ = _authority_rows()
    revision = cast(ProposalRevisionRow, rows[(ProposalRevisionRow, REVISION_ID)])
    version = cast(ExperimentVersionRow, rows[(ExperimentVersionRow, VERSION_ID)])
    if mutation == "proposal_hash":
        revision.payload_hash = "f" * 64
    elif mutation == "snapshot":
        revision.repository_snapshot_identity = "f" * 64
    elif mutation == "dataset":
        version.dataset_reference_hash = "f" * 64
    else:
        version.environment_expectation_hash = "f" * 64

    adapter = adapters.VersionExecutionSemanticsAdapter(_session_factory(rows))

    with pytest.raises(ports.ExecutionInputError) as captured:
        await adapter.load(VERSION_ID)

    assert captured.value.reason_code == reason_code


class _AcceptedSnapshotSource:
    def __init__(self, manifest: SourceManifest) -> None:
        self.manifest = manifest
        self.calls: list[tuple[UUID, str, Path]] = []

    async def materialize_exact_source(
        self,
        *,
        repository_snapshot_id: UUID,
        expected_repository_snapshot_identity: str,
        destination: Path,
    ) -> SourceManifest:
        self.calls.append(
            (
                repository_snapshot_id,
                expected_repository_snapshot_identity,
                destination,
            )
        )
        return self.manifest


async def test_snapshot_adapter_uses_accepted_source_authority(tmp_path: Path) -> None:
    adapters = importlib.import_module("labpilot.coordinator.execution_adapters")
    manifest = SourceManifest("v1", SNAPSHOT_ID, "a" * 64, ())
    source = _AcceptedSnapshotSource(manifest)
    adapter = adapters.RepositorySnapshotMaterializationAdapter(source)

    result = await adapter.materialize_exact_source(
        repository_snapshot_id=SNAPSHOT_ID,
        expected_repository_snapshot_identity="a" * 64,
        destination=tmp_path / "source",
    )

    assert result == manifest
    assert source.calls == [(SNAPSHOT_ID, "a" * 64, tmp_path / "source")]


async def test_snapshot_adapter_rejects_returned_identity_mismatch(
    tmp_path: Path,
) -> None:
    adapters = importlib.import_module("labpilot.coordinator.execution_adapters")
    ports = importlib.import_module("labpilot.execution.ports")
    source = _AcceptedSnapshotSource(
        SourceManifest("v1", SNAPSHOT_ID, "f" * 64, ())
    )
    adapter = adapters.RepositorySnapshotMaterializationAdapter(source)

    with pytest.raises(ports.ExecutionInputError) as captured:
        await adapter.materialize_exact_source(
            repository_snapshot_id=SNAPSHOT_ID,
            expected_repository_snapshot_identity="a" * 64,
            destination=tmp_path / "source",
        )

    assert captured.value.reason_code == "REPOSITORY_SNAPSHOT_BINDING_MISMATCH"
