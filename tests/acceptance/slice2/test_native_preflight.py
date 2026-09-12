from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _run_fake_native_preflight(
    monkeypatch: pytest.MonkeyPatch, *, app_mode: int
) -> tuple[dict[str, object], list[tuple[str, ...]], list[Path]]:
    from tests.acceptance.slice2 import native_preflight

    calls: list[tuple[str, ...]] = []
    metadata_paths: list[Path] = []
    service = {
        **native_preflight._EXPECTED_SERVICE,
        "ExecStart": (
            "{ path=/opt/labpilot-runner/app/.venv/bin/python ; "
            "argv[]=/opt/labpilot-runner/app/.venv/bin/python -E -s -B "
            "-m labpilot.execution.runner_daemon "
            "--policy /etc/labpilot/runner-policy.json ; ignore_errors=no ; }"
        ),
    }

    def fake_run_command(argv: tuple[str, ...], **_kwargs: object) -> object:
        command = tuple(argv)
        calls.append(command)
        if command[:6] == ("git", "-c", "safe.directory=/repo", "-C", "/repo", "rev-parse"):
            stdout = "a" * 40
        elif command[:6] == ("git", "-c", "safe.directory=/repo", "-C", "/repo", "status"):
            stdout = ""
        elif command[:3] == ("systemctl", "show", "labpilot-runner.service"):
            stdout = "\n".join(f"{name}={value}" for name, value in service.items())
        elif command[:2] == ("docker", "ps"):
            service_name = next(
                value.rsplit("=", 1)[-1]
                for value in command
                if value.startswith("label=com.docker.compose.service=")
            )
            stdout = f"{service_name}-container"
        elif command[:2] == ("docker", "inspect"):
            if command[2] == "postgres-container":
                value = {
                    "NetworkSettings": {
                        "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]}
                    }
                }
            else:
                value = {
                    "Mounts": [
                        {
                            "Destination": "/app/content",
                            "Source": "/srv/labpilot/content",
                        }
                    ]
                }
            stdout = json.dumps([value])
        elif command[:3] == ("docker", "exec", "coordinator-container"):
            if command[3:5] == ("python", "-c"):
                stdout = json.dumps(
                    {
                        "version": "labpilot-runner:v1",
                        "operation": "INFO",
                        "outcome": "ok",
                        "result": {
                            "runner_version": "candidate-b-v2",
                            "policy_identity": "b" * 64,
                            "launch_profile_version": "candidate-b-v2",
                            "workspace_root": "/srv/labpilot/attempts",
                            "boot_id": "11111111-1111-1111-1111-111111111111",
                            "readiness_reason": "ready",
                        },
                    }
                )
            else:
                stdout = {"-u": "999", "-g": "983", "-G": "999 983 982"}[command[-1]]
        elif command[:5] == (
            "runuser",
            "--user",
            "labpilot-controller",
            "--group",
            "labpilot-control",
        ):
            stdout = (
                "/opt/labpilot-runner/app/.venv/lib/python3.12/site-packages/labpilot/__init__.py"
            )
        else:
            raise AssertionError(f"unexpected native command: {command}")
        return native_preflight.CommandResult(command, stdout.encode(), b"")

    def fake_metadata(path: Path) -> dict[str, object]:
        metadata_paths.append(path)
        if path == Path("/opt/labpilot-runner/app"):
            return {"kind": "directory", "uid": 0, "gid": 0, "mode": app_mode}
        if path == Path("/run/labpilot-runner"):
            return {"kind": "directory", "uid": 0, "gid": 983, "mode": 0o750}
        return {"kind": "socket", "uid": 0, "gid": 983, "mode": 0o660}

    monkeypatch.setattr(native_preflight, "run_command", fake_run_command)
    monkeypatch.setattr(native_preflight, "_metadata", fake_metadata)
    monkeypatch.setattr(
        native_preflight.pwd,
        "getpwnam",
        lambda name: SimpleNamespace(
            pw_uid=999 if name == "labpilot-controller" else 995,
            pw_gid=983 if name == "labpilot-controller" else 982,
        ),
    )
    monkeypatch.setattr(
        native_preflight.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=983),
    )
    result = native_preflight.run_preflight_request(
        {
            "tested_sha": "a" * 40,
            "config": {
                "repository_root": "/repo",
                "workspace_root": "/srv/labpilot/attempts",
                "compose_project": "labpilot-slice2-acceptance",
            },
        }
    )
    return result, calls, metadata_paths


