from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import select

from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.ports import ExecutionInputError
from labpilot.execution.repository import ExecutionRepository
from labpilot.execution.runner_client import RunnerPreSendError
from labpilot.execution.runner_protocol import (
    PROTOCOL_VERSION,
    InfoInspectionRequest,
    RunnerInfoRequest,
    RunnerLaunchRequest,
    RunnerObserveRequest,
    RunnerOperation,
    RunnerRequest,
    RunnerStopRequest,
)
from labpilot.execution.systemd_supervisor import PhysicalObservationKind
from labpilot.execution.types import (
    AttemptStatus,
    FrozenExecutionSpec,
    FrozenLaunchSpec,
    ObservedEnvironment,
    ObservedFrameworkVersion,
    SourceManifest,
    SourceManifestEntry,
    TerminalKind,
    frozen_execution_spec_hash,
    frozen_launch_spec_hash,
    frozen_launch_spec_value,
    observed_environment_value,
    source_manifest_hash,
)
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import RuntimeEventRow
from labpilot.observability.payloads import (
    EventJsonObject,
    prepare_runtime_event_payload,
)


class RunnerPort(Protocol):
    def request(self, request: RunnerRequest) -> dict[str, object]: ...


class ReconciliationReason(StrEnum):
    PERIODIC = "periodic"
    STARTUP = "startup"


@dataclass(frozen=True, slots=True)
class _DispatchInputs:
    attempt_id: UUID
    trial_id: UUID
    batch_id: UUID
    project_id: UUID
    execution_identity: str
    workspace_path: str
    source_manifest: dict[str, object]
    source_manifest_sha256: str
    execution_spec: FrozenExecutionSpec
    execution_spec_hash: str


@dataclass(frozen=True, slots=True)
class _PhysicalCoordinates:
    attempt_id: UUID
    project_id: UUID
    execution_identity: str
    execution_spec_hash: str


@dataclass(frozen=True, slots=True)
class _FirstDispatchPermit:
    attempt_id: UUID
    execution_identity: str
    execution_spec_hash: str
    spec: FrozenLaunchSpec


@dataclass(frozen=True, slots=True)
class _Observation:
    kind: PhysicalObservationKind
    observed_spec_hash: str | None
    invocation_id: str | None
    boot_id: str | None
    result: str | None
    exit_code: int | None
    termination_signal: int | None


