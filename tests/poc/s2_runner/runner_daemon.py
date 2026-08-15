import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import socket
import stat
import struct
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from tests.poc.s2_runner.contract import (
    ControlOutcome,
    FrozenLaunchSpec,
    PhysicalObservation,
    PhysicalObservationKind,
    PositiveCreationEvidence,
    systemd_unit_name,
    validate_request_hash,
)
from tests.poc.s2_runner.runner_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    RunnerInfoRequest,
    RunnerLaunchRequest,
    RunnerObserveRequest,
    RunnerRequest,
    RunnerStopRequest,
    decode_request,
    encode_response,
    recv_frame,
    send_frame,
)
from tests.poc.s2_runner.systemd_cli import SystemdCli

RUNNER_VERSION = "s2-runner-poc-b1"
_ALLOWED_FAULT_POINTS = {
    "none",
    "before_systemd_dispatch",
    "after_systemd_dispatch_before_response",
}
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_POLICY_KEYS = {
    "controller_user",
    "controller_group",
    "execution_user",
    "execution_group",
    "socket_path",
    "launch_profile_version",
    "allowed_runtime_path",
    "allowed_working_root",
    "allowed_marker_root",
    "allowed_environment_keys",
    "stop_timeout_seconds",
    "collect_after_exit",
}


@dataclass(frozen=True, slots=True)
class RunnerPolicy:
    controller_user: str
    controller_group: str
    controller_uid: int
    controller_gid: int
    execution_user: str
    execution_group: str
    socket_path: Path
    launch_profile_version: str
    allowed_runtime_path: Path
    allowed_working_root: Path
    allowed_marker_root: Path
    allowed_environment_keys: frozenset[str]
    stop_timeout_seconds: float
    collect_after_exit: bool
    policy_identity: str


class InjectedRunnerCrash(RuntimeError):
    pass


class RunnerDaemon:
    def __init__(
        self,
        *,
        policy: RunnerPolicy,
        systemd_cli: SystemdCli,
        allow_fault_injection: bool,
    ) -> None:
        self._policy = policy
        self._systemd_cli = systemd_cli
        self._allow_fault_injection = allow_fault_injection

    def handle_request(
        self, request: RunnerRequest, *, peer_uid: int, peer_gid: int
    ) -> dict[str, object]:
        if (
            peer_uid != self._policy.controller_uid
            or peer_gid != self._policy.controller_gid
        ):
            return {"ok": False, "error": "peer_not_authorized"}

        if isinstance(request, RunnerInfoRequest):
            return {
                "ok": True,
                "protocol_version": PROTOCOL_VERSION,
                "runner_version": RUNNER_VERSION,
                "policy_identity": self._policy.policy_identity,
                "peer_uid": peer_uid,
                "peer_gid": peer_gid,
            }
        if not _valid_identity_and_hash(request.execution_identity, request.execution_spec_hash):
            return {"ok": False, "error": "execution_identity_or_hash_invalid"}
        if isinstance(request, RunnerLaunchRequest):
            return self._handle_launch(request)
        if isinstance(request, RunnerObserveRequest):
            observation = self._systemd_cli.observe(
                execution_identity=request.execution_identity,
                execution_spec_hash=request.execution_spec_hash,
            )
            return _observation_response(observation)
        return self._handle_stop(request)

    def serve_forever(self) -> NoReturn:
        listener = _bind_listener(self._policy)
        while True:
            connection, _ = listener.accept()
            with connection:
                try:
                    self._serve_connection(connection)
                except InjectedRunnerCrash:
                    os._exit(73)

    def _serve_connection(self, connection: socket.socket) -> None:
        try:
            peer_pid, peer_uid, peer_gid = _peer_credentials(connection)
            del peer_pid
            request = decode_request(recv_frame(connection))
            response = self.handle_request(
                request, peer_uid=peer_uid, peer_gid=peer_gid
            )
            self._try_send_response(connection, response)
        except ProtocolError as error:
            self._try_send_response(connection, {"ok": False, "error": str(error)})
        except (OSError, ValueError):
            self._try_send_response(
                connection, {"ok": False, "error": "runner_request_failed"}
            )

    @staticmethod
    def _try_send_response(
        connection: socket.socket, response: dict[str, object]
    ) -> None:
        try:
            send_frame(connection, encode_response(response))
        except OSError:
            pass

    def _handle_launch(self, request: RunnerLaunchRequest) -> dict[str, object]:
        try:
            validate_request_hash(request.spec, request.execution_spec_hash)
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        policy_error = _validate_spec_policy(request.spec, self._policy)
        if policy_error is not None:
            return {"ok": False, "error": policy_error}
        if request.fault_point not in _ALLOWED_FAULT_POINTS:
            return {"ok": False, "error": "fault_point_invalid"}
        if request.fault_point != "none" and not self._allow_fault_injection:
            return {"ok": False, "error": "fault_injection_disabled"}
        if request.fault_point == "before_systemd_dispatch":
            raise InjectedRunnerCrash("before_systemd_dispatch")

        outcome = self._systemd_cli.launch(
            execution_identity=request.execution_identity,
            execution_spec_hash=request.execution_spec_hash,
            spec=request.spec,
            creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        )
        if request.fault_point == "after_systemd_dispatch_before_response":
            raise InjectedRunnerCrash("after_systemd_dispatch_before_response")
        return _outcome_response(outcome)

    def _handle_stop(self, request: RunnerStopRequest) -> dict[str, object]:
        observation = self._systemd_cli.observe(
            execution_identity=request.execution_identity,
            execution_spec_hash=request.execution_spec_hash,
        )
        if observation.kind is not PhysicalObservationKind.ACTIVE_MATCH:
            return _observation_response(observation)
        return _outcome_response(
            self._systemd_cli.stop(
                execution_identity=request.execution_identity,
                execution_spec_hash=request.execution_spec_hash,
                force=request.force,
            )
        )


