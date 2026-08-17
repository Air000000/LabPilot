import json
import subprocess
import sys

import pytest

from labpilot.execution.runner_policy import parse_runner_policy
from labpilot.execution.runner_protocol import InfoInspectionRequest
from labpilot.execution.runtime_probe import FIXED_PROBE_SOURCE, build_probe_argv, run_probe


def _value() -> dict[str, object]:
    return {
        "controller_user": "labpilot-controller",
        "controller_group": "labpilot-control",
        "execution_user": "labpilot-train",
        "execution_group": "labpilot-train",
        "socket_path": "/run/labpilot-runner/control.sock",
        "workspace_root": "/srv/labpilot/attempts",
        "runtime_profiles": {
            "python-3.12": {
                "runtime_path": "/opt/python/bin/python",
                "environment": {
                    "PATH": "/opt/python/bin:/usr/bin",
                    "PYTHONPATH": "/opt/labpilot/packages",
                },
            }
        },
        "allowed_local_dataset_roots": ["/datasets"],
        "launch_profile_version": "candidate-b-v1",
        "max_runtime_seconds": 3600,
        "stop_timeout_seconds": 30,
    }


def test_probe_is_fixed_non_shell_and_policy_owned() -> None:
    policy = parse_runner_policy(_value(), controller_uid=1, controller_gid=2)
    inspection = InfoInspectionRequest("python-3.12", ("torch", "xformers"))

    argv = build_probe_argv(policy, inspection)

    assert argv[:6] == (
        "/usr/sbin/runuser",
        "--preserve-environment",
        "--user",
        "labpilot-train",
        "--",
        "/opt/python/bin/python",
    )
    assert argv[6] == "-c"
    assert argv[7] == FIXED_PROBE_SOURCE
    assert json.loads(argv[8]) == ["torch", "xformers"]


def test_caller_cannot_supply_probe_source_executable_or_imports() -> None:
    assert set(InfoInspectionRequest.__dataclass_fields__) == {
        "python_profile",
        "framework_distribution_names",
    }


def test_fixed_probe_bounds_missing_distribution_and_cuda_failure() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-E",
            "-s",
            "-c",
            FIXED_PROBE_SOURCE,
            '["definitely-missing-labpilot-package"]',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["framework_versions"] == [
        {
            "distribution_name": "definitely-missing-labpilot-package",
            "installed_version": None,
        }
    ]
    assert all(isinstance(code, str) and len(code) <= 100 for code in payload["probe_issue_codes"])


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired(("probe",), 10), OSError("runuser unavailable")],
)
def test_probe_execution_failure_becomes_bounded_evidence(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise failure

    monkeypatch.setattr("labpilot.execution.runtime_probe.subprocess.run", fail)
    policy = parse_runner_policy(_value(), controller_uid=1, controller_gid=2)

    result = run_probe(policy, InfoInspectionRequest("python-3.12", ("torch",)))

    assert result == {"probe_issue_codes": ["PROBE_FAILED"]}


@pytest.mark.parametrize(
    ("completed", "expected"),
    [
        (
            subprocess.CompletedProcess(("probe",), 1, "", "secret stderr"),
            {"probe_issue_codes": ["PROBE_FAILED"]},
        ),
        (
            subprocess.CompletedProcess(("probe",), 0, "not-json", ""),
            {"probe_issue_codes": ["PROBE_OUTPUT_INVALID"]},
        ),
        (
            subprocess.CompletedProcess(("probe",), 0, "x" * (64 * 1024 + 1), ""),
            {"probe_issue_codes": ["PROBE_FAILED"]},
        ),
    ],
)
def test_probe_process_failures_never_return_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
    expected: dict[str, object],
) -> None:
    monkeypatch.setattr(
        "labpilot.execution.runtime_probe.subprocess.run",
        lambda *args, **kwargs: completed,
    )
    policy = parse_runner_policy(_value(), controller_uid=1, controller_gid=2)

    result = run_probe(policy, InfoInspectionRequest("python-3.12", ("torch",)))

    assert result == expected
