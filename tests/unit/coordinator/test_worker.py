import asyncio
from contextlib import asynccontextmanager

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