def load_policy(path: Path) -> RunnerPolicy:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("policy_symlink_denied")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("policy_not_regular_file")
    if metadata.st_uid != 0:
        raise ValueError("policy_not_root_owned")
    if metadata.st_mode & 0o022:
        raise ValueError("policy_permissions_unsafe")

    raw = path.read_bytes()
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("policy_invalid_json") from error
    if not isinstance(decoded, dict) or set(decoded) != _POLICY_KEYS:
        raise ValueError("policy_schema_invalid")

    controller_user = _policy_str(decoded, "controller_user")
    controller_group = _policy_str(decoded, "controller_group")
    execution_user = _policy_str(decoded, "execution_user")
    execution_group = _policy_str(decoded, "execution_group")
    if (
        controller_user != "labpilot-controller"
        or controller_group != "labpilot-control"
        or execution_user != "labpilot-train"
        or execution_group != "labpilot-train"
    ):
        raise ValueError("policy_identity_mismatch")

    socket_path = _absolute_policy_path(decoded, "socket_path")
    allowed_runtime_path = _absolute_policy_path(decoded, "allowed_runtime_path")
    allowed_working_root = _absolute_policy_path(decoded, "allowed_working_root")
    allowed_marker_root = _absolute_policy_path(decoded, "allowed_marker_root")
    launch_profile_version = _policy_str(decoded, "launch_profile_version")
    environment_keys = decoded.get("allowed_environment_keys")
    if not isinstance(environment_keys, list) or not all(
        isinstance(key, str) and key for key in environment_keys
    ):
        raise ValueError("policy_environment_keys_invalid")
    if len(environment_keys) != len(set(environment_keys)):
        raise ValueError("policy_environment_keys_invalid")
    stop_timeout = decoded.get("stop_timeout_seconds")
    if (
        isinstance(stop_timeout, bool)
        or not isinstance(stop_timeout, int | float)
        or stop_timeout <= 0
    ):
        raise ValueError("policy_stop_timeout_invalid")
    collect_after_exit = decoded.get("collect_after_exit")
    if not isinstance(collect_after_exit, bool):
        raise ValueError("policy_collect_invalid")

    controller_uid = pwd.getpwnam(controller_user).pw_uid
    controller_gid = grp.getgrnam(controller_group).gr_gid
    pwd.getpwnam(execution_user)
    grp.getgrnam(execution_group)
    canonical = json.dumps(
        decoded, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return RunnerPolicy(
        controller_user=controller_user,
        controller_group=controller_group,
        controller_uid=controller_uid,
        controller_gid=controller_gid,
        execution_user=execution_user,
        execution_group=execution_group,
        socket_path=socket_path,
        launch_profile_version=launch_profile_version,
        allowed_runtime_path=allowed_runtime_path,
        allowed_working_root=allowed_working_root,
        allowed_marker_root=allowed_marker_root,
        allowed_environment_keys=frozenset(environment_keys),
        stop_timeout_seconds=float(stop_timeout),
        collect_after_exit=collect_after_exit,
        policy_identity=hashlib.sha256(canonical).hexdigest(),
    )


def _validate_spec_policy(spec: FrozenLaunchSpec, policy: RunnerPolicy) -> str | None:
    if spec.launch_profile_version != policy.launch_profile_version:
        return "launch_profile_version_denied"
    if Path(spec.runtime_path).resolve() != policy.allowed_runtime_path.resolve():
        return "runtime_path_denied"
    if not _under_root(Path(spec.working_directory), policy.allowed_working_root):
        return "working_directory_denied"
    if not _under_root(Path(spec.start_marker_path), policy.allowed_marker_root):
        return "start_marker_path_denied"
    if spec.credential_slots:
        return "credential_slots_denied"
    environment_keys = {key for key, _ in spec.environment}
    if not environment_keys.issubset(policy.allowed_environment_keys):
        return "environment_key_denied"
    if not set(spec.print_env_keys).issubset(environment_keys):
        return "print_env_key_denied"
    return None


def _under_root(candidate: Path, root: Path) -> bool:
    if not candidate.is_absolute():
        return False
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _valid_identity_and_hash(execution_identity: str, execution_spec_hash: str) -> bool:
    try:
        systemd_unit_name(execution_identity)
    except ValueError:
        return False
    return _HASH_PATTERN.fullmatch(execution_spec_hash) is not None


def _outcome_response(outcome: ControlOutcome) -> dict[str, object]:
    return {
        "ok": True,
        "kind": outcome.kind.value,
        "execution_identity": outcome.execution_identity,
        "execution_spec_hash": outcome.expected_spec_hash,
        "detail_code": outcome.detail_code,
    }


def _observation_response(observation: PhysicalObservation) -> dict[str, object]:
    return {
        "ok": True,
        "kind": observation.kind.value,
        "execution_identity": observation.execution_identity,
        "execution_spec_hash": observation.expected_spec_hash,
        "observed_spec_hash": observation.observed_spec_hash,
        "pid": observation.pid,
        "invocation_id": observation.invocation_id,
        "started_at": observation.started_at,
        "ended_at": observation.ended_at,
        "exit_code": observation.exit_code,
        "result": observation.result,
        "evidence_source": observation.evidence_source,
    }


def _policy_str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError("policy_schema_invalid")
    return item


def _absolute_policy_path(value: dict[str, object], key: str) -> Path:
    path = Path(_policy_str(value, key))
    if not path.is_absolute():
        raise ValueError("policy_path_not_absolute")
    return path


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    size = struct.calcsize("3i")
    return struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
    )


def _bind_listener(policy: RunnerPolicy) -> socket.socket:
    path = policy.socket_path
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != 0:
            raise ValueError("socket_path_unsafe")
        path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chown(path, 0, policy.controller_gid)
    os.chmod(path, 0o660)
    listener.listen(16)
    return listener


def _run_command(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )


def _crash() -> NoReturn:
    os._exit(73)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--test-only-fault-injection", action="store_true")
    arguments = parser.parse_args()
    policy = load_policy(arguments.policy)
    daemon = RunnerDaemon(
        policy=policy,
        systemd_cli=SystemdCli(
            command_runner=_run_command,
            use_journal_evidence=True,
            execution_user=policy.execution_user,
            execution_group=policy.execution_group,
            collect_after_exit=policy.collect_after_exit,
            stop_timeout_seconds=policy.stop_timeout_seconds,
        ),
        allow_fault_injection=arguments.test_only_fault_injection,
    )
    daemon.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