class AttemptExecutionService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        runner: RunnerPort,
        *,
        workspace_root: Path,
        unknown_attention_grace_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not workspace_root.is_absolute():
            raise ValueError("workspace_root must be absolute")
        if unknown_attention_grace_seconds <= 0:
            raise ValueError("unknown attention grace must be positive")
        self._session_context_factory = session_context_factory
        self._runner = runner
        self._workspace_root = workspace_root
        self._unknown_grace = timedelta(seconds=unknown_attention_grace_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = ExecutionRepository()

    @staticmethod
    def validate_reconciliation(
        reason: ReconciliationReason,
        *,
        coordinator_start_id: UUID | None,
    ) -> None:
        if reason is ReconciliationReason.STARTUP and coordinator_start_id is None:
            raise ValueError("STARTUP reconciliation requires coordinator_start_id")
        if reason is ReconciliationReason.PERIODIC and coordinator_start_id is not None:
            raise ValueError("PERIODIC reconciliation forbids coordinator_start_id")

    async def dispatch_attempt(self, attempt_id: UUID) -> None:
        inputs = await self._load_dispatch_inputs(attempt_id)
        if inputs is None:
            return
        _verify_source(inputs.workspace_path, inputs.source_manifest)
        inspection_distributions = tuple(
            sorted(set(inputs.execution_spec.framework_distribution_names))
        )
        info = await self._request(
            RunnerInfoRequest(
                PROTOCOL_VERSION,
                RunnerOperation.INFO,
                InfoInspectionRequest(
                    inputs.execution_spec.python_profile,
                    inspection_distributions,
                ),
            )
        )
        launch_spec, observed = self._resolve_launch(
            inputs, info, inspection_distributions=inspection_distributions
        )
        launch_hash = frozen_launch_spec_hash(launch_spec)
        permit = await self._commit_launch_intent(
            inputs, launch_spec, launch_hash, observed
        )
        if permit is None:
            return

        try:
            response = await self._request(
                RunnerLaunchRequest(
                    PROTOCOL_VERSION,
                    RunnerOperation.LAUNCH,
                    permit.execution_identity,
                    permit.execution_spec_hash,
                    permit.spec,
                    "first_dispatch_proven",
                )
            )
        except RunnerPreSendError:
            await self._mark_launch_not_accepted(permit.attempt_id)
            return
        except ConnectionError:
            response = None
        if response is not None and (
            response.get("outcome") == "definite_non_acceptance"
            or _result_kind(response) == "definite_non_acceptance"
        ):
            await self._mark_launch_not_accepted(permit.attempt_id)
            return
        await self.reconcile_attempt(
            inputs.attempt_id, reason=ReconciliationReason.PERIODIC
        )

    async def reconcile_attempt(
        self,
        attempt_id: UUID,
        *,
        reason: ReconciliationReason,
        coordinator_start_id: UUID | None = None,
    ) -> None:
        self.validate_reconciliation(reason, coordinator_start_id=coordinator_start_id)
        coordinates = await self._load_coordinates(attempt_id)
        if coordinates is None:
            return
        try:
            response = await self._request(
                RunnerObserveRequest(
                    PROTOCOL_VERSION,
                    RunnerOperation.OBSERVE,
                    coordinates.execution_identity,
                    coordinates.execution_spec_hash,
                )
            )
            observation = _observation(response)
        except ConnectionError:
            observation = _unknown_observation()
        await self._persist_observation(
            coordinates,
            observation,
            reason=reason,
            coordinator_start_id=coordinator_start_id,
        )

    async def request_stop(self, attempt_id: UUID) -> None:
        coordinates = await self._load_coordinates(attempt_id, require_cancel=True)
        if coordinates is None:
            return
        try:
            response = await self._request(
                RunnerObserveRequest(
                    PROTOCOL_VERSION,
                    RunnerOperation.OBSERVE,
                    coordinates.execution_identity,
                    coordinates.execution_spec_hash,
                )
            )
            observation = _observation(response)
        except ConnectionError:
            observation = _unknown_observation()
        await self._persist_observation(
            coordinates,
            observation,
            reason=ReconciliationReason.PERIODIC,
            coordinator_start_id=None,
        )
        if observation.kind is not PhysicalObservationKind.ACTIVE_MATCH:
            return
        try:
            await self._request(
                RunnerStopRequest(
                    PROTOCOL_VERSION,
                    RunnerOperation.STOP,
                    coordinates.execution_identity,
                    coordinates.execution_spec_hash,
                )
            )
        except ConnectionError:
            pass
        await self.reconcile_attempt(
            attempt_id, reason=ReconciliationReason.PERIODIC
        )

    async def _request(self, request: RunnerRequest) -> dict[str, object]:
        return await asyncio.to_thread(self._runner.request, request)

    async def _load_dispatch_inputs(self, attempt_id: UUID) -> _DispatchInputs | None:
        async with self._session_context_factory() as session:
            attempt = await self._repository.get_attempt(session, attempt_id)
            if attempt is None:
                raise ExecutionInputError("ATTEMPT_NOT_FOUND")
            if attempt.status != AttemptStatus.LAUNCH_READY.value:
                return None
            trial = await self._repository.get_trial(session, attempt.trial_id)
            batch = (
                await self._repository.get_batch(session, trial.batch_id)
                if trial is not None
                else None
            )
            if trial is None or batch is None:
                raise ExecutionInputError("EXECUTION_INPUTS_MISSING")
            if batch.start_requested_at is None or batch.cancel_requested_at is not None:
                return None
            return _dispatch_inputs(attempt, trial, batch)

    def _resolve_launch(
        self,
        inputs: _DispatchInputs,
        response: dict[str, object],
        *,
        inspection_distributions: tuple[str, ...],
    ) -> tuple[FrozenLaunchSpec, ObservedEnvironment]:
        result = _successful_result(response, RunnerOperation.INFO)
        if (
            _string(result, "readiness_reason") != "ready"
            or not _string(result, "runner_version")
            or not _string(result, "boot_id")
        ):
            raise ExecutionInputError("RUNNER_NOT_READY")
        workspace_root = _string(result, "workspace_root")
        configured_root = str(self._workspace_root.resolve(strict=True))
        if workspace_root != configured_root or Path(inputs.workspace_path).parent != Path(
            configured_root
        ):
            raise ExecutionInputError("WORKSPACE_AUTHORITY_MISMATCH")
        profile = _runtime_profile(result, inputs.execution_spec.python_profile)
        runtime_path = _string(profile, "runtime_path")
        environment_value = profile.get("environment")
        if type(environment_value) is not dict or any(
            type(key) is not str or type(value) is not str
            for key, value in environment_value.items()
        ):
            raise ExecutionInputError("RUNNER_INFO_INVALID")
        roots = _string_list(result.get("allowed_local_dataset_roots"))
        if inputs.execution_spec.dataset_reference_type == "local_path" and not any(
            _under_root(Path(inputs.execution_spec.dataset_argument), Path(root))
            for root in roots
        ):
            raise ExecutionInputError("DATASET_POLICY_DENIED")
        max_runtime = result.get("max_runtime_seconds")
        if type(max_runtime) is not int or inputs.execution_spec.timeout_seconds > max_runtime:
            raise ExecutionInputError("RUNTIME_POLICY_DENIED")
        observed = _observed_environment(
            result.get("observed_environment"),
            expected_hash=inputs.execution_spec.environment_expectation_hash,
            expected_profile=inputs.execution_spec.python_profile,
            expected_runtime=runtime_path,
            expected_distributions=inspection_distributions,
        )
        launch = build_frozen_launch_spec(
            inputs.execution_spec,
            workspace_path=Path(inputs.workspace_path),
            source_manifest_sha256=inputs.source_manifest_sha256,
            runtime_path=runtime_path,
            environment=tuple(sorted(cast(dict[str, str], environment_value).items())),
            execution_user=_string(result, "execution_user"),
            execution_group=_string(result, "execution_group"),
            launch_profile_version=_string(result, "launch_profile_version"),
            launch_policy_identity=_string(result, "policy_identity"),
        )
        return launch, observed

    async def _commit_launch_intent(
        self,
        inputs: _DispatchInputs,
        spec: FrozenLaunchSpec,
        launch_hash: str,
        observed: ObservedEnvironment,
    ) -> _FirstDispatchPermit | None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await self._repository.get_attempt_for_update(
                uow.session, inputs.attempt_id
            )
            if attempt is None or attempt.status != AttemptStatus.LAUNCH_READY.value:
                await uow.commit()
                return None
            trial = await self._repository.get_trial(uow.session, attempt.trial_id)
            batch = (
                await self._repository.get_batch_for_update(uow.session, trial.batch_id)
                if trial is not None
                else None
            )
            if (
                trial is None
                or batch is None
                or batch.start_requested_at is None
                or batch.cancel_requested_at is not None
                or _dispatch_inputs(attempt, trial, batch) != inputs
            ):
                await uow.commit()
                return None
            now = self._clock()
            attempt.observed_environment_json = cast(
                dict[str, object], observed_environment_value(observed)
            )
            attempt.observed_environment_recorded_at = now
            attempt.frozen_launch_spec_json = cast(
                dict[str, object], frozen_launch_spec_value(spec)
            )
            attempt.execution_spec_hash = launch_hash
            attempt.launch_intent_committed_at = now
            attempt.status = AttemptStatus.LAUNCH_PENDING.value
            await _append_attempt_event(
                uow,
                project_id=inputs.project_id,
                attempt_id=inputs.attempt_id,
                event_type="attempt.launch_intent_committed",
                payload={
                    "attempt_id": str(inputs.attempt_id),
                    "X": inputs.execution_identity,
                    "H": launch_hash,
                },
                occurred_at=now,
            )
            await uow.commit()
            return _FirstDispatchPermit(
                inputs.attempt_id,
                inputs.execution_identity,
                launch_hash,
                spec,
            )
        return None

    async def _load_coordinates(
        self, attempt_id: UUID, *, require_cancel: bool = False
    ) -> _PhysicalCoordinates | None:
        async with self._session_context_factory() as session:
            attempt = await self._repository.get_attempt(session, attempt_id)
            if attempt is None:
                raise ExecutionInputError("ATTEMPT_NOT_FOUND")
            if attempt.status not in {
                AttemptStatus.LAUNCH_PENDING.value,
                AttemptStatus.RUNNING.value,
                AttemptStatus.UNKNOWN.value,
                AttemptStatus.IDENTITY_CONFLICT.value,
            }:
                return None
            if attempt.execution_spec_hash is None:
                raise ExecutionInputError("PHYSICAL_IDENTITY_MISSING")
            trial = await self._repository.get_trial(session, attempt.trial_id)
            batch = (
                await self._repository.get_batch(session, trial.batch_id)
                if trial is not None
                else None
            )
            if trial is None or batch is None:
                raise ExecutionInputError("EXECUTION_INPUTS_MISSING")
            if require_cancel and batch.cancel_requested_at is None:
                return None
            return _PhysicalCoordinates(
                attempt.attempt_id,
                batch.project_id,
                attempt.supervisor_execution_identity,
                attempt.execution_spec_hash,
            )

    async def _mark_launch_not_accepted(self, attempt_id: UUID) -> None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await self._repository.get_attempt_for_update(uow.session, attempt_id)
            if attempt is None or attempt.status != AttemptStatus.LAUNCH_PENDING.value:
                await uow.commit()
                return
            trial = await self._repository.get_trial(uow.session, attempt.trial_id)
            batch = (
                await self._repository.get_batch(uow.session, trial.batch_id)
                if trial is not None
                else None
            )
            if batch is None or attempt.execution_spec_hash is None:
                raise ExecutionInputError("EXECUTION_INPUTS_MISSING")
            now = self._clock()
            _set_terminal(attempt, TerminalKind.LAUNCH_NOT_ACCEPTED, now)
            await _append_terminal_event(uow, attempt, batch.project_id, now)
            await uow.commit()

    async def _persist_observation(
        self,
        coordinates: _PhysicalCoordinates,
        observation: _Observation,
        *,
        reason: ReconciliationReason,
        coordinator_start_id: UUID | None,
    ) -> None:
        async with UnitOfWork(self._session_context_factory) as uow:
            attempt = await self._repository.get_attempt_for_update(
                uow.session, coordinates.attempt_id
            )
            if attempt is None or attempt.execution_spec_hash != coordinates.execution_spec_hash:
                await uow.commit()
                return
            if attempt.status not in {
                AttemptStatus.LAUNCH_PENDING.value,
                AttemptStatus.RUNNING.value,
                AttemptStatus.UNKNOWN.value,
                AttemptStatus.IDENTITY_CONFLICT.value,
            }:
                await uow.commit()
                return
            pre_status = attempt.status
            now = self._clock()
            if attempt.status != AttemptStatus.IDENTITY_CONFLICT.value:
                await self._apply_observation(
                    uow, attempt, coordinates.project_id, observation, now
                )
            if reason is ReconciliationReason.STARTUP:
                assert coordinator_start_id is not None
                if not await _startup_already_audited(
                    uow, attempt.attempt_id, coordinator_start_id
                ):
                    payload: EventJsonObject = {
                        "attempt_id": str(attempt.attempt_id),
                        "X": attempt.supervisor_execution_identity,
                        "H": coordinates.execution_spec_hash,
                        "coordinator_start_id": str(coordinator_start_id),
                        "pre_reconciliation_status": pre_status,
                        "physical_observation_kind": observation.kind.value,
                        "post_reconciliation_status": attempt.status,
                    }
                    if observation.boot_id is not None:
                        payload["physical_boot_id"] = observation.boot_id
                    if observation.invocation_id is not None:
                        payload["supervisor_invocation_id"] = observation.invocation_id
                    await _append_attempt_event(
                        uow,
                        project_id=coordinates.project_id,
                        attempt_id=attempt.attempt_id,
                        event_type="attempt.reconciled_after_restart",
                        payload=payload,
                        occurred_at=now,
                    )
            await uow.commit()

    async def _apply_observation(
        self,
        uow: UnitOfWork,
        attempt: AttemptRow,
        project_id: UUID,
        observation: _Observation,
        now: datetime,
    ) -> None:
        if observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT:
            attempt.status = AttemptStatus.IDENTITY_CONFLICT.value
            attempt.unknown_since = None
            attempt.attention_required_at = now
            attempt.execution_issue_code = "IDENTITY_CONFLICT"
            await _append_attempt_event(
                uow,
                project_id=project_id,
                attempt_id=attempt.attempt_id,
                event_type="attempt.identity_conflict_observed",
                payload={
                    "attempt_id": str(attempt.attempt_id),
                    "X": attempt.supervisor_execution_identity,
                    "H": cast(str, attempt.execution_spec_hash),
                    "observed_H": observation.observed_spec_hash or "unavailable",
                },
                occurred_at=now,
            )
            return
        if observation.kind is PhysicalObservationKind.ACTIVE_MATCH:
            first_start = attempt.physical_started_at is None
            attempt.status = AttemptStatus.RUNNING.value
            attempt.physical_started_at = attempt.physical_started_at or now
            attempt.supervisor_invocation_id = observation.invocation_id
            attempt.physical_boot_id = observation.boot_id
            attempt.supervisor_result = observation.result
            attempt.unknown_since = None
            attempt.attention_required_at = None
            attempt.execution_issue_code = None
            if first_start:
                await _append_attempt_event(
                    uow,
                    project_id=project_id,
                    attempt_id=attempt.attempt_id,
                    event_type="attempt.physical_started_observed",
                    payload={
                        "attempt_id": str(attempt.attempt_id),
                        "X": attempt.supervisor_execution_identity,
                        "H": cast(str, attempt.execution_spec_hash),
                    },
                    occurred_at=now,
                )
            return
        if observation.kind is PhysicalObservationKind.TERMINAL_MATCH:
            kind = (
                TerminalKind.PROCESS_EXITED
                if observation.exit_code is not None
                else TerminalKind.PROCESS_SIGNALED
                if observation.termination_signal is not None
                else TerminalKind.PROCESS_TERMINAL_UNCLASSIFIED
            )
            _set_terminal(
                attempt,
                kind,
                now,
                exit_code=observation.exit_code,
                termination_signal=observation.termination_signal,
                supervisor_result=observation.result,
            )
            attempt.supervisor_invocation_id = observation.invocation_id
            attempt.physical_boot_id = observation.boot_id
            await _append_terminal_event(uow, attempt, project_id, now)
            return
        if observation.kind in {
            PhysicalObservationKind.UNKNOWN,
            PhysicalObservationKind.NOT_FOUND,
        }:
            attempt.unknown_since = attempt.unknown_since or now
            if now - attempt.unknown_since >= self._unknown_grace:
                attempt.status = AttemptStatus.UNKNOWN.value
                attempt.attention_required_at = attempt.attention_required_at or now
                attempt.execution_issue_code = "PHYSICAL_STATE_UNKNOWN"


def build_frozen_launch_spec(
    logical: FrozenExecutionSpec,
    *,
    workspace_path: Path,
    source_manifest_sha256: str,
    runtime_path: str,
    environment: tuple[tuple[str, str], ...],
    execution_user: str,
    execution_group: str,
    launch_profile_version: str,
    launch_policy_identity: str,
) -> FrozenLaunchSpec:
    argv = list(logical.argv)
    output_dir = str(workspace_path / "runtime" / "output")
    for index in logical.output_dir_arg_indexes:
        argv[index] = output_dir
    entrypoint = workspace_path / "source" / logical.train_entrypoint_relative_path
    exec_argv = (runtime_path, str(entrypoint), *argv)
    if len(exec_argv) > 258 or any(
        len(token.encode("utf-8", "strict")) > 8192 or "\0" in token
        for token in exec_argv
    ) or sum(len(token.encode("utf-8", "strict")) + 1 for token in exec_argv) > 32768:
        raise ExecutionInputError("EXECUTION_ARGV_UNSUPPORTED")
    return FrozenLaunchSpec(
        "v1",
        str(workspace_path),
        source_manifest_sha256,
        logical.python_profile,
        runtime_path,
        logical.train_entrypoint_relative_path,
        logical.workdir_relative_path,
        tuple(argv),
        logical.dataset_reference_type,
        logical.dataset_argument,
        logical.dataset_reference_hash,
        environment,
        logical.timeout_seconds,
        execution_user,
        execution_group,
        launch_profile_version,
        launch_policy_identity,
    )


def _dispatch_inputs(attempt: AttemptRow, trial: TrialRow, batch: BatchRow) -> _DispatchInputs:
    manifest = attempt.source_manifest_json
    manifest_hash = attempt.source_manifest_sha256
    spec_json = attempt.frozen_execution_spec_json
    spec_hash = attempt.frozen_execution_spec_hash
    if manifest is None or manifest_hash is None or spec_json is None or spec_hash is None:
        raise ExecutionInputError("PREPARED_INPUTS_MISSING")
    spec = _execution_spec_from_json(spec_json)
    parsed_manifest = _source_manifest_from_json(manifest)
    if (
        frozen_execution_spec_hash(spec) != spec_hash
        or source_manifest_hash(parsed_manifest) != manifest_hash
    ):
        raise ExecutionInputError("PREPARED_INPUTS_TAMPERED")
    return _DispatchInputs(
        attempt.attempt_id,
        trial.trial_id,
        batch.batch_id,
        batch.project_id,
        attempt.supervisor_execution_identity,
        attempt.workspace_path,
        manifest,
        manifest_hash,
        spec,
        spec_hash,
    )


def _execution_spec_from_json(value: dict[str, object]) -> FrozenExecutionSpec:
    try:
        return FrozenExecutionSpec(
            schema_version=_string(value, "schema_version"),
            experiment_version_id=UUID(_string(value, "experiment_version_id")),
            experiment_version_identity=_string(value, "experiment_version_identity"),
            trial_identity=_string(value, "trial_identity"),
            train_entrypoint_relative_path=_string(value, "train_entrypoint_relative_path"),
            workdir_relative_path=_string(value, "workdir_relative_path"),
            python_profile=_string(value, "python_profile"),
            argv=_string_list(value.get("argv")),
            output_dir_arg_indexes=_int_list(value.get("output_dir_arg_indexes")),
            dataset_reference_type=_string(value, "dataset_reference_type"),
            dataset_argument=_string(value, "dataset_argument"),
            dataset_reference_hash=_string(value, "dataset_reference_hash"),
            environment_expectation_hash=_string(value, "environment_expectation_hash"),
            framework_requirements=_string_list(value.get("framework_requirements")),
            framework_distribution_names=_string_list(
                value.get("framework_distribution_names")
            ),
            device_requirement=_optional_string(value.get("device_requirement")),
            cuda_requirement=_optional_string(value.get("cuda_requirement")),
            timeout_seconds=_integer(value, "timeout_seconds"),
        )
    except (ExecutionInputError, TypeError, ValueError) as error:
        raise ExecutionInputError("PREPARED_INPUTS_TAMPERED") from error


def _source_manifest_from_json(value: dict[str, object]) -> SourceManifest:
    if set(value) != {
        "schema_version",
        "repository_snapshot_id",
        "repository_snapshot_identity",
        "entries",
    }:
        raise ExecutionInputError("PREPARED_INPUTS_TAMPERED")
    entries = value.get("entries")
    if type(entries) is not list:
        raise ExecutionInputError("PREPARED_INPUTS_TAMPERED")
    try:
        parsed_entries = tuple(
            SourceManifestEntry(
                relative_path=_string(item, "relative_path"),
                file_type=_string(item, "file_type"),
                file_mode=_string(item, "file_mode"),
                content_sha256=_string(item, "content_sha256"),
            )
            for item in entries
            if type(item) is dict
            and set(item)
            == {"relative_path", "file_type", "file_mode", "content_sha256"}
        )
        if len(parsed_entries) != len(entries):
            raise ValueError("invalid manifest entry")
        return SourceManifest(
            schema_version=_string(value, "schema_version"),
            repository_snapshot_id=UUID(_string(value, "repository_snapshot_id")),
            repository_snapshot_identity=_string(
                value, "repository_snapshot_identity"
            ),
            entries=parsed_entries,
        )
    except (ExecutionInputError, TypeError, ValueError) as error:
        raise ExecutionInputError("PREPARED_INPUTS_TAMPERED") from error


def _verify_source(workspace_value: str, manifest: dict[str, object]) -> None:
    workspace = Path(workspace_value)
    source = workspace / "source"
    entries = manifest.get("entries")
    if (
        type(entries) is not list
        or workspace.is_symlink()
        or not workspace.is_dir()
        or workspace.resolve(strict=True) != workspace
        or source.is_symlink()
        or not source.is_dir()
    ):
        raise ExecutionInputError("SOURCE_TAMPERED")
    expected_files: set[str] = set()
    expected_directories: set[str] = set()
    try:
        for raw in entries:
            if type(raw) is not dict:
                raise ExecutionInputError("SOURCE_TAMPERED")
            relative = _string(raw, "relative_path")
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
                raise ExecutionInputError("SOURCE_TAMPERED")
            path = source.joinpath(*pure.parts)
            file_type = _string(raw, "file_type")
            mode = _string(raw, "file_mode")
            expected_hash = _string(raw, "content_sha256")
            metadata = path.lstat()
            if file_type == "regular" and stat.S_ISREG(metadata.st_mode):
                content = path.read_bytes()
                expected_mode = 0o550 if mode == "100755" else 0o440
                if stat.S_IMODE(metadata.st_mode) != expected_mode:
                    raise ExecutionInputError("SOURCE_TAMPERED")
            elif file_type == "symlink" and stat.S_ISLNK(metadata.st_mode):
                content = os.readlink(path).encode("utf-8", "strict")
            else:
                raise ExecutionInputError("SOURCE_TAMPERED")
            if hashlib.sha256(content).hexdigest() != expected_hash:
                raise ExecutionInputError("SOURCE_TAMPERED")
            expected_files.add(relative)
            for parent in pure.parents:
                if str(parent) != ".":
                    expected_directories.add(str(parent))
        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        for path in source.rglob("*"):
            relative = path.relative_to(source).as_posix()
            (actual_directories if path.is_dir() and not path.is_symlink() else actual_files).add(
                relative
            )
        if actual_files != expected_files or actual_directories != expected_directories:
            raise ExecutionInputError("SOURCE_TAMPERED")
    except OSError as error:
        raise ExecutionInputError("SOURCE_TAMPERED") from error


def _successful_result(
    response: dict[str, object], operation: RunnerOperation
) -> dict[str, object]:
    if response.get("operation") != operation.value or response.get("outcome") != "ok":
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID")
    result = response.get("result")
    if type(result) is not dict:
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID")
    return result


def _runtime_profile(result: dict[str, object], name: str) -> dict[str, object]:
    profiles = result.get("runtime_profiles")
    if type(profiles) is not list:
        raise ExecutionInputError("RUNNER_INFO_INVALID")
    for profile in profiles:
        if type(profile) is dict and profile.get("python_profile") == name:
            return profile
    raise ExecutionInputError("PYTHON_PROFILE_NOT_ADMITTED")


def _observed_environment(
    value: object,
    *,
    expected_hash: str,
    expected_profile: str,
    expected_runtime: str,
    expected_distributions: tuple[str, ...],
) -> ObservedEnvironment:
    if type(value) is not dict:
        raise ExecutionInputError("RUNNER_INFO_INVALID")
    issues = _string_list(value.get("probe_issue_codes"))
    probe_failed = set(value) == {"probe_issue_codes"}
    versions = value.get("framework_versions", [])
    if not probe_failed and (
        set(value)
        != {
            "python_version",
            "framework_versions",
            "cuda_runtime_version",
            "cuda_device_count",
            "probe_issue_codes",
        }
        or type(versions) is not list
    ):
        raise ExecutionInputError("RUNNER_INFO_INVALID")
    try:
        observed_versions = (
            tuple(ObservedFrameworkVersion(name, None) for name in expected_distributions)
            if probe_failed
            else tuple(
                ObservedFrameworkVersion(
                    _string(cast(dict[str, object], item), "distribution_name"),
                    _optional_string(cast(dict[str, object], item).get("installed_version")),
                )
                for item in versions
                if type(item) is dict
            )
        )
        observed = ObservedEnvironment(
            schema_version="v1",
            environment_expectation_hash=expected_hash,
            python_profile=expected_profile,
            runtime_path=expected_runtime,
            python_version="unavailable" if probe_failed else _string(value, "python_version"),
            framework_versions=observed_versions,
            cuda_runtime_version=(
                None if probe_failed else _optional_integer(value.get("cuda_runtime_version"))
            ),
            cuda_device_count=(
                None if probe_failed else _optional_integer(value.get("cuda_device_count"))
            ),
            probe_issue_codes=issues,
        )
    except (TypeError, ValueError) as error:
        raise ExecutionInputError("RUNNER_INFO_INVALID") from error
    if tuple(version.distribution_name for version in observed.framework_versions) != (
        expected_distributions
    ):
        raise ExecutionInputError("RUNNER_INFO_INVALID")
    return observed


def _observation(response: dict[str, object]) -> _Observation:
    result = _successful_result(response, RunnerOperation.OBSERVE)
    try:
        kind = PhysicalObservationKind(_string(result, "kind"))
    except ValueError as error:
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID") from error
    return _Observation(
        kind,
        _optional_string(result.get("observed_spec_hash")),
        _optional_string(result.get("invocation_id")),
        _optional_string(result.get("boot_id")),
        _optional_string(result.get("result")),
        _optional_integer(result.get("exit_code")),
        _optional_integer(result.get("termination_signal")),
    )


def _unknown_observation() -> _Observation:
    return _Observation(PhysicalObservationKind.UNKNOWN, None, None, None, None, None, None)


def _set_terminal(
    attempt: AttemptRow,
    kind: TerminalKind,
    now: datetime,
    *,
    exit_code: int | None = None,
    termination_signal: int | None = None,
    supervisor_result: str | None = None,
) -> None:
    attempt.status = AttemptStatus.TERMINAL.value
    attempt.terminal_kind = kind.value
    attempt.terminal_at = now
    attempt.physical_ended_at = now
    attempt.exit_code = exit_code
    attempt.termination_signal = termination_signal
    attempt.supervisor_result = supervisor_result
    attempt.unknown_since = None
    attempt.attention_required_at = None
    attempt.execution_issue_code = None


async def _append_terminal_event(
    uow: UnitOfWork, attempt: AttemptRow, project_id: UUID, now: datetime
) -> None:
    payload: EventJsonObject = {
        "attempt_id": str(attempt.attempt_id),
        "X": attempt.supervisor_execution_identity,
        "H": cast(str, attempt.execution_spec_hash),
        "terminal_kind": cast(str, attempt.terminal_kind),
    }
    if attempt.exit_code is not None:
        payload["exit_code"] = attempt.exit_code
    if attempt.termination_signal is not None:
        payload["termination_signal"] = attempt.termination_signal
    if attempt.supervisor_result is not None:
        payload["supervisor_result"] = attempt.supervisor_result
    await _append_attempt_event(
        uow,
        project_id=project_id,
        attempt_id=attempt.attempt_id,
        event_type="attempt.terminal_observed",
        payload=payload,
        occurred_at=now,
    )


async def _append_attempt_event(
    uow: UnitOfWork,
    *,
    project_id: UUID,
    attempt_id: UUID,
    event_type: str,
    payload: EventJsonObject,
    occurred_at: datetime,
) -> None:
    await append_runtime_event(
        uow,
        RuntimeEventEnvelope(
            project_id=project_id,
            aggregate_type="attempt",
            aggregate_id=str(attempt_id),
            event_type=event_type,
            event_schema_version=1,
            payload=prepare_runtime_event_payload(
                event_type=event_type,
                event_schema_version=1,
                payload=payload,
            ),
            occurred_at=occurred_at,
            interaction_correlation_id=None,
            workflow_correlation_id=None,
            causation_id=None,
            actor_type="system",
            actor_id=None,
            visibility_class="internal",
            topic="runtime-events",
            partition_key=f"attempt:{attempt_id}",
        ),
        enqueue_outbox=False,
    )


async def _startup_already_audited(
    uow: UnitOfWork, attempt_id: UUID, coordinator_start_id: UUID
) -> bool:
    result = await uow.session.scalar(
        select(RuntimeEventRow.event_id).where(
            RuntimeEventRow.aggregate_type == "attempt",
            RuntimeEventRow.aggregate_id == str(attempt_id),
            RuntimeEventRow.event_type == "attempt.reconciled_after_restart",
            RuntimeEventRow.payload_json["coordinator_start_id"].astext
            == str(coordinator_start_id),
        )
    )
    return result is not None


def _result_kind(response: dict[str, object]) -> object:
    result = response.get("result")
    return result.get("kind") if type(result) is dict else None


def _string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str:
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID")
    return item


def _integer(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID")
    return item


def _optional_string(value: object) -> str | None:
    if value is None or type(value) is str:
        return value
    raise ExecutionInputError("RUNNER_RESPONSE_INVALID")


def _optional_integer(value: object) -> int | None:
    if value is None or type(value) is int:
        return value
    raise ExecutionInputError("RUNNER_RESPONSE_INVALID")


def _string_list(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID")
    return tuple(value)


def _int_list(value: object) -> tuple[int, ...]:
    if type(value) is not list or any(type(item) is not int for item in value):
        raise ExecutionInputError("RUNNER_RESPONSE_INVALID")
    return tuple(value)


def _under_root(candidate: Path, root: Path) -> bool:
    if not candidate.is_absolute():
        return False
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True
