from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from tests.acceptance.slice2.scenarios import SCENARIOS, scenario_operation_names

if TYPE_CHECKING:
    from labpilot.common.commands import CommandContext
    from labpilot.execution.models import AttemptRow


class NativeWorkerSelectionError(ValueError):
    pass


class NativeWorkerExecutionError(RuntimeError):
    pass


class _CrashBoundary(RuntimeError):
    pass


class _FaultRunner:
    def __init__(
        self,
        delegate: object,
        mode: str,
        barrier_root: Path | None = None,
        target_execution_identity: str | None = None,
    ) -> None:
        self.delegate = delegate
        self.mode = mode
        self.barrier_root = barrier_root
        self.target_execution_identity = target_execution_identity

    def request(self, request: object) -> dict[str, object]:
        from labpilot.execution.runner_client import RunnerPreSendError
        from labpilot.execution.runner_protocol import RunnerOperation

        operation = getattr(request, "operation", None)
        if self.mode == "info_unavailable" and operation == RunnerOperation.INFO:
            raise ConnectionError("test-admin INFO pause before launch intent")
        if self.mode == "prevent_launch" and operation == RunnerOperation.LAUNCH:
            raise RunnerPreSendError("test-admin pre-send boundary")
        if self.mode == "barrier_prevent_launch" and operation == RunnerOperation.LAUNCH:
            if self.barrier_root is None:
                raise NativeWorkerExecutionError("F20 barrier authority is missing")
            ready = self.barrier_root / ".f20-launch-boundary"
            release = self.barrier_root / ".f20-runner-stopped"
            descriptor = os.open(ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b"launch_intent_committed\n")
                handle.flush()
                os.fsync(handle.fileno())
            deadline = time.monotonic() + 30
            while not release.is_file():
                if time.monotonic() >= deadline:
                    raise NativeWorkerExecutionError("F20 host barrier timed out")
                time.sleep(0.05)
        if (
            self.mode == "observation_outage"
            and operation == RunnerOperation.OBSERVE
            and (
                self.target_execution_identity is None
                or getattr(request, "execution_identity", None) == self.target_execution_identity
            )
        ):
            raise ConnectionError("test-admin observation outage")
        if self.mode == "crash_before_launch" and operation == RunnerOperation.LAUNCH:
            raise _CrashBoundary("test-admin crash before Runner delegation")
        result = cast(
            dict[str, object],
            self.delegate.request(request),  # type: ignore[attr-defined]
        )
        if self.mode == "drop_launch" and operation == RunnerOperation.LAUNCH:
            raise ConnectionError("test-admin dropped real LAUNCH response")
        if self.mode == "drop_stop" and operation == RunnerOperation.STOP:
            raise ConnectionError("test-admin dropped real STOP response")
        if self.mode == "crash_after_observe" and operation == RunnerOperation.OBSERVE:
            raise _CrashBoundary("test-admin crash after real OBSERVE response")
        return result


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    scenario: str
    operation: str
    tested_sha: str
    python_profile: str


@dataclass(frozen=True, slots=True)
class FixtureProfile:
    trial_count: int = 1
    duration_seconds: float = 30.0
    exit_code: int = 0
    spawn_child: bool = False
    timeout_seconds: int = 120


def fixture_profile(scenario: str) -> FixtureProfile:
    if scenario not in SCENARIOS:
        raise ValueError("unknown fixture scenario")
    if scenario == "F03":
        return FixtureProfile(trial_count=6)
    if scenario == "F16":
        return FixtureProfile(trial_count=6)
    if scenario == "F17":
        return FixtureProfile(trial_count=6, duration_seconds=120)
    if scenario == "F11":
        return FixtureProfile(spawn_child=True)
    if scenario == "F12":
        return FixtureProfile(duration_seconds=0.2)
    if scenario in {"F01", "F02", "F08"}:
        return FixtureProfile(duration_seconds=0.5)
    if scenario == "F21":
        return FixtureProfile(duration_seconds=10.0, timeout_seconds=1)
    return FixtureProfile()


def worker_state_root(workspace_root: Path, tested_sha: str, scenario: str) -> Path:
    if (
        not workspace_root.is_absolute()
        or re.fullmatch(r"[0-9a-f]{40}", tested_sha) is None
        or scenario not in SCENARIOS
    ):
        raise ValueError("invalid native worker state authority")
    return workspace_root / ".task5-acceptance" / tested_sha / scenario


WorkerOperation = Callable[[WorkerRequest], dict[str, object]]


def _execute_registered(request: WorkerRequest) -> dict[str, object]:
    return asyncio.run(_execute_product_operation(request))


WORKER_OPERATIONS: dict[tuple[str, str], WorkerOperation] = {
    (scenario, operation): _execute_registered
    for scenario in SCENARIOS
    for operation in ("initialize", "snapshot", *scenario_operation_names(scenario))
}


