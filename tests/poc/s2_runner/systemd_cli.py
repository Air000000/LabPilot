import json
import subprocess
from collections.abc import Callable, Sequence
from enum import StrEnum

from tests.poc.s2_runner.contract import (
    ControlOutcome,
    ControlOutcomeKind,
    FrozenLaunchSpec,
    PhysicalObservation,
    PhysicalObservationKind,
    PositiveCreationEvidence,
    systemd_unit_name,
    validate_request_hash,
)

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
_COMMAND_TIMEOUT_SECONDS = 10.0


class RawUnitLookupKind(StrEnum):
    PRESENT = "present"
    CONFIRMED_ABSENT = "confirmed_absent"
    LOOKUP_UNKNOWN = "lookup_unknown"


class SystemdCli:
    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        use_journal_evidence: bool,
        execution_user: str = "labpilot-train",
        execution_group: str = "labpilot-train",
        collect_after_exit: bool = True,
        stop_timeout_seconds: float = 5.0,
    ) -> None:
        self._command_runner = command_runner
        self._use_journal_evidence = use_journal_evidence
        self._execution_user = execution_user
        self._execution_group = execution_group
        self._collect_after_exit = collect_after_exit
        self._stop_timeout_seconds = stop_timeout_seconds

    def launch(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        spec: FrozenLaunchSpec,
        creation_evidence: PositiveCreationEvidence,
    ) -> ControlOutcome:
        validate_request_hash(spec, execution_spec_hash)
        if not isinstance(creation_evidence, PositiveCreationEvidence):
            return self._outcome(
                ControlOutcomeKind.CONTROL_REJECTED,
                execution_identity,
                execution_spec_hash,
                "positive_creation_evidence_required",
            )

        lookup = self._raw_lookup(execution_identity)
        if lookup is RawUnitLookupKind.PRESENT:
            return self._outcome_from_observation(
                self.observe(
                    execution_identity=execution_identity,
                    execution_spec_hash=execution_spec_hash,
                )
            )
        if lookup is RawUnitLookupKind.LOOKUP_UNKNOWN:
            return self._outcome(
                ControlOutcomeKind.CONTROL_UNKNOWN,
                execution_identity,
                execution_spec_hash,
                "creation_preflight_unknown",
            )

        argv = self._launch_argv(execution_identity, execution_spec_hash, spec)
        try:
            result = self._command_runner(argv, _COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return self._outcome_from_observation(
                self.observe(
                    execution_identity=execution_identity,
                    execution_spec_hash=execution_spec_hash,
                )
            )
        if result.returncode == 0:
            return self._outcome(
                ControlOutcomeKind.CREATED,
                execution_identity,
                execution_spec_hash,
                "systemd_run_accepted",
            )
        return self._outcome_from_observation(
            self.observe(
                execution_identity=execution_identity,
                execution_spec_hash=execution_spec_hash,
            )
        )

    def observe(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
    ) -> PhysicalObservation:
        unit_name = systemd_unit_name(execution_identity)
        argv = (
            "systemctl",
            "--system",
            "--no-ask-password",
            "show",
            unit_name,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=Description",
            "--property=Result",
            "--property=MainPID",
            "--property=InvocationID",
            "--property=ExecMainStatus",
            "--property=ActiveEnterTimestamp",
            "--property=InactiveEnterTimestamp",
        )
        try:
            result = self._command_runner(argv, _COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return self._unknown(execution_identity, execution_spec_hash, "systemctl_error")
        properties = _properties(result.stdout) if result.returncode == 0 else {}
        if properties.get("LoadState") == "not-found":
            if self._use_journal_evidence:
                return self._observe_journal(execution_identity, execution_spec_hash)
            return self._unknown(execution_identity, execution_spec_hash, "unit_not_found")
        if result.returncode != 0 or properties.get("LoadState") in (None, ""):
            return self._unknown(execution_identity, execution_spec_hash, "unit_lookup_unknown")

        description = properties.get("Description", "")
        prefix = "labpilot-poc:"
        observed_hash = description.removeprefix(prefix) if description.startswith(prefix) else None
        if observed_hash != execution_spec_hash:
            return PhysicalObservation(
                kind=PhysicalObservationKind.IDENTITY_CONFLICT,
                execution_identity=execution_identity,
                expected_spec_hash=execution_spec_hash,
                observed_spec_hash=observed_hash,
                evidence_source="systemd_unit",
            )

        active_state = properties.get("ActiveState")
        if active_state in {"active", "activating", "reloading"}:
            return PhysicalObservation(
                kind=PhysicalObservationKind.ACTIVE_MATCH,
                execution_identity=execution_identity,
                expected_spec_hash=execution_spec_hash,
                observed_spec_hash=observed_hash,
                pid=_integer_or_none(properties.get("MainPID")),
                invocation_id=properties.get("InvocationID") or None,
                started_at=properties.get("ActiveEnterTimestamp") or None,
                ended_at=properties.get("InactiveEnterTimestamp") or None,
                result=properties.get("Result") or None,
                evidence_source="systemd_unit",
            )
        if active_state in {"inactive", "failed"}:
            return PhysicalObservation(
                kind=PhysicalObservationKind.TERMINAL_MATCH,
                execution_identity=execution_identity,
                expected_spec_hash=execution_spec_hash,
                observed_spec_hash=observed_hash,
                invocation_id=properties.get("InvocationID") or None,
                started_at=properties.get("ActiveEnterTimestamp") or None,
                ended_at=properties.get("InactiveEnterTimestamp") or None,
                exit_code=_integer_or_none(properties.get("ExecMainStatus")),
                result=properties.get("Result") or None,
                evidence_source="systemd_unit",
            )
        return self._unknown(execution_identity, execution_spec_hash, "unit_state_unknown")

    def stop(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        force: bool,
    ) -> ControlOutcome:
        observation = self.observe(
            execution_identity=execution_identity,
            execution_spec_hash=execution_spec_hash,
        )
        if observation.kind is PhysicalObservationKind.TERMINAL_MATCH:
            return self._outcome(
                ControlOutcomeKind.TERMINAL_MATCH,
                execution_identity,
                execution_spec_hash,
                "already_terminal",
            )
        if observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT:
            return self._outcome(
                ControlOutcomeKind.IDENTITY_CONFLICT,
                execution_identity,
                execution_spec_hash,
                "destructive_control_blocked_identity_conflict",
            )
        if observation.kind is PhysicalObservationKind.UNKNOWN:
            return self._outcome(
                ControlOutcomeKind.CONTROL_UNKNOWN,
                execution_identity,
                execution_spec_hash,
                "destructive_control_blocked_unknown",
            )

        unit_name = systemd_unit_name(execution_identity)
        action = ("kill", "--signal=SIGKILL") if force else ("stop",)
        argv = ("systemctl", "--system", "--no-ask-password", *action, unit_name)
        try:
            result = self._command_runner(argv, _COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return self._outcome(
                ControlOutcomeKind.CONTROL_UNKNOWN,
                execution_identity,
                execution_spec_hash,
                "systemctl_control_error",
            )
        return self._outcome(
            ControlOutcomeKind.CONTROL_ACCEPTED
            if result.returncode == 0
            else ControlOutcomeKind.CONTROL_UNKNOWN,
            execution_identity,
            execution_spec_hash,
            "systemctl_control_accepted" if result.returncode == 0 else "systemctl_control_unknown",
        )

    def _raw_lookup(self, execution_identity: str) -> RawUnitLookupKind:
        argv = (
            "systemctl",
            "--system",
            "--no-ask-password",
            "show",
            systemd_unit_name(execution_identity),
            "--property=LoadState",
            "--value",
        )
        try:
            result = self._command_runner(argv, _COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return RawUnitLookupKind.LOOKUP_UNKNOWN
        if result.returncode != 0:
            return RawUnitLookupKind.LOOKUP_UNKNOWN
        load_state = result.stdout.strip().removeprefix("LoadState=")
        if load_state == "not-found":
            return RawUnitLookupKind.CONFIRMED_ABSENT
        return RawUnitLookupKind.PRESENT if load_state else RawUnitLookupKind.LOOKUP_UNKNOWN

    def _launch_argv(
        self,
        execution_identity: str,
        execution_spec_hash: str,
        spec: FrozenLaunchSpec,
    ) -> tuple[str, ...]:
        argv = [
            "systemd-run",
            "--system",
            "--no-ask-password",
            f"--unit={systemd_unit_name(execution_identity)}",
            f"--description=labpilot-poc:{execution_spec_hash}",
            "--service-type=exec",
            "--expand-environment=no",
            "--property=Restart=no",
            "--property=StandardOutput=journal",
            "--property=StandardError=journal",
            "--property=KillMode=control-group",
            "--property=NoNewPrivileges=yes",
            "--property=AmbientCapabilities=",
            "--property=CapabilityBoundingSet=",
            f"--property=TimeoutStopSec={_seconds_property(self._stop_timeout_seconds)}",
            "--property=UMask=0022",
            f"--uid={self._execution_user}",
            f"--gid={self._execution_group}",
            f"--working-directory={spec.working_directory}",
        ]
        argv.extend(f"--setenv={key}={value}" for key, value in sorted(spec.environment))
        if self._collect_after_exit:
            argv.append("--collect")
        argv.extend(
            [
                "--",
                spec.runtime_path,
                *spec.argv,
                "--execution-identity",
                execution_identity,
                "--execution-spec-hash",
                execution_spec_hash,
                "--start-marker",
                spec.start_marker_path,
                "--sleep-seconds",
                str(spec.sleep_seconds),
                "--exit-code",
                str(spec.exit_code),
                "--child-sleep-seconds",
                str(spec.child_sleep_seconds),
            ]
        )
        for key in sorted(spec.print_env_keys):
            argv.extend(("--print-env", key))
        return tuple(argv)

    def _observe_journal(
        self, execution_identity: str, execution_spec_hash: str
    ) -> PhysicalObservation:
        argv = (
            "journalctl",
            f"--unit={systemd_unit_name(execution_identity)}",
            "--output=json",
            "--no-pager",
            "-n",
            "100",
        )
        try:
            result = self._command_runner(argv, _COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return self._unknown(execution_identity, execution_spec_hash, "journal_error")
        if result.returncode != 0:
            return self._unknown(execution_identity, execution_spec_hash, "journal_query_unknown")
        for line in reversed(result.stdout.splitlines()):
            record = _json_object(line)
            message = _json_object(record.get("MESSAGE")) if record is not None else None
            if message is None or message.get("execution_identity") != execution_identity:
                continue
            observed_hash = message.get("execution_spec_hash")
            if not isinstance(observed_hash, str):
                continue
            if observed_hash != execution_spec_hash:
                return PhysicalObservation(
                    kind=PhysicalObservationKind.IDENTITY_CONFLICT,
                    execution_identity=execution_identity,
                    expected_spec_hash=execution_spec_hash,
                    observed_spec_hash=observed_hash,
                    evidence_source="journal",
                )
            if message.get("event") == "probe_terminal":
                return PhysicalObservation(
                    kind=PhysicalObservationKind.TERMINAL_MATCH,
                    execution_identity=execution_identity,
                    expected_spec_hash=execution_spec_hash,
                    observed_spec_hash=observed_hash,
                    ended_at=_string_or_none(message.get("timestamp")),
                    exit_code=_integer_or_none(message.get("exit_code")),
                    evidence_source="journal",
                )
        return self._unknown(
            execution_identity, execution_spec_hash, "journal_evidence_insufficient"
        )

    @staticmethod
    def _unknown(
        execution_identity: str, execution_spec_hash: str, evidence_source: str
    ) -> PhysicalObservation:
        return PhysicalObservation(
            kind=PhysicalObservationKind.UNKNOWN,
            execution_identity=execution_identity,
            expected_spec_hash=execution_spec_hash,
            evidence_source=evidence_source,
        )

    @staticmethod
    def _outcome(
        kind: ControlOutcomeKind,
        execution_identity: str,
        execution_spec_hash: str,
        detail_code: str,
    ) -> ControlOutcome:
        return ControlOutcome(kind, execution_identity, execution_spec_hash, detail_code)

    def _outcome_from_observation(self, observation: PhysicalObservation) -> ControlOutcome:
        kinds = {
            PhysicalObservationKind.ACTIVE_MATCH: ControlOutcomeKind.EXISTING_MATCH,
            PhysicalObservationKind.TERMINAL_MATCH: ControlOutcomeKind.TERMINAL_MATCH,
            PhysicalObservationKind.IDENTITY_CONFLICT: ControlOutcomeKind.IDENTITY_CONFLICT,
            PhysicalObservationKind.UNKNOWN: ControlOutcomeKind.CONTROL_UNKNOWN,
        }
        return self._outcome(
            kinds[observation.kind],
            observation.execution_identity,
            observation.expected_spec_hash,
            f"observed_{observation.kind.value}",
        )


def _properties(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _integer_or_none(value: object) -> int | None:
    if not isinstance(value, int | str):
        return None
    try:
        return int(value) if value != "" else None
    except ValueError:
        return None


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _seconds_property(value: float) -> str:
    return f"{int(value)}s" if value.is_integer() else f"{value}s"
