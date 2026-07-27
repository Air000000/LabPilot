import asyncio
from contextlib import asynccontextmanager

import pytest

from labpilot.observability.projection_worker import run


class FakeSession:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.probes = 0

    async def execute(self, statement: object) -> None:
        self.probes += 1
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