def select_worker_operation(
    scenario: str,
    operation: str,
    *,
    before_select: Callable[[WorkerOperation], object] | None = None,
) -> WorkerOperation:
    try:
        selected = WORKER_OPERATIONS[(scenario, operation)]
    except KeyError as error:
        raise NativeWorkerSelectionError("native worker operation is not registered") from error
    if before_select is not None:
        before_select(selected)
    return selected


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--tested-sha", required=True)
    parser.add_argument("--python-profile", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    request = WorkerRequest(
        scenario=args.scenario,
        operation=args.operation,
        tested_sha=args.tested_sha,
        python_profile=args.python_profile,
    )
    try:
        result = select_worker_operation(request.scenario, request.operation)(request)
    except _CrashBoundary:
        return 86
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


async def _execute_product_operation(request: WorkerRequest) -> dict[str, object]:
    runtime = NativeWorkerRuntime(
        database_url=_required_environment("LABPILOT_DATABASE_URL"),
        workspace_root=Path(_required_environment("LABPILOT_ATTEMPT_WORKSPACE_ROOT")),
        content_store_root=Path(_required_environment("LABPILOT_REPOSITORY_CONTENT_STORE_ROOT")),
        runner_socket=Path(_required_environment("LABPILOT_RUNNER_SOCKET_PATH")),
        train_gid=int(_required_environment("LABPILOT_TRAIN_GID")),
        installation_identity=_required_environment("LABPILOT_INSTALLATION_IDENTITY"),
        request=request,
    )
    return await runtime.execute()


@dataclass(frozen=True, slots=True)
class WorkerState:
    schema_version: int
    scenario: str
    tested_sha: str
    source_root: str
    project_id: str
    repository_snapshot_id: str
    experiment_version_id: str
    batch_id: str


class NativeWorkerRuntime:
    def __init__(
        self,
        *,
        database_url: str,
        workspace_root: Path,
        content_store_root: Path,
        runner_socket: Path,
        train_gid: int,
        installation_identity: str,
        request: WorkerRequest,
    ) -> None:
        if (
            not database_url.startswith("postgresql+asyncpg://")
            or not workspace_root.is_absolute()
            or not content_store_root.is_absolute()
            or not runner_socket.is_absolute()
            or train_gid < 0
            or not installation_identity
        ):
            raise NativeWorkerExecutionError("native worker authority is invalid")
        self.database_url = database_url
        self.workspace_root = workspace_root
        self.content_store_root = content_store_root
        self.runner_socket = runner_socket
        self.train_gid = train_gid
        self.installation_identity = installation_identity
        self.request = request
        self.root = worker_state_root(workspace_root, request.tested_sha, request.scenario)
        self.state_path = self.root / "state.json"

    async def execute(self) -> dict[str, object]:
        if self.request.operation == "initialize":
            return await self._initialize()
        state = self._load_state()
        operation = self.request.operation
        if operation in {"start", "persist_start"}:
            await self._start_batch(state)
        elif operation == "delegate_real_launch":
            await self._start_batch(state)
            await self._scan_with_fault("drop_launch")
        elif operation == "commit_launch_pending":
            await self._start_batch(state)
            if self.request.scenario in {"F05", "F20"}:
                await self._scan_with_fault("info_unavailable")
        elif operation == "crash_before_runner":
            await self._scan_with_fault("crash_before_launch")
        elif operation == "delegate_real_stop":
            await self._scan_with_fault("drop_stop")
        elif operation == "cause_real_observation_outage":
            await self._scan_with_fault("observation_outage")
        elif operation == "poll_beyond_unknown_grace":
            await self._poll_unknown(state)
        elif operation == "crash_before_uow_commit":
            await self._scan_with_fault("crash_after_observe")
        elif operation == "attempt_dispatch" and self.request.scenario == "F20":
            await self._scan_with_fault("barrier_prevent_launch")
        elif operation == "dirty_worktree":
            with (self.root / "repository" / "train.py").open("ab") as handle:
                handle.write(b"\n# dirty bytes excluded from captured snapshot\n")
                handle.flush()
                os.fsync(handle.fileno())
        elif operation == "prepare":
            await self._start_batch(state)
            await self._scan_with_fault("info_unavailable")
        elif operation == "tamper_prelaunch":
            await self._tamper_prepared_source(state)
        elif operation == "create_real_wrong_h_unit":
            await self._create_wrong_h_unit(state)
        elif operation == "establish_unknown":
            await self._establish_one_unknown(state)
        elif operation == "prepare_mixed_recovery_set":
            await self._prepare_mixed_recovery_set(state)
        elif operation == "capture_order":
            await self._poll_startup_audits(state, minimum=4)
        elif operation == "persist_pre_reboot_identity":
            await self._start_batch(state)
            await self._poll_status(state, {"running"}, tick=True)
        elif operation == "verify_new_boot":
            pass
        elif operation == "observe_old_x":
            await self._observe_post_reboot(state)
        elif operation in {
            "create_real_batch",
            "capture_exact_source",
            "create_six_trials",
            "drop_launch_response",
            "drop_stop_response",
            "delegate_real_observe",
        }:
            pass
        elif operation == "observe":
            await self._coordinator_scan()
        elif operation == "observe_systemd_timeout":
            await self._poll_terminal(state, tick=True)
        elif operation in {"stop_exact_match", "fresh_observe"}:
            await self._coordinator_scan()
        elif operation in {"persist_cancel", "race_cancel_with_exit"}:
            await self._cancel_batch(state)
        elif operation in {
            "dispatch",
            "scan_until_capacity",
            "scan",
            "attempt_dispatch",
            "prepare",
            "startup_reconcile",
            "reconcile",
            "scan_stop",
            "observe_conflict",
            "fill_remaining_slots",
            "queue_excess",
            "query_attempt",
            "repeat_observe",
        }:
            await self._coordinator_scan(startup=operation == "startup_reconcile")
        elif operation in {
            "launch_active",
            "launch_with_child",
            "launch_short_workload",
            "launch_beyond_timeout",
        }:
            await self._start_batch(state)
            await self._poll_status(state, {"running"}, tick=True)
        elif operation == "wait_real_exit":
            await self._poll_workload_exit(state)
        elif operation in {"observe_terminal", "reconcile_terminal"}:
            await self._poll_terminal(state, tick=True)
        return await self._snapshot(state, operation=operation)

    async def _tamper_prepared_source(self, state: WorkerState) -> None:
        from sqlalchemy import select

        from labpilot.db.session import Database
        from labpilot.execution.models import AttemptRow, TrialRow

        database = Database(self.database_url)
        try:
            async with database.session() as session:
                workspace = await session.scalar(
                    select(AttemptRow.workspace_path)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .where(TrialRow.batch_id == UUID(state.batch_id))
                )
            if not isinstance(workspace, str):
                raise NativeWorkerExecutionError("prepared Attempt workspace is missing")
            source = Path(workspace) / "source" / "train.py"
            source.chmod(source.stat().st_mode | 0o200)
            with source.open("ab") as handle:
                handle.write(b"\n# Task 5 deliberate prelaunch tamper\n")
                handle.flush()
                os.fsync(handle.fileno())
            source.chmod(source.stat().st_mode & ~0o222)
        finally:
            await database.dispose()

    async def _create_wrong_h_unit(self, state: WorkerState) -> None:
        from sqlalchemy import select

        from labpilot.db.session import Database
        from labpilot.execution.models import AttemptRow, TrialRow

        await self._start_batch(state)
        try:
            await self._scan_with_fault("crash_before_launch")
        except _CrashBoundary:
            pass
        database = Database(self.database_url)
        try:
            async with database.session() as session:
                attempt = await session.scalar(
                    select(AttemptRow)
                    .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                    .where(TrialRow.batch_id == UUID(state.batch_id))
                )
            if attempt is None or attempt.frozen_launch_spec_json is None:
                raise NativeWorkerExecutionError("F15 expected launch identity is missing")
            await self._launch_wrong_h(attempt)
        finally:
            await database.dispose()

    async def _launch_wrong_h(self, attempt: AttemptRow) -> None:
        from dataclasses import replace

        from labpilot.execution.runner_client import RunnerClient
        from labpilot.execution.runner_protocol import (
            PROTOCOL_VERSION,
            RunnerLaunchRequest,
            RunnerOperation,
            _spec_from_wire,
        )
        from labpilot.execution.types import frozen_launch_spec_hash

        if attempt.frozen_launch_spec_json is None:
            raise NativeWorkerExecutionError("wrong-H launch spec is missing")
        expected = _spec_from_wire(attempt.frozen_launch_spec_json)
        conflicting = replace(expected, timeout_seconds=expected.timeout_seconds + 1)
        conflicting_hash = frozen_launch_spec_hash(conflicting)
        response = await asyncio.to_thread(
            RunnerClient(str(self.runner_socket), timeout_seconds=10).request,
            RunnerLaunchRequest(
                PROTOCOL_VERSION,
                RunnerOperation.LAUNCH,
                attempt.supervisor_execution_identity,
                conflicting_hash,
                conflicting,
                "first_dispatch_proven",
            ),
        )
        if response.get("outcome") != "ok":
            raise NativeWorkerExecutionError("wrong-H unit was not accepted")

    async def _dispatch_attempt_with_fault(self, attempt_id: UUID, mode: str) -> None:
        from labpilot.coordinator.worker import build_coordinator
        from labpilot.db.session import Database
        from labpilot.execution.runner_client import RunnerClient
        from labpilot.settings import get_settings

        settings = get_settings()
        socket_path = settings.runner_socket_path
        if socket_path is None:
            raise NativeWorkerExecutionError("Runner socket authority is missing")
        database = Database(self.database_url)
        try:
            coordinator = build_coordinator(settings, database)
            coordinator._execution._runner = _FaultRunner(
                RunnerClient(
                    str(socket_path),
                    timeout_seconds=settings.runner_request_timeout_seconds,
                ),
                mode,
                self.root,
            )
            await coordinator._execution.dispatch_attempt(attempt_id)
        finally:
            await database.dispose()

    async def _reconcile_attempt_with_fault(self, attempt_id: UUID, mode: str) -> None:
        from labpilot.coordinator.worker import build_coordinator
        from labpilot.db.session import Database
        from labpilot.execution.execution_service import ReconciliationReason
        from labpilot.execution.runner_client import RunnerClient
        from labpilot.settings import get_settings

        settings = get_settings()
        socket_path = settings.runner_socket_path
        if socket_path is None:
            raise NativeWorkerExecutionError("Runner socket authority is missing")
        database = Database(self.database_url)
        try:
            coordinator = build_coordinator(settings, database)
            coordinator._execution._runner = _FaultRunner(
                RunnerClient(
                    str(socket_path),
                    timeout_seconds=settings.runner_request_timeout_seconds,
                ),
                mode,
                self.root,
            )
            await coordinator._execution.reconcile_attempt(
                attempt_id, reason=ReconciliationReason.PERIODIC
            )
        finally:
            await database.dispose()

    async def _prepare_mixed_recovery_set(self, state: WorkerState) -> None:
        from sqlalchemy import select

        from labpilot.db.session import Database
        from labpilot.execution.models import AttemptRow, TrialRow
        from labpilot.settings import get_settings

        await self._start_batch(state)
        await self._scan_with_fault("info_unavailable")
        database = Database(self.database_url)
        try:
            async with database.session() as session:
                attempts = list(
                    (
                        await session.scalars(
                            select(AttemptRow)
                            .join(TrialRow, TrialRow.trial_id == AttemptRow.trial_id)
                            .where(TrialRow.batch_id == UUID(state.batch_id))
                            .order_by(TrialRow.trial_ordinal)
                        )
                    ).all()
                )
            if len(attempts) != 6:
                raise NativeWorkerExecutionError("F17 requires six prepared Attempts")
            await self._dispatch_attempt_with_fault(attempts[0].attempt_id, "none")
            try:
                await self._dispatch_attempt_with_fault(
                    attempts[1].attempt_id, "crash_before_launch"
                )
            except _CrashBoundary:
                pass
            await self._dispatch_attempt_with_fault(attempts[2].attempt_id, "none")
            target = attempts[2].supervisor_execution_identity
            deadline = time.monotonic() + get_settings().unknown_attention_grace_seconds + 10
            while True:
                await self._reconcile_attempt_with_fault(
                    attempts[2].attempt_id, "observation_outage"
                )
                current = await self._snapshot(state, operation="prepare_mixed_recovery_set")
                values = cast(list[dict[str, object]], current["attempts"])
                if any(
                    item["execution_identity"] == target and item["status"] == "unknown"
                    for item in values
                ):
                    break
                if time.monotonic() >= deadline:
                    raise NativeWorkerExecutionError("F17 UNKNOWN did not converge")
                await asyncio.sleep(0.2)
            try:
                await self._dispatch_attempt_with_fault(
                    attempts[3].attempt_id, "crash_before_launch"
                )
            except _CrashBoundary:
                pass
            async with database.session() as session:
                conflict = await session.get(AttemptRow, attempts[3].attempt_id)
            if conflict is None:
                raise NativeWorkerExecutionError("F17 conflict Attempt is missing")
            await self._launch_wrong_h(conflict)
            await self._coordinator_scan()
        finally:
            await database.dispose()

    async def _initialize(self) -> dict[str, object]:
        if self.state_path.exists():
            return await self._snapshot(self._load_state(), operation="initialize")
        self.root.mkdir(parents=True, mode=0o750, exist_ok=False)
        source = self.root / "repository"
        source.mkdir(mode=0o750)
        fixture = Path(__file__).with_name("training_fixture") / "train.py"
        (source / "train.py").write_bytes(fixture.read_bytes())
        (source / "train.py").chmod(0o750)
        (source / "README.md").write_text("LabPilot Task 5 native fixture\n")
        await self._git(source, "init", "-q", "-b", "main")
        await self._git(source, "add", "train.py", "README.md")
        await self._git(
            source,
            "-c",
            "user.name=LabPilot Task 5",
            "-c",
            "user.email=task5@example.invalid",
            "commit",
            "-qm",
            "native fixture",
        )
        state = await self._create_product_state(source)
        self._save_state(state)
        return await self._snapshot(state, operation="initialize")

    async def _create_product_state(self, source: Path) -> WorkerState:
        from labpilot.common.commands import CommandActor, CommandContext
        from labpilot.context.content_store import ContentAddressedStore
        from labpilot.context.git_adapter import GitAdapter
        from labpilot.context.models import RepositoryRegistrationRow
        from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
        from labpilot.context.repository_identities import repository_registration_identity
        from labpilot.context.repository_service import RepositoryApplicationService
        from labpilot.context.repository_source import RepositorySnapshotSourceService
        from labpilot.context.repository_types import CaptureRepositorySnapshot
        from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
        from labpilot.db.session import Database
        from labpilot.db.uow import UnitOfWork
        from labpilot.execution.batch_service import BatchApplicationService, CreateBatch
        from labpilot.experiments.models import ProjectRow
        from labpilot.experiments.proposal_service import ProposalApplicationService
        from labpilot.experiments.proposal_types import (
            ArtifactDeclaration,
            DatasetReference,
            EnvironmentExpectation,
            MetricDeclaration,
            ProposalPayload,
            ValidationRule,
        )
        from labpilot.experiments.proposal_verifier import ProposalVerifier

        profile = fixture_profile(self.request.scenario)
        database = Database(self.database_url)
        project_id = self._uuid("project")
        registration_id = self._uuid("registration")
        context = CommandContext(
            installation_identity=self.installation_identity,
            actor=CommandActor(actor_type="test_admin", actor_id="task5-native"),
            interaction_correlation_id=f"task5-{self.request.scenario.lower()}",
        )
        canonical_root = str(source.resolve(strict=True))
        try:
            async with UnitOfWork(database.session) as uow:
                uow.session.add(
                    ProjectRow(
                        project_id=project_id,
                        project_identity=self._digest("project-identity"),
                        name=f"Task 5 {self.request.scenario}",
                        description=None,
                        status="active",
                        primary_repository_registration_id=None,
                        artifact_root_ref="task5-native",
                        row_version=1,
                    )
                )
                await uow.session.flush()
                uow.session.add(
                    RepositoryRegistrationRow(
                        repository_registration_id=registration_id,
                        project_id=project_id,
                        repository_type="local_git",
                        canonical_root_path=canonical_root,
                        root_path_identity=repository_registration_identity(
                            canonical_root_path=canonical_root,
                            repository_type="local_git",
                        ),
                        remote_origin_sanitized=None,
                        default_branch="main",
                        status="active",
                        last_access_check_at=None,
                        row_version=1,
                    )
                )
                await uow.session.flush()
                project = await uow.session.get(ProjectRow, project_id)
                assert project is not None
                project.primary_repository_registration_id = registration_id
                await uow.commit()
            store = ContentAddressedStore(self.content_store_root)
            git = GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=5)
            repositories = RepositoryApplicationService(
                database.session,
                git_adapter=git,
                allowed_roots=(self.root,),
                content_store=store,
            )
            captured = await repositories.capture_repository_snapshot(
                context=context,
                idempotency_key=f"capture-{self.request.scenario.lower()}",
                command=CaptureRepositorySnapshot(registration_id),
            )
            snapshot_id = UUID(captured.envelope.primary_result_id)
            source_service = RepositorySnapshotSourceService(
                database.session,
                git_adapter=git,
                content_store=store,
            )
            descriptor = await source_service.describe(snapshot_id)
            facts = ProposalRepositoryFactAdapter(source_service)
            proposals = ProposalApplicationService(
                database.session,
                repository_facts=facts,
                verifier=ProposalVerifier(facts),
            )
            argv = [
                "--seed",
                "{seed}",
                "--value",
                "{parameter:value}",
                "--dataset",
                "{dataset}",
                "--output-dir",
                "{output_dir}",
                "--duration",
                str(profile.duration_seconds),
                "--exit-code",
                str(profile.exit_code),
            ]
            if profile.spawn_child:
                argv.append("--spawn-child")
            payload = ProposalPayload(
                goal=f"Task 5 {self.request.scenario}",
                experiment_type="reproduction",
                repository_snapshot_id=snapshot_id,
                repository_snapshot_identity=descriptor.repository_snapshot_identity,
                train_entrypoint="train.py",
                eval_entrypoint=None,
                workdir=".",
                parameter_matrix={"value": tuple(range(profile.trial_count))},
                seeds=(7,),
                dataset_reference=DatasetReference(
                    reference_type="uri",
                    path_or_uri="task5://dataset",
                    stable_identity="task5-dataset-v1",
                    reproducibility_level="strict",
                ),
                environment_expectation=EnvironmentExpectation(
                    python_profile=self.request.python_profile,
                    framework_requirements=(),
                    device_requirement=None,
                    cuda_requirement=None,
                ),
                metrics=(MetricDeclaration("completion", "workload_oracle", "test"),),
                artifacts=(
                    ArtifactDeclaration("workload_oracle", "workload-test-oracle.json", True),
                ),
                validation_rules=(
                    ValidationRule("artifact_exists", "workload_oracle"),
                    ValidationRule("metric_finite", "completion"),
                ),
                timeout_seconds=profile.timeout_seconds,
                max_retries=0,
                train_argv_template=tuple(argv),
            )
            revision = await proposals.create_agent_revision(
                context=context,
                project_id=project_id,
                origin_agent_step_id=self._uuid("agent-step"),
                payload=payload,
            )
            verification = await proposals.verify_revision(
                context=context,
                proposal_revision_id=revision.proposal_revision_id,
            )
            version = await proposals.approve_revision(
                context=context,
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
            batches = BatchApplicationService(
                database.session,
                version_semantics=VersionExecutionSemanticsAdapter(database.session),
            )
            created = await batches.create_batch(
                context=context,
                idempotency_key=f"batch-{self.request.scenario.lower()}",
                command=CreateBatch(version.experiment_version_id),
            )
            return WorkerState(
                schema_version=1,
                scenario=self.request.scenario,
                tested_sha=self.request.tested_sha,
                source_root=canonical_root,
                project_id=str(project_id),
                repository_snapshot_id=str(snapshot_id),
                experiment_version_id=str(version.experiment_version_id),
                batch_id=created.envelope.primary_result_id,
            )
        finally:
            await database.dispose()

    async def _start_batch(self, state: WorkerState) -> None:
        from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
        from labpilot.db.session import Database
        from labpilot.execution.batch_service import BatchApplicationService, StartBatch
        from labpilot.execution.models import BatchRow

        database = Database(self.database_url)
        try:
            async with database.session() as session:
                batch = await session.get(BatchRow, UUID(state.batch_id))
                if batch is None:
                    raise NativeWorkerExecutionError("native Batch is missing")
                row_version = batch.row_version
                already_started = batch.start_requested_at is not None
            if already_started:
                return
            service = BatchApplicationService(
                database.session,
                version_semantics=VersionExecutionSemanticsAdapter(database.session),
            )
            await service.start_batch(
                context=self._context(),
                idempotency_key=f"start-{state.scenario.lower()}",
                command=StartBatch(UUID(state.batch_id), row_version),
            )
        finally:
            await database.dispose()

    async def _cancel_batch(self, state: WorkerState) -> None:
        from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
        from labpilot.db.session import Database
        from labpilot.execution.batch_service import BatchApplicationService, CancelBatch
        from labpilot.execution.models import BatchRow

        database = Database(self.database_url)
        try:
            async with database.session() as session:
                batch = await session.get(BatchRow, UUID(state.batch_id))
                if batch is None:
                    raise NativeWorkerExecutionError("native Batch is missing")
                row_version = batch.row_version
                already_cancelled = batch.cancel_requested_at is not None
            if already_cancelled:
                return
            service = BatchApplicationService(
                database.session,
                version_semantics=VersionExecutionSemanticsAdapter(database.session),
            )
            await service.cancel_batch(
                context=self._context(),
                idempotency_key=f"cancel-{state.scenario.lower()}",
                command=CancelBatch(UUID(state.batch_id), row_version),
            )
        finally:
            await database.dispose()

    async def _coordinator_scan(self, *, startup: bool = False) -> None:
        from uuid import uuid4

        from labpilot.coordinator.worker import build_coordinator
        from labpilot.db.session import Database
        from labpilot.settings import get_settings

        database = Database(self.database_url)
        try:
            coordinator = build_coordinator(get_settings(), database)
            if startup:
                await coordinator.run_startup_barrier(coordinator_start_id=uuid4())
            await coordinator.run_scan()
        finally:
            await database.dispose()

    async def _scan_with_fault(
        self, mode: str, *, target_execution_identity: str | None = None
    ) -> None:
        from labpilot.coordinator.worker import build_coordinator
        from labpilot.db.session import Database
        from labpilot.execution.runner_client import RunnerClient
        from labpilot.settings import get_settings

        settings = get_settings()
        socket_path = settings.runner_socket_path
        if socket_path is None:
            raise NativeWorkerExecutionError("Runner socket authority is missing")
        database = Database(self.database_url)
        try:
            coordinator = build_coordinator(settings, database)
            coordinator._execution._runner = _FaultRunner(
                RunnerClient(
                    str(socket_path),
                    timeout_seconds=settings.runner_request_timeout_seconds,
                ),
                mode,
                self.root,
                target_execution_identity,
            )
            await coordinator.run_scan()
        finally:
            await database.dispose()

    async def _establish_one_unknown(self, state: WorkerState) -> None:
        from labpilot.settings import get_settings

        await self._start_batch(state)
        await self._coordinator_scan()
        initial = await self._snapshot(state, operation="establish_unknown")
        attempts = cast(list[dict[str, object]], initial["attempts"])
        physical = [item for item in attempts if item["status"] in {"running", "launch_pending"}]
        if len(physical) != 4 or not isinstance(physical[0]["execution_identity"], str):
            raise NativeWorkerExecutionError("F16 did not establish four physical slots")
        target = physical[0]["execution_identity"]
        deadline = time.monotonic() + get_settings().unknown_attention_grace_seconds + 10
        while True:
            await self._scan_with_fault("observation_outage", target_execution_identity=target)
            value = await self._snapshot(state, operation="establish_unknown")
            current = cast(list[dict[str, object]], value["attempts"])
            if sum(item["status"] == "unknown" for item in current) == 1:
                return
            if time.monotonic() >= deadline:
                raise NativeWorkerExecutionError("F16 one-UNKNOWN fixture did not converge")
            await asyncio.sleep(0.2)

    async def _poll_attempts(self, state: WorkerState, *, minimum: int) -> None:
        await self._poll(
            state,
            lambda value: len(cast(list[object], value["attempts"])) >= minimum,
            reason="Attempt creation",
            tick=True,
        )

    async def _poll_status(
        self, state: WorkerState, statuses: set[str], *, tick: bool = False
    ) -> None:
        await self._poll(
            state,
            lambda value: any(
                item["status"] in statuses
                for item in cast(list[dict[str, object]], value["attempts"])
            ),
            reason=f"Attempt status {sorted(statuses)}",
            tick=tick,
        )

    async def _poll_terminal(self, state: WorkerState, *, tick: bool = False) -> None:
        await self._poll(
            state,
            lambda value: bool(value["attempts"])
            and all(
                item["terminal_at"] is not None
                for item in cast(list[dict[str, object]], value["attempts"])
            ),
            reason="Attempt terminal evidence",
            tick=tick,
        )

    async def _poll_workload_exit(self, state: WorkerState) -> None:
        await self._poll(
            state,
            lambda value: bool(value["workload_oracles"])
            and all(
                item.get("natural_exit") is True
                for item in cast(list[dict[str, object]], value["workload_oracles"])
            ),
            reason="real workload natural exit",
        )

    async def _poll_unknown(self, state: WorkerState) -> None:
        from labpilot.settings import get_settings

        deadline = time.monotonic() + get_settings().unknown_attention_grace_seconds + 10
        while True:
            await self._scan_with_fault("observation_outage")
            value = await self._snapshot(state, operation="poll_beyond_unknown_grace")
            attempts = cast(list[dict[str, object]], value["attempts"])
            if attempts and all(item["status"] == "unknown" for item in attempts):
                return
            if time.monotonic() >= deadline:
                raise NativeWorkerExecutionError("UNKNOWN grace did not converge")
            await asyncio.sleep(0.2)

    async def _poll_startup_audits(self, state: WorkerState, *, minimum: int) -> None:
        await self._poll(
            state,
            lambda value: sum(
                item.get("event_type") == "attempt.reconciled_after_restart"
                for item in cast(list[dict[str, object]], value["runtime_events"])
            )
            >= minimum,
            reason="startup reconciliation audit barrier",
        )

    async def _observe_post_reboot(self, state: WorkerState) -> None:
        from uuid import uuid4

        from labpilot.coordinator.worker import build_coordinator
        from labpilot.db.session import Database
        from labpilot.settings import get_settings

        database = Database(self.database_url)
        try:
            coordinator = build_coordinator(get_settings(), database)
            await coordinator.run_startup_barrier(coordinator_start_id=uuid4())
        finally:
            await database.dispose()
        await self._poll(
            state,
            lambda value: all(
                item["status"] in {"terminal", "unknown"}
                for item in cast(list[dict[str, object]], value["attempts"])
            ),
            reason="post-reboot old-X convergence",
            tick=True,
        )

    async def _poll(
        self,
        state: WorkerState,
        predicate: Callable[[dict[str, object]], bool],
        *,
        reason: str,
        timeout_seconds: float = 180,
        tick: bool = False,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if tick:
                await self._coordinator_scan()
            value = await self._snapshot(state, operation="poll")
            if predicate(value):
                return value
            if time.monotonic() >= deadline:
                raise NativeWorkerExecutionError(f"bounded poll failed: {reason}")
            await asyncio.sleep(0.2)

    async def _snapshot(self, state: WorkerState, *, operation: str) -> dict[str, object]:
        from sqlalchemy import select

        from labpilot.db.session import Database
        from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
        from labpilot.observability.models import RuntimeEventRow

        database = Database(self.database_url)
        try:
            async with database.session() as session:
                batch = await session.get(BatchRow, UUID(state.batch_id))
                if batch is None:
                    raise NativeWorkerExecutionError("native Batch is missing")
                trials = tuple(
                    (
                        await session.scalars(
                            select(TrialRow)
                            .where(TrialRow.batch_id == batch.batch_id)
                            .order_by(TrialRow.trial_ordinal)
                        )
                    ).all()
                )
                trial_ids = [row.trial_id for row in trials]
                attempts = tuple(
                    (
                        await session.scalars(
                            select(AttemptRow)
                            .where(AttemptRow.trial_id.in_(trial_ids))
                            .order_by(AttemptRow.created_at, AttemptRow.attempt_id)
                        )
                    ).all()
                )
                attempt_ids = {str(row.attempt_id) for row in attempts}
                events = (
                    tuple(
                        (
                            await session.scalars(
                                select(RuntimeEventRow)
                                .where(
                                    RuntimeEventRow.aggregate_type == "attempt",
                                    RuntimeEventRow.aggregate_id.in_(attempt_ids),
                                )
                                .order_by(RuntimeEventRow.occurred_at, RuntimeEventRow.event_id)
                            )
                        ).all()
                    )
                    if attempt_ids
                    else ()
                )
            return {
                "scenario": state.scenario,
                "operation": operation,
                "tested_sha": state.tested_sha,
                "installation_identity": self.installation_identity,
                "repository_train_sha256": self._file_sha256(self.root / "repository" / "train.py"),
                "batch": {
                    "batch_id": state.batch_id,
                    "row_version": batch.row_version,
                    "started": batch.start_requested_at is not None,
                    "cancelled": batch.cancel_requested_at is not None,
                },
                "trials": [
                    {
                        "trial_id": str(row.trial_id),
                        "ordinal": row.trial_ordinal,
                    }
                    for row in trials
                ],
                "attempts": [self._attempt_value(row) for row in attempts],
                "workload_oracles": [
                    value
                    for row in attempts
                    if (value := self._workload_oracle(Path(row.workspace_path))) is not None
                ],
                "runtime_events": [
                    {
                        "event_type": row.event_type,
                        "aggregate_id": row.aggregate_id,
                        "aggregate_sequence": row.aggregate_sequence,
                        "payload": row.payload_json,
                    }
                    for row in events
                ],
            }
        finally:
            await database.dispose()

    @staticmethod
    def _workload_oracle(workspace: Path) -> dict[str, object] | None:
        path = workspace / "runtime" / "output" / "workload-test-oracle.json"
        try:
            encoded = path.read_bytes()
            if not encoded or len(encoded) > 16 * 1024:
                raise ValueError
            value = json.loads(encoded)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise NativeWorkerExecutionError("workload oracle is invalid") from error
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise NativeWorkerExecutionError("workload oracle is invalid")
        return value

    def _attempt_value(self, row: AttemptRow) -> dict[str, object]:
        source = Path(row.workspace_path) / "source" / "train.py"
        return {
            "attempt_id": str(row.attempt_id),
            "trial_id": str(row.trial_id),
            "status": row.status,
            "execution_identity": row.supervisor_execution_identity,
            "execution_spec_hash": row.execution_spec_hash,
            "workspace_path": row.workspace_path,
            "source_manifest_sha256": row.source_manifest_sha256,
            "prepared_train_sha256": self._file_sha256(source),
            "observed_environment": row.observed_environment_json,
            "terminal_kind": row.terminal_kind,
            "terminal_at": None if row.terminal_at is None else row.terminal_at.isoformat(),
            "exit_code": row.exit_code,
            "termination_signal": row.termination_signal,
            "supervisor_result": row.supervisor_result,
            "unknown_since": None if row.unknown_since is None else row.unknown_since.isoformat(),
            "attention_required_at": (
                None if row.attention_required_at is None else row.attention_required_at.isoformat()
            ),
            "execution_issue_code": row.execution_issue_code,
            "physical_boot_id": row.physical_boot_id,
            "supervisor_invocation_id": row.supervisor_invocation_id,
        }

    @staticmethod
    def _file_sha256(path: Path) -> str | None:
        try:
            encoded = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise NativeWorkerExecutionError("prepared source cannot be read") from error
        if len(encoded) > 1024 * 1024:
            raise NativeWorkerExecutionError("prepared source exceeds evidence bound")
        return hashlib.sha256(encoded).hexdigest()

    def _context(self) -> CommandContext:
        from labpilot.common.commands import CommandActor, CommandContext

        return CommandContext(
            installation_identity=self.installation_identity,
            actor=CommandActor(actor_type="test_admin", actor_id="task5-native"),
            interaction_correlation_id=f"task5-{self.request.scenario.lower()}",
        )

    async def _git(self, root: Path, *arguments: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/git",
            *arguments,
            cwd=root,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0 or len(stdout) > 64 * 1024 or len(stderr) > 64 * 1024:
            raise NativeWorkerExecutionError("bounded native Git operation failed")

    def _uuid(self, kind: str) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"labpilot-task5:{self.request.tested_sha}:{self.request.scenario}:{kind}",
        )

    def _digest(self, kind: str) -> str:
        return hashlib.sha256(
            f"labpilot-task5\0{self.request.tested_sha}\0{self.request.scenario}\0{kind}".encode()
        ).hexdigest()

    def _load_state(self) -> WorkerState:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if type(value) is not dict or set(value) != set(WorkerState.__annotations__):
                raise ValueError
            state = WorkerState(**value)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise NativeWorkerExecutionError("native worker state is invalid") from error
        if (
            state.schema_version != 1
            or state.scenario != self.request.scenario
            or state.tested_sha != self.request.tested_sha
        ):
            raise NativeWorkerExecutionError("native worker state identity mismatch")
        return state

    def _save_state(self, state: WorkerState) -> None:
        encoded = json.dumps(asdict(state), separators=(",", ":"), sort_keys=True).encode()
        temporary = self.state_path.with_name(".state.json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or len(value.encode("utf-8")) > 4096:
        raise NativeWorkerExecutionError(f"required worker environment is missing: {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
