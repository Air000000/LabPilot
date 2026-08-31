import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from tests.integration.db_helpers import upgrade_database_to_head

_STDERR_LIMIT = 4096
_TRACEBACK_NEEDLE = b"Traceback"


@dataclass(frozen=True, slots=True)
class _StderrCapture:
    retained: bytes
    traceback_seen: bool


async def _drain_stderr_bounded(
    stream: asyncio.StreamReader,
    *,
    limit: int,
) -> _StderrCapture:
    if limit <= 0:
        raise ValueError("limit must be positive")
    captured = bytearray()
    tail = b""
    traceback_seen = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        scan_window = tail + chunk
        if _TRACEBACK_NEEDLE in scan_window:
            traceback_seen = True
        tail = scan_window[-(len(_TRACEBACK_NEEDLE) - 1) :]
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
    return _StderrCapture(
        retained=bytes(captured),
        traceback_seen=traceback_seen,
    )


def _sanitize_stderr(
    stderr: bytes,
    isolated_database_url: str,
) -> str:
    safe_stderr = stderr.decode(errors="replace")
    for database_url in (
        isolated_database_url,
        os.environ.get("LABPILOT_TEST_ADMIN_DATABASE_URL", ""),
    ):
        if not database_url:
            continue
        safe_stderr = safe_stderr.replace(database_url, "<redacted>")
        password = urlsplit(database_url).password
        if password:
            safe_stderr = safe_stderr.replace(password, "<redacted>")
    return safe_stderr[:_STDERR_LIMIT]


async def _cleanup_process(
    process: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[_StderrCapture],
) -> None:
    if process.returncode is None:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass
        raise
    await stderr_task


async def _stop_and_collect_stderr(
    process: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[_StderrCapture],
) -> _StderrCapture:
    await _cleanup_process(process, stderr_task)
    return stderr_task.result()


async def _read_ready_marker(
    process: asyncio.subprocess.Process,
    module_name: str,
    isolated_database_url: str,
    stderr_task: asyncio.Task[_StderrCapture],
) -> dict[str, str]:
    assert process.stdout is not None
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    except TimeoutError:
        capture = await _stop_and_collect_stderr(process, stderr_task)
        safe_stderr = _sanitize_stderr(
            capture.retained, isolated_database_url
        )
        pytest.fail(
            f"{module_name} timed out waiting for ready marker; "
            f"child return code: {process.returncode}; "
            f"bounded stderr: {safe_stderr}"
        )

    if line:
        marker: dict[str, str] = json.loads(line)
        return marker

    try:
        return_code = await asyncio.wait_for(process.wait(), timeout=5)
        capture = await stderr_task
    except TimeoutError:
        capture = await _stop_and_collect_stderr(process, stderr_task)
        safe_stderr = _sanitize_stderr(
            capture.retained, isolated_database_url
        )
        pytest.fail(
            f"{module_name} closed stdout but did not exit; "
            f"child return code: {process.returncode}; "
            f"bounded stderr: {safe_stderr}"
        )

    safe_stderr = _sanitize_stderr(
        capture.retained, isolated_database_url
    )
    pytest.fail(
        f"{module_name} exited before ready marker; "
        f"child return code: {return_code}; "
        f"bounded stderr: {safe_stderr}"
    )


