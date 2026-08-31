from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import selectors
import shlex
import signal
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_OPERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$", re.ASCII)
_COMPOSE_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$", re.ASCII)
_EXECUTION_IDENTITY = re.compile(r"^labpilot-v1-[0-9a-f]{64}-[0-9a-f]{32}$", re.ASCII)
_COMPOSE_SERVICES = {"postgres", "migrate", "api", "coordinator"}
_COMPOSE_ACTIONS = {"start", "stop", "restart", "kill"}
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$", re.ASCII)
_PYTHON_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_SECRET_KEYS = (
    "secret",
    "password",
    "token",
    "credential",
    "authorization",
    "apikey",
    "privatekey",
    "databaseurl",
)
_CREDENTIAL_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@", re.I)
_EXPECTED_IDENTITIES = {
    "labpilot-controller": {"uid": 999, "gid": 983},
    "labpilot-control": {"gid": 983},
    "labpilot-train": {"uid": 995, "gid": 982},
}
_EXPECTED_SERVICE = {
    "User": "root",
    "Group": "labpilot-control",
    "UMask": "0077",
    "RuntimeDirectory": "labpilot-runner",
    "RuntimeDirectoryMode": "0750",
    "RuntimeDirectoryPreserve": "restart",
    "Restart": "on-failure",
    "NoNewPrivileges": "yes",
    "RestrictAddressFamilies": "AF_UNIX",
}
_EXPECTED_EXEC_START = (
    "/opt/labpilot-runner/app/.venv/bin/python",
    "-E",
    "-s",
    "-B",
    "-m",
    "labpilot.execution.runner_daemon",
    "--policy",
    "/etc/labpilot/runner-policy.json",
)
_APP_ROOT = Path("/opt/labpilot-runner/app")
_APP_PYTHON = str(_APP_ROOT / ".venv/bin/python")
_CONTROLLER_IMPORT_PROOF = """
import sysconfig
from pathlib import Path

import labpilot
from labpilot.execution.runner_client import RunnerClient
from labpilot.execution.runner_protocol import PROTOCOL_VERSION, RunnerInfoRequest, RunnerOperation

app_root = Path("/opt/labpilot-runner/app").resolve(strict=True)
venv_root = (app_root / ".venv").resolve(strict=True)
site_packages = Path(sysconfig.get_path("purelib")).resolve(strict=True)
module_file = getattr(labpilot, "__file__", None)
if not module_file:
    raise SystemExit("labpilot must resolve from final installed venv")
resolved_module = Path(module_file).resolve(strict=True)
if not site_packages.is_relative_to(venv_root) or not resolved_module.is_relative_to(site_packages):
    raise SystemExit("labpilot must resolve from final installed venv")
if any(part.startswith(".stage.") for part in resolved_module.parts):
    raise SystemExit("labpilot must not resolve from staging")
assert RunnerClient and PROTOCOL_VERSION and RunnerInfoRequest and RunnerOperation
print(resolved_module)
""".strip()
_COORDINATOR_INFO_PROOF = """
import json

from labpilot.execution.runner_client import RunnerClient
from labpilot.execution.runner_protocol import PROTOCOL_VERSION, RunnerInfoRequest, RunnerOperation

response = RunnerClient("/run/labpilot-runner/control.sock", timeout_seconds=10).request(
    RunnerInfoRequest(PROTOCOL_VERSION, RunnerOperation.INFO, None)
)
result = response["result"]
selected = {
    "runner_version": result["runner_version"],
    "policy_identity": result["policy_identity"],
    "launch_profile_version": result["launch_profile_version"],
    "workspace_root": result["workspace_root"],
    "boot_id": result["boot_id"],
    "readiness_reason": result["readiness_reason"],
}
print(json.dumps({
    "version": response["version"],
    "operation": response["operation"],
    "outcome": response["outcome"],
    "result": selected,
}, separators=(",", ":"), sort_keys=True))
""".strip()
_EXPECTED_WORKSPACE = {
    "workspace": {"kind": "directory", "gid": 982, "mode": 0o750},
    "source_directory": {"kind": "directory", "gid": 982, "mode": 0o550},
    "source_file": {"kind": "file", "gid": 982, "mode": 0o440},
    "source_executable": {"kind": "file", "gid": 982, "mode": 0o550},
    "runtime": {"kind": "directory", "gid": 982, "mode": 0o770},
    "output": {"kind": "directory", "gid": 982, "mode": 0o770},
}


