from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def test_preflight_cli_requires_and_binds_exact_tested_sha(tmp_path: Path) -> None:
    from tests.acceptance.slice2.test_native_slice2 import _native_config

    script = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_preflight_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = (tmp_path / "config.json").resolve()
    config.write_text(json.dumps(_native_config(expected_hostname="candidate-b-vm")))
    calls: list[object] = []

    assert (
        module.main(
            ["--preflight-only"],
            environ={
                module.CONFIG_ENV: str(config),
                module.TESTED_SHA_ENV: "a" * 40,
                "LABPILOT_SLICE2_ACCEPTANCE_DATABASE_URL": "postgresql://authority",
            },
            execute=calls.append,
            hostname=lambda: "candidate-b-vm",
            wsl_detector=lambda: False,
        )
        == 0
    )
    assert calls == [
        {
            "config": _native_config(expected_hostname="candidate-b-vm"),
            "scenarios": (),
            "phase": "preflight",
            "database_url": "postgresql://authority",
            "tested_sha": "a" * 40,
        }
    ]


def test_native_command_output_is_bounded() -> None:
    from tests.acceptance.slice2.native_preflight import NativePreflightError, run_command

    for stream in ("stdout", "stderr"):
        code = (
            "import sys; "
            f"sys.{stream}.buffer.write(b'x' * 65); "
            f"sys.{stream}.flush()"
        )
        with pytest.raises(NativePreflightError, match=stream):
            run_command((sys.executable, "-c", code), max_output_bytes=64)


def test_native_request_binds_preflight_without_exposing_scenario_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.acceptance.slice2 import native_preflight, scenarios

    request = {
        "config": {"repository_root": "/repo"},
        "scenarios": (),
        "phase": "preflight",
        "database_url": "postgresql://must-not-be-serialized",
        "tested_sha": "a" * 40,
    }
    monkeypatch.setattr(
        native_preflight,
        "run_preflight_request",
        lambda value: {"tested_sha": value["tested_sha"], "database_url": value["database_url"]},
    )

    scenarios.run_native_request(request)

    assert json.loads(capsys.readouterr().out) == {"tested_sha": "a" * 40}


def test_native_admin_commands_are_confined_to_test_code() -> None:
    root = Path(__file__).parents[3]
    production = "\n".join(path.read_text() for path in (root / "src").rglob("*.py"))

    assert "tests.acceptance.slice2.native_preflight" not in production
    assert "run_slice2_native_acceptance" not in production


def test_exact_native_identities_and_tested_sha_are_required() -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_identities,
        require_tested_sha,
    )

    identities = {
        "labpilot-controller": {"uid": 999, "gid": 983},
        "labpilot-control": {"gid": 983},
        "labpilot-train": {"uid": 995, "gid": 982},
    }
    require_identities(identities)
    require_tested_sha("a" * 40, "a" * 40, clean=True)

    identities["labpilot-train"]["uid"] = 996
    with pytest.raises(NativePreflightError, match="labpilot-train"):
        require_identities(identities)
    with pytest.raises(NativePreflightError, match="tested SHA"):
        require_tested_sha("a" * 40, "b" * 40, clean=True)


def test_effective_runner_service_contract_is_exact() -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_effective_service,
    )

    properties = {
        "User": "root",
        "Group": "labpilot-control",
        "UMask": "0077",
        "RuntimeDirectory": "labpilot-runner",
        "RuntimeDirectoryMode": "0750",
        "RuntimeDirectoryPreserve": "restart",
        "Restart": "on-failure",
        "NoNewPrivileges": "yes",
        "RestrictAddressFamilies": "AF_UNIX",
        "ExecStart": (
            "{ path=/opt/labpilot-runner/app/.venv/bin/python ; "
            "argv[]=/opt/labpilot-runner/app/.venv/bin/python -E -s -B "
            "-m labpilot.execution.runner_daemon "
            "--policy /etc/labpilot/runner-policy.json ; ignore_errors=no ; }"
        ),
    }
    require_effective_service(properties)

    properties["NoNewPrivileges"] = "no"
    with pytest.raises(NativePreflightError, match="NoNewPrivileges"):
        require_effective_service(properties)


@pytest.mark.parametrize(
    ("name", "expected_kind", "expected_mode"),
    (("runtime directory", "directory", 0o750), ("socket", "socket", 0o660)),
)
def test_runner_runtime_metadata_is_exact(
    name: str, expected_kind: str, expected_mode: int
) -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_metadata,
    )

    metadata = {
        "kind": expected_kind,
        "uid": 0,
        "gid": 983,
        "mode": expected_mode,
        "inode": 42,
    }
    require_metadata(
        name,
        metadata,
        kind=expected_kind,
        uid=0,
        gid=983,
        mode=expected_mode,
    )

    metadata["mode"] = 0o777
    with pytest.raises(NativePreflightError, match=name):
        require_metadata(
            name,
            metadata,
            kind=expected_kind,
            uid=0,
            gid=983,
            mode=expected_mode,
        )


def test_compose_network_and_api_mount_authority_are_narrow() -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_api_mounts,
        require_postgres_bind,
    )

    require_postgres_bind(
        {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]}
    )
    require_api_mounts(
        [{"Destination": "/app/content", "Source": "/srv/labpilot/content"}],
        workspace_root="/srv/labpilot/attempts",
    )

    with pytest.raises(NativePreflightError, match="loopback"):
        require_postgres_bind(
            {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "55432"}]}
        )
    with pytest.raises(NativePreflightError, match="Runner runtime"):
        require_api_mounts(
            [{"Destination": "/run/labpilot-runner", "Source": "/run/labpilot-runner"}],
            workspace_root="/srv/labpilot/attempts",
        )


def test_runner_restart_requires_the_same_coordinator_container() -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_coordinator_continuity,
    )

    require_coordinator_continuity("coordinator-container-a", "coordinator-container-a")
    with pytest.raises(NativePreflightError, match="Coordinator container"):
        require_coordinator_continuity("coordinator-container-a", "coordinator-container-b")


def test_workspace_permission_oracle_is_exact() -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_workspace_permissions,
    )

    nodes = {
        "workspace": {"kind": "directory", "gid": 982, "mode": 0o750},
        "source_directory": {"kind": "directory", "gid": 982, "mode": 0o550},
        "source_file": {"kind": "file", "gid": 982, "mode": 0o440},
        "source_executable": {"kind": "file", "gid": 982, "mode": 0o550},
        "runtime": {"kind": "directory", "gid": 982, "mode": 0o770},
        "output": {"kind": "directory", "gid": 982, "mode": 0o770},
    }
    require_workspace_permissions(nodes)

    nodes["source_file"]["mode"] = 0o640
    with pytest.raises(NativePreflightError, match="source_file"):
        require_workspace_permissions(nodes)


def test_evidence_redaction_removes_credentials_and_ssh_sudo_material() -> None:
    from tests.acceptance.slice2.native_preflight import redact_evidence

    redacted = redact_evidence(
        {
            "hostname": "labpilot-s2",
            "database_url": "postgresql://user:pass@127.0.0.1/db",
            "nested": {
                "sudo_password": "do-not-serialize",
                "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----",
                "header": "Bearer do-not-serialize",
            },
        }
    )
    encoded = json.dumps(redacted)

    assert redacted == {
        "hostname": "labpilot-s2",
        "nested": {"header": "[REDACTED]"},
    }
    assert "do-not-serialize" not in encoded
    assert "OPENSSH" not in encoded
