from pathlib import Path
from typing import Protocol
from uuid import UUID

from labpilot.db.uow import SessionContextFactory
from labpilot.execution.ports import ExecutionInputError
from labpilot.execution.types import (
    SourceManifest,
    SourceManifestEntry,
    VersionExecutionSemantics,
)
from labpilot.experiments.proposal_models import (
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
)
from labpilot.experiments.proposal_service import (
    _dataset_from_json,
    _environment_from_json,
    _payload_from_json,
)
from labpilot.experiments.proposal_types import (
    dataset_reference_hash,
    environment_expectation_hash,
    proposal_payload_hash,
)


class _AcceptedSnapshotSource(Protocol):
    async def materialize_exact_source(
        self,
        *,
        repository_snapshot_id: UUID,
        expected_repository_snapshot_identity: str,
        destination: Path,
    ) -> object:
        raise NotImplementedError


class VersionExecutionSemanticsAdapter:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory

    async def load(self, experiment_version_id: UUID) -> VersionExecutionSemantics:
        async with self._session_context_factory() as session:
            version = await session.get(ExperimentVersionRow, experiment_version_id)
            if version is None:
                raise ExecutionInputError("EXPERIMENT_VERSION_NOT_FOUND")
            revision = await session.get(
                ProposalRevisionRow,
                version.proposal_revision_id,
            )
            experiment = await session.get(ExperimentRow, version.experiment_id)

        if (
            revision is None
            or experiment is None
            or revision.proposal_revision_id != version.proposal_revision_id
            or revision.experiment_id != version.experiment_id
            or revision.payload_hash != version.proposal_payload_hash
        ):
            raise ExecutionInputError("PROPOSAL_BINDING_MISMATCH")

        try:
            payload = _payload_from_json(revision.payload_json)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionInputError("PROPOSAL_BINDING_MISMATCH") from exc
        if proposal_payload_hash(payload) != version.proposal_payload_hash:
            raise ExecutionInputError("PROPOSAL_BINDING_MISMATCH")
        if (
            revision.repository_snapshot_id != version.repository_snapshot_id
            or revision.repository_snapshot_identity
            != version.repository_snapshot_identity
            or payload.repository_snapshot_id != version.repository_snapshot_id
            or payload.repository_snapshot_identity
            != version.repository_snapshot_identity
        ):
            raise ExecutionInputError("REPOSITORY_SNAPSHOT_BINDING_MISMATCH")

        try:
            frozen_dataset = _dataset_from_json(version.dataset_reference_json)
            payload_dataset_hash = dataset_reference_hash(payload.dataset_reference)
            frozen_dataset_hash = dataset_reference_hash(frozen_dataset)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionInputError("DATASET_REFERENCE_HASH_MISMATCH") from exc
        if (
            payload_dataset_hash != version.dataset_reference_hash
            or frozen_dataset_hash != version.dataset_reference_hash
        ):
            raise ExecutionInputError("DATASET_REFERENCE_HASH_MISMATCH")

        try:
            frozen_environment = _environment_from_json(
                version.environment_expectation_json
            )
            payload_environment_hash = environment_expectation_hash(
                payload.environment_expectation
            )
            frozen_environment_hash = environment_expectation_hash(
                frozen_environment
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionInputError(
                "ENVIRONMENT_EXPECTATION_HASH_MISMATCH"
            ) from exc
        if (
            payload_environment_hash != version.environment_expectation_hash
            or frozen_environment_hash != version.environment_expectation_hash
        ):
            raise ExecutionInputError("ENVIRONMENT_EXPECTATION_HASH_MISMATCH")

        if payload.train_argv_template is None:
            raise ExecutionInputError("EXECUTION_SEMANTICS_UNSUPPORTED")

        return VersionExecutionSemantics(
            project_id=experiment.project_id,
            experiment_version_id=version.experiment_version_id,
            experiment_version_identity=version.experiment_version_identity,
            proposal_revision_id=revision.proposal_revision_id,
            proposal_payload_hash=version.proposal_payload_hash,
            repository_snapshot_id=version.repository_snapshot_id,
            repository_snapshot_identity=version.repository_snapshot_identity,
            train_entrypoint=payload.train_entrypoint,
            workdir=payload.workdir,
            train_argv_template=payload.train_argv_template,
            parameter_matrix={
                key: tuple(values) for key, values in payload.parameter_matrix.items()
            },
            seeds=payload.seeds,
            dataset_reference_type=payload.dataset_reference.reference_type,
            dataset_path_or_uri=payload.dataset_reference.path_or_uri,
            dataset_reference_hash=version.dataset_reference_hash,
            environment_expectation_hash=version.environment_expectation_hash,
            python_profile=frozen_environment.python_profile,
            framework_requirements=frozen_environment.framework_requirements,
            device_requirement=frozen_environment.device_requirement,
            cuda_requirement=frozen_environment.cuda_requirement,
            artifact_relative_paths=tuple(
                artifact.relative_path for artifact in payload.artifacts
            ),
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
        )


class RepositorySnapshotMaterializationAdapter:
    def __init__(self, source: _AcceptedSnapshotSource) -> None:
        self._source = source

    async def materialize_exact_source(
        self,
        *,
        repository_snapshot_id: UUID,
        expected_repository_snapshot_identity: str,
        destination: Path,
    ) -> SourceManifest:
        try:
            raw_manifest = await self._source.materialize_exact_source(
                repository_snapshot_id=repository_snapshot_id,
                expected_repository_snapshot_identity=expected_repository_snapshot_identity,
                destination=destination,
            )
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", None)
            if type(reason_code) is str:
                raise ExecutionInputError(reason_code) from exc
            raise
        try:
            manifest = SourceManifest(
                schema_version=raw_manifest.schema_version,  # type: ignore[attr-defined]
                repository_snapshot_id=raw_manifest.repository_snapshot_id,  # type: ignore[attr-defined]
                repository_snapshot_identity=raw_manifest.repository_snapshot_identity,  # type: ignore[attr-defined]
                entries=tuple(
                    SourceManifestEntry(
                        entry.relative_path,
                        entry.file_type,
                        entry.file_mode,
                        entry.content_sha256,
                    )
                    for entry in raw_manifest.entries  # type: ignore[attr-defined]
                ),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ExecutionInputError("SOURCE_MANIFEST_INVALID") from exc
        if (
            manifest.repository_snapshot_id != repository_snapshot_id
            or manifest.repository_snapshot_identity
            != expected_repository_snapshot_identity
        ):
            raise ExecutionInputError("REPOSITORY_SNAPSHOT_BINDING_MISMATCH")
        return manifest