class NativePreflightError(RuntimeError):
    pass


class NativeCommandFailure(NativePreflightError):
    operation: str
    argv_sha256: str
    return_code: int
    stdout: bytes
    stderr: bytes

    def __init__(
        self,
        operation: str,
        argv_sha256: str,
        return_code: int,
        stdout: bytes,
        stderr: bytes,
    ) -> None:
        if (
            _SAFE_OPERATION.fullmatch(operation) is None
            or _SHA256.fullmatch(argv_sha256) is None
            or type(return_code) is not int
            or type(stdout) is not bytes
            or type(stderr) is not bytes
            or len(stdout) > 64 * 1024
            or len(stderr) > 64 * 1024
        ):
            raise ValueError("native command failure evidence is invalid")
        super().__init__(f"native command failed with status {return_code}")
        self.operation = operation
        self.argv_sha256 = argv_sha256
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    stdout: bytes
    stderr: bytes


def bounded_failure_stream(value: bytes, *, limit: int = 8192) -> dict[str, object]:
    if type(value) is not bytes or type(limit) is not int or not 0 < limit <= 8192:
        raise ValueError("failure stream bounds are invalid")
    excerpt_bytes = value[:limit]
    try:
        full_text = value.decode("utf-8", "strict")
        excerpt = excerpt_bytes.decode("utf-8", "strict")
    except UnicodeError:
        excerpt = "[REDACTED]"
    else:
        if (
            any(
                character not in "\n\t" and not character.isprintable()
                for character in full_text
            )
            or _secret_key(full_text)
            or _CREDENTIAL_URL.search(full_text) is not None
            or "PRIVATE KEY-----" in full_text
        ):
            excerpt = "[REDACTED]"
    return {
        "byte_count": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
        "truncated": len(value) > limit,
        "excerpt": excerpt,
    }


def compose_admin_command(project: str, service: str, action: str) -> tuple[str, ...]:
    if (
        _COMPOSE_PROJECT.fullmatch(project) is None
        or service not in _COMPOSE_SERVICES
        or action not in _COMPOSE_ACTIONS
    ):
        raise ValueError("compose admin selection is not authorized")
    return ("docker", "compose", "--project-name", project, action, service)


def exact_systemd_unit(execution_identity: str) -> str:
    if _EXECUTION_IDENTITY.fullmatch(execution_identity) is None:
        raise ValueError("invalid supervisor execution identity")
    return f"{execution_identity}.service"


def native_worker_command(
    container_id: str,
    *,
    scenario: str,
    operation: str,
    tested_sha: str,
    python_profile: str,
) -> tuple[str, ...]:
    from tests.acceptance.slice2.scenarios import scenario_operation_names

    if (
        _CONTAINER_ID.fullmatch(container_id) is None
        or _SHA.fullmatch(tested_sha) is None
        or _PYTHON_PROFILE.fullmatch(python_profile) is None
        or operation not in ("initialize", "snapshot", *scenario_operation_names(scenario))
    ):
        raise ValueError("native worker command is not authorized")
    return (
        "docker",
        "exec",
        container_id,
        "python",
        "-m",
        "tests.acceptance.slice2.native_worker",
        "--scenario",
        scenario,
        "--operation",
        operation,
        "--tested-sha",
        tested_sha,
        "--python-profile",
        python_profile,
    )


