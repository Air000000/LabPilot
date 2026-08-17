from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

import pytest
from sqlalchemy import select

from labpilot.execution.execution_service import (
    AttemptExecutionService,
    ReconciliationReason,
    _execution_spec_from_json,
)
from labpilot.execution.models import AttemptRow, BatchRow
from labpilot.execution.ports import ExecutionInputError
from labpilot.execution.runner_client import RunnerControlUnknownError
from labpilot.execution.runner_protocol import (
    RunnerInfoRequest,
    RunnerLaunchRequest,
    RunnerObserveRequest,
    RunnerRequest,
    RunnerStopRequest,
    encode_request,
)
from labpilot.execution.types import (
    AttemptStatus,
    TerminalKind,
    frozen_execution_spec_hash,
    frozen_execution_spec_value,
)
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow
from tests.integration.test_attempt_preparation import _attempt, _prepared_system


class FakeRunner:
    def __init__(
        self,
        workspace_root: Path,
        *,
        observations: list[dict[str, object]] | None = None,
        lose_launch_ack: bool = False,
    ) -> None:
        self.workspace_root = workspace_root
        self.observations = observations or [_active()]
        self.lose_launch_ack = lose_launch_ack
        self.requests: list[RunnerRequest] = []
        self._lock = Lock()

    def request(self, request: RunnerRequest) -> dict[str, object]:
        encode_request(request)
        with self._lock:
            self.requests.append(request)
            if isinstance(request, RunnerInfoRequest):
                assert request.inspection is not None
                return _response(
                    "INFO",
                    {
                        "runner_version": "candidate-b-v1",
                        "policy_identity": "f" * 64,
                        "workspace_root": str(self.workspace_root.resolve()),
                        "execution_user": "labpilot-train",
                        "execution_group": "labpilot-train",
                        "launch_profile_version": "candidate-b-v1",
                        "allowed_local_dataset_roots": ["/datasets"],
                        "max_runtime_seconds": 3600,
                        "boot_id": "boot-1",
                        "readiness_reason": "ready",
                        "runtime_profiles": [
                            {
                                "python_profile": request.inspection.python_profile,
                                "runtime_path": "/opt/python/bin/python",
                                "environment": {"PATH": "/opt/python/bin:/usr/bin"},
                            }
                        ],
                        "observed_environment": {
                            "python_version": "3.12.4",
                            "framework_versions": [
                                {
                                    "distribution_name": name,
                                    "installed_version": "2.7.0",
                                }
                                for name in request.inspection.framework_distribution_names
                            ],
                            "cuda_runtime_version": None,
                            "cuda_device_count": None,
                            "probe_issue_codes": ["CUDA_RUNTIME_UNAVAILABLE"],
                        },
                    },
                )
            if isinstance(request, RunnerLaunchRequest):
                if self.lose_launch_ack:
                    raise RunnerControlUnknownError(100)
                return _response(
                    "LAUNCH", {"kind": "created", "detail_code": "accepted"}
                )
            if isinstance(request, RunnerObserveRequest):
                value = (
                    self.observations.pop(0)
                    if len(self.observations) > 1
                    else self.observations[0]
                )
                return _response("OBSERVE", value)
            assert isinstance(request, RunnerStopRequest)
            return _response(
                "STOP", {"kind": "control_accepted", "detail_code": "accepted"}
            )

    def count(self, request_type: type[object]) -> int:
        return sum(isinstance(request, request_type) for request in self.requests)


def _response(operation: str, result: dict[str, object]) -> dict[str, object]:
    return {
        "version": "labpilot-runner:v1",
        "operation": operation,
        "outcome": "ok",
        "result": result,
    }


def _active() -> dict[str, object]:
    return {
        "kind": "active_match",
        "observed_spec_hash": None,
        "pid": 42,
        "invocation_id": "inv-1",
        "boot_id": "boot-1",
        "result": "success",
        "exit_code": None,
        "termination_signal": None,
    }