async def _wait_for_clean_exit(
    process: asyncio.subprocess.Process,
    module_name: str,
    isolated_database_url: str,
    stderr_task: asyncio.Task[_StderrCapture],
) -> None:
    try:
        return_code = await asyncio.wait_for(process.wait(), timeout=5)
        capture = await stderr_task
    except TimeoutError:
        capture = await _stop_and_collect_stderr(process, stderr_task)
        safe_stderr = _sanitize_stderr(
            capture.retained, isolated_database_url
        )
        pytest.fail(
            f"{module_name} timed out while exiting; "
            f"child return code: {process.returncode}; "
            f"bounded stderr: {safe_stderr}"
        )

    safe_stderr = _sanitize_stderr(
        capture.retained, isolated_database_url
    )
    if return_code != 0:
        pytest.fail(
            f"{module_name} exited with child return code: {return_code}; "
            f"bounded stderr: {safe_stderr}"
        )
    if capture.traceback_seen:
        pytest.fail(
            f"{module_name} exited with child return code: {return_code}; "
            f"stderr contains Traceback; bounded stderr: {safe_stderr}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stderr_drain_detects_traceback_across_chunks() -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(b"x" * 5000 + b"Trace")
    drain_task = asyncio.create_task(
        _drain_stderr_bounded(stream, limit=_STDERR_LIMIT)
    )
    await asyncio.sleep(0)
    stream.feed_data(b"back (most recent call last):")
    stream.feed_eof()

    capture = await drain_task

    assert capture.traceback_seen
    assert len(capture.retained) <= _STDERR_LIMIT
    assert b"Traceback" not in capture.retained


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stderr_drain_requires_positive_limit() -> None:
    stream = asyncio.StreamReader()
    stream.feed_eof()

    with pytest.raises(ValueError, match="positive"):
        await _drain_stderr_bounded(stream, limit=0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_large_stderr_is_drained_and_diagnostic_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_database_url = (
        "postgresql+asyncpg://worker:worker-password@127.0.0.1:5432/isolated"
    )
    admin_url = (
        "postgresql+asyncpg://admin:admin-password@127.0.0.1:5432/postgres"
    )
    monkeypatch.setenv("LABPILOT_TEST_ADMIN_DATABASE_URL", admin_url)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        (
            "import sys\n"
            f"sys.stderr.write('isolated={isolated_database_url}\\n')\n"
            f"sys.stderr.write('admin={admin_url}\\n')\n"
            "chunk = b'x' * (64 * 1024)\n"
            "for _ in range(128):\n"
            "    sys.stderr.buffer.write(chunk)\n"
            "sys.stderr.flush()\n"
            "raise SystemExit(7)\n"
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stderr is not None
    stderr_task = asyncio.create_task(
        _drain_stderr_bounded(process.stderr, limit=_STDERR_LIMIT)
    )
    try:
        with pytest.raises(pytest.fail.Exception) as failure:
            await asyncio.wait_for(
                _read_ready_marker(
                    process,
                    "diagnostic.worker",
                    isolated_database_url,
                    stderr_task,
                ),
                timeout=5,
            )
    finally:
        await _cleanup_process(process, stderr_task)

    message = str(failure.value)
    assert "diagnostic.worker" in message
    assert "child return code: 7" in message
    assert "bounded stderr: " in message
    assert isolated_database_url not in message
    assert admin_url not in message
    assert "worker-password" not in message
    assert "admin-password" not in message
    assert len(message.partition("bounded stderr: ")[2]) <= 4096
    assert "x" * 4097 not in message
    assert process.returncode == 7


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_zero_exit_rejects_traceback_after_retained_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_database_url = (
        "postgresql+asyncpg://worker:worker-password@127.0.0.1:5432/isolated"
    )
    admin_url = (
        "postgresql+asyncpg://admin:admin-password@127.0.0.1:5432/postgres"
    )
    monkeypatch.setenv("LABPILOT_TEST_ADMIN_DATABASE_URL", admin_url)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        (
            "import json, sys\n"
            "print(json.dumps("
            "{'event': 'process_shell.ready', 'process': 'diagnostic'}"
            "), flush=True)\n"
            "sys.stderr.write('x' * 8192)\n"
            f"sys.stderr.write('Traceback: {isolated_database_url}\\n')\n"
            f"sys.stderr.write('admin={admin_url}\\n')\n"
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stderr is not None
    stderr_task = asyncio.create_task(
        _drain_stderr_bounded(process.stderr, limit=_STDERR_LIMIT)
    )
    try:
        marker = await _read_ready_marker(
            process,
            "traceback.worker",
            isolated_database_url,
            stderr_task,
        )
        assert marker == {
            "event": "process_shell.ready",
            "process": "diagnostic",
        }
        with pytest.raises(
            pytest.fail.Exception,
            match="stderr contains Traceback",
        ) as failure:
            await _wait_for_clean_exit(
                process,
                "traceback.worker",
                isolated_database_url,
                stderr_task,
            )
    finally:
        await _cleanup_process(process, stderr_task)

    message = str(failure.value)
    assert isolated_database_url not in message
    assert admin_url not in message
    assert "worker-password" not in message
    assert "admin-password" not in message
    assert len(message.partition("bounded stderr: ")[2]) <= _STDERR_LIMIT
    assert process.returncode == 0


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
    tmp_path: Path,
) -> None:
    content_store_root = tmp_path / "repository-content"
    content_store_root.mkdir()

    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("LABPILOT_")
    }
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
            "LABPILOT_REPOSITORY_ALLOWED_ROOTS": "[]",
            "LABPILOT_REPOSITORY_CONTENT_STORE_ROOT": str(
                content_store_root
            ),
        }
    )
    if module_name == "labpilot.coordinator.worker":
        upgrade_database_to_head(isolated_database_url)
        attempt_workspace_root = tmp_path / "attempts"
        attempt_workspace_root.mkdir()
        environment.update(
            {
                "LABPILOT_ATTEMPT_WORKSPACE_ROOT": str(attempt_workspace_root),
                "LABPILOT_RUNNER_SOCKET_PATH": (
                    "/run/labpilot-runner/control.sock"
                ),
                "LABPILOT_CONTROLLER_UID": "1234",
                "LABPILOT_CONTROLLER_GID": "2345",
                "LABPILOT_TRAIN_GID": "1234",
            }
        )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module_name,
        cwd=tmp_path,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stderr is not None
    stderr_task = asyncio.create_task(
        _drain_stderr_bounded(process.stderr, limit=_STDERR_LIMIT)
    )
    try:
        marker = await _read_ready_marker(
            process,
            module_name,
            isolated_database_url,
            stderr_task,
        )
        assert marker["event"] == "process_shell.ready"
        assert marker["process"] in {"coordinator", "projection"}
        process.send_signal(signal.SIGTERM)
        await _wait_for_clean_exit(
            process,
            module_name,
            isolated_database_url,
            stderr_task,
        )
    finally:
        await _cleanup_process(process, stderr_task)