def run_preflight_request(request: Mapping[str, object]) -> dict[str, object]:
    config = request.get("config")
    tested_sha = request.get("tested_sha")
    if not isinstance(config, Mapping) or not isinstance(tested_sha, str):
        raise NativePreflightError("native preflight request is invalid")
    repository_root = _config_path(config, "repository_root")
    workspace_root = _config_path(config, "workspace_root")
    compose_project = _config_text(config, "compose_project")

    actual_sha = _text(run_command(("git", "-C", repository_root, "rev-parse", "HEAD")))
    clean = not _text(run_command(("git", "-C", repository_root, "status", "--short")))
    require_tested_sha(tested_sha, actual_sha, clean=clean)

    controller = pwd.getpwnam("labpilot-controller")
    train = pwd.getpwnam("labpilot-train")
    identities = {
        "labpilot-controller": {"uid": controller.pw_uid, "gid": controller.pw_gid},
        "labpilot-control": {"gid": grp.getgrnam("labpilot-control").gr_gid},
        "labpilot-train": {"uid": train.pw_uid, "gid": train.pw_gid},
    }
    require_identities(identities)

    application_root = _metadata(_APP_ROOT)
    require_metadata(
        "application root", application_root, kind="directory", uid=0, gid=0, mode=0o711
    )
    controller_final_import = _controller_final_import()

    service_names = (*_EXPECTED_SERVICE, "ExecStart")
    service = _properties(
        _text(
            run_command(
                (
                    "systemctl",
                    "show",
                    "labpilot-runner.service",
                    *(f"--property={name}" for name in service_names),
                )
            )
        )
    )
    require_effective_service(service)
    runtime = _metadata(Path("/run/labpilot-runner"))
    socket = _metadata(Path("/run/labpilot-runner/control.sock"))
    require_metadata("runtime directory", runtime, kind="directory", uid=0, gid=983, mode=0o750)
    require_metadata("socket", socket, kind="socket", uid=0, gid=983, mode=0o660)

    container_ids = {
        name: _container_id(compose_project, name) for name in ("postgres", "api", "coordinator")
    }
    postgres = _inspect_container(container_ids["postgres"])
    api = _inspect_container(container_ids["api"])
    require_postgres_bind(_mapping(_mapping(postgres, "NetworkSettings"), "Ports"))
    mounts = api.get("Mounts")
    if not isinstance(mounts, list) or not all(isinstance(item, Mapping) for item in mounts):
        raise NativePreflightError("API mount inspection is invalid")
    require_api_mounts(mounts, workspace_root=workspace_root)

    coordinator_id = container_ids["coordinator"]
    coordinator_uid = int(_docker_exec(coordinator_id, "id", "-u"))
    coordinator_gid = int(_docker_exec(coordinator_id, "id", "-g"))
    coordinator_groups = [int(value) for value in _docker_exec(coordinator_id, "id", "-G").split()]
    coordinator_identity = {
        "uid": coordinator_uid,
        "gid": coordinator_gid,
        "groups": coordinator_groups,
    }
    if coordinator_uid != 999 or coordinator_gid != 983 or 982 not in coordinator_groups:
        raise NativePreflightError("Coordinator numeric identity mismatch")
    try:
        runner_info = json.loads(
            _docker_exec(coordinator_id, "python", "-c", _COORDINATOR_INFO_PROOF)
        )
    except json.JSONDecodeError as error:
        raise NativePreflightError("Coordinator Runner INFO is invalid JSON") from error
    if (
        type(runner_info) is not dict
        or set(runner_info) != {"version", "operation", "outcome", "result"}
        or runner_info.get("version") != "labpilot-runner:v1"
        or runner_info.get("operation") != "INFO"
        or runner_info.get("outcome") != "ok"
        or type(runner_info.get("result")) is not dict
        or runner_info["result"].get("runner_version") != "candidate-b-v2"
        or runner_info["result"].get("launch_profile_version") != "candidate-b-v2"
        or runner_info["result"].get("readiness_reason") != "ready"
    ):
        raise NativePreflightError("Coordinator Runner INFO contract mismatch")

    return {
        "tested_sha": tested_sha,
        "identities": identities,
        "application_root": application_root,
        "controller_final_import": controller_final_import,
        "runner_service": service,
        "runtime_directory": runtime,
        "socket": socket,
        "containers": container_ids,
        "coordinator_identity": coordinator_identity,
        "runner_info": runner_info,
    }