def _unknown() -> dict[str, object]:
    return {
        "kind": "unknown",
        "observed_spec_hash": None,
        "pid": None,
        "invocation_id": None,
        "boot_id": "boot-1",
        "result": None,
        "exit_code": None,
        "termination_signal": None,
    }


def _terminal(*, exit_code: int | None = 0) -> dict[str, object]:
    return {
        "kind": "terminal_match",
        "observed_spec_hash": None,
        "pid": None,
        "invocation_id": "inv-1",
        "boot_id": "boot-1",
        "result": "success",
        "exit_code": exit_code,
        "termination_signal": None,
    }


def _conflict() -> dict[str, object]:
    value = _unknown()
    value.update(kind="identity_conflict", observed_spec_hash="0" * 64)
    return value


async def _prepare(system: object) -> None:
    await system.service.prepare_attempt(system.attempt_id)  # type: ignore[attr-defined]


@pytest.mark.integration
async def test_rejected_info_profile_stops_before_launch_intent(
    isolated_database_url: str, tmp_path: Path
) -> None:
    class RejectedInfoRunner:
        def __init__(self) -> None:
            self.requests: list[RunnerRequest] = []

        def request(self, request: RunnerRequest) -> dict[str, object]:
            encode_request(request)
            self.requests.append(request)
            assert isinstance(request, RunnerInfoRequest)
            return {
                "version": "labpilot-runner:v1",
                "operation": "INFO",
                "outcome": "definite_non_acceptance",
                "result": {"detail_code": "python_profile_not_admitted"},
            }

    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = RejectedInfoRunner()
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,  # type: ignore[arg-type]
            workspace_root=system.workspace.parent,
        )

        with pytest.raises(
            ExecutionInputError, match="^PYTHON_PROFILE_NOT_ADMITTED$"
        ):
            await service.dispatch_attempt(system.attempt_id)

        row = await _attempt(system)
        assert row.status == AttemptStatus.LAUNCH_READY.value
        assert row.frozen_launch_spec_json is None
        assert row.execution_spec_hash is None
        assert row.launch_intent_committed_at is None
        assert row.observed_environment_json is None
        assert not any(
            isinstance(request, RunnerLaunchRequest) for request in runner.requests
        )
        async with system.database.session() as session:  # type: ignore[attr-defined]
            events = list(
                await session.scalars(
                    select(RuntimeEventRow.event_type).where(
                        RuntimeEventRow.aggregate_type == "attempt",
                        RuntimeEventRow.aggregate_id == str(system.attempt_id),
                        RuntimeEventRow.event_type
                        == "attempt.launch_intent_committed",
                    )
                )
            )
        assert events == []


@pytest.mark.integration
async def test_one_cas_winner_dispatches_and_audits_without_outbox(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(system.workspace.parent, observations=[_active()])
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )

        await asyncio.gather(
            service.dispatch_attempt(system.attempt_id),
            service.dispatch_attempt(system.attempt_id),
        )

        row = await _attempt(system)
        assert runner.count(RunnerLaunchRequest) == 1
        assert row.status == AttemptStatus.RUNNING.value
        assert row.observed_environment_recorded_at <= row.launch_intent_committed_at
        assert row.observed_environment_json["environment_expectation_hash"]
        assert row.physical_started_at is not None
        async with system.database.session() as session:  # type: ignore[attr-defined]
            event_types = list(
                await session.scalars(
                    select(RuntimeEventRow.event_type).where(
                        RuntimeEventRow.aggregate_type == "attempt",
                        RuntimeEventRow.aggregate_id == str(system.attempt_id),
                    )
                )
            )
            event_ids = select(RuntimeEventRow.event_id).where(
                RuntimeEventRow.aggregate_type == "attempt",
                RuntimeEventRow.aggregate_id == str(system.attempt_id),
            )
            outbox_count = len(
                list(
                    await session.scalars(
                        select(OutboxRecordRow.outbox_id).where(
                            OutboxRecordRow.event_id.in_(event_ids)
                        )
                    )
                )
            )
        assert event_types == [
            "attempt.launch_intent_committed",
            "attempt.physical_started_observed",
        ]
        assert outbox_count == 0


