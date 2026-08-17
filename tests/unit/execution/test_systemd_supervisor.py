import json
import subprocess

import pytest

from labpilot.execution.systemd_supervisor import (
    ControlOutcomeKind,
    PhysicalObservationKind,
    SystemdSupervisor,
)
from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_hash

X = f"labpilot-v1-{'a' * 64}-{'b' * 32}"
UNIT = f"{X}.service"


def _spec() -> FrozenLaunchSpec:
    return FrozenLaunchSpec(
        "v1",
        "/srv/labpilot/attempts/a",
        "a" * 64,
        "python-3.12",
        "/opt/python/bin/python",
        "train.py",
        ".",
        ("--output", "/srv/labpilot/attempts/a/runtime/output"),
        "uri",
        "s3://dataset/v1",
        "b" * 64,
        (("PATH", "/opt/python/bin:/usr/bin"),),
        300,
        "labpilot-train",
        "labpilot-train",
        "candidate-b-v1",
        "c" * 64,
    )


class Commands:
    def __init__(self, outputs: list[tuple[int, str]]) -> None:
        self.outputs = outputs
        self.argv: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        assert timeout == 10.0
        self.argv.append(argv)
        code, stdout = self.outputs.pop(0)
        return subprocess.CompletedProcess(argv, code, stdout, "")


def _loaded(*, active: str, description: str) -> str:
    return (
        "LoadState=loaded\n"
        f"ActiveState={active}\n"
        f"Description={description}\n"
        "Result=success\nMainPID=42\nInvocationID=inv-1\nExecMainCode=1\nExecMainStatus=0\n"
    )


def test_same_x_different_h_is_identity_conflict_without_creation() -> None:
    commands = Commands([(0, _loaded(active="active", description="labpilot:a"))])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = _spec()

    outcome = supervisor.launch(
        X, frozen_launch_spec_hash(spec), spec, creation_authority=True
    )

    assert outcome.kind is ControlOutcomeKind.IDENTITY_CONFLICT
    assert len(commands.argv) == 1
    assert all("systemd-run" not in argv for argv in commands.argv)


def test_not_found_never_creates_without_positive_authority() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = _spec()

    outcome = supervisor.launch(
        X, frozen_launch_spec_hash(spec), spec, creation_authority=False
    )

    assert outcome.kind is ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE
    assert len(commands.argv) == 2


def test_stop_only_runs_after_fresh_exact_active_match() -> None:
    expected = "a" * 64
    commands = Commands(
        [
            (0, _loaded(active="failed", description=f"labpilot:{expected}")),
            (0, _loaded(active="active", description=f"labpilot:{expected}")),
            (0, ""),
        ]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    blocked = supervisor.stop(X, expected)
    accepted = supervisor.stop(X, expected)

    assert blocked.kind is ControlOutcomeKind.TERMINAL_MATCH
    assert accepted.kind is ControlOutcomeKind.CONTROL_ACCEPTED
    assert commands.argv[-1][:4] == ("systemctl", "--system", "--no-ask-password", "stop")
    assert commands.argv[-1][4] == UNIT


def test_workload_stdout_cannot_fabricate_terminal_evidence() -> None:
    fake = json.dumps(
        {
            "_SYSTEMD_UNIT": "some.service",
            "MESSAGE": json.dumps({"event": "terminal", "execution_spec_hash": "a" * 64}),
        }
    )
    commands = Commands([(0, "LoadState=not-found\n"), (0, fake)])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    observation = supervisor.observe(X, "a" * 64)

    assert observation.kind is PhysicalObservationKind.NOT_FOUND


def test_pid1_journal_terminal_evidence_survives_unit_gc() -> None:
    expected = "a" * 64
    commands = Commands(
        [
            (0, "LoadState=not-found\n"),
            (
                0,
                json.dumps(
                    {
                        "_PID": "1",
                        "_SYSTEMD_UNIT": "init.scope",
                        "SYSLOG_IDENTIFIER": "systemd",
                        "UNIT": UNIT,
                        "MESSAGE": (
                            f"Finished {UNIT} - labpilot:{expected}."
                        ),
                    }
                ),
            ),
        ]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    assert supervisor.observe(X, expected).kind is PhysicalObservationKind.TERMINAL_MATCH
    assert commands.argv[0][4] == UNIT
    assert commands.argv[1][1] == f"--unit={UNIT}"


def test_launch_uses_exact_fixed_systemd_profile() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, ""), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = _spec()

    assert (
        supervisor.launch(
            X,
            frozen_launch_spec_hash(spec),
            spec,
            creation_authority=True,
        ).kind
        is ControlOutcomeKind.CREATED
    )
    argv = commands.argv[-1]
    assert f"--unit={UNIT}" in argv
    for exact in (
        "--service-type=exec",
        "--uid=labpilot-train",
        "--gid=labpilot-train",
        "--property=Restart=no",
        "--property=KillMode=control-group",
        "--property=NoNewPrivileges=yes",
        "--property=AmbientCapabilities=",
        "--property=CapabilityBoundingSet=",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "--property=RuntimeMaxSec=300s",
        "--property=TimeoutStopSec=30s",
    ):
        assert exact in argv


def test_terminal_signal_is_not_invented_as_exit_code() -> None:
    expected = "a" * 64
    loaded = _loaded(active="failed", description=f"labpilot:{expected}").replace(
        "ExecMainCode=1\nExecMainStatus=0",
        "ExecMainCode=2\nExecMainStatus=15",
    )
    supervisor = SystemdSupervisor(
        Commands([(0, loaded)]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    observation = supervisor.observe(X, expected)

    assert observation.exit_code is None
    assert observation.termination_signal == 15


def test_observe_uses_exact_production_x_service() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    supervisor.observe(X, "a" * 64)

    assert commands.argv[0][4] == UNIT
    assert commands.argv[1][1] == f"--unit={UNIT}"


@pytest.mark.parametrize(
    "invalid_identity",
    (
        "ssh",
        "ssh.service",
        "../ssh",
        "/etc/systemd/system/ssh.service",
        "labpilot-v1-x",
        f"{X} ",
        f"{X}\n",
        f"labpilot-v1-{'a' * 63}-{'b' * 32}",
        f"labpilot-v1-{'g' * 64}-{'b' * 32}",
    ),
)
def test_malformed_execution_identity_is_rejected_before_systemd(
    invalid_identity: str,
) -> None:
    commands = Commands(
        [(0, _loaded(active="active", description=f"labpilot:{'a' * 64}"))]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    with pytest.raises(ValueError, match="execution_identity_invalid"):
        supervisor.observe(invalid_identity, "a" * 64)

    assert commands.argv == []