def run_command(
    argv: Sequence[str], *, timeout_seconds: float = 20, max_output_bytes: int = 64 * 1024
) -> CommandResult:
    command = tuple(argv)
    if (
        not command
        or len(command) > 64
        or any(not argument or len(argument.encode()) > 4096 for argument in command)
        or timeout_seconds <= 0
        or max_output_bytes <= 0
        or max_output_bytes > 64 * 1024
    ):
        raise ValueError("native command bounds are invalid")
    operation = Path(command[0]).name
    if _SAFE_OPERATION.fullmatch(operation) is None:
        raise ValueError("native command operation is invalid")
    argv_sha256 = hashlib.sha256(
        json.dumps(
            list(command), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8", "strict")
    ).hexdigest()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout_seconds
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativePreflightError("native command timed out")
                events = selector.select(remaining)
                if not events:
                    raise NativePreflightError("native command timed out")
                for key, _mask in events:
                    stream = str(key.data)
                    chunk = os.read(key.fd, min(8192, max_output_bytes - len(outputs[stream]) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    outputs[stream].extend(chunk)
                    if len(outputs[stream]) > max_output_bytes:
                        raise NativePreflightError(f"native command {stream} limit exceeded")
            remaining = max(0.001, deadline - time.monotonic())
            return_code = process.wait(timeout=remaining)
        except BaseException:
            _kill_process_group(process)
            raise
    if return_code != 0:
        raise NativeCommandFailure(
            operation,
            argv_sha256,
            return_code,
            bytes(outputs["stdout"]),
            bytes(outputs["stderr"]),
        )
    return CommandResult(command, bytes(outputs["stdout"]), bytes(outputs["stderr"]))


def require_identities(identities: Mapping[str, Mapping[str, object]]) -> None:
    for name, expected in _EXPECTED_IDENTITIES.items():
        actual = identities.get(name)
        if actual is None:
            raise NativePreflightError(f"identity mismatch for {name}")
        for field, value in expected.items():
            if actual.get(field) != value:
                raise NativePreflightError(f"identity mismatch for {name} {field}")


def require_tested_sha(expected: str, actual: str, *, clean: bool) -> None:
    if _SHA.fullmatch(expected) is None or actual != expected or not clean:
        raise NativePreflightError("tested SHA does not match a clean native checkout")


def require_effective_service(properties: Mapping[str, str]) -> None:
    for name, expected in _EXPECTED_SERVICE.items():
        if properties.get(name) != expected:
            raise NativePreflightError(f"effective Runner property mismatch: {name}")
    match = re.search(r"(?:^|;\s*)argv\[\]=(.*?)\s*;", properties.get("ExecStart", ""))
    if match is None or tuple(shlex.split(match.group(1))) != _EXPECTED_EXEC_START:
        raise NativePreflightError("effective Runner property mismatch: ExecStart")


def _controller_final_import() -> dict[str, str]:
    resolved = _text(
        run_command(
            (
                "runuser",
                "--user",
                "labpilot-controller",
                "--group",
                "labpilot-control",
                "--",
                _APP_PYTHON,
                "-E",
                "-s",
                "-B",
                "-c",
                _CONTROLLER_IMPORT_PROOF,
            ),
            max_output_bytes=4096,
        )
    )
    if (
        not Path(resolved).is_absolute()
        or not _at_or_below(resolved, str(_APP_ROOT / ".venv"))
        or any(part.startswith(".stage.") for part in Path(resolved).parts)
    ):
        raise NativePreflightError("controller final import resolution mismatch")
    return {"labpilot_path": resolved}


def require_metadata(
    name: str,
    metadata: Mapping[str, object],
    *,
    kind: str,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    expected = {"kind": kind, "uid": uid, "gid": gid, "mode": mode}
    if any(metadata.get(field) != value for field, value in expected.items()):
        raise NativePreflightError(f"{name} metadata mismatch")


def require_postgres_bind(bindings: Mapping[str, object]) -> None:
    expected = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]}
    if bindings != expected:
        raise NativePreflightError("PostgreSQL publication must be loopback-only on port 55432")


def require_api_mounts(mounts: Sequence[Mapping[str, object]], *, workspace_root: str) -> None:
    for mount in mounts:
        source = mount.get("Source")
        destination = mount.get("Destination")
        if _at_or_below(source, "/run/labpilot-runner") or _at_or_below(
            destination, "/run/labpilot-runner"
        ):
            raise NativePreflightError("API must not receive the Runner runtime mount")
        if _at_or_below(source, workspace_root) or _at_or_below(destination, workspace_root):
            raise NativePreflightError("API must not receive the Attempt workspace mount")


def require_coordinator_continuity(before: str, after: str) -> None:
    if not before or before != after:
        raise NativePreflightError("Coordinator container changed across Runner restart")


def require_workspace_permissions(nodes: Mapping[str, Mapping[str, object]]) -> None:
    if set(nodes) != set(_EXPECTED_WORKSPACE):
        raise NativePreflightError("workspace permission nodes mismatch")
    for name, expected in _EXPECTED_WORKSPACE.items():
        actual = nodes[name]
        if any(actual.get(field) != value for field, value in expected.items()):
            raise NativePreflightError(f"workspace permission mismatch: {name}")


def redact_evidence(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): redact_evidence(item) for key, item in value.items() if not _secret_key(key)
        }
    if isinstance(value, list | tuple):
        return [redact_evidence(item) for item in value]
    if isinstance(value, str) and (
        value.lower().startswith("bearer ")
        or _CREDENTIAL_URL.match(value)
        or "PRIVATE KEY-----" in value
    ):
        return "[REDACTED]"
    return value


def _secret_key(value: object) -> bool:
    normalized = "".join(character for character in str(value).lower() if character.isalnum())
    return any(secret in normalized for secret in _SECRET_KEYS)


def _at_or_below(value: object, root: str) -> bool:
    return isinstance(value, str) and (value == root or value.startswith(f"{root}/"))


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _text(result: CommandResult) -> str:
    try:
        return result.stdout.decode("utf-8", "strict").strip()
    except UnicodeError as error:
        raise NativePreflightError("native command output is not UTF-8") from error


def _config_text(config: Mapping[str, object], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value:
        raise NativePreflightError(f"native config is missing {name}")
    return value


def _config_path(config: Mapping[str, object], name: str) -> str:
    value = _config_text(config, name)
    if not Path(value).is_absolute():
        raise NativePreflightError(f"native config path is not absolute: {name}")
    return value


def _properties(value: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in value.splitlines():
        name, separator, item = line.partition("=")
        if not separator or not name or name in properties:
            raise NativePreflightError("effective Runner properties are invalid")
        properties[name] = item
    return properties


def _metadata(path: Path) -> dict[str, object]:
    try:
        value = path.lstat()
    except OSError as error:
        raise NativePreflightError(f"cannot inspect native path: {path}") from error
    if stat.S_ISLNK(value.st_mode):
        kind = "symlink"
    elif stat.S_ISDIR(value.st_mode):
        kind = "directory"
    elif stat.S_ISSOCK(value.st_mode):
        kind = "socket"
    elif stat.S_ISREG(value.st_mode):
        kind = "file"
    else:
        kind = "other"
    return {
        "kind": kind,
        "uid": value.st_uid,
        "gid": value.st_gid,
        "mode": stat.S_IMODE(value.st_mode),
        "inode": value.st_ino,
    }


def _container_id(project: str, service: str) -> str:
    value = _text(
        run_command(
            (
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            )
        )
    )
    identifiers = value.splitlines()
    if len(identifiers) != 1:
        raise NativePreflightError(f"expected one running {service} container")
    return identifiers[0]


def _inspect_container(container_id: str) -> Mapping[str, object]:
    try:
        value = json.loads(_text(run_command(("docker", "inspect", container_id))))
    except json.JSONDecodeError as error:
        raise NativePreflightError("Docker inspection is not valid JSON") from error
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise NativePreflightError("Docker inspection shape is invalid")
    return value[0]


def _mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    item = value.get(name)
    if not isinstance(item, Mapping):
        raise NativePreflightError(f"Docker inspection is missing {name}")
    return item


def _docker_exec(container_id: str, *argv: str) -> str:
    return _text(run_command(("docker", "exec", container_id, *argv)))