def test_preflight_runs_real_runner_info_from_coordinator_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, _paths = _run_fake_native_preflight(monkeypatch, app_mode=0o711)

    assert result["runner_info"]["outcome"] == "ok"
    assert any(
        command[:4] == ("docker", "exec", "coordinator-container", "python") for command in calls
    )


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


def test_profile_matrix_cli_is_closed_and_cannot_select_formal_scenarios(
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2.test_native_slice2 import _native_config

    script = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_profile_matrix_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = (tmp_path / "config.json").resolve()
    config.write_text(json.dumps(_native_config(expected_hostname="candidate-b-vm")))
    calls: list[object] = []
    environ = {
        module.CONFIG_ENV: str(config),
        module.TESTED_SHA_ENV: "a" * 40,
        "LABPILOT_SLICE2_ACCEPTANCE_DATABASE_URL": "postgresql://authority",
    }

    assert module.main(
        ["--profile-matrix", "V2-A1-V2-A20"],
        environ=environ,
        execute=calls.append,
        hostname=lambda: "candidate-b-vm",
        wsl_detector=lambda: False,
    ) == 0
    assert calls == [
        {
            "config": _native_config(expected_hostname="candidate-b-vm"),
            "scenarios": (),
            "profile_matrix": "V2-A1-V2-A20",
            "phase": "profile_matrix",
            "database_url": "postgresql://authority",
            "tested_sha": "a" * 40,
        }
    ]
    with pytest.raises(SystemExit):
        module.main(
            ["--profile-matrix", "V2-A1,V2-A2"],
            environ=environ,
            execute=calls.append,
            hostname=lambda: "candidate-b-vm",
            wsl_detector=lambda: False,
        )


def test_native_command_output_is_bounded() -> None:
    from tests.acceptance.slice2.native_preflight import NativePreflightError, run_command

    for stream in ("stdout", "stderr"):
        code = f"import sys; sys.{stream}.buffer.write(b'x' * 65); sys.{stream}.flush()"
        with pytest.raises(NativePreflightError, match=stream):
            run_command((sys.executable, "-c", code), max_output_bytes=64)


def test_native_command_failure_retains_both_streams_without_raw_argv() -> None:
    from tests.acceptance.slice2 import native_preflight

    code = (
        "import os; os.write(1, b'safe-out\\n'); "
        "os.write(2, b'safe-err\\n'); raise SystemExit(17)"
    )
    argv = (sys.executable, "-c", code)

    with pytest.raises(native_preflight.NativePreflightError) as raised:
        native_preflight.run_command(argv)

    failure = raised.value
    assert type(failure) is native_preflight.NativeCommandFailure
    assert failure.operation == Path(sys.executable).name
    assert failure.argv_sha256 == hashlib.sha256(
        json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert failure.return_code == 17
    assert failure.stdout == b"safe-out\n"
    assert failure.stderr == b"safe-err\n"
    assert set(vars(failure)) == {
        "operation",
        "argv_sha256",
        "return_code",
        "stdout",
        "stderr",
    }
    assert not hasattr(failure, "argv")


def test_native_command_output_bound_terminates_process_group(tmp_path: Path) -> None:
    from tests.acceptance.slice2.native_preflight import NativePreflightError, run_command

    marker = tmp_path / "child-survived"
    child = (
        "import time; from pathlib import Path; time.sleep(0.5); "
        f"Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([{sys.executable!r}, '-c', {child!r}]); "
        "sys.stdout.buffer.write(b'x' * (64 * 1024 + 1)); "
        "sys.stdout.flush(); time.sleep(5)"
    )

    with pytest.raises(NativePreflightError, match="stdout limit exceeded"):
        run_command((sys.executable, "-c", parent), timeout_seconds=2)
    time.sleep(0.8)

    assert not marker.exists()


@pytest.mark.parametrize(
    "value",
    (
        b"invalid-utf8:\xff",
        b"unsafe-control:\x00",
        b"-----BEGIN PRIVATE KEY-----",
        b"access_token=do-not-retain",
        b"credential=do-not-retain",
        b"x" * 8192 + b"\xff",
        b"x" * 8192 + b"\ncredential=do-not-retain",
    ),
    ids=(
        "invalid-utf8",
        "unsafe-control",
        "private-key",
        "token",
        "credential",
        "invalid-utf8-after-limit",
        "credential-after-limit",
    ),
)
def test_command_failure_stream_redacts_unsafe_excerpt(value: bytes) -> None:
    from tests.acceptance.slice2.native_preflight import bounded_failure_stream

    assert bounded_failure_stream(value) == {
        "byte_count": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
        "truncated": len(value) > 8192,
        "excerpt": "[REDACTED]",
    }


def test_command_failure_stream_truncates_safe_excerpt_at_8192_bytes() -> None:
    from tests.acceptance.slice2.native_preflight import bounded_failure_stream

    value = b"x" * 9000

    assert bounded_failure_stream(value) == {
        "byte_count": 9000,
        "sha256": hashlib.sha256(value).hexdigest(),
        "truncated": True,
        "excerpt": "x" * 8192,
    }


def test_compose_admin_command_selects_only_exact_project_service_and_action() -> None:
    from tests.acceptance.slice2.native_preflight import compose_admin_command

    assert compose_admin_command("labpilot-task5", "coordinator", "restart") == (
        "docker",
        "compose",
        "--project-name",
        "labpilot-task5",
        "restart",
        "coordinator",
    )
    for project, service, action in (
        ("bad;project", "coordinator", "restart"),
        ("labpilot-task5", "other", "restart"),
        ("labpilot-task5", "coordinator", "exec sh"),
    ):
        with pytest.raises(ValueError):
            compose_admin_command(project, service, action)


def test_exact_systemd_unit_is_derived_only_from_x() -> None:
    from tests.acceptance.slice2.native_preflight import exact_systemd_unit

    execution_identity = f"labpilot-v1-{'a' * 64}-{'b' * 32}"
    assert exact_systemd_unit(execution_identity) == f"{execution_identity}.service"
    for invalid in ("other", f"{execution_identity}.service", f"{execution_identity};reboot"):
        with pytest.raises(ValueError):
            exact_systemd_unit(invalid)


def test_native_worker_command_is_fixed_argv_not_arbitrary_shell() -> None:
    from tests.acceptance.slice2.native_preflight import native_worker_command

    assert native_worker_command(
        "a1b2c3d4e5f6",
        scenario="F06",
        operation="delegate_real_launch",
        tested_sha="a" * 40,
        python_profile="python-3.12",
    ) == (
        "docker",
        "exec",
        "a1b2c3d4e5f6",
        "python",
        "-m",
        "tests.acceptance.slice2.native_worker",
        "--scenario",
        "F06",
        "--operation",
        "delegate_real_launch",
        "--tested-sha",
        "a" * 40,
        "--python-profile",
        "python-3.12",
    )
    assert native_worker_command(
        "a1b2c3d4e5f6",
        scenario="F06",
        operation="initialize",
        tested_sha="a" * 40,
        python_profile="python-3.12",
    )[6:10] == ("--scenario", "F06", "--operation", "initialize")
    for container, scenario, operation in (
        ("bad;id", "F06", "delegate_real_launch"),
        ("a1b2c3d4e5f6", "F23", "delegate_real_launch"),
        ("a1b2c3d4e5f6", "F06", "sh -c reboot"),
    ):
        with pytest.raises(ValueError):
            native_worker_command(
                container,
                scenario=scenario,
                operation=operation,
                tested_sha="a" * 40,
                python_profile="python-3.12",
            )


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


def test_profile_matrix_request_never_dispatches_a_formal_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance.slice2 import native_campaign, native_preflight, scenarios

    request = {
        "config": {"repository_root": "/repo"},
        "scenarios": (),
        "profile_matrix": "V2-A1-V2-A20",
        "phase": "profile_matrix",
        "database_url": "postgresql://authority",
        "tested_sha": "a" * 40,
    }
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        native_preflight,
        "run_preflight_request",
        lambda value: {"tested_sha": value["tested_sha"]},
    )
    monkeypatch.setattr(
        native_campaign,
        "run_profile_matrix",
        lambda selection, config: calls.append((selection, config)),
    )
    monkeypatch.setattr(
        scenarios,
        "dispatch_native_scenario",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("F dispatch")),
    )

    scenarios.run_native_request(request)

    assert calls == [("V2-A1-V2-A20", {"repository_root": "/repo"})]


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


