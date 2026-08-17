from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import cast
from uuid import UUID

from labpilot.common.canonical_json import canonical_json_bytes
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.ports import (
    ExecutionInputError,
    SnapshotMaterializationPort,
    VersionExecutionSemanticsPort,
)
from labpilot.execution.repository import ExecutionRepository
from labpilot.execution.types import (
    AttemptStatus,
    FrozenExecutionSpec,
    ParameterValue,
    SourceManifest,
    VersionExecutionSemantics,
    frozen_execution_spec_hash,
    frozen_execution_spec_value,
    source_manifest_hash,
    source_manifest_value,
    trial_identity,
)

_DISTRIBUTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True, slots=True)
class _PreparationInputs:
    attempt_id: UUID
    trial_id: UUID
    batch_id: UUID
    experiment_version_id: UUID
    project_id: UUID
    trial_identity: str
    resolved_parameters: dict[str, ParameterValue]
    seed: int
    workspace_path: str
    supervisor_execution_identity: str


class AttemptPreparationService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        version_semantics: VersionExecutionSemanticsPort,
        snapshot_materialization: SnapshotMaterializationPort,
        execution_gid: int,
    ) -> None:
        if type(execution_gid) is not int or execution_gid < 0:
            raise ValueError("execution_gid must be a non-negative integer")
        self._session_context_factory = session_context_factory
        self._version_semantics = version_semantics
        self._snapshot_materialization = snapshot_materialization
        self._execution_gid = execution_gid
        self._repository = ExecutionRepository()

    async def prepare_attempt(self, attempt_id: UUID) -> None:
        inputs = await self._load_inputs(attempt_id)
        if inputs is None:
            return
        staging: Path | None = None
        try:
            semantics = await self._version_semantics.load(inputs.experiment_version_id)
            _verify_semantics_binding(inputs, semantics)
            workspace = _workspace_path(inputs)
            staging = Path(f"{workspace}.staging")
            if workspace.exists():
                _verify_workspace_shape(workspace)
                manifest = await self._snapshot_materialization.materialize_exact_source(
                    repository_snapshot_id=semantics.repository_snapshot_id,
                    expected_repository_snapshot_identity=semantics.repository_snapshot_identity,
                    destination=workspace / "source",
                )
                _verify_workspace_permissions(
                    workspace, manifest, execution_gid=self._execution_gid
                )
                spec = build_frozen_execution_spec(
                    semantics,
                    trial_identity=inputs.trial_identity,
                    resolved_parameters=inputs.resolved_parameters,
                    seed=inputs.seed,
                )
            else:
                _remove_staging(staging, workspace)
                (staging / "runtime" / "output").mkdir(parents=True)
                manifest = await self._snapshot_materialization.materialize_exact_source(
                    repository_snapshot_id=semantics.repository_snapshot_id,
                    expected_repository_snapshot_identity=semantics.repository_snapshot_identity,
                    destination=staging / "source",
                )
                spec = build_frozen_execution_spec(
                    semantics,
                    trial_identity=inputs.trial_identity,
                    resolved_parameters=inputs.resolved_parameters,
                    seed=inputs.seed,
                )
                set_workspace_permissions(staging, execution_gid=self._execution_gid)
                os.replace(staging, workspace)
                _verify_workspace_permissions(
                    workspace, manifest, execution_gid=self._execution_gid
                )
            await self._persist_prepared(inputs, manifest, spec)
        except ExecutionInputError as exc:
            if staging is not None and staging.exists():
                _remove_staging(staging, Path(inputs.workspace_path))
            await self._mark_failed(attempt_id, exc.reason_code)
        except OSError:
            if staging is not None and staging.exists():
                _remove_staging(staging, Path(inputs.workspace_path))
            await self._mark_failed(attempt_id, "WORKSPACE_PREPARATION_FAILED")
        return None

    async def _load_inputs(self, attempt_id: UUID) -> _PreparationInputs | None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await self._repository.get_attempt_for_update(uow.session, attempt_id)
            if attempt is None:
                raise ExecutionInputError("ATTEMPT_NOT_FOUND")
            if attempt.status != AttemptStatus.PREPARING.value:
                await uow.commit()
                return None
            trial = await self._repository.get_trial(uow.session, attempt.trial_id)
            if trial is None:
                raise ExecutionInputError("TRIAL_NOT_FOUND")
            batch = await self._repository.get_batch(uow.session, trial.batch_id)
            if batch is None:
                raise ExecutionInputError("BATCH_NOT_FOUND")
            inputs = _inputs(attempt, trial, batch)
            await uow.commit()
            return inputs
        return None

    async def _persist_prepared(
        self,
        inputs: _PreparationInputs,
        manifest: SourceManifest,
        spec: FrozenExecutionSpec,
    ) -> None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await self._repository.get_attempt_for_update(
                uow.session, inputs.attempt_id
            )
            if attempt is None or attempt.status != AttemptStatus.PREPARING.value:
                await uow.commit()
                return
            trial = await self._repository.get_trial(uow.session, attempt.trial_id)
            batch = (
                await self._repository.get_batch_for_update(uow.session, trial.batch_id)
                if trial is not None
                else None
            )
            if trial is None or batch is None or _inputs(attempt, trial, batch) != inputs:
                raise ExecutionInputError("PREPARATION_INPUTS_CHANGED")
            now = datetime.now(UTC)
            if batch.cancel_requested_at is not None:
                attempt.status = AttemptStatus.CANCELLED_PRE_LAUNCH.value
                attempt.terminal_at = now
                await uow.commit()
                return
            attempt.source_manifest_json = cast(
                dict[str, object], source_manifest_value(manifest)
            )
            attempt.source_manifest_sha256 = source_manifest_hash(manifest)
            attempt.frozen_execution_spec_json = cast(
                dict[str, object], frozen_execution_spec_value(spec)
            )
            attempt.frozen_execution_spec_hash = frozen_execution_spec_hash(spec)
            attempt.preparation_failure_code = None
            attempt.prepared_at = now
            attempt.status = AttemptStatus.LAUNCH_READY.value
            await uow.commit()

    async def _mark_failed(self, attempt_id: UUID, reason_code: str) -> None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await self._repository.get_attempt_for_update(uow.session, attempt_id)
            if attempt is not None and attempt.status == AttemptStatus.PREPARING.value:
                attempt.status = AttemptStatus.PREPARATION_FAILED.value
                attempt.preparation_failure_code = reason_code[:100]
                attempt.terminal_at = datetime.now(UTC)
            await uow.commit()


