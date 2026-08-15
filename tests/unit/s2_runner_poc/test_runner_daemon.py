import grp
import json
import os
import pwd
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from tests.poc.s2_runner.contract import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    ControlOutcome,
    ControlOutcomeKind,
    FrozenLaunchSpec,
    PhysicalObservation,
    PhysicalObservationKind,
    execution_spec_hash,
    supervisor_execution_identity,
)
from tests.poc.s2_runner.runner_daemon import RunnerDaemon, RunnerPolicy, load_policy
from tests.poc.s2_runner.runner_protocol import (
    PROTOCOL_VERSION,
    RunnerLaunchRequest,
    RunnerOperation,
    RunnerStopRequest,
)
from tests.poc.s2_runner.systemd_cli import SystemdCli

X = supervisor_execution_identity(
    "native-installation", UUID("12345678-1234-5678-1234-567812345678")
)


def _spec(**changes: object) -> FrozenLaunchSpec:
    values: dict[str, object] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "runtime_path": "/usr/bin/python3",
        "argv": ("-m", "tests.poc.s2_runner.probe"),
        "working_directory": "/var/lib/labpilot-s2-poc/workspace/run-1",
        "environment": (("LABPILOT_POC_ENV", "visible"),),
        "credential_slots": (),
        "launch_profile_version": "s2-poc-b1",
        "start_marker_path": "/var/lib/labpilot-s2-poc/markers/run-1.jsonl",
        "sleep_seconds": 2.0,
        "exit_code": 0,
        "child_sleep_seconds": 0.0,
        "print_env_keys": ("LABPILOT_POC_ENV",),
    }
    values.update(changes)
    return FrozenLaunchSpec(**values)  # type: ignore[arg-type]


def _policy() -> RunnerPolicy:
    return RunnerPolicy(
        controller_user="labpilot-controller",
        controller_group="labpilot-control",
        controller_uid=2101,
        controller_gid=2101,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        socket_path=Path("/run/labpilot-s2-runner-poc/control.sock"),
        launch_profile_version="s2-poc-b1",
        allowed_runtime_path=Path("/usr/bin/python3"),
        allowed_working_root=Path("/var/lib/labpilot-s2-poc/workspace"),
        allowed_marker_root=Path("/var/lib/labpilot-s2-poc/markers"),
        allowed_environment_keys=frozenset({"LABPILOT_POC_ENV", "LABPILOT_POC_SECOND"}),
        stop_timeout_seconds=5.0,
        collect_after_exit=True,
        policy_identity="a" * 64,
    )


class FakeSystemdCli(SystemdCli):
    def __init__(self) -> None:
        self.launch_calls = 0
        self.observe_calls = 0
        self.stop_calls = 0

    def launch(self, **kwargs: object) -> ControlOutcome:
        self.launch_calls += 1
        return ControlOutcome(
            kind=ControlOutcomeKind.CREATED,
            execution_identity=cast(str, kwargs["execution_identity"]),
            expected_spec_hash=cast(str, kwargs["execution_spec_hash"]),
            detail_code="created",
        )

    def observe(self, **kwargs: object) -> PhysicalObservation:
        self.observe_calls += 1
        return PhysicalObservation(
            kind=PhysicalObservationKind.ACTIVE_MATCH,
            execution_identity=cast(str, kwargs["execution_identity"]),
            expected_spec_hash=cast(str, kwargs["execution_spec_hash"]),
            observed_spec_hash=cast(str, kwargs["execution_spec_hash"]),
        )

    def stop(self, **kwargs: object) -> ControlOutcome:
        self.stop_calls += 1
        return ControlOutcome(
            kind=ControlOutcomeKind.CONTROL_ACCEPTED,
            execution_identity=cast(str, kwargs["execution_identity"]),
            expected_spec_hash=cast(str, kwargs["execution_spec_hash"]),
            detail_code="stopped",
        )


def _launch_request(
    spec: FrozenLaunchSpec | None = None, *, asserted_hash: str | None = None
) -> RunnerLaunchRequest:
    launch_spec = spec or _spec()
    return RunnerLaunchRequest(
        version=PROTOCOL_VERSION,
        operation=RunnerOperation.LAUNCH,
        execution_identity=X,
        execution_spec_hash=asserted_hash or execution_spec_hash(launch_spec),
        spec=launch_spec,
        fault_point="none",
    )


def test_wrong_peer_uid_or_gid_is_rejected_before_systemd() -> None:
    systemd = FakeSystemdCli()
    daemon = RunnerDaemon(policy=_policy(), systemd_cli=systemd, allow_fault_injection=False)

    response = daemon.handle_request(_launch_request(), peer_uid=9999, peer_gid=9999)

    assert response == {"ok": False, "error": "peer_not_authorized"}
    assert systemd.launch_calls == systemd.observe_calls == systemd.stop_calls == 0


