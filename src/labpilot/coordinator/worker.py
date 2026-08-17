import asyncio
import json
from uuid import uuid4

from sqlalchemy import text

from labpilot.common.signals import install_shutdown_handlers
from labpilot.coordinator.service import CoordinatorService
from labpilot.db.session import Database
from labpilot.settings import get_settings


async def run(
    stop_event: asyncio.Event | None = None,
    database: Database | None = None,
    coordinator: CoordinatorService | None = None,
    probe_interval_seconds: float = 5.0,
) -> None:
    event = stop_event or asyncio.Event()
    if stop_event is None:
        install_shutdown_handlers(event)
    active_database = database or Database(get_settings().database_url)
    coordinator_start_id = uuid4()
    startup_complete = False
    ready_emitted = False
    try:
        while not event.is_set():
            async with active_database.session() as session:
                await session.execute(text("SELECT 1"))
            if coordinator is not None and not startup_complete:
                await coordinator.run_startup_barrier(
                    coordinator_start_id=coordinator_start_id
                )
                startup_complete = True
            if coordinator is not None:
                await coordinator.run_scan()
            if not ready_emitted:
                print(
                    json.dumps(
                        {"event": "process_shell.ready", "process": "coordinator"}
                    ),
                    flush=True,
                )
                ready_emitted = True
            try:
                await asyncio.wait_for(event.wait(), timeout=probe_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await active_database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