@pytest.mark.integration
async def test_info_inspection_canonicalizes_g1_names_without_rewriting_identity(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        async with system.database.session() as session:  # type: ignore[attr-defined]
            async with session.begin():
                row = await session.get(AttemptRow, system.attempt_id)
                assert row is not None and row.frozen_execution_spec_json is not None
                value = dict(row.frozen_execution_spec_json)
                value["framework_requirements"] = [
                    "torch==2.7.0",
                    "scikit-learn>=1.7",
                    "torch>=2",
                ]
                value["framework_distribution_names"] = [
                    "torch",
                    "scikit-learn",
                    "torch",
                ]
                logical = _execution_spec_from_json(value)
                original_hash = frozen_execution_spec_hash(logical)
                row.frozen_execution_spec_json = frozen_execution_spec_value(logical)
                row.frozen_execution_spec_hash = original_hash

        runner = FakeRunner(system.workspace.parent, observations=[_active()])
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )

        await service.dispatch_attempt(system.attempt_id)

        info = next(
            request
            for request in runner.requests
            if isinstance(request, RunnerInfoRequest)
        )
        assert info.inspection is not None
        assert info.inspection.framework_distribution_names == ("scikit-learn", "torch")
        row = await _attempt(system)
        assert row.frozen_execution_spec_hash == original_hash
        assert row.frozen_execution_spec_json is not None
        assert row.frozen_execution_spec_json["framework_distribution_names"] == [
            "torch",
            "scikit-learn",
            "torch",
        ]
        assert row.observed_environment_json is not None
        assert [
            item["distribution_name"]
            for item in row.observed_environment_json["framework_versions"]
        ] == ["scikit-learn", "torch"]


@pytest.mark.integration
async def test_ack_loss_observes_without_resend_and_short_job_is_terminal(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(
            system.workspace.parent,
            observations=[_terminal(exit_code=0)],
            lose_launch_ack=True,
        )
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )

        await service.dispatch_attempt(system.attempt_id)
        await service.dispatch_attempt(system.attempt_id)

        row = await _attempt(system)
        assert runner.count(RunnerLaunchRequest) == 1
        assert row.status == AttemptStatus.TERMINAL.value
        assert row.terminal_kind == TerminalKind.PROCESS_EXITED.value
        assert row.exit_code == 0


@pytest.mark.integration
async def test_identity_conflict_is_sticky_and_startup_audit_is_once_per_pass(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(system.workspace.parent, observations=[_conflict(), _unknown()])
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )
        await service.dispatch_attempt(system.attempt_id)
        start_id = uuid4()

        await service.reconcile_attempt(
            system.attempt_id,
            reason=ReconciliationReason.STARTUP,
            coordinator_start_id=start_id,
        )
        await service.reconcile_attempt(
            system.attempt_id,
            reason=ReconciliationReason.STARTUP,
            coordinator_start_id=start_id,
        )

        row = await _attempt(system)
        assert row.status == AttemptStatus.IDENTITY_CONFLICT.value
        assert row.execution_issue_code == "IDENTITY_CONFLICT"
        assert row.attention_required_at is not None
        assert row.terminal_at is None
        async with system.database.session() as session:  # type: ignore[attr-defined]
            events = list(
                await session.scalars(
                    select(RuntimeEventRow.event_type).where(
                        RuntimeEventRow.aggregate_id == str(system.attempt_id),
                        RuntimeEventRow.event_type == "attempt.reconciled_after_restart",
                    )
                )
            )
        assert events == ["attempt.reconciled_after_restart"]


