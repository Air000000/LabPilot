import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest

from labpilot.execution.runner_daemon import RunnerDaemon, bind_listener
from labpilot.execution.runner_policy import parse_runner_policy
from labpilot.execution.runner_protocol import (
    PROTOCOL_VERSION,
    RunnerInfoRequest,
    RunnerLaunchRequest,
    RunnerObserveRequest,
    RunnerOperation,
)
from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_hash


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
                "environment": {"PATH": "/opt/python/bin:/usr/bin"},
            }
        },
        "allowed_local_dataset_roots": ["/datasets"],
        "launch_profile_version": "candidate-b-v1",
        "max_runtime_seconds": 3600,
        "stop_timeout_seconds": 30,
    }


class Supervisor:
    def __init__(self) -> None:
        self.calls = 0

    def observe(self, *args: object) -> object:
        self.calls += 1
        raise AssertionError("unauthorized peer reached supervisor")

    def launch(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("invalid policy reached supervisor")


def test_peer_credential_mismatch_causes_zero_systemd_calls() -> None:
    supervisor = Supervisor()
    daemon = RunnerDaemon(
        parse_runner_policy(_value(), controller_uid=1000, controller_gid=1001),
        supervisor,  # type: ignore[arg-type]
    )
    request = RunnerObserveRequest(
        PROTOCOL_VERSION, RunnerOperation.OBSERVE, "labpilot-v1-x", "a" * 64
    )

    response = daemon.handle_request(request, peer_uid=999, peer_gid=1001)

    assert response["outcome"] == "peer_not_authorized"
    assert supervisor.calls == 0


def test_info_returns_complete_nonsecret_policy_and_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "labpilot.execution.runner_daemon._boot_id", lambda: "boot-1"
    )
    daemon = RunnerDaemon(
        parse_runner_policy(_value(), controller_uid=1000, controller_gid=1001),
        Supervisor(),  # type: ignore[arg-type]
    )

    response = daemon.handle_request(
        RunnerInfoRequest(PROTOCOL_VERSION, RunnerOperation.INFO, None),
        peer_uid=1000,
        peer_gid=1001,
    )

    result = response["result"]
    assert isinstance(result, dict)
    assert result["allowed_local_dataset_roots"] == ["/datasets"]
    assert result["max_runtime_seconds"] == 3600
    assert result["boot_id"] == "boot-1"
    assert result["readiness_reason"] == "ready"


def test_launch_rechecks_current_root_policy_before_systemd() -> None:
    supervisor = Supervisor()
    policy = parse_runner_policy(_value(), controller_uid=1000, controller_gid=1001)
    daemon = RunnerDaemon(policy, supervisor)  # type: ignore[arg-type]
    spec = FrozenLaunchSpec(
        "v1",
        "/srv/labpilot/attempts/a",
        "a" * 64,
        "python-3.12",
        "/caller/chosen/python",
        "train.py",
        ".",
        (),
        "uri",
        "s3://data",
        "b" * 64,
        (("PATH", "/opt/python/bin:/usr/bin"),),
        300,
        "labpilot-train",
        "labpilot-train",
        "candidate-b-v1",
        policy.policy_identity,
    )
    request = RunnerLaunchRequest(
        PROTOCOL_VERSION,
        RunnerOperation.LAUNCH,
        "labpilot-v1-x",
        frozen_launch_spec_hash(spec),
        spec,
        "first_dispatch_proven",
    )

    response = daemon.handle_request(request, peer_uid=1000, peer_gid=1001)

    assert response["outcome"] == "definite_non_acceptance"
    unknown_profile = replace(spec, python_profile="not-admitted")
    unknown_response = daemon.handle_request(
        replace(
            request,
            spec=unknown_profile,
            execution_spec_hash=frozen_launch_spec_hash(unknown_profile),
        ),
        peer_uid=1000,
        peer_gid=1001,
    )
    assert unknown_response["outcome"] == "definite_non_acceptance"
    escaped = replace(
        spec,
        runtime_path="/opt/python/bin/python",
        workspace_path="/srv/labpilot/attempts/../escaped",
        dataset_reference_type="local_path",
        dataset_argument="/datasets/../etc/shadow",
    )
    escaped_response = daemon.handle_request(
        replace(
            request,
            spec=escaped,
            execution_spec_hash=frozen_launch_spec_hash(escaped),
        ),
        peer_uid=1000,
        peer_gid=1001,
    )
    assert escaped_response["outcome"] == "definite_non_acceptance"
    assert supervisor.calls == 0


class Listener:
    def bind(self, path: str) -> None:
        self.path = path

    def listen(self, backlog: int) -> None:
        self.backlog = backlog

    def close(self) -> None:
        pass


def test_socket_metadata_is_set_and_reread_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = Listener()
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("labpilot.execution.runner_daemon.socket.socket", lambda *args: listener)
    monkeypatch.setattr(
        "labpilot.execution.runner_daemon.os.chown", lambda *args: calls.append(("chown", *args))
    )
    monkeypatch.setattr(
        "labpilot.execution.runner_daemon.os.chmod", lambda *args: calls.append(("chmod", *args))
    )
    monkeypatch.setattr(
        "labpilot.execution.runner_daemon.os.stat",
        lambda path: SimpleNamespace(st_uid=0, st_gid=1001, st_mode=stat.S_IFSOCK | 0o660),
    )
    policy = parse_runner_policy(_value(), controller_uid=1000, controller_gid=1001)

    assert bind_listener(policy) is listener
    assert ("chown", "/run/labpilot-runner/control.sock", 0, 1001) in calls
    assert ("chmod", "/run/labpilot-runner/control.sock", 0o660) in calls


def test_socket_metadata_mismatch_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = Listener()
    monkeypatch.setattr("labpilot.execution.runner_daemon.socket.socket", lambda *args: listener)
    monkeypatch.setattr("labpilot.execution.runner_daemon.os.chown", lambda *args: None)
    monkeypatch.setattr("labpilot.execution.runner_daemon.os.chmod", lambda *args: None)
    monkeypatch.setattr(
        "labpilot.execution.runner_daemon.os.stat",
        lambda path: SimpleNamespace(st_uid=0, st_gid=1001, st_mode=stat.S_IFSOCK | 0o600),
    )
    with pytest.raises(RuntimeError, match="socket_metadata"):
        bind_listener(parse_runner_policy(_value(), controller_uid=1, controller_gid=1001))
