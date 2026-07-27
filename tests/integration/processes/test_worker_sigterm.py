import asyncio
import json
import os
import signal
import sys

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name",
    [
        "labpilot.coordinator.worker",
        "labpilot.observability.projection_worker",
    ],
)
async def test_worker_process_exits_zero_on_sigterm(
    module_name: str,
    isolated_database_url: str,
) -> None:
    environment = os.environ.copy()
    environment["LABPILOT_DATABASE_URL"] = isolated_database_url
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module_name,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        marker = json.loads(line)
        assert marker["event"] == "process_shell.ready"
        assert marker["process"] in {"coordinator", "projection"}
        process.send_signal(signal.SIGTERM)
        return_code = await asyncio.wait_for(process.wait(), timeout=5)
        assert return_code == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
