import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from labpilot.execution.terminal_receipt import (
    TerminalCause,
    TerminalReceiptV1,
    map_terminal_receipt_v1,
    parse_terminal_receipt_v1,
)
from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_hash

CommandRunner = Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]
_EXECUTION_IDENTITY = re.compile(r"^labpilot-v1-[0-9a-f]{64}-[0-9a-f]{32}$")
_BOOT_ID = re.compile(
    r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_INVOCATION_ID = re.compile(r"^[0-9a-f]{32}$")
_SYSTEMD_STARTED = "39f53479d3a045ac8e11786248231fbf"
_SYSTEMD_SUCCESS = "7ad2d189f7e94e70a38c781354912448"
_SYSTEMD_PROCESS_EXIT = "98e322203f7a4ed290d09fe03c09fe15"
_SYSTEMD_FAILURE = "d9b373ed55a64feb8242e02dbe79a49c"
_SYSTEMD_STOPPING = "de5b426a63be47a7b6ac3eaac82e2f6f"
_SYSTEMD_STOPPED = "9d1aaa27d60140bd96365438aad20286"


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
    boot_id: str | None = None
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
        boot_id: str | None = None,
    ) -> None:
        if boot_id is not None and _BOOT_ID.fullmatch(boot_id) is None:
            raise ValueError("boot_id_invalid")
        self._run = command_runner
        self._user = execution_user
        self._group = execution_group
        self._stop_timeout = stop_timeout_seconds
        self._boot_id = boot_id

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
                boot_id=self._boot_id,
            )
        if properties.get("ActiveState") in {"active", "activating", "reloading"}:
            return PhysicalObservation(
                PhysicalObservationKind.ACTIVE_MATCH,
                observed_spec_hash=observed_hash,
                pid=_integer(properties.get("MainPID")),
                invocation_id=properties.get("InvocationID") or None,
                boot_id=self._boot_id,
                result=properties.get("Result") or None,
            )
        if properties.get("ActiveState") in {"inactive", "failed"}:
            cause = self._current_unit_terminal_cause(properties)
            invocation_id = _journal_invocation_id(properties.get("InvocationID"))
            if cause is not None and _cause_is_strong(cause):
                return PhysicalObservation(
                    PhysicalObservationKind.TERMINAL_MATCH,
                    observed_spec_hash=observed_hash,
                    invocation_id=invocation_id,
                    boot_id=self._boot_id,
                    result=cause.result,
                    exit_code=cause.exit_code,
                    termination_signal=cause.termination_signal,
                )
            if invocation_id is None or self._boot_id is None:
                return PhysicalObservation(
                    PhysicalObservationKind.TERMINAL_MATCH,
                    observed_spec_hash=observed_hash,
                    invocation_id=invocation_id,
                    boot_id=self._boot_id,
                    result=cause.result if cause is not None else None,
                )
            receipts = self._observe_receipts(
                _unit_name(execution_identity), invocation_id, self._boot_id
            )
            if receipts is None or len(receipts) > 1:
                return PhysicalObservation(
                    PhysicalObservationKind.UNKNOWN,
                    observed_spec_hash=observed_hash,
                    invocation_id=invocation_id,
                    boot_id=self._boot_id,
                )
            terminal_cause = (
                map_terminal_receipt_v1(receipts[0])
                if receipts
                else cause or TerminalCause(None, None, None)
            )
            return PhysicalObservation(
                PhysicalObservationKind.TERMINAL_MATCH,
                observed_spec_hash=observed_hash,
                invocation_id=invocation_id,
                boot_id=self._boot_id,
                result=terminal_cause.result,
                exit_code=terminal_cause.exit_code,
                termination_signal=terminal_cause.termination_signal,
            )
        return PhysicalObservation(PhysicalObservationKind.UNKNOWN, boot_id=self._boot_id)

    def _current_unit_terminal_cause(
        self,
        properties: Mapping[str, str],
    ) -> TerminalCause | None:
        result = properties.get("Result") or None
        if result == "timeout":
            return TerminalCause("timeout", None, None)
        status = _integer(properties.get("ExecMainStatus"))
        code = properties.get("ExecMainCode")
        if status is not None and code in {"1", "exited"}:
            return TerminalCause(result, status, None)
        if status is not None and code in {"2", "3", "killed", "dumped"}:
            return TerminalCause(result, None, status)
        return TerminalCause(result, None, None) if result is not None else None

    def _retained_manager_terminal_cause(
        self,
        records: Sequence[Mapping[str, object]],
        invocation_id: str,
        boot_id: str,
    ) -> TerminalCause | None:
        result: str | None = None
        exit_code: int | None = None
        termination_signal: int | None = None
        for record in records:
            if (
                _journal_invocation_id(record.get("INVOCATION_ID")) != invocation_id
                or _journal_boot_id(record.get("_BOOT_ID")) != boot_id
            ):
                continue
            message = record.get("MESSAGE")
            message_id = record.get("MESSAGE_ID")
            if message_id == _SYSTEMD_PROCESS_EXIT:
                status = _integer_object(record.get("EXIT_STATUS"))
                if record.get("EXIT_CODE") == "exited" and status is not None:
                    exit_code = status
                    termination_signal = None
                elif record.get("EXIT_CODE") in {"killed", "dumped"} and status is not None:
                    exit_code = None
                    termination_signal = status
            elif message_id == _SYSTEMD_SUCCESS and type(message) is str:
                result = "success"
            elif message_id == _SYSTEMD_FAILURE and type(message) is str:
                for failure_result in ("exit-code", "timeout", "signal"):
                    if message.endswith(f"Failed with result '{failure_result}'."):
                        result = failure_result
                        break
        if result == "timeout":
            return TerminalCause("timeout", None, None)
        if result is not None or exit_code is not None or termination_signal is not None:
            return TerminalCause(result, exit_code, termination_signal)
        return None

    def _observe_receipts(
        self,
        unit: str,
        invocation_id: str,
        boot_id: str,
    ) -> tuple[TerminalReceiptV1, ...] | None:
        try:
            result = self._run(
                (
                    "journalctl",
                    f"_SYSTEMD_UNIT={unit}",
                    "_UID=0",
                    "_GID=0",
                    f"_SYSTEMD_INVOCATION_ID={invocation_id}",
                    f"_BOOT_ID={boot_id}",
                    "--output=json",
                    "--no-pager",
                    "-n",
                    "10",
                ),
                10.0,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError, IndexError):
            return None
        if result.returncode != 0:
            return None
        receipts: list[TerminalReceiptV1] = []
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return None
            if type(record) is not dict:
                return None
            if (
                record.get("_SYSTEMD_UNIT") != unit
                or record.get("_UID") != "0"
                or record.get("_GID") != "0"
                or record.get("_SYSTEMD_INVOCATION_ID") != invocation_id
                or record.get("_BOOT_ID") != boot_id
            ):
                continue
            try:
                receipts.append(parse_terminal_receipt_v1(record.get("MESSAGE")))
            except ValueError:
                return None
        return tuple(receipts)

    def _observe_journal(
        self, execution_identity: str, execution_spec_hash: str
    ) -> PhysicalObservation:
        unit = _unit_name(execution_identity)
        try:
            result = self._run(
                (
                    "journalctl",
                    f"UNIT={unit}",
                    "_PID=1",
                    "_SYSTEMD_UNIT=init.scope",
                    "SYSLOG_IDENTIFIER=systemd",
                    "--output=json",
                    "--no-pager",
                    "-n",
                    "100",
                ),
                10.0,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError, IndexError):
            return PhysicalObservation(PhysicalObservationKind.UNKNOWN)
        if result.returncode != 0:
            return PhysicalObservation(PhysicalObservationKind.UNKNOWN)
        retained_hash: str | None = None
        retained_invocation: str | None = None
        retained_boot: str | None = None
        lifecycle: tuple[str, str] | None = None
        lifecycle_records: list[Mapping[str, object]] = []
        for line in result.stdout.splitlines():
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
            boot_id = _journal_boot_id(record.get("_BOOT_ID"))
            invocation_id = _journal_invocation_id(record.get("INVOCATION_ID"))
            observed_hash = _journal_message_hash(message)
            if observed_hash is not None and observed_hash != execution_spec_hash:
                return PhysicalObservation(
                    PhysicalObservationKind.IDENTITY_CONFLICT,
                    observed_spec_hash=observed_hash,
                    boot_id=boot_id,
                )
            message_id = record.get("MESSAGE_ID")
            if (
                observed_hash == execution_spec_hash
                and message_id == _SYSTEMD_STARTED
                and record.get("JOB_TYPE") == "start"
                and record.get("JOB_RESULT") == "done"
                and message == f"Started {unit} - labpilot:{execution_spec_hash}."
            ):
                retained_hash = observed_hash
                retained_invocation = invocation_id
                retained_boot = boot_id
                lifecycle = (
                    (invocation_id, boot_id)
                    if invocation_id is not None and boot_id is not None
                    else None
                )
                lifecycle_records = []
                continue
            if (
                observed_hash == execution_spec_hash
                and message_id is None
                and message.startswith(("Stopped ", "Finished ", "Failed "))
            ):
                return PhysicalObservation(
                    PhysicalObservationKind.TERMINAL_MATCH,
                    observed_spec_hash=observed_hash,
                    boot_id=boot_id,
                    result="systemd_manager_terminal",
                )
            if lifecycle != (invocation_id, boot_id):
                continue
            lifecycle_records.append(record)
            if (
                message_id == _SYSTEMD_SUCCESS
                and message == f"{unit}: Deactivated successfully."
            ):
                return self._terminal_from_manager_evidence(
                    unit=unit,
                    observed_hash=retained_hash,
                    invocation_id=retained_invocation,
                    boot_id=retained_boot,
                    records=lifecycle_records,
                    manager_result="success",
                )
            if message_id == _SYSTEMD_FAILURE:
                for failure_result in ("exit-code", "timeout", "signal"):
                    if message == f"{unit}: Failed with result '{failure_result}'.":
                        return self._terminal_from_manager_evidence(
                            unit=unit,
                            observed_hash=retained_hash,
                            invocation_id=retained_invocation,
                            boot_id=retained_boot,
                            records=lifecycle_records,
                            manager_result=failure_result,
                        )
        if retained_hash is not None:
            return PhysicalObservation(
                PhysicalObservationKind.UNKNOWN,
                observed_spec_hash=retained_hash,
                invocation_id=retained_invocation,
                boot_id=retained_boot,
                result="systemd_manager_start_retained",
            )
        return PhysicalObservation(PhysicalObservationKind.NOT_FOUND)

    def _terminal_from_manager_evidence(
        self,
        *,
        unit: str,
        observed_hash: str | None,
        invocation_id: str | None,
        boot_id: str | None,
        records: Sequence[Mapping[str, object]],
        manager_result: str,
    ) -> PhysicalObservation:
        if invocation_id is None or boot_id is None:
            return PhysicalObservation(
                PhysicalObservationKind.TERMINAL_MATCH,
                observed_spec_hash=observed_hash,
                invocation_id=invocation_id,
                boot_id=boot_id,
                result=manager_result,
            )
        manager_cause = self._retained_manager_terminal_cause(
            records, invocation_id, boot_id
        ) or TerminalCause(manager_result, None, None)
        if _cause_is_strong(manager_cause):
            cause = manager_cause
        else:
            receipts = self._observe_receipts(unit, invocation_id, boot_id)
            if receipts is None or len(receipts) > 1:
                return PhysicalObservation(
                    PhysicalObservationKind.UNKNOWN,
                    observed_spec_hash=observed_hash,
                    invocation_id=invocation_id,
                    boot_id=boot_id,
                )
            cause = map_terminal_receipt_v1(receipts[0]) if receipts else manager_cause
        return PhysicalObservation(
            PhysicalObservationKind.TERMINAL_MATCH,
            observed_spec_hash=observed_hash,
            invocation_id=invocation_id,
            boot_id=boot_id,
            result=cause.result,
            exit_code=cause.exit_code,
            termination_signal=cause.termination_signal,
        )

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
    if _EXECUTION_IDENTITY.fullmatch(execution_identity) is None:
        raise ValueError("execution_identity_invalid")
    return f"{execution_identity}.service"


def _journal_boot_id(value: object) -> str | None:
    return value if type(value) is str and _BOOT_ID.fullmatch(value) is not None else None


def _journal_invocation_id(value: object) -> str | None:
    return value if type(value) is str and _INVOCATION_ID.fullmatch(value) is not None else None


def _journal_message_hash(message: str) -> str | None:
    marker = " - labpilot:"
    if marker not in message or not message.endswith("."):
        return None
    value = message.rsplit(marker, 1)[1][:-1]
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _properties(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _cause_is_strong(cause: TerminalCause) -> bool:
    return (
        cause.result == "timeout"
        or cause.exit_code is not None
        or cause.termination_signal is not None
    )


def _integer_object(value: object) -> int | None:
    return _integer(value if type(value) is str else None)


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def subprocess_command_runner(
    argv: tuple[str, ...], timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
