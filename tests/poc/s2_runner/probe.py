import argparse
import errno
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-identity", required=True)
    parser.add_argument("--execution-spec-hash", required=True)
    parser.add_argument("--start-marker", required=True, type=Path)
    parser.add_argument("--sleep-seconds", required=True, type=float)
    parser.add_argument("--exit-code", required=True, type=int)
    parser.add_argument("--child-sleep-seconds", required=True, type=float)
    parser.add_argument("--print-env", action="append", default=[])
    parser.add_argument("--security-evidence", action="store_true")
    arguments = parser.parse_args(argv)

    requested_environment = {
        key: os.environ.get(key) for key in arguments.print_env
    }
    security = security_facts() if arguments.security_evidence else None
    started = {
        "event": "probe_started",
        "execution_identity": arguments.execution_identity,
        "execution_spec_hash": arguments.execution_spec_hash,
        "pid": os.getpid(),
        "runtime_path": sys.executable,
        "working_directory": os.getcwd(),
        "argv": list(sys.argv),
        "environment": requested_environment,
        "timestamp": _timestamp(),
    }
    if security is not None:
        started["security"] = security
    _append_marker(arguments.start_marker, started)
    _print_record(started)
    print(
        json.dumps(
            {
                "event": "probe_stderr",
                "execution_identity": arguments.execution_identity,
                "execution_spec_hash": arguments.execution_spec_hash,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )
    if arguments.print_env:
        _print_record(
            {
                "event": "probe_environment",
                "execution_identity": arguments.execution_identity,
                "execution_spec_hash": arguments.execution_spec_hash,
                "environment": requested_environment,
            }
        )
    if security is not None:
        _print_record(
            {
                "event": "probe_security",
                "execution_identity": arguments.execution_identity,
                "execution_spec_hash": arguments.execution_spec_hash,
                **security,
            }
        )
    if arguments.child_sleep_seconds > 0:
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time; time.sleep(float(sys.argv[1]))",
                str(arguments.child_sleep_seconds),
            ],
            close_fds=True,
        )
    time.sleep(arguments.sleep_seconds)
    _print_record(
        {
            "event": "probe_terminal",
            "execution_identity": arguments.execution_identity,
            "execution_spec_hash": arguments.execution_spec_hash,
            "exit_code": arguments.exit_code,
            "timestamp": _timestamp(),
        }
    )
    return int(arguments.exit_code)


def security_facts() -> dict[str, object]:
    capabilities = _capability_fields()
    return {
        "euid": os.geteuid(),
        "egid": os.getegid(),
        "cap_eff": capabilities.get("CapEff"),
        "cap_bnd": capabilities.get("CapBnd"),
        "cap_amb": capabilities.get("CapAmb"),
        "direct_systemd_run": _direct_systemd_run_result(),
        "runner_socket_connect": _runner_socket_result(),
        "policy_write": _policy_write_result(),
    }


def _capability_fields() -> dict[str, str]:
    try:
        lines = Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"CapEff", "CapBnd", "CapAmb"}:
            result[key] = value.strip()
    return result


def _direct_systemd_run_result() -> str:
    if shutil.which("systemd-run") is None:
        return "unavailable"
    try:
        result = subprocess.run(
            [
                "systemd-run",
                "--system",
                "--no-ask-password",
                f"--unit=labpilot-s2-train-direct-{os.getpid()}.service",
                "--collect",
                "--wait",
                "/usr/bin/true",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return "unavailable"
    return "allowed" if result.returncode == 0 else "denied"


def _runner_socket_result() -> str:
    path = "/run/labpilot-s2-runner-poc/control.sock"
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(2)
        connection.connect(path)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EPERM}:
            return "denied"
        return "unavailable"
    finally:
        connection.close()
    return "allowed"


def _policy_write_result() -> str:
    path = "/etc/labpilot-s2-runner-poc/policy.json"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EPERM}:
            return "denied"
        return "unavailable"
    os.close(descriptor)
    return "allowed"


def _append_marker(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as marker:
        marker.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        marker.flush()
        os.fsync(marker.fileno())


def _print_record(record: dict[str, object]) -> None:
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