@pytest.mark.integration
async def test_unknown_attention_grace_and_fresh_process_never_relaunches(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        now = datetime(2026, 8, 17, tzinfo=UTC)
        runner = FakeRunner(
            system.workspace.parent,
            observations=[_unknown(), _unknown(), _unknown(), _active()],
        )
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
            unknown_attention_grace_seconds=60,
            clock=lambda: now,
        )
        await service.dispatch_attempt(system.attempt_id)
        row = await _attempt(system)
        assert row.status == AttemptStatus.LAUNCH_PENDING.value
        assert row.unknown_since == now
        assert row.attention_required_at is None

        before_expiry = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
            unknown_attention_grace_seconds=60,
            clock=lambda: now + timedelta(seconds=30),
        )
        await before_expiry.dispatch_attempt(system.attempt_id)
        await before_expiry.reconcile_attempt(
            system.attempt_id, reason=ReconciliationReason.PERIODIC
        )
        row = await _attempt(system)
        assert row.status == AttemptStatus.LAUNCH_PENDING.value
        assert row.unknown_since == now
        assert row.attention_required_at is None

        after_expiry = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
            unknown_attention_grace_seconds=60,
            clock=lambda: now + timedelta(seconds=61),
        )
        await after_expiry.reconcile_attempt(
            system.attempt_id, reason=ReconciliationReason.PERIODIC
        )
        row = await _attempt(system)
        assert runner.count(RunnerLaunchRequest) == 1
        assert row.status == AttemptStatus.UNKNOWN.value
        assert row.attention_required_at is not None
        assert row.execution_issue_code == "PHYSICAL_STATE_UNKNOWN"

        await after_expiry.reconcile_attempt(
            system.attempt_id, reason=ReconciliationReason.PERIODIC
        )
        row = await _attempt(system)
        assert row.status == AttemptStatus.RUNNING.value
        assert row.unknown_since is None
        assert row.attention_required_at is None
        assert row.execution_issue_code is None


@pytest.mark.integration
async def test_running_retains_status_during_first_unknown_grace(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        now = datetime(2026, 8, 17, tzinfo=UTC)
        runner = FakeRunner(system.workspace.parent, observations=[_active(), _unknown()])
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
            clock=lambda: now,
        )
        await service.dispatch_attempt(system.attempt_id)

        await service.reconcile_attempt(
            system.attempt_id, reason=ReconciliationReason.PERIODIC
        )

        row = await _attempt(system)
        assert row.status == AttemptStatus.RUNNING.value
        assert row.unknown_since == now
        assert row.attention_required_at is None


@pytest.mark.integration
@pytest.mark.parametrize("recovery", [_active(), _terminal(exit_code=0)])
async def test_authoritative_observation_resolves_unknown_during_grace(
    isolated_database_url: str,
    tmp_path: Path,
    recovery: dict[str, object],
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        now = datetime(2026, 8, 17, tzinfo=UTC)
        runner = FakeRunner(system.workspace.parent, observations=[_unknown(), recovery])
        initial = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
            clock=lambda: now,
        )
        await initial.dispatch_attempt(system.attempt_id)
        row = await _attempt(system)
        assert row.status == AttemptStatus.LAUNCH_PENDING.value

        recovering = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
            clock=lambda: now + timedelta(seconds=30),
        )
        await recovering.reconcile_attempt(
            system.attempt_id, reason=ReconciliationReason.PERIODIC
        )

        row = await _attempt(system)
        expected = (
            AttemptStatus.RUNNING.value
            if recovery["kind"] == "active_match"
            else AttemptStatus.TERMINAL.value
        )
        assert row.status == expected
        assert row.unknown_since is None


@pytest.mark.integration
async def test_stop_ack_does_not_terminalize_but_natural_terminal_observation_does(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(
            system.workspace.parent,
            observations=[_active(), _active(), _terminal(exit_code=0)],
        )
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )
        await service.dispatch_attempt(system.attempt_id)
        async with system.database.session() as session:  # type: ignore[attr-defined]
            async with session.begin():
                batch = await session.get(BatchRow, system.batch_id)
                assert batch is not None
                batch.cancel_requested_at = datetime.now(UTC)

        await service.request_stop(system.attempt_id)

        row = await _attempt(system)
        assert runner.count(RunnerStopRequest) == 1
        assert row.status == AttemptStatus.TERMINAL.value
        assert row.terminal_kind == TerminalKind.PROCESS_EXITED.value


