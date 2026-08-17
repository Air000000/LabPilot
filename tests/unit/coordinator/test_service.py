from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest


class _Attempts:
    def __init__(self, log: list[tuple[str, UUID]]) -> None:
        self.log = log

    async def create_attempt_for_trial(self, trial_id: UUID):
        self.log.append(("create", trial_id))
        return SimpleNamespace(attempt_id=trial_id)


class _Preparation:
    def __init__(self, log: list[tuple[str, UUID]]) -> None:
        self.log = log

    async def prepare_attempt(self, attempt_id: UUID) -> None:
        self.log.append(("prepare", attempt_id))


class _Execution:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, object, UUID | None]] = []
        self.dispatched: list[UUID] = []

    async def request_stop(
        self,
        attempt_id: UUID,
        *,
        reason: object,
        coordinator_start_id: UUID | None,
    ) -> None:
        self.calls.append(("stop", attempt_id, reason, coordinator_start_id))

    async def reconcile_attempt(
        self,
        attempt_id: UUID,
        *,
        reason: object,
        coordinator_start_id: UUID | None = None,
    ) -> None:
        self.calls.append(("reconcile", attempt_id, reason, coordinator_start_id))

    async def dispatch_attempt(self, attempt_id: UUID) -> None:
        self.dispatched.append(attempt_id)


@pytest.mark.asyncio
async def test_scan_stage_priority_is_control_reconcile_prepare_admit() -> None:
    from labpilot.coordinator.service import CoordinatorService

    order: list[str] = []

    class RecordingCoordinator(CoordinatorService):
        async def _control_existing_work(self) -> None:
            order.append("control")

        async def _reconcile_physical_work(self) -> None:
            order.append("reconcile")

        async def _prepare_eligible_work(self) -> None:
            order.append("prepare")

        async def _admit_launch_ready(self) -> None:
            order.append("admit")

    service = RecordingCoordinator(
        object(),
        attempt_creation=_Attempts([]),
        preparation=_Preparation([]),
        execution=_Execution(),
    )

    await service.run_scan()

    assert order == ["control", "reconcile", "prepare", "admit"]


@pytest.mark.asyncio
async def test_startup_routes_cancel_through_stop_except_identity_conflict() -> None:
    from labpilot.coordinator.service import CoordinatorService, _StartupCandidate
    from labpilot.execution.execution_service import ReconciliationReason

    normal, cancelled, conflict = uuid4(), uuid4(), uuid4()
    execution = _Execution()

    class StartupCoordinator(CoordinatorService):
        async def _startup_candidates(self):
            return (
                _StartupCandidate(normal, "unknown", False),
                _StartupCandidate(cancelled, "running", True),
                _StartupCandidate(conflict, "identity_conflict", True),
            )

    service = StartupCoordinator(
        object(),
        attempt_creation=_Attempts([]),
        preparation=_Preparation([]),
        execution=execution,
    )
    start_id = uuid4()

    await service.run_startup_barrier(coordinator_start_id=start_id)

    assert execution.calls == [
        ("reconcile", normal, ReconciliationReason.STARTUP, start_id),
        ("stop", cancelled, ReconciliationReason.STARTUP, start_id),
        ("reconcile", conflict, ReconciliationReason.STARTUP, start_id),
    ]


@pytest.mark.asyncio
async def test_first_attempts_are_created_before_retries() -> None:
    from labpilot.coordinator.service import CoordinatorService

    first_a, first_b, retry = uuid4(), uuid4(), uuid4()
    log: list[tuple[str, UUID]] = []

    class FairCoordinator(CoordinatorService):
        async def _preparing_attempts(self):
            return ()

        async def _first_attempt_trials(self):
            return (first_a, first_b)

        async def _retry_trials(self):
            return (retry,)

    service = FairCoordinator(
        object(),
        attempt_creation=_Attempts(log),
        preparation=_Preparation(log),
        execution=_Execution(),
    )

    await service._prepare_eligible_work()

    assert [item for item in log if item[0] == "create"] == [
        ("create", first_a),
        ("create", first_b),
        ("create", retry),
    ]


@pytest.mark.asyncio
async def test_capacity_four_stops_after_one_dispatch_when_three_are_active() -> None:
    from labpilot.coordinator.service import CoordinatorService
    from labpilot.execution.types import AttemptStatus

    oldest, younger = uuid4(), uuid4()
    execution = _Execution()
    statuses = {
        oldest: AttemptStatus.LAUNCH_READY.value,
        younger: AttemptStatus.LAUNCH_READY.value,
    }

    async def dispatch(attempt_id: UUID) -> None:
        execution.dispatched.append(attempt_id)
        statuses[attempt_id] = AttemptStatus.LAUNCH_PENDING.value

    execution.dispatch_attempt = dispatch  # type: ignore[method-assign]

    class CapacityCoordinator(CoordinatorService):
        async def _launch_ready_attempts(self):
            return (oldest, younger)

        async def _physical_active_count(self):
            return 3 + len(execution.dispatched)

        async def _attempt_status(self, attempt_id: UUID):
            return statuses[attempt_id]

    service = CapacityCoordinator(
        object(),
        attempt_creation=_Attempts([]),
        preparation=_Preparation([]),
        execution=execution,
    )

    await service._admit_launch_ready()

    assert execution.dispatched == [oldest]


@pytest.mark.asyncio
async def test_unchanged_oldest_launch_ready_blocks_younger_admission() -> None:
    from labpilot.coordinator.service import CoordinatorService
    from labpilot.execution.types import AttemptStatus

    oldest, younger = uuid4(), uuid4()
    execution = _Execution()

    class BlockedCoordinator(CoordinatorService):
        async def _launch_ready_attempts(self):
            return (oldest, younger)

        async def _physical_active_count(self):
            return 0

        async def _attempt_status(self, attempt_id: UUID):
            return AttemptStatus.LAUNCH_READY.value

    service = BlockedCoordinator(
        object(),
        attempt_creation=_Attempts([]),
        preparation=_Preparation([]),
        execution=execution,
    )

    await service._admit_launch_ready()

    assert execution.dispatched == [oldest]