def test_native_preflight_git_uses_exact_command_local_trust(tmp_path: Path) -> None:
    from tests.acceptance.slice2 import native_preflight

    repository = tmp_path / "candidate"
    repository.mkdir()
    canonical = str(repository.resolve())
    command = native_preflight._native_repository_git_command(
        repository / ".", "status", "--short"
    )

    assert command == (
        "git",
        "-c",
        f"safe.directory={canonical}",
        "-C",
        canonical,
        "status",
        "--short",
    )
    assert "safe.directory=*" not in command
    assert "config" not in command

    missing = native_preflight._native_repository_git_command(
        tmp_path / "missing", "rev-parse", "HEAD"
    )
    with pytest.raises(native_preflight.NativeCommandFailure):
        native_preflight.run_command(missing)


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


def test_native_preflight_records_exact_app_root_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _calls, metadata_paths = _run_fake_native_preflight(monkeypatch, app_mode=0o711)

    assert Path("/opt/labpilot-runner/app") in metadata_paths
    assert result["application_root"] == {
        "kind": "directory",
        "uid": 0,
        "gid": 0,
        "mode": 0o711,
    }


@pytest.mark.parametrize("app_mode", [0o700, 0o755])
def test_native_preflight_rejects_non_exact_app_root_mode(
    monkeypatch: pytest.MonkeyPatch, app_mode: int
) -> None:
    from tests.acceptance.slice2.native_preflight import NativePreflightError

    with pytest.raises(NativePreflightError, match="application root"):
        _run_fake_native_preflight(monkeypatch, app_mode=app_mode)