async def _request_cancel(system: object) -> None:
    async with system.database.session() as session:  # type: ignore[attr-defined]
        async with session.begin():
            batch = await session.get(BatchRow, system.batch_id)  # type: ignore[attr-defined]
            assert batch is not None
            batch.cancel_requested_at = datetime.now(UTC)


async def _restart_events(system: object) -> list[RuntimeEventRow]:
    async with system.database.session() as session:  # type: ignore[attr-defined]
        return list(
            await session.scalars(
                select(RuntimeEventRow).where(
                    RuntimeEventRow.aggregate_id == str(system.attempt_id),  # type: ignore[attr-defined]
                    RuntimeEventRow.event_type == "attempt.reconciled_after_restart",
                )
            )
        )


@pytest.mark.integration
async def test_startup_stop_terminal_observation_persists_truth_and_audit_without_stop(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(
            system.workspace.parent,
            observations=[_active(), _terminal(exit_code=0)],
        )
        service = AttemptExecutionService(
            system.database.session,
            runner,
            workspace_root=system.workspace.parent,
        )
        await service.dispatch_attempt(system.attempt_id)
        await _request_cancel(system)
        start_id = uuid4()

        await service.request_stop(
            system.attempt_id,
            reason=ReconciliationReason.STARTUP,
            coordinator_start_id=start_id,
        )

        row = await _attempt(system)
        events = await _restart_events(system)
        assert row.status == AttemptStatus.TERMINAL.value
        assert row.terminal_kind == TerminalKind.PROCESS_EXITED.value
        assert row.exit_code == 0
        assert runner.count(RunnerStopRequest) == 0
        assert len(events) == 1
        assert events[0].payload_json["coordinator_start_id"] == str(start_id)
        assert events[0].payload_json["physical_observation_kind"] == "terminal_match"
        assert events[0].payload_json["post_reconciliation_status"] == "terminal"


@pytest.mark.integration
async def test_startup_stop_active_observation_audits_then_stops_without_fabrication(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(
            system.workspace.parent,
            observations=[_active(), _active(), _active()],
        )
        service = AttemptExecutionService(
            system.database.session,
            runner,
            workspace_root=system.workspace.parent,
        )
        await service.dispatch_attempt(system.attempt_id)
        await _request_cancel(system)
        start_id = uuid4()

        await service.request_stop(
            system.attempt_id,
            reason=ReconciliationReason.STARTUP,
            coordinator_start_id=start_id,
        )

        row = await _attempt(system)
        events = await _restart_events(system)
        assert runner.count(RunnerStopRequest) == 1
        assert row.status == AttemptStatus.RUNNING.value
        assert row.terminal_at is None
        assert len(events) == 1
        assert events[0].payload_json["physical_observation_kind"] == "active_match"


@pytest.mark.integration
async def test_startup_stop_unknown_observation_audits_without_blind_stop(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(system.workspace.parent, observations=[_active(), _unknown()])
        service = AttemptExecutionService(
            system.database.session,
            runner,
            workspace_root=system.workspace.parent,
        )
        await service.dispatch_attempt(system.attempt_id)
        await _request_cancel(system)

        await service.request_stop(
            system.attempt_id,
            reason=ReconciliationReason.STARTUP,
            coordinator_start_id=uuid4(),
        )

        events = await _restart_events(system)
        assert runner.count(RunnerStopRequest) == 0
        assert len(events) == 1
        assert events[0].payload_json["physical_observation_kind"] == "unknown"


@pytest.mark.integration
async def test_identity_conflict_request_stop_has_zero_physical_calls(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(system.workspace.parent, observations=[_conflict(), _active()])
        service = AttemptExecutionService(
            system.database.session,
            runner,
            workspace_root=system.workspace.parent,
        )
        await service.dispatch_attempt(system.attempt_id)
        await _request_cancel(system)
        requests_before = len(runner.requests)

        await service.request_stop(
            system.attempt_id,
            reason=ReconciliationReason.STARTUP,
            coordinator_start_id=uuid4(),
        )

        row = await _attempt(system)
        assert row.status == AttemptStatus.IDENTITY_CONFLICT.value
        assert len(runner.requests) == requests_before
        assert runner.count(RunnerStopRequest) == 0


@pytest.mark.integration
async def test_launch_state_rolls_back_when_same_uow_audit_fails(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        runner = FakeRunner(system.workspace.parent)
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )

        async def fail_audit(*args: object, **kwargs: object) -> None:
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(
            "labpilot.execution.execution_service.append_runtime_event", fail_audit
        )
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await service.dispatch_attempt(system.attempt_id)

        row = await _attempt(system)
        assert row.status == AttemptStatus.LAUNCH_READY.value
        assert row.frozen_launch_spec_json is None
        assert row.observed_environment_json is None
        assert runner.count(RunnerLaunchRequest) == 0


@pytest.mark.integration
async def test_manifest_and_source_cannot_be_rewritten_together_before_dispatch(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        train = system.workspace / "source" / "train.py"
        train.chmod(0o640)
        content = b"print('rewritten')\n"
        train.write_bytes(content)
        train.chmod(0o440)
        async with system.database.session() as session:  # type: ignore[attr-defined]
            async with session.begin():
                attempt = await session.get(AttemptRow, system.attempt_id)
                assert attempt is not None and attempt.source_manifest_json is not None
                manifest = dict(attempt.source_manifest_json)
                entries = [dict(entry) for entry in manifest["entries"]]
                for entry in entries:
                    if entry["relative_path"] == "train.py":
                        entry["content_sha256"] = hashlib.sha256(content).hexdigest()
                manifest["entries"] = entries
                attempt.source_manifest_json = manifest
        runner = FakeRunner(system.workspace.parent)
        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            runner,
            workspace_root=system.workspace.parent,
        )

        with pytest.raises(ExecutionInputError, match="PREPARED_INPUTS_TAMPERED"):
            await service.dispatch_attempt(system.attempt_id)

        assert runner.requests == []


@pytest.mark.integration
async def test_stale_active_observation_cannot_resurrect_terminal_attempt(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _prepared_system(isolated_database_url, tmp_path) as system:
        await _prepare(system)
        initial = FakeRunner(system.workspace.parent, observations=[_active()])
        await AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            initial,
            workspace_root=system.workspace.parent,
        ).dispatch_attempt(system.attempt_id)

        class TerminalThenStaleActive:
            def __init__(self) -> None:
                self.count = 0
                self.lock = Lock()

            def request(self, request: RunnerRequest) -> dict[str, object]:
                assert isinstance(request, RunnerObserveRequest)
                with self.lock:
                    index = self.count
                    self.count += 1
                if index == 0:
                    return _response("OBSERVE", _terminal(exit_code=0))
                time.sleep(0.2)
                return _response("OBSERVE", _active())

        service = AttemptExecutionService(
            system.database.session,  # type: ignore[attr-defined]
            TerminalThenStaleActive(),
            workspace_root=system.workspace.parent,
        )

        await asyncio.gather(
            service.reconcile_attempt(
                system.attempt_id, reason=ReconciliationReason.PERIODIC
            ),
            service.reconcile_attempt(
                system.attempt_id, reason=ReconciliationReason.PERIODIC
            ),
        )

        row = await _attempt(system)
        assert row.status == AttemptStatus.TERMINAL.value
        assert row.terminal_kind == TerminalKind.PROCESS_EXITED.value
