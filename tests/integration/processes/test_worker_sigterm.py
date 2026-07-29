import asyncio
import json
import os
import signal
import sys

import pytest

_STDERR_LIMIT = 4096


async def _read_ready_marker(
    process: asyncio.subprocess.Process,
    module_name: str,
    isolated_database_url: str,
) -> dict[str, str]:
    assert process.stdout is not None
    assert process.stderr is not None
    line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    if line:
        marker: dict[str, str] = json.loads(line)
        return marker

    return_code = await process.wait()
    stderr = await process.stderr.read(_STDERR_LIMIT)
    safe_stderr = stderr.decode(errors="replace")
    for database_url in (
        isolated_database_url,
        os.environ.get("LABPILOT_TEST_ADMIN_DATABASE_URL", ""),
    ):
        if database_url:
            safe_stderr = safe_stderr.replace(database_url, "<redacted>")
    safe_stderr = safe_stderr[:_STDERR_LIMIT]
    pytest.fail(
        f"{module_name} exited before ready marker; "
        f"child return code: {return_code}; "
        f"bounded stderr: {safe_stderr}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_early_exit_diagnostic_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_database_url = (
        "postgresql+asyncpg://worker:worker-password@127.0.0.1:5432/isolated"
    )
    admin_url = (
        "postgresql+asyncpg://admin:admin-password@127.0.0.1:5432/postgres"
    )
    monkeypatch.setenv("LABPILOT_TEST_ADMIN_DATABASE_URL", admin_url)
    environment = os.environ.copy()
    environment["LABPILOT_TEST_EARLY_EXIT_STDERR"] = (
        f"isolated={isolated_database_url}\nadmin={admin_url}\n"
        + "x" * 5000
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        (
            "import os, sys; "
            "sys.stderr.write("
            "os.environ['LABPILOT_TEST_EARLY_EXIT_STDERR']"
            "); raise SystemExit(7)"
        ),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        with pytest.raises(pytest.fail.Exception) as failure:
            await _read_ready_marker(
                process,
                "diagnostic.worker",
                isolated_database_url,
            )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    message = str(failure.value)
    assert "diagnostic.worker" in message
    assert "child return code: 7" in message
    assert "bounded stderr: " in message
    assert isolated_database_url not in message
    assert admin_url not in message
    assert "worker-password" not in message
    assert "admin-password" not in message
    assert len(message.partition("bounded stderr: ")[2]) <= 4096


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
    environment.update(
        {
            "LABPILOT_ENVIRONMENT": "test",
            "LABPILOT_DATABASE_URL": isolated_database_url,
            "LABPILOT_INSTALLATION_IDENTITY": (
                "integration-test-installation"
            ),
            "LABPILOT_LOCAL_ACTOR_ID": (
                "integration-test-worker"
            ),
        }
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module_name,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        marker = await _read_ready_marker(
            process, module_name, isolated_database_url
        )
        assert marker["event"] == "process_shell.ready"
        assert marker["process"] in {"coordinator", "projection"}
        process.send_signal(signal.SIGTERM)
        return_code = await asyncio.wait_for(process.wait(), timeout=5)
        assert return_code == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