def _inputs(attempt: AttemptRow, trial: TrialRow, batch: BatchRow) -> _PreparationInputs:
    try:
        parameters = {
            key: _parameter_value(value)
            for key, value in trial.resolved_parameters_json.items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise ExecutionInputError("TRIAL_INPUTS_INVALID") from exc
    return _PreparationInputs(
        attempt_id=attempt.attempt_id,
        trial_id=trial.trial_id,
        batch_id=batch.batch_id,
        experiment_version_id=batch.experiment_version_id,
        project_id=batch.project_id,
        trial_identity=trial.trial_identity,
        resolved_parameters=parameters,
        seed=trial.seed,
        workspace_path=attempt.workspace_path,
        supervisor_execution_identity=attempt.supervisor_execution_identity,
    )


def _parameter_value(value: object) -> ParameterValue:
    if value is None or type(value) in {str, int, float, bool}:
        if type(value) is float and not isfinite(value):
            raise ValueError("non-finite trial parameter")
        return value  # type: ignore[return-value]
    raise ValueError("trial parameter must be scalar")


def _verify_semantics_binding(
    inputs: _PreparationInputs,
    semantics: VersionExecutionSemantics,
) -> None:
    if (
        semantics.experiment_version_id != inputs.experiment_version_id
        or semantics.project_id != inputs.project_id
        or set(semantics.parameter_matrix) != set(inputs.resolved_parameters)
        or inputs.seed not in semantics.seeds
    ):
        raise ExecutionInputError("PREPARATION_INPUTS_CHANGED")
    for key, value in inputs.resolved_parameters.items():
        if not any(_same_scalar(value, candidate) for candidate in semantics.parameter_matrix[key]):
            raise ExecutionInputError("PREPARATION_INPUTS_CHANGED")
    if trial_identity(
        experiment_version_identity=semantics.experiment_version_identity,
        resolved_parameters=inputs.resolved_parameters,
        seed=inputs.seed,
    ) != inputs.trial_identity:
        raise ExecutionInputError("TRIAL_IDENTITY_MISMATCH")


def _same_scalar(left: ParameterValue, right: ParameterValue) -> bool:
    return type(left) is type(right) and left == right


def _workspace_path(inputs: _PreparationInputs) -> Path:
    workspace = Path(inputs.workspace_path)
    if (
        not workspace.is_absolute()
        or workspace.name != inputs.attempt_id.hex
        or not workspace.parent.exists()
        or workspace.parent.resolve(strict=True) != workspace.parent
    ):
        raise ExecutionInputError("WORKSPACE_PATH_INVALID")
    return workspace


def _remove_staging(staging: Path, workspace: Path) -> None:
    if staging != Path(f"{workspace}.staging") or staging.parent != workspace.parent:
        raise ExecutionInputError("WORKSPACE_PATH_INVALID")
    if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
        raise ExecutionInputError("WORKSPACE_STAGING_INVALID")
    if staging.exists():
        shutil.rmtree(staging)


def _verify_workspace_shape(workspace: Path) -> None:
    try:
        if workspace.is_symlink() or not workspace.is_dir():
            raise ExecutionInputError("SOURCE_TAMPERED")
        if {path.name for path in workspace.iterdir()} != {"source", "runtime"}:
            raise ExecutionInputError("SOURCE_TAMPERED")
        runtime = workspace / "runtime"
        if runtime.is_symlink() or {path.name for path in runtime.iterdir()} != {"output"}:
            raise ExecutionInputError("SOURCE_TAMPERED")
        source = workspace / "source"
        output = runtime / "output"
        if (
            source.is_symlink()
            or output.is_symlink()
            or not source.is_dir()
            or not output.is_dir()
        ):
            raise ExecutionInputError("SOURCE_TAMPERED")
    except OSError as exc:
        raise ExecutionInputError("SOURCE_TAMPERED") from exc


def _verify_workspace_permissions(
    workspace: Path,
    manifest: SourceManifest,
    *,
    execution_gid: int,
) -> None:
    source = workspace / "source"
    _verify_path_permissions(workspace, 0o750, execution_gid)
    _verify_path_permissions(source, 0o550, execution_gid)
    regular_modes = {
        entry.relative_path: 0o550 if entry.file_mode == "100755" else 0o440
        for entry in manifest.entries
        if entry.file_type == "regular"
    }
    for path in source.rglob("*"):
        if path.is_symlink():
            continue
        expected_mode = (
            0o550
            if path.is_dir()
            else regular_modes.get(path.relative_to(source).as_posix())
        )
        if expected_mode is None:
            raise ExecutionInputError("SOURCE_TAMPERED")
        _verify_path_permissions(path, expected_mode, execution_gid)
    _verify_path_permissions(workspace / "runtime", 0o770, execution_gid)
    _verify_path_permissions(workspace / "runtime" / "output", 0o770, execution_gid)


def _verify_path_permissions(path: Path, mode: int, execution_gid: int) -> None:
    metadata = path.lstat()
    if stat.S_IMODE(metadata.st_mode) != mode or (
        os.name == "posix" and metadata.st_gid != execution_gid
    ):
        raise ExecutionInputError("SOURCE_TAMPERED")


def build_frozen_execution_spec(
    semantics: VersionExecutionSemantics,
    *,
    trial_identity: str,
    resolved_parameters: dict[str, ParameterValue],
    seed: int,
) -> FrozenExecutionSpec:
    values = {
        f"{{parameter:{key}}}": _render_scalar(value)
        for key, value in resolved_parameters.items()
    }
    values.update(
        {
            "{seed}": str(seed),
            "{dataset}": semantics.dataset_path_or_uri,
            "{output_dir}": "{output_dir}",
        }
    )
    argv: list[str] = []
    output_dir_arg_indexes: list[int] = []
    for index, token in enumerate(semantics.train_argv_template):
        if token.startswith("{") and token not in values:
            raise ExecutionInputError("EXECUTION_SEMANTICS_UNSUPPORTED")
        if token == "{output_dir}":
            output_dir_arg_indexes.append(index)
        argv.append(values.get(token, token))
    _validate_argv(tuple(argv))

    distributions: list[str] = []
    for requirement in semantics.framework_requirements:
        matched = _DISTRIBUTION.match(requirement)
        if requirement and matched is None:
            raise ExecutionInputError("EXECUTION_ENVIRONMENT_UNSUPPORTED")
        if matched is not None:
            distributions.append(matched.group())

    return FrozenExecutionSpec(
        schema_version="v1",
        experiment_version_id=semantics.experiment_version_id,
        experiment_version_identity=semantics.experiment_version_identity,
        trial_identity=trial_identity,
        train_entrypoint_relative_path=semantics.train_entrypoint,
        workdir_relative_path=semantics.workdir,
        python_profile=semantics.python_profile,
        argv=tuple(argv),
        output_dir_arg_indexes=tuple(output_dir_arg_indexes),
        dataset_reference_type=semantics.dataset_reference_type,
        dataset_argument=semantics.dataset_path_or_uri,
        dataset_reference_hash=semantics.dataset_reference_hash,
        environment_expectation_hash=semantics.environment_expectation_hash,
        framework_requirements=semantics.framework_requirements,
        framework_distribution_names=tuple(distributions),
        device_requirement=semantics.device_requirement,
        cuda_requirement=semantics.cuda_requirement,
        timeout_seconds=semantics.timeout_seconds,
    )


def set_workspace_permissions(workspace: Path, *, execution_gid: int) -> None:
    source = workspace / "source"
    runtime = workspace / "runtime"
    output = runtime / "output"
    source_paths = [path for path in source.rglob("*") if not path.is_symlink()]
    paths = [workspace, source, *source_paths, runtime, output]
    if os.name == "posix":
        for path in paths:
            os.chown(path, -1, execution_gid, follow_symlinks=False)
    workspace.chmod(0o750)
    source.chmod(0o550)
    for path in source_paths:
        mode = path.lstat().st_mode
        path.chmod(0o550 if path.is_dir() or mode & 0o111 else 0o440)
    runtime.chmod(0o770)
    output.chmod(0o770)


def _render_scalar(value: ParameterValue) -> str:
    if type(value) is str:
        return value
    try:
        canonical = Decimal(str(value)) if type(value) is float and isfinite(value) else value
        return canonical_json_bytes(canonical).decode("utf-8")  # type: ignore[arg-type]
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ExecutionInputError("EXECUTION_SEMANTICS_UNSUPPORTED") from exc


def _validate_argv(argv: tuple[str, ...]) -> None:
    if len(argv) > 256:
        raise ExecutionInputError("EXECUTION_ARGV_UNSUPPORTED")
    total = 0
    for token in argv:
        try:
            encoded = token.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ExecutionInputError("EXECUTION_ARGV_UNSUPPORTED") from exc
        if b"\0" in encoded or len(encoded) > 8192:
            raise ExecutionInputError("EXECUTION_ARGV_UNSUPPORTED")
        total += len(encoded) + 1
    if total > 32768:
        raise ExecutionInputError("EXECUTION_ARGV_UNSUPPORTED")
