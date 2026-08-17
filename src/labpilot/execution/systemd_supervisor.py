import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_hash

CommandRunner = Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]


class PhysicalObservationKind(StrEnum):
    ACTIVE_MATCH = "active_match"
    TERMINAL_MATCH = "terminal_match"
    IDENTITY_CONFLICT = "identity_conflict"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class ControlOutcomeKind(StrEnum):
    CREATED = "created"
    EXISTING_MATCH = "existing_match"
    TERMINAL_MATCH = "terminal_match"
    IDENTITY_CONFLICT = "identity_conflict"
    DEFINITE_NON_ACCEPTANCE = "definite_non_acceptance"
    CONTROL_ACCEPTED = "control_accepted"
    CONTROL_UNKNOWN = "control_unknown"


@dataclass(frozen=True, slots=True)
class PhysicalObservation:
    kind: PhysicalObservationKind
    observed_spec_hash: str | None = None
    pid: int | None = None
    invocation_id: str | None = None
    result: str | None = None
    exit_code: int | None = None
    termination_signal: int | None = None


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    kind: ControlOutcomeKind
    detail_code: str


class SystemdSupervisor:
    def __init__(
        self,
        command_runner: CommandRunner,
        *,
        execution_user: str,
        execution_group: str,
        stop_timeout_seconds: int,
    ) -> None:
        self._run = command_runner
        self._user = execution_user
        self._group = execution_group
        self._stop_timeout = stop_timeout_seconds

    def observe(self, execution_identity: str, execution_spec_hash: str) -> PhysicalObservation:
        result = self._run(
            (
                "systemctl",
                "--system",
                "--no-ask-password",
                "show",
                _unit_name(execution_identity),
                "--property=LoadState",
                "--property=ActiveState",
                "--property=Description",
                "--property=Result",
                "--property=MainPID",
                "--property=InvocationID",
                "--property=ExecMainCode",
                "--property=ExecMainStatus",
            ),
            10.0,
        )
        if result.returncode != 0:
            return PhysicalObservation(PhysicalObservationKind.UNKNOWN)
        properties = _properties(result.stdout)
        if properties.get("LoadState") == "not-found":
            return self._observe_journal(execution_identity, execution_spec_hash)
        if properties.get("LoadState") != "loaded":
            return PhysicalObservation(PhysicalObservationKind.UNKNOWN)
        description = properties.get("Description", "")
        observed_hash = (
            description.removeprefix("labpilot:") if description.startswith("labpilot:") else None
        )
        if observed_hash != execution_spec_hash:
            return PhysicalObservation(
                PhysicalObservationKind.IDENTITY_CONFLICT,
                observed_spec_hash=observed_hash,
            )
        if properties.get("ActiveState") in {"active", "activating", "reloading"}:
            return PhysicalObservation(
                PhysicalObservationKind.ACTIVE_MATCH,
                observed_spec_hash=observed_hash,
                pid=_integer(properties.get("MainPID")),
                invocation_id=properties.get("InvocationID") or None,
                result=properties.get("Result") or None,
            )
        if properties.get("ActiveState") in {"inactive", "failed"}:
            status = _integer(properties.get("ExecMainStatus"))
            code = properties.get("ExecMainCode")
            return PhysicalObservation(
                PhysicalObservationKind.TERMINAL_MATCH,
                observed_spec_hash=observed_hash,
                invocation_id=properties.get("InvocationID") or None,
                result=properties.get("Result") or None,
                exit_code=status if code in {"1", "exited"} else None,
                termination_signal=(
                    status if code in {"2", "3", "killed", "dumped"} else None
                ),
            )
        return PhysicalObservation(PhysicalObservationKind.UNKNOWN)

    def _observe_journal(
        self, execution_identity: str, execution_spec_hash: str
    ) -> PhysicalObservation:
        unit = _unit_name(execution_identity)
        try:
            result = self._run(
                ("journalctl", f"--unit={unit}", "--output=json", "--no-pager", "-n", "100"),
                10.0,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError, IndexError):
            return PhysicalObservation(PhysicalObservationKind.UNKNOWN)
        if result.returncode != 0:
            return PhysicalObservation(PhysicalObservationKind.UNKNOWN)
        for line in reversed(result.stdout.splitlines()):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                type(record) is not dict
                or record.get("_PID") != "1"
                or record.get("_SYSTEMD_UNIT") != "init.scope"
                or record.get("SYSLOG_IDENTIFIER") != "systemd"
                or record.get("UNIT") != unit
            ):
                continue
            message = record.get("MESSAGE")
            if type(message) is not str:
                continue
            marker = " - labpilot:"
            if marker not in message or not message.endswith("."):
                continue
            observed_hash = message.rsplit(marker, 1)[1][:-1]
            if len(observed_hash) != 64 or any(
                character not in "0123456789abcdef" for character in observed_hash
            ):
                continue
            if observed_hash != execution_spec_hash:
                return PhysicalObservation(
                    PhysicalObservationKind.IDENTITY_CONFLICT,
                    observed_spec_hash=observed_hash,
                )
            if message.startswith(("Stopped ", "Finished ", "Failed ")):
                return PhysicalObservation(
                    PhysicalObservationKind.TERMINAL_MATCH,
                    observed_spec_hash=observed_hash,
                    result="systemd_manager_terminal",
                )
            return PhysicalObservation(
                PhysicalObservationKind.UNKNOWN,
                observed_spec_hash=observed_hash,
                result="systemd_manager_start_retained",
            )
        return PhysicalObservation(PhysicalObservationKind.NOT_FOUND)

    def launch(
        self,
        execution_identity: str,
        execution_spec_hash: str,
        spec: FrozenLaunchSpec,
        *,
        creation_authority: bool,
    ) -> ControlOutcome:
        if frozen_launch_spec_hash(spec) != execution_spec_hash:
            return ControlOutcome(ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE, "hash_mismatch")
        observation = self.observe(execution_identity, execution_spec_hash)
        if observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT:
            return ControlOutcome(ControlOutcomeKind.IDENTITY_CONFLICT, "identity_conflict")
        if observation.kind is PhysicalObservationKind.ACTIVE_MATCH:
            return ControlOutcome(ControlOutcomeKind.EXISTING_MATCH, "existing_match")
        if observation.kind is PhysicalObservationKind.TERMINAL_MATCH:
            return ControlOutcome(ControlOutcomeKind.TERMINAL_MATCH, "terminal_match")
        if observation.kind is not PhysicalObservationKind.NOT_FOUND:
            return ControlOutcome(ControlOutcomeKind.CONTROL_UNKNOWN, "preflight_unknown")
        if not creation_authority:
            return ControlOutcome(
                ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE,
                "positive_creation_authority_required",
            )
        result = self._run(self._launch_argv(execution_identity, execution_spec_hash, spec), 10.0)
        return ControlOutcome(
            ControlOutcomeKind.CREATED
            if result.returncode == 0
            else ControlOutcomeKind.CONTROL_UNKNOWN,
            "systemd_run_accepted" if result.returncode == 0 else "systemd_run_unknown",
        )

    def stop(self, execution_identity: str, execution_spec_hash: str) -> ControlOutcome:
        observation = self.observe(execution_identity, execution_spec_hash)
        if observation.kind is not PhysicalObservationKind.ACTIVE_MATCH:
            kinds = {
                PhysicalObservationKind.TERMINAL_MATCH: ControlOutcomeKind.TERMINAL_MATCH,
                PhysicalObservationKind.IDENTITY_CONFLICT: ControlOutcomeKind.IDENTITY_CONFLICT,
            }
            return ControlOutcome(
                kinds.get(observation.kind, ControlOutcomeKind.CONTROL_UNKNOWN), "stop_blocked"
            )
        result = self._run(
            ("systemctl", "--system", "--no-ask-password", "stop", _unit_name(execution_identity)),
            10.0,
        )
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_ACCEPTED
            if result.returncode == 0
            else ControlOutcomeKind.CONTROL_UNKNOWN,
            "stop_accepted" if result.returncode == 0 else "stop_unknown",
        )

    def _launch_argv(self, x: str, h: str, spec: FrozenLaunchSpec) -> tuple[str, ...]:
        source = f"{spec.workspace_path}/source"
        command = [
            "systemd-run",
            "--system",
            "--no-ask-password",
            f"--unit={_unit_name(x)}",
            f"--description=labpilot:{h}",
            "--service-type=exec",
            "--expand-environment=no",
            "--property=Restart=no",
            "--property=KillMode=control-group",
            "--property=NoNewPrivileges=yes",
            "--property=AmbientCapabilities=",
            "--property=CapabilityBoundingSet=",
            "--property=StandardOutput=journal",
            "--property=StandardError=journal",
            f"--property=RuntimeMaxSec={spec.timeout_seconds}s",
            f"--property=TimeoutStopSec={self._stop_timeout}s",
            f"--uid={self._user}",
            f"--gid={self._group}",
            f"--working-directory={source}/{spec.workdir_relative_path}",
        ]
        command.extend(f"--setenv={key}={value}" for key, value in spec.environment)
        command.extend(
            ("--", spec.runtime_path, f"{source}/{spec.train_entrypoint_relative_path}", *spec.argv)
        )
        return tuple(command)


def _unit_name(execution_identity: str) -> str:
    return f"labpilot-{hashlib.sha256(execution_identity.encode()).hexdigest()}.service"


def _properties(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def subprocess_command_runner(
    argv: tuple[str, ...], timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
