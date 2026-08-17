import argparse
import os
import socket
import stat
import struct
from dataclasses import fields
from pathlib import Path
from typing import NoReturn

from labpilot.execution.runner_policy import RunnerPolicy, load_runner_policy
from labpilot.execution.runner_protocol import (
    PROTOCOL_VERSION,
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
from labpilot.execution.runtime_probe import run_probe
from labpilot.execution.systemd_supervisor import (
    ControlOutcome,
    PhysicalObservation,
    SystemdSupervisor,
    subprocess_command_runner,
)
from labpilot.execution.types import FrozenLaunchSpec

RUNNER_VERSION = "candidate-b-v1"


class RunnerDaemon:
    def __init__(self, policy: RunnerPolicy, supervisor: SystemdSupervisor) -> None:
        self._policy = policy
        self._supervisor = supervisor

    def handle_request(
        self, request: RunnerRequest, *, peer_uid: int, peer_gid: int
    ) -> dict[str, object]:
        if peer_uid != self._policy.controller_uid or peer_gid != self._policy.controller_gid:
            return _response(request, "peer_not_authorized", {})
        if isinstance(request, RunnerInfoRequest):
            result: dict[str, object] = {
                "runner_version": RUNNER_VERSION,
                "policy_identity": self._policy.policy_identity,
                "workspace_root": self._policy.workspace_root,
                "execution_user": self._policy.execution_user,
                "execution_group": self._policy.execution_group,
                "launch_profile_version": self._policy.launch_profile_version,
                "runtime_profiles": [
                    {
                        "python_profile": profile.name,
                        "runtime_path": profile.runtime_path,
                        "environment": dict(profile.environment),
                    }
                    for profile in self._policy.runtime_profiles
                ],
            }
            if request.inspection is not None:
                result["observed_environment"] = run_probe(self._policy, request.inspection)
            return _response(request, "ok", result)
        if isinstance(request, RunnerObserveRequest):
            observation = self._supervisor.observe(
                request.execution_identity, request.execution_spec_hash
            )
            return _response(request, "ok", _object_dict(observation))
        if isinstance(request, RunnerLaunchRequest):
            policy_error = _validate_launch_spec(self._policy, request.spec)
            if policy_error is not None:
                return _response(
                    request,
                    "definite_non_acceptance",
                    {"detail_code": policy_error},
                )
            outcome = self._supervisor.launch(
                request.execution_identity,
                request.execution_spec_hash,
                request.spec,
                creation_authority=request.creation_authority == "first_dispatch_proven",
            )
            return _response(request, "ok", _object_dict(outcome))
        assert isinstance(request, RunnerStopRequest)
        outcome = self._supervisor.stop(request.execution_identity, request.execution_spec_hash)
        return _response(request, "ok", _object_dict(outcome))

    def serve_forever(self) -> NoReturn:
        listener = bind_listener(self._policy)
        while True:
            connection, _ = listener.accept()
            with connection:
                self._serve_connection(connection)

    def _serve_connection(self, connection: socket.socket) -> None:
        _, uid, gid = struct.unpack(
            "3i",
            connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
        )
        request = decode_request(recv_frame(connection))
        send_frame(
            connection, encode_response(self.handle_request(request, peer_uid=uid, peer_gid=gid))
        )


def bind_listener(policy: RunnerPolicy) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(policy.socket_path)
    os.chown(policy.socket_path, 0, policy.controller_gid)
    os.chmod(policy.socket_path, 0o660)
    metadata = os.stat(policy.socket_path)
    if (
        metadata.st_uid != 0
        or metadata.st_gid != policy.controller_gid
        or stat.S_IMODE(metadata.st_mode) != 0o660
        or not stat.S_ISSOCK(metadata.st_mode)
    ):
        listener.close()
        raise RuntimeError("socket_metadata_mismatch")
    listener.listen(16)
    return listener


def _response(request: RunnerRequest, outcome: str, result: dict[str, object]) -> dict[str, object]:
    return {
        "version": PROTOCOL_VERSION,
        "operation": request.operation.value,
        "outcome": outcome,
        "result": result,
    }


def _object_dict(value: PhysicalObservation | ControlOutcome) -> dict[str, object]:
    result: dict[str, object] = {}
    for field in fields(value):
        name = field.name
        item = getattr(value, name)
        result[name] = item.value if hasattr(item, "value") else item
    return result


def _validate_launch_spec(policy: RunnerPolicy, spec: FrozenLaunchSpec) -> str | None:
    try:
        profile = policy.runtime_profile(spec.python_profile)
    except ValueError:
        return "python_profile_not_admitted"
    if spec.launch_policy_identity != policy.policy_identity:
        return "launch_policy_identity_mismatch"
    if spec.launch_profile_version != policy.launch_profile_version:
        return "launch_profile_version_mismatch"
    if spec.runtime_path != profile.runtime_path:
        return "runtime_path_denied"
    if spec.environment != profile.environment:
        return "environment_denied"
    if (
        spec.execution_user != policy.execution_user
        or spec.execution_group != policy.execution_group
    ):
        return "execution_identity_denied"
    if not _under_root(Path(spec.workspace_path), Path(policy.workspace_root)):
        return "workspace_path_denied"
    for relative_path in (
        spec.train_entrypoint_relative_path,
        spec.workdir_relative_path,
    ):
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            return "source_path_denied"
    if spec.dataset_reference_type == "local_path" and not any(
        _under_root(Path(spec.dataset_argument), Path(root))
        for root in policy.allowed_local_dataset_roots
    ):
        return "dataset_path_denied"
    if spec.timeout_seconds > policy.max_runtime_seconds:
        return "runtime_timeout_denied"
    return None


def _under_root(candidate: Path, root: Path) -> bool:
    if not candidate.is_absolute():
        return False
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def main() -> NoReturn:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    policy = load_runner_policy(args.policy)
    RunnerDaemon(
        policy,
        SystemdSupervisor(
            subprocess_command_runner,
            execution_user=policy.execution_user,
            execution_group=policy.execution_group,
            stop_timeout_seconds=policy.stop_timeout_seconds,
        ),
    ).serve_forever()


if __name__ == "__main__":
    main()