def test_native_preflight_controller_import_command_is_fixed_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, _metadata_paths = _run_fake_native_preflight(monkeypatch, app_mode=0o711)
    controller_calls = [command for command in calls if command[0] == "runuser"]

    assert len(controller_calls) == 1
    command = controller_calls[0]
    assert command[:11] == (
        "runuser",
        "--user",
        "labpilot-controller",
        "--group",
        "labpilot-control",
        "--",
        "/opt/labpilot-runner/app/.venv/bin/python",
        "-E",
        "-s",
        "-B",
        "-c",
    )
    proof = command[11]
    assert "import labpilot" in proof
    assert "labpilot.execution.runner_client" in proof
    assert "labpilot.execution.runner_protocol" in proof
    assert result["controller_final_import"] == {
        "labpilot_path": (
            "/opt/labpilot-runner/app/.venv/lib/python3.12/site-packages/labpilot/__init__.py"
        )
    }


def test_compose_network_and_api_mount_authority_are_narrow() -> None:
    from tests.acceptance.slice2.native_preflight import (
        NativePreflightError,
        require_api_mounts,
        require_postgres_bind,
    )

    require_postgres_bind({"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]})
    require_api_mounts(
        [{"Destination": "/app/content", "Source": "/srv/labpilot/content"}],
        workspace_root="/srv/labpilot/attempts",
    )

    with pytest.raises(NativePreflightError, match="loopback"):
        require_postgres_bind({"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "55432"}]})
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
