from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPORTER = Path("deploy/libexec/labpilot-terminal-receipt-v1.py")


def _load_reporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("labpilot_terminal_receipt_v1", REPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reporter_emits_exact_canonical_record(
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    reporter = _load_reporter()

    status = reporter.main(
        {
            "SERVICE_RESULT": "exit-code",
            "EXIT_CODE": "exited",
            "EXIT_STATUS": "7",
        }
    )

    assert status == 0
    assert capfdbinary.readouterr().out == (
        b'{"exit_code":"exited","exit_status":"7",'
        b'"schema_version":"labpilot-terminal-receipt-v1",'
        b'"service_result":"exit-code"}\n'
    )


def test_reporter_maps_missing_sources_to_null(
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    reporter = _load_reporter()

    assert reporter.main({}) == 0

    encoded = capfdbinary.readouterr().out
    assert encoded == (
        b'{"exit_code":null,"exit_status":null,'
        b'"schema_version":"labpilot-terminal-receipt-v1",'
        b'"service_result":null}\n'
    )
    assert json.loads(encoded) == {
        "schema_version": "labpilot-terminal-receipt-v1",
        "service_result": None,
        "exit_code": None,
        "exit_status": None,
    }


def test_reporter_accepts_exact_64_byte_token(
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    reporter = _load_reporter()
    token = "A" + "z" * 63

    assert reporter.main({"SERVICE_RESULT": token}) == 0

    assert json.loads(capfdbinary.readouterr().out)["service_result"] == token


@pytest.mark.parametrize(
    "value",
    [
        "",
        "-leading",
        "has space",
        "has/slash",
        "é",
        "A" * 65,
    ],
)
def test_reporter_rejects_invalid_source_without_output(
    value: str,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    reporter = _load_reporter()

    assert reporter.main({"SERVICE_RESULT": value}) != 0

    captured = capfdbinary.readouterr()
    assert captured.out == b""


def test_reporter_validates_all_sources_before_writing(
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    reporter = _load_reporter()

    assert reporter.main({"SERVICE_RESULT": "success", "EXIT_STATUS": "bad value"}) != 0

    assert capfdbinary.readouterr().out == b""


def test_reporter_is_deterministic(
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    reporter = _load_reporter()
    environ = {"SERVICE_RESULT": "success", "EXIT_CODE": "exited", "EXIT_STATUS": "0"}

    assert reporter.main(environ) == 0
    first = capfdbinary.readouterr().out
    assert reporter.main(environ) == 0
    second = capfdbinary.readouterr().out

    assert first == second


def test_reporter_runs_in_isolated_python_without_workspace_imports() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(REPORTER.resolve())],
        check=False,
        capture_output=True,
        env={},
        timeout=5,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout == (
        b'{"exit_code":null,"exit_status":null,'
        b'"schema_version":"labpilot-terminal-receipt-v1",'
        b'"service_result":null}\n'
    )