def test_hash_mismatch_causes_zero_systemd_calls() -> None:
    systemd = FakeSystemdCli()
    daemon = RunnerDaemon(policy=_policy(), systemd_cli=systemd, allow_fault_injection=False)

    response = daemon.handle_request(
        _launch_request(asserted_hash="0" * 64), peer_uid=2101, peer_gid=2101
    )

    assert response == {"ok": False, "error": "execution_spec_hash_mismatch"}
    assert systemd.launch_calls == 0


def test_path_escape_causes_zero_systemd_calls() -> None:
    systemd = FakeSystemdCli()
    daemon = RunnerDaemon(policy=_policy(), systemd_cli=systemd, allow_fault_injection=False)

    response = daemon.handle_request(
        _launch_request(_spec(working_directory="/etc")), peer_uid=2101, peer_gid=2101
    )

    assert response == {"ok": False, "error": "working_directory_denied"}
    assert systemd.launch_calls == 0


def test_stop_wrong_h_has_zero_destructive_calls() -> None:
    class ConflictSystemd(FakeSystemdCli):
        def stop(self, **kwargs: object) -> ControlOutcome:
            raise AssertionError("daemon must not call stop after identity conflict")

        def observe(self, **kwargs: object) -> PhysicalObservation:
            self.observe_calls += 1
            return PhysicalObservation(
                kind=PhysicalObservationKind.IDENTITY_CONFLICT,
                execution_identity=cast(str, kwargs["execution_identity"]),
                expected_spec_hash=cast(str, kwargs["execution_spec_hash"]),
                observed_spec_hash="c" * 64,
            )

    systemd = ConflictSystemd()
    daemon = RunnerDaemon(policy=_policy(), systemd_cli=systemd, allow_fault_injection=False)
    request = RunnerStopRequest(
        version=PROTOCOL_VERSION,
        operation=RunnerOperation.STOP,
        execution_identity=X,
        execution_spec_hash="d" * 64,
        force=True,
    )

    response = daemon.handle_request(request, peer_uid=2101, peer_gid=2101)

    assert response["ok"] is True
    assert response["kind"] == PhysicalObservationKind.IDENTITY_CONFLICT.value
    assert systemd.observe_calls == 1
    assert systemd.stop_calls == 0


def test_policy_file_must_be_root_owned_not_writable_and_use_fixed_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pwd,
        "getpwnam",
        lambda name: SimpleNamespace(
            pw_uid={"labpilot-controller": 2101, "labpilot-train": 2102}[name]
        ),
    )
    monkeypatch.setattr(
        grp,
        "getgrnam",
        lambda name: SimpleNamespace(
            gr_gid={"labpilot-control": 2101, "labpilot-train": 2102}[name]
        ),
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "controller_user": "labpilot-controller",
                "controller_group": "labpilot-control",
                "execution_user": "labpilot-train",
                "execution_group": "labpilot-train",
                "socket_path": "/run/labpilot-s2-runner-poc/control.sock",
                "launch_profile_version": "s2-poc-b1",
                "allowed_runtime_path": "/usr/bin/python3",
                "allowed_working_root": "/var/lib/labpilot-s2-poc/workspace",
                "allowed_marker_root": "/var/lib/labpilot-s2-poc/markers",
                "allowed_environment_keys": ["LABPILOT_POC_ENV", "LABPILOT_POC_SECOND"],
                "stop_timeout_seconds": 5.0,
                "collect_after_exit": True,
            }
        )
    )
    os.chmod(policy_path, 0o600)

    loaded = load_policy(policy_path)

    assert loaded.controller_uid == 2101
    assert loaded.controller_gid == 2101
    assert loaded.execution_user == "labpilot-train"

    os.chmod(policy_path, 0o620)
    with pytest.raises(ValueError, match="policy_permissions_unsafe"):
        load_policy(policy_path)


def test_policy_rejects_identity_names_other_than_candidate_b_principals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=2101))
    monkeypatch.setattr(grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=2101))
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "controller_user": "air",
                "controller_group": "labpilot-control",
                "execution_user": "labpilot-train",
                "execution_group": "labpilot-train",
                "socket_path": "/run/labpilot-s2-runner-poc/control.sock",
                "launch_profile_version": "s2-poc-b1",
                "allowed_runtime_path": "/usr/bin/python3",
                "allowed_working_root": "/var/lib/labpilot-s2-poc/workspace",
                "allowed_marker_root": "/var/lib/labpilot-s2-poc/markers",
                "allowed_environment_keys": ["LABPILOT_POC_ENV"],
                "stop_timeout_seconds": 5.0,
                "collect_after_exit": True,
            }
        )
    )
    os.chmod(policy_path, 0o600)

    with pytest.raises(ValueError, match="policy_identity_mismatch"):
        load_policy(policy_path)
