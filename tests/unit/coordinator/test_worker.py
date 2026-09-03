import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from labpilot.coordinator.worker import run


class FakeSession:
    def __init__(self, stop_event: asyncio.Event, *, stop_on_probe: bool = True) -> None:
        self.stop_event = stop_event
        self.stop_on_probe = stop_on_probe
        self.probes = 0

    async def execute(self, statement: object) -> None:
        self.probes += 1
        if self.stop_on_probe:
            self.stop_event.set()


class FakeDatabase:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.session_value = FakeSession(stop_event)
        self.disposed = False

    @asynccontextmanager
    async def session(self):
        yield self.session_value

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_process_shell_probes_once_and_disposes() -> None:
    stop_event = asyncio.Event()
    database = FakeDatabase(stop_event)
    await run(stop_event=stop_event, database=database, probe_interval_seconds=0.01)
    assert database.session_value.probes == 1
    assert database.disposed is True


@pytest.mark.asyncio
async def test_worker_runs_one_startup_barrier_before_first_scan() -> None:
    events: list[tuple[str, object]] = []
    stop_event = asyncio.Event()
    database = FakeDatabase(stop_event)
    database.session_value.stop_on_probe = False

    class Coordinator:
        async def run_startup_barrier(self, *, coordinator_start_id: object) -> None:
            events.append(("barrier", coordinator_start_id))

        async def run_scan(self) -> None:
            events.append(("scan", None))
            stop_event.set()

    await run(
        stop_event=stop_event,
        database=database,
        coordinator=Coordinator(),  # type: ignore[arg-type]
        probe_interval_seconds=0.01,
    )

    assert [event for event, _ in events] == ["barrier", "scan"]
    assert events[0][1] is not None
    assert database.disposed is True


def test_build_coordinator_wires_one_shared_semantics_and_result_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import labpilot.coordinator.worker as worker

    created: dict[str, list[object]] = {}

    def factory(name: str):
        class Factory:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs
                created.setdefault(name, []).append(self)

        return Factory

    coordinator_calls: list[SimpleNamespace] = []

    def coordinator_factory(*args: object, **kwargs: object) -> SimpleNamespace:
        instance = SimpleNamespace(args=args, kwargs=kwargs)
        coordinator_calls.append(instance)
        return instance

    for name in (
        "RepositorySnapshotSourceService",
        "GitAdapter",
        "ContentAddressedStore",
        "RepositorySnapshotMaterializationAdapter",
        "AttemptCreationService",
        "AttemptPreparationService",
        "AttemptExecutionService",
        "RunnerClient",
        "VersionExecutionSemanticsAdapter",
    ):
        monkeypatch.setattr(worker, name, factory(name))
    monkeypatch.setattr(
        worker,
        "TrialExecutionOutcomeService",
        factory("TrialExecutionOutcomeService"),
        raising=False,
    )
    monkeypatch.setattr(
        worker,
        "ResultApplicationService",
        factory("ResultApplicationService"),
        raising=False,
    )
    monkeypatch.setattr(
        worker,
        "BatchSummaryService",
        factory("BatchSummaryService"),
        raising=False,
    )
    monkeypatch.setattr(worker, "CoordinatorService", coordinator_factory)

    workspace = tmp_path / "attempts"
    workspace.mkdir()
    settings = SimpleNamespace(
        attempt_workspace_root=workspace,
        runner_socket_path=Path("/run/labpilot-runner/control.sock"),
        controller_uid=1234,
        controller_gid=2345,
        train_gid=3456,
        repository_git_timeout_seconds=15,
        repository_content_store_root=tmp_path,
        installation_identity="test-installation",
        runner_request_timeout_seconds=10,
        unknown_attention_grace_seconds=60,
    )
    session = object()

    coordinator = worker.build_coordinator(
        settings, SimpleNamespace(session=session)
    )

    semantics = created["VersionExecutionSemanticsAdapter"][0]
    assert len(created["VersionExecutionSemanticsAdapter"]) == 1
    assert created["AttemptCreationService"][0].kwargs["version_semantics"] is semantics
    assert created["AttemptPreparationService"][0].kwargs["version_semantics"] is semantics

    assert len(created.get("TrialExecutionOutcomeService", ())) == 1
    assert len(created.get("ResultApplicationService", ())) == 1
    assert len(created.get("BatchSummaryService", ())) == 1

    outcomes = created["TrialExecutionOutcomeService"][0]
    result_processing = created["ResultApplicationService"][0]
    summary = created["BatchSummaryService"][0]
    assert outcomes.kwargs["version_semantics"] is semantics
    assert outcomes.args == result_processing.args == summary.args == (session,)
    assert summary.kwargs["trial_execution_outcome_service"] is outcomes
    assert coordinator is coordinator_calls[0]
    assert coordinator.kwargs["result_processing"] is result_processing
    assert coordinator.kwargs["summary"] is summary
