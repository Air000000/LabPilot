import asyncio
import json

from sqlalchemy import text

from labpilot.common.signals import install_shutdown_handlers
from labpilot.db.session import Database
from labpilot.settings import get_settings


async def run(
    stop_event: asyncio.Event | None = None,
    database: Database | None = None,
    probe_interval_seconds: float = 5.0,
) -> None:
    event = stop_event or asyncio.Event()
    if stop_event is None:
        install_shutdown_handlers(event)
    active_database = database or Database(get_settings().database_url)
    ready_emitted = False
    try:
        while not event.is_set():
            async with active_database.session() as session:
                await session.execute(text("SELECT 1"))
            if not ready_emitted:
                print(
                    json.dumps(
                        {"event": "process_shell.ready", "process": "projection"}
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
