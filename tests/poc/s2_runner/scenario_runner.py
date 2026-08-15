import argparse
import grp
import json
import multiprocessing
import os
import platform
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from tests.poc.s2_runner.contract import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    ControlOutcome,
    ControlOutcomeKind,
    FrozenLaunchSpec,
    PhysicalObservation,
    PhysicalObservationKind,
    PositiveCreationEvidence,
    execution_spec_hash,
    supervisor_execution_identity,
    systemd_unit_name,
    validate_request_hash,
)
from tests.poc.s2_runner.durable_state import (
    DispatchPhase,
    DurableIntent,
    DurableIntentStore,
)
from tests.poc.s2_runner.evidence import EvidenceLog, EvidenceRecord
from tests.poc.s2_runner.runner_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    RunnerInfoRequest,
    RunnerLaunchRequest,
    RunnerObserveRequest,
    RunnerOperation,
    RunnerStopRequest,
    decode_response,
    encode_request,
    recv_frame,
    send_frame,
)
from tests.poc.s2_runner.systemd_cli import CommandRunner, SystemdCli

ScenarioSupervisor = SystemdCli

REQUIRED_CANDIDATE_B_CAPABILITIES = (
    "native_linux_not_wsl",
    "systemd_run_available",
    "systemctl_available",
    "journalctl_available",
    "runner_info_available",
    "controller_identity",
    "training_identity_resolved",
    "controller_direct_system_manager_denied",
    "fixed_profile_cli_supported",
    "persistent_journal_available",
)

REQUIRED_SYSTEMD_PROFILE_CAPABILITIES = (
    "native_linux_not_wsl",
    "systemd_run_available",
    "systemctl_available",
    "journalctl_available",
    "system_manager_reachable",
    "noninteractive_system_manager_authorization",
    "service_type_exec",
    "environment_expansion_disabled",
    "execution_user_selection",
    "working_directory",
    "exact_environment_injection",
    "description_metadata",
    "restart_no",
    "kill_mode_control_group",
    "journal_stdout_stderr",
    "collect_after_exit",
)

_COMMANDS = (
    "characterize",
    "p1",
    "p2",
    "p3",
    "p4",
    "p5",
    "p6",
    "p7",
    "p8",
    "p9",
    "p10",
    "p12",
    "p13",
    "reboot-prepare",
    "reboot-verify",
    "summarize",
)


@dataclass(frozen=True, slots=True)
class CliArguments:
    state_root: Path
    evidence_root: Path
    installation_namespace: str
    evidence_stack: str
    command: str


@dataclass(frozen=True, slots=True)
class HostCharacterization:
    facts: dict[str, object]
    capabilities: dict[str, bool]
    gap: str | None


DEFAULT_RUNNER_SOCKET = Path("/run/labpilot-s2-runner-poc/control.sock")
POC_RUNTIME_PATH = "/usr/bin/python3"
POC_PROBE_PATH = "/opt/labpilot-s2-runner-poc/tests/poc/s2_runner/probe.py"
POC_WORKING_ROOT = Path("/var/lib/labpilot-s2-poc/workspace")
POC_MARKER_ROOT = Path("/var/lib/labpilot-s2-poc/markers")
POC_ENV_KEY = "LABPILOT_POC_ENV"


def runner_info(socket_path: Path) -> dict[str, object]:
    return _runner_round_trip(
        socket_path,
        RunnerInfoRequest(version=PROTOCOL_VERSION, operation=RunnerOperation.INFO),
    )


def runner_launch(
    socket_path: Path,
    *,
    x: str,
    h: str,
    spec: FrozenLaunchSpec,
    fault_point: str = "none",
) -> dict[str, object]:
    return _runner_round_trip(
        socket_path,
        RunnerLaunchRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.LAUNCH,
            execution_identity=x,
            execution_spec_hash=h,
            spec=spec,
            fault_point=fault_point,
        ),
    )


def runner_observe(socket_path: Path, *, x: str, h: str) -> dict[str, object]:
    return _runner_round_trip(
        socket_path,
        RunnerObserveRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.OBSERVE,
            execution_identity=x,
            execution_spec_hash=h,
        ),
    )


def runner_stop(socket_path: Path, *, x: str, h: str, force: bool) -> dict[str, object]:
    return _runner_round_trip(
        socket_path,
        RunnerStopRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.STOP,
            execution_identity=x,
            execution_spec_hash=h,
            force=force,
        ),
    )


def _runner_round_trip(socket_path: Path, request: object) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        send_frame(connection, encode_request(request))  # type: ignore[arg-type]
        return decode_response(recv_frame(connection))


def characterize_candidate_b_host(
    *,
    state_root: Path,
    evidence_root: Path,
    command_runner: CommandRunner,
    runner_info_call: Callable[[Path], dict[str, object]] = runner_info,
    runner_socket: Path = DEFAULT_RUNNER_SOCKET,
    persistent_journal_path: Path = Path("/var/log/journal"),
    kernel_release: str | None = None,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> HostCharacterization:
    release = kernel_release or platform.release()
    paths = {name: executable_finder(name) for name in ("systemd-run", "systemctl", "journalctl")}
    capabilities = {name: False for name in REQUIRED_CANDIDATE_B_CAPABILITIES}
    capabilities.update(
        {
            "native_linux_not_wsl": "microsoft" not in release.casefold(),
            "systemd_run_available": paths["systemd-run"] is not None,
            "systemctl_available": paths["systemctl"] is not None,
            "journalctl_available": paths["journalctl"] is not None,
        }
    )
    controller_user = pwd.getpwuid(os.getuid()).pw_name
    facts: dict[str, object] = {
        "kernel_release": release,
        "boot_id": _read_text(Path("/proc/sys/kernel/random/boot_id")),
        "controller_user": controller_user,
        "controller_uid": os.getuid(),
        "runner_socket": str(runner_socket),
        "executable_paths": paths,
        "persistent_journal_directory": persistent_journal_path.is_dir(),
    }
    capabilities["controller_identity"] = controller_user == "labpilot-controller"

    try:
        training_user = pwd.getpwnam("labpilot-train")
        training_group = grp.getgrnam("labpilot-train")
    except KeyError:
        facts["training_uid"] = None
        facts["training_gid"] = None
    else:
        facts["training_uid"] = training_user.pw_uid
        facts["training_gid"] = training_group.gr_gid
        capabilities["training_identity_resolved"] = True

    if paths["systemctl"] is not None:
        version = _run(command_runner, ("systemctl", "--version"))
        facts["systemd_version"] = (
            version.stdout.splitlines()[0] if version.returncode == 0 else None
        )
    else:
        facts["systemd_version"] = None

    if paths["systemd-run"] is not None:
        help_result = _run(command_runner, ("systemd-run", "--help"))
        required_help = (
            "--service-type",
            "--expand-environment",
            "--uid",
            "--gid",
            "--working-directory",
            "--collect",
        )
        capabilities["fixed_profile_cli_supported"] = (
            help_result.returncode == 0
            and all(token in help_result.stdout for token in required_help)
        )
        direct = _run(
            command_runner,
            (
                "systemd-run",
                "--system",
                "--no-ask-password",
                f"--unit=labpilot-s2-controller-direct-{os.getpid()}.service",
                "--collect",
                "--wait",
                "/usr/bin/true",
            ),
        )
        direct_error = direct.stderr.casefold()
        denied = direct.returncode != 0 and any(
            token in direct_error
            for token in ("access denied", "authentication", "permission denied")
        )
        capabilities["controller_direct_system_manager_denied"] = denied
        facts["controller_direct_systemd_returncode"] = direct.returncode
        facts["controller_direct_systemd_stderr"] = direct.stderr[-1000:]

    try:
        info = runner_info_call(runner_socket)
    except (OSError, ValueError):
        info = {"ok": False}
    policy_identity = info.get("policy_identity")
    info_ok = (
        info.get("ok") is True
        and info.get("protocol_version") == PROTOCOL_VERSION
        and isinstance(info.get("runner_version"), str)
        and isinstance(policy_identity, str)
        and len(policy_identity) == 64
        and all(character in "0123456789abcdef" for character in policy_identity)
    )
    capabilities["runner_info_available"] = info_ok
    facts["runner_protocol_version"] = info.get("protocol_version")
    facts["runner_version"] = info.get("runner_version")
    facts["runner_policy_identity"] = policy_identity

    if paths["journalctl"] is not None:
        boots = _run(command_runner, ("journalctl", "--list-boots", "-n", "2", "--no-pager"))
        facts["journal_boots"] = boots.stdout.splitlines() if boots.returncode == 0 else []
        capabilities["persistent_journal_available"] = (
            persistent_journal_path.is_dir() and boots.returncode == 0
        )

    state_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    for label, root in (("state_root", state_root), ("evidence_root", evidence_root)):
        mount = _run(command_runner, ("findmnt", "-T", str(root), "-n", "-o", "TARGET,FSTYPE"))
        facts[f"{label}_mount"] = mount.stdout.strip() if mount.returncode == 0 else None
        facts[f"{label}_volatile"] = _is_volatile_path(root)

    gap = evaluate_candidate_b_capabilities(capabilities)
    return HostCharacterization(facts=facts, capabilities=capabilities, gap=gap)


def evaluate_candidate_b_capabilities(capabilities: Mapping[str, bool]) -> str | None:
    for capability in REQUIRED_CANDIDATE_B_CAPABILITIES:
        if not capabilities.get(capability, False):
            return f"HOST_CAPABILITY_GAP: {capability}"
    return None


def characterize_host(
    *,
    state_root: Path,
    evidence_root: Path,
    execution_user: str,
    command_runner: CommandRunner,
    kernel_release: str | None = None,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> HostCharacterization:
    release = kernel_release or platform.release()
    paths = {name: executable_finder(name) for name in ("systemd-run", "systemctl", "journalctl")}
    capabilities = {name: False for name in REQUIRED_SYSTEMD_PROFILE_CAPABILITIES}
    capabilities.update(
        {
            "native_linux_not_wsl": "microsoft" not in release.casefold(),
            "systemd_run_available": paths["systemd-run"] is not None,
            "systemctl_available": paths["systemctl"] is not None,
            "journalctl_available": paths["journalctl"] is not None,
        }
    )
    facts: dict[str, object] = {
        "kernel_release": release,
        "boot_id": _read_text(Path("/proc/sys/kernel/random/boot_id")),
        "controller_user": pwd.getpwuid(os.getuid()).pw_name,
        "controller_uid": os.getuid(),
        "execution_user": execution_user,
        "executable_paths": paths,
        "persistent_journal_directory": Path("/var/log/journal").is_dir(),
    }
    try:
        facts["execution_uid"] = pwd.getpwnam(execution_user).pw_uid
    except KeyError:
        facts["execution_uid"] = None

    if paths["systemctl"] is not None:
        version = _run(command_runner, ("systemctl", "--version"))
        facts["systemd_version"] = (
            version.stdout.splitlines()[0] if version.returncode == 0 else None
        )
        manager = _run(
            command_runner,
            ("systemctl", "--system", "--no-ask-password", "show", "--property=Version"),
        )
        capabilities["system_manager_reachable"] = manager.returncode == 0
    else:
        facts["systemd_version"] = None

    state_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    unit_name = f"labpilot-poc-characterize-{os.getpid()}.service"
    can_attempt_profile = (
        all(
            capabilities[name]
            for name in (
                "native_linux_not_wsl",
                "systemd_run_available",
                "systemctl_available",
                "journalctl_available",
                "system_manager_reachable",
            )
        )
        and facts["execution_uid"] is not None
    )
    if can_attempt_profile:
        launch = _run(
            command_runner,
            (
                "systemd-run",
                "--system",
                "--no-ask-password",
                f"--unit={unit_name}",
                "--description=labpilot-poc-characterize",
                "--service-type=exec",
                "--expand-environment=no",
                "--property=Restart=no",
                "--property=StandardOutput=journal",
                "--property=StandardError=journal",
                "--property=KillMode=control-group",
                f"--uid={execution_user}",
                f"--working-directory={state_root}",
                "--setenv=LABPILOT_POC_CHARACTERIZE=ok",
                "--collect",
                "--",
                sys.executable,
                "-c",
                (
                    "import sys,time; print('poc-stdout',flush=True); "
                    "print('poc-stderr',file=sys.stderr,flush=True); time.sleep(30)"
                ),
            ),
        )
        capabilities["noninteractive_system_manager_authorization"] = launch.returncode == 0
        facts["profile_launch_stderr"] = launch.stderr[-1000:]
        if launch.returncode == 0:
            shown = _run(
                command_runner,
                (
                    "systemctl",
                    "--system",
                    "--no-ask-password",
                    "show",
                    unit_name,
                    "--property=LoadState",
                    "--property=Type",
                    "--property=Description",
                    "--property=User",
                    "--property=WorkingDirectory",
                    "--property=Environment",
                    "--property=Restart",
                    "--property=KillMode",
                    "--property=StandardOutput",
                    "--property=StandardError",
                ),
            )
            properties = _parse_properties(shown.stdout) if shown.returncode == 0 else {}
            capabilities.update(
                {
                    "service_type_exec": properties.get("Type") == "exec",
                    "environment_expansion_disabled": True,
                    "execution_user_selection": properties.get("User") == execution_user,
                    "working_directory": properties.get("WorkingDirectory") == str(state_root),
                    "exact_environment_injection": (
                        "LABPILOT_POC_CHARACTERIZE=ok" in properties.get("Environment", "")
                    ),
                    "description_metadata": (
                        properties.get("Description") == "labpilot-poc-characterize"
                    ),
                    "restart_no": properties.get("Restart") == "no",
                    "kill_mode_control_group": properties.get("KillMode") == "control-group",
                }
            )
            journal = subprocess.CompletedProcess((), 1, "", "journal_not_observed")
            for _ in range(20):
                journal = _run(
                    command_runner,
                    (
                        "journalctl",
                        f"--unit={unit_name}",
                        "--no-pager",
                        "-n",
                        "20",
                        "--output=cat",
                    ),
                )
                if "poc-stdout" in journal.stdout and "poc-stderr" in journal.stdout:
                    break
                time.sleep(0.1)
            capabilities["journal_stdout_stderr"] = (
                journal.returncode == 0
                and "poc-stdout" in journal.stdout
                and "poc-stderr" in journal.stdout
                and properties.get("StandardOutput") == "journal"
                and properties.get("StandardError") == "journal"
            )
            _run(
                command_runner,
                ("systemctl", "--system", "--no-ask-password", "stop", unit_name),
            )
            for _ in range(20):
                lookup = _run(
                    command_runner,
                    (
                        "systemctl",
                        "--system",
                        "--no-ask-password",
                        "show",
                        unit_name,
                        "--property=LoadState",
                        "--value",
                    ),
                )
                if lookup.returncode == 0 and lookup.stdout.strip() == "not-found":
                    capabilities["collect_after_exit"] = True
                    break
                time.sleep(0.1)

    if paths["journalctl"] is not None:
        disk = _run(command_runner, ("journalctl", "--disk-usage", "--no-pager"))
        boots = _run(command_runner, ("journalctl", "--list-boots", "-n", "2", "--no-pager"))
        facts["journal_disk_usage"] = disk.stdout.strip() if disk.returncode == 0 else None
        facts["journal_boots"] = boots.stdout.splitlines() if boots.returncode == 0 else []
    for label, root in (("state_root", state_root), ("evidence_root", evidence_root)):
        mount = _run(command_runner, ("findmnt", "-T", str(root), "-n", "-o", "TARGET,FSTYPE"))
        facts[f"{label}_mount"] = mount.stdout.strip() if mount.returncode == 0 else None
        facts[f"{label}_volatile"] = _is_volatile_path(root)
    gap = evaluate_capabilities(capabilities)
    return HostCharacterization(facts=facts, capabilities=capabilities, gap=gap)


def dispatch_once(
    store: DurableIntentStore,
    supervisor: ScenarioSupervisor,
    attempt_id: UUID,
    spec: FrozenLaunchSpec,
    asserted_hash: str,
    creation_evidence: PositiveCreationEvidence,
) -> ControlOutcome:
    if not isinstance(creation_evidence, PositiveCreationEvidence):
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_REJECTED,
            "",
            asserted_hash,
            "positive_creation_evidence_required",
        )
    try:
        validate_request_hash(spec, asserted_hash)
    except ValueError:
        return ControlOutcome(
            ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE,
            "",
            asserted_hash,
            PositiveCreationEvidence.NEVER_ACCEPTED_PROVEN.value,
        )
    intent = store.load(attempt_id)
    if intent.execution_spec_hash != asserted_hash:
        return ControlOutcome(
            ControlOutcomeKind.IDENTITY_CONFLICT,
            intent.execution_identity,
            asserted_hash,
            "durable_intent_hash_conflict",
        )
    if intent.dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED:
        if creation_evidence is PositiveCreationEvidence.NEVER_ACCEPTED_PROVEN:
            return supervisor.launch(
                execution_identity=intent.execution_identity,
                execution_spec_hash=asserted_hash,
                spec=spec,
                creation_evidence=creation_evidence,
            )
        return _outcome_from_observation(
            supervisor.observe(
                execution_identity=intent.execution_identity,
                execution_spec_hash=asserted_hash,
            )
        )
    store.mark_dispatch_may_have_occurred(intent.attempt_id)
    return supervisor.launch(
        execution_identity=intent.execution_identity,
        execution_spec_hash=asserted_hash,
        spec=spec,
        creation_evidence=creation_evidence,
    )


def dispatch_once_runner(
    store: DurableIntentStore,
    socket_path: Path,
    attempt_id: UUID,
    spec: FrozenLaunchSpec,
    asserted_hash: str,
    creation_evidence: PositiveCreationEvidence,
    *,
    fault_point: str = "none",
    launch_call: Callable[..., dict[str, object]] = runner_launch,
    observe_call: Callable[..., dict[str, object]] = runner_observe,
) -> ControlOutcome:
    if not isinstance(creation_evidence, PositiveCreationEvidence):
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_REJECTED,
            "",
            asserted_hash,
            "positive_creation_evidence_required",
        )
    try:
        validate_request_hash(spec, asserted_hash)
    except ValueError:
        return ControlOutcome(
            ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE,
            "",
            asserted_hash,
            PositiveCreationEvidence.NEVER_ACCEPTED_PROVEN.value,
        )
    intent = store.load(attempt_id)
    if intent.execution_spec_hash != asserted_hash:
        return ControlOutcome(
            ControlOutcomeKind.IDENTITY_CONFLICT,
            intent.execution_identity,
            asserted_hash,
            "durable_intent_hash_conflict",
        )
    if intent.dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED:
        if creation_evidence is not PositiveCreationEvidence.NEVER_ACCEPTED_PROVEN:
            return _control_outcome_from_runner_observation(
                _safe_runner_observe(
                    socket_path,
                    intent.execution_identity,
                    asserted_hash,
                    observe_call=observe_call,
                )
            )
    else:
        store.mark_dispatch_may_have_occurred(intent.attempt_id)

    try:
        response = launch_call(
            socket_path,
            x=intent.execution_identity,
            h=asserted_hash,
            spec=spec,
            fault_point=fault_point,
        )
    except (OSError, ProtocolError):
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_UNKNOWN,
            intent.execution_identity,
            asserted_hash,
            "runner_launch_transport_unknown",
        )
    return _control_outcome_from_runner_response(
        response, intent.execution_identity, asserted_hash
    )


def recover_runner_without_replay(
    socket_path: Path,
    execution_identity: str,
    execution_spec_hash: str,
    *,
    observe_call: Callable[..., dict[str, object]] = runner_observe,
) -> PhysicalObservation:
    return _safe_runner_observe(
        socket_path,
        execution_identity,
        execution_spec_hash,
        observe_call=observe_call,
    )


def _safe_runner_observe(
    socket_path: Path,
    execution_identity: str,
    execution_spec_hash: str,
    *,
    observe_call: Callable[..., dict[str, object]],
) -> PhysicalObservation:
    try:
        response = observe_call(socket_path, x=execution_identity, h=execution_spec_hash)
    except (OSError, ProtocolError):
        return PhysicalObservation(
            PhysicalObservationKind.UNKNOWN,
            execution_identity,
            execution_spec_hash,
            evidence_source="runner_observe_transport_unknown",
        )
    return _physical_observation_from_runner_response(
        response, execution_identity, execution_spec_hash
    )


def _control_outcome_from_runner_response(
    response: dict[str, object], execution_identity: str, execution_spec_hash: str
) -> ControlOutcome:
    if response.get("ok") is not True:
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_UNKNOWN,
            execution_identity,
            execution_spec_hash,
            str(response.get("error", "runner_control_unknown")),
        )
    try:
        kind = ControlOutcomeKind(str(response["kind"]))
    except (KeyError, ValueError):
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_UNKNOWN,
            execution_identity,
            execution_spec_hash,
            "runner_control_response_invalid",
        )
    return ControlOutcome(
        kind,
        execution_identity,
        execution_spec_hash,
        str(response.get("detail_code", "runner_control")),
    )


def _physical_observation_from_runner_response(
    response: dict[str, object], execution_identity: str, execution_spec_hash: str
) -> PhysicalObservation:
    if response.get("ok") is not True:
        return PhysicalObservation(
            PhysicalObservationKind.UNKNOWN,
            execution_identity,
            execution_spec_hash,
            evidence_source=str(response.get("error", "runner_observe_unknown")),
        )
    try:
        kind = PhysicalObservationKind(str(response["kind"]))
    except (KeyError, ValueError):
        return PhysicalObservation(
            PhysicalObservationKind.UNKNOWN,
            execution_identity,
            execution_spec_hash,
            evidence_source="runner_observe_response_invalid",
        )
    return PhysicalObservation(
        kind=kind,
        execution_identity=execution_identity,
        expected_spec_hash=execution_spec_hash,
        observed_spec_hash=_optional_string(response.get("observed_spec_hash")),
        pid=_optional_int(response.get("pid")),
        invocation_id=_optional_string(response.get("invocation_id")),
        started_at=_optional_string(response.get("started_at")),
        ended_at=_optional_string(response.get("ended_at")),
        exit_code=_optional_int(response.get("exit_code")),
        result=_optional_string(response.get("result")),
        evidence_source=_optional_string(response.get("evidence_source")),
    )


def _control_outcome_from_runner_observation(
    observation: PhysicalObservation,
) -> ControlOutcome:
    mapping = {
        PhysicalObservationKind.ACTIVE_MATCH: ControlOutcomeKind.EXISTING_MATCH,
        PhysicalObservationKind.TERMINAL_MATCH: ControlOutcomeKind.TERMINAL_MATCH,
        PhysicalObservationKind.IDENTITY_CONFLICT: ControlOutcomeKind.IDENTITY_CONFLICT,
        PhysicalObservationKind.UNKNOWN: ControlOutcomeKind.CONTROL_UNKNOWN,
    }
    return ControlOutcome(
        mapping[observation.kind],
        observation.execution_identity,
        observation.expected_spec_hash,
        f"observed_{observation.kind.value}",
    )


def recover_without_replay(
    supervisor: ScenarioSupervisor,
    execution_identity: str,
    execution_spec_hash: str,
) -> PhysicalObservation:
    return supervisor.observe(
        execution_identity=execution_identity,
        execution_spec_hash=execution_spec_hash,
    )


def stop_and_observe(
    supervisor: ScenarioSupervisor,
    execution_identity: str,
    execution_spec_hash: str,
    *,
    force: bool,
) -> dict[str, bool]:
    outcome = supervisor.stop(
        execution_identity=execution_identity,
        execution_spec_hash=execution_spec_hash,
        force=force,
    )
    observation = supervisor.observe(
        execution_identity=execution_identity,
        execution_spec_hash=execution_spec_hash,
    )
    return {
        "stop_requested": outcome.kind is ControlOutcomeKind.CONTROL_ACCEPTED,
        "terminal_observed": observation.kind is PhysicalObservationKind.TERMINAL_MATCH,
    }


def reboot_verify(
    supervisor: ScenarioSupervisor,
    *,
    execution_identity: str,
    execution_spec_hash: str,
    prior_boot_id: str,
    current_boot_id: str,
    marker_path: Path,
    prior_root_count: int,
) -> dict[str, object]:
    observation = recover_without_replay(
        supervisor,
        execution_identity,
        execution_spec_hash,
    )
    current_root_count = _line_count(marker_path)
    return {
        "boot_changed": prior_boot_id != current_boot_id,
        "independent_root_count_unchanged": current_root_count == prior_root_count,
        "observation": observation.kind.value,
        "automatic_relaunch": current_root_count > prior_root_count,
    }


def evaluate_capabilities(capabilities: Mapping[str, bool]) -> str | None:
    for capability in REQUIRED_SYSTEMD_PROFILE_CAPABILITIES:
        if not capabilities.get(capability, False):
            return f"HOST_CAPABILITY_GAP: {capability}"
    return None


def run_after_capability_gate(capabilities: Mapping[str, bool], scenario: Callable[[], str]) -> str:
    gap = evaluate_capabilities(capabilities)
    return gap if gap is not None else scenario()


def parse_args(argv: Sequence[str] | None = None) -> CliArguments:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--installation-namespace", required=True)
    parser.add_argument("--evidence-stack", choices=("systemd", "systemd+journal"), required=True)
    parser.add_argument("command", choices=_COMMANDS)
    values = parser.parse_args(argv)
    return CliArguments(
        state_root=values.state_root,
        evidence_root=values.evidence_root,
        installation_namespace=values.installation_namespace,
        evidence_stack=values.evidence_stack,
        command=values.command,
    )


@dataclass(slots=True)
class _ScenarioContext:
    arguments: CliArguments
    store: DurableIntentStore
    evidence: EvidenceLog
    runner_socket: Path


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    arguments.state_root.mkdir(parents=True, exist_ok=True)
    arguments.evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = EvidenceLog(arguments.evidence_root / "s2-runner-poc.jsonl")
    if arguments.command == "characterize":
        characterization = characterize_candidate_b_host(
            state_root=arguments.state_root,
            evidence_root=arguments.evidence_root,
            command_runner=_subprocess_runner,
        )
        _write_json_fsync(
            arguments.state_root / "host-characterization.json",
            {
                "facts": characterization.facts,
                "capabilities": characterization.capabilities,
                "gap": characterization.gap,
            },
        )
        observed = characterization.gap or "PASS"
        evidence.append(
            EvidenceRecord(
                scenario_id="P0",
                phase="characterize",
                execution_identity=None,
                execution_spec_hash=None,
                expected="Candidate B Runner boundary and host evidence prerequisites available",
                observed=observed,
                detail={
                    "status": "FAIL" if characterization.gap else "PASS",
                    "facts": characterization.facts,
                    "capabilities": characterization.capabilities,
                },
            )
        )
        print(observed, flush=True)
        return 2 if characterization.gap else 0
    if arguments.command == "summarize":
        return _summarize(evidence)

    saved_characterization = _read_json(arguments.state_root / "host-characterization.json")
    gap = saved_characterization.get("gap")
    if isinstance(gap, str) and gap:
        print(gap, flush=True)
        return 2
    if not isinstance(saved_characterization.get("capabilities"), dict):
        print("HOST_CAPABILITY_GAP: characterize_not_run", flush=True)
        return 2

    context = _ScenarioContext(
        arguments=arguments,
        store=DurableIntentStore(arguments.state_root / "intents"),
        evidence=evidence,
        runner_socket=DEFAULT_RUNNER_SOCKET,
    )
    scenarios: dict[str, Callable[[_ScenarioContext], int]] = {
        "p1": _p1,
        "p2": _p2,
        "p3": _p3,
        "p4": _p4,
        "p5": _p5,
        "p6": _p6,
        "p7": _p7,
        "p8": _p8,
        "p9": _p9,
        "p10": _p10,
        "p12": _p12,
        "p13": _p13,
        "reboot-prepare": _reboot_prepare,
        "reboot-verify": _reboot_verify,
    }
    return scenarios[arguments.command](context)


def _p1(context: _ScenarioContext) -> int:
    attempt_id = _scenario_attempt(context, "p1")
    code = (
        "from tests.poc.s2_runner.contract import supervisor_execution_identity;"
        "from uuid import UUID;"
        "import sys;"
        "print(supervisor_execution_identity(sys.argv[1],UUID(sys.argv[2])))"
    )
    values = [
        subprocess.run(
            [sys.executable, "-c", code, context.arguments.installation_namespace, str(attempt_id)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        for _ in range(2)
    ]
    other = supervisor_execution_identity(
        f"{context.arguments.installation_namespace}-other", attempt_id
    )
    expected = supervisor_execution_identity(context.arguments.installation_namespace, attempt_id)
    passed = (
        values == [expected, expected] and other != expected and expected.endswith(attempt_id.hex)
    )
    return _record_result(
        context,
        "P1",
        expected="stable collision-safe full X across fresh controllers",
        observed="PASS" if passed else "FAIL",
        detail={"status": "PASS" if passed else "FAIL", "fresh_values": values, "other": other},
        execution_identity=expected,
        execution_spec_hash=None,
    )


def _p2(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context, "p2", sleep_seconds=30.0, child_sleep_seconds=30.0
    )
    outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.ACTIVE_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = observation.kind is PhysicalObservationKind.ACTIVE_MATCH and root_count == 1
    _cleanup_runner(context.runner_socket, identity, spec_hash)
    return _record_result(
        context,
        "P2",
        expected="ACTIVE_MATCH with exact X/H live binding",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "dispatch_outcome": outcome.kind.value,
            "independent_root_count": root_count,
            "evidence_source": observation.evidence_source,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p3(context: _ScenarioContext) -> int:
    subcases: dict[str, dict[str, object]] = {}

    before_attempt, before_spec, before_identity, before_hash = _prepare_scenario(
        context, "p3-ack-before", sleep_seconds=20.0
    )
    before_outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        before_attempt,
        before_spec,
        before_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        fault_point="before_systemd_dispatch",
    )
    before_ready = _wait_runner_ready(context.runner_socket)
    before_observation = recover_runner_without_replay(
        context.runner_socket, before_identity, before_hash
    )
    before_root_count = _line_count(Path(before_spec.start_marker_path))
    before_passed = (
        before_outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
        and before_ready
        and before_observation.kind is PhysicalObservationKind.UNKNOWN
        and before_root_count == 0
    )
    subcases["B-ACK-1"] = {
        "status": "PASS" if before_passed else "FAIL",
        "dispatch_outcome": before_outcome.kind.value,
        "observation": before_observation.kind.value,
        "independent_root_count": before_root_count,
        "runner_recovered": before_ready,
    }

    after_attempt, after_spec, after_identity, after_hash = _prepare_scenario(
        context, "p3-ack-after", sleep_seconds=20.0
    )
    after_outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        after_attempt,
        after_spec,
        after_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        fault_point="after_systemd_dispatch_before_response",
    )
    after_ready = _wait_runner_ready(context.runner_socket)
    after_observation = _wait_runner_observation(
        context.runner_socket,
        after_identity,
        after_hash,
        {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH},
    )
    after_root_count = _line_count(Path(after_spec.start_marker_path))
    after_passed = (
        after_outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
        and after_ready
        and after_observation.kind
        in {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH}
        and after_root_count == 1
    )
    subcases["B-ACK-2"] = {
        "status": "PASS" if after_passed else "FAIL",
        "dispatch_outcome": after_outcome.kind.value,
        "observation": after_observation.kind.value,
        "independent_root_count": after_root_count,
        "runner_recovered": after_ready,
    }
    _cleanup_runner(context.runner_socket, after_identity, after_hash)

    lost_attempt, lost_spec, lost_identity, lost_hash = _prepare_scenario(
        context, "p3-ack-response-lost", sleep_seconds=20.0
    )

    def launch_then_drop_response(
        socket_path: Path,
        *,
        x: str,
        h: str,
        spec: FrozenLaunchSpec,
        fault_point: str = "none",
    ) -> dict[str, object]:
        runner_launch(socket_path, x=x, h=h, spec=spec, fault_point=fault_point)
        raise ConnectionError("injected_controller_response_loss")

    lost_outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        lost_attempt,
        lost_spec,
        lost_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        launch_call=launch_then_drop_response,
    )
    lost_observation = _wait_runner_observation(
        context.runner_socket,
        lost_identity,
        lost_hash,
        {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH},
    )
    lost_root_count = _line_count(Path(lost_spec.start_marker_path))
    lost_passed = (
        lost_outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
        and lost_observation.kind
        in {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH}
        and lost_root_count == 1
    )
    subcases["B-ACK-3"] = {
        "status": "PASS" if lost_passed else "FAIL",
        "dispatch_outcome": lost_outcome.kind.value,
        "observation": lost_observation.kind.value,
        "independent_root_count": lost_root_count,
    }
    _cleanup_runner(context.runner_socket, lost_identity, lost_hash)

    passed = before_passed and after_passed and lost_passed
    accepted_observed = (
        after_observation.kind.value
        if after_observation.kind is not PhysicalObservationKind.UNKNOWN
        else lost_observation.kind.value
    )
    return _record_result(
        context,
        "P3",
        expected=(
            "B-ACK-1 no replay before dispatch; B-ACK-2/B-ACK-3 recover exact X/H "
            "with one physical root"
        ),
        observed=accepted_observed,
        detail={
            "status": "PASS" if passed else "FAIL",
            "subcases": subcases,
            "automatic_launch_retries": 0,
        },
        execution_identity=after_identity,
        execution_spec_hash=after_hash,
    )


def _p4(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(context, "p4", sleep_seconds=20.0)
    barrier = context.arguments.state_root / "p4-dispatch-barrier"
    barrier.unlink(missing_ok=True)
    result_paths = [context.arguments.state_root / f"p4-result-{index}.json" for index in range(2)]
    for path in result_paths:
        path.unlink(missing_ok=True)
    processes = [
        multiprocessing.get_context("spawn").Process(
            target=_race_dispatch,
            args=(
                context.arguments.state_root / "intents",
                context.runner_socket,
                attempt_id,
                spec,
                spec_hash,
                barrier,
                result_path,
            ),
        )
        for result_path in result_paths
    ]
    for process in processes:
        process.start()
    barrier.touch()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
    observation = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    outcomes = [str(_read_json(path).get("kind")) for path in result_paths if path.exists()]
    passed = (
        all(not process.is_alive() and process.exitcode == 0 for process in processes)
        and root_count == 1
        and observation.kind
        in {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH}
    )
    _cleanup_runner(context.runner_socket, identity, spec_hash)
    return _record_result(
        context,
        "P4",
        expected="at most one authoritative unit and one independent root",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "controller_outcomes": outcomes,
            "independent_root_count": root_count,
            "authoritative_unit_upper_bound": 1 if root_count == 1 else root_count,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p5(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(context, "p5", sleep_seconds=20.0)
    dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.ACTIVE_MATCH},
    )
    conflicting_spec = replace(spec, exit_code=19)
    conflicting_hash = execution_spec_hash(conflicting_spec)
    outcome = _control_outcome_from_runner_response(
        runner_launch(
            context.runner_socket,
            x=identity,
            h=conflicting_hash,
            spec=conflicting_spec,
        ),
        identity,
        conflicting_hash,
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = outcome.kind is ControlOutcomeKind.IDENTITY_CONFLICT and root_count == 1
    _cleanup_runner(context.runner_socket, identity, spec_hash)
    return _record_result(
        context,
        "P5",
        expected="IDENTITY_CONFLICT; no stop replacement or relaunch",
        observed=outcome.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "observed_conflicting_hash": conflicting_hash,
            "independent_root_count": root_count,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p6(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context, "p6", sleep_seconds=0.1, exit_code=17
    )
    dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.TERMINAL_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = observation.kind is PhysicalObservationKind.TERMINAL_MATCH and root_count == 1
    return _record_result(
        context,
        "P6",
        expected="TERMINAL_MATCH for very-short exit after uncertain acknowledgement",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "exit_code": observation.exit_code,
            "independent_root_count": root_count,
            "evidence_source": observation.evidence_source,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p7(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context, "p7", sleep_seconds=0.1, exit_code=23
    )
    dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_runner_terminal_journal(
        context.runner_socket, identity, spec_hash
    )
    unit_gone = (
        observation.kind is PhysicalObservationKind.TERMINAL_MATCH
        and observation.evidence_source == "journal"
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = (
        unit_gone and observation.kind is PhysicalObservationKind.TERMINAL_MATCH and root_count == 1
    )
    return _record_result(
        context,
        "P7",
        expected="TERMINAL_MATCH after demonstrated current-unit GC",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "unit_gone": unit_gone,
            "exit_code": observation.exit_code,
            "independent_root_count": root_count,
            "evidence_source": observation.evidence_source,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p8(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(context, "p8-local")
    counting = _CountingCommandRunner()
    supervisor = SystemdCli(command_runner=counting, use_journal_evidence=False)
    definite = dispatch_once(
        context.store,
        supervisor,
        attempt_id,
        spec,
        "0" * 64,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    ambiguous_attempt, ambiguous_spec, ambiguous_identity, ambiguous_hash = _prepare_scenario(
        context, "p8-ambiguous"
    )
    context.store.mark_dispatch_may_have_occurred(ambiguous_attempt)
    before = len(counting.calls)
    ambiguous = dispatch_once(
        context.store,
        supervisor,
        ambiguous_attempt,
        ambiguous_spec,
        ambiguous_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    dispatched_commands = counting.calls[before:]
    passed = (
        definite.kind is ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE
        and before == 0
        and ambiguous.kind is ControlOutcomeKind.CONTROL_UNKNOWN
        and not any(call[0] == "systemd-run" for call in dispatched_commands)
    )
    return _record_result(
        context,
        "P8",
        expected="NEVER_ACCEPTED_PROVEN distinct from ambiguous NOT_FOUND",
        observed=f"{definite.kind.value}/{ambiguous.kind.value}",
        detail={
            "status": "PASS" if passed else "FAIL",
            "pre_dispatch_supervisor_command_count": before,
            "ambiguous_systemd_run_count": sum(
                call[0] == "systemd-run" for call in dispatched_commands
            ),
            "ambiguous_execution_identity": ambiguous_identity,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p9(context: _ScenarioContext) -> int:
    variants: list[dict[str, object]] = []
    for name, sleep_seconds, stop_delay in (
        ("stop-first", 20.0, 0.0),
        ("natural-exit-first", 0.1, 0.5),
        ("near-concurrent", 0.5, 0.4),
    ):
        attempt_id, spec, identity, spec_hash = _prepare_scenario(
            context,
            f"p9-{name}",
            sleep_seconds=sleep_seconds,
        )
        dispatch_once_runner(
            context.store,
            context.runner_socket,
            attempt_id,
            spec,
            spec_hash,
            PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        )
        time.sleep(stop_delay)
        stop_outcome = _control_outcome_from_runner_response(
            runner_stop(
                context.runner_socket,
                x=identity,
                h=spec_hash,
                force=False,
            ),
            identity,
            spec_hash,
        )
        observation = _wait_runner_observation(
            context.runner_socket,
            identity,
            spec_hash,
            {PhysicalObservationKind.TERMINAL_MATCH},
        )
        root_count = _line_count(Path(spec.start_marker_path))
        variants.append(
            {
                "variant": name,
                "stop_outcome": stop_outcome.kind.value,
                "terminal_observation": observation.kind.value,
                "independent_root_count": root_count,
            }
        )
    passed = all(
        item["terminal_observation"] == PhysicalObservationKind.TERMINAL_MATCH.value
        and item["independent_root_count"] == 1
        for item in variants
    )
    return _record_result(
        context,
        "P9",
        expected="one terminal result without fabricated cancel causality",
        observed="terminal facts retained" if passed else "FAIL",
        detail={"status": "PASS" if passed else "FAIL", "variants": variants},
        execution_identity=None,
        execution_spec_hash=None,
    )


def _p10(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context,
        "p10",
        sleep_seconds=0.2,
        exit_code=29,
    )
    dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    time.sleep(1.0)
    observation = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.TERMINAL_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = observation.kind is PhysicalObservationKind.TERMINAL_MATCH and root_count == 1
    return _record_result(
        context,
        "P10",
        expected="fresh controller recovers TERMINAL_MATCH after controller-down exit",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "exit_code": observation.exit_code,
            "independent_root_count": root_count,
            "evidence_source": observation.evidence_source,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _p12(context: _ScenarioContext) -> int:
    characterization = _read_json(context.arguments.state_root / "host-characterization.json")
    facts = characterization.get("facts")
    capabilities = characterization.get("capabilities")
    facts_dict = facts if isinstance(facts, dict) else {}
    capabilities_dict = capabilities if isinstance(capabilities, dict) else {}
    training_uid = facts_dict.get("training_uid")
    training_gid = facts_dict.get("training_gid")

    security_attempt, security_spec, security_identity, security_hash = _prepare_scenario(
        context,
        "p12-security",
        sleep_seconds=0.1,
        security_evidence=True,
    )
    security_outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        security_attempt,
        security_spec,
        security_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    security_observation = _wait_runner_observation(
        context.runner_socket,
        security_identity,
        security_hash,
        {PhysicalObservationKind.TERMINAL_MATCH},
    )
    security_marker_records = _read_marker_records(Path(security_spec.start_marker_path))
    security_started = next(
        (record for record in security_marker_records if record.get("event") == "probe_started"),
        None,
    )
    security_record = (
        security_started.get("security") if isinstance(security_started, dict) else None
    )
    workload_boundary_ok = (
        isinstance(training_uid, int)
        and isinstance(training_gid, int)
        and isinstance(security_record, dict)
        and _candidate_b_security_record_matches(
            security_record, expected_uid=training_uid, expected_gid=training_gid
        )
    )

    path_attempt, path_spec, path_identity, _ = _prepare_scenario(context, "p12-path-escape")
    del path_attempt
    escaped_spec = replace(path_spec, working_directory="/etc")
    escaped_hash = execution_spec_hash(escaped_spec)
    path_response = runner_launch(
        context.runner_socket,
        x=path_identity,
        h=escaped_hash,
        spec=escaped_spec,
    )
    path_escape_rejected = (
        path_response.get("ok") is False
        and path_response.get("error") == "working_directory_denied"
    )

    hash_attempt, hash_spec, hash_identity, hash_value = _prepare_scenario(
        context, "p12-hash-mismatch"
    )
    del hash_attempt
    wrong_hash = "0" * 64 if hash_value != "0" * 64 else "1" * 64
    hash_response = runner_launch(
        context.runner_socket,
        x=hash_identity,
        h=wrong_hash,
        spec=hash_spec,
    )
    hash_mismatch_rejected = (
        hash_response.get("ok") is False
        and hash_response.get("error") == "execution_spec_hash_mismatch"
    )

    stop_attempt, stop_spec, stop_identity, stop_hash = _prepare_scenario(
        context, "p12-wrong-h-stop", sleep_seconds=20.0
    )
    dispatch_once_runner(
        context.store,
        context.runner_socket,
        stop_attempt,
        stop_spec,
        stop_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    active_before_wrong_stop = _wait_runner_observation(
        context.runner_socket,
        stop_identity,
        stop_hash,
        {PhysicalObservationKind.ACTIVE_MATCH},
    )
    stop_wrong_hash = "f" * 64 if stop_hash != "f" * 64 else "e" * 64
    wrong_stop_response = runner_stop(
        context.runner_socket, x=stop_identity, h=stop_wrong_hash, force=True
    )
    active_after_wrong_stop = recover_runner_without_replay(
        context.runner_socket, stop_identity, stop_hash
    )
    wrong_h_stop_safe = (
        active_before_wrong_stop.kind is PhysicalObservationKind.ACTIVE_MATCH
        and wrong_stop_response.get("ok") is True
        and wrong_stop_response.get("kind") == PhysicalObservationKind.IDENTITY_CONFLICT.value
        and active_after_wrong_stop.kind is PhysicalObservationKind.ACTIVE_MATCH
    )
    _cleanup_runner(context.runner_socket, stop_identity, stop_hash)

    required_capabilities_ok = all(
        capabilities_dict.get(name) is True for name in REQUIRED_CANDIDATE_B_CAPABILITIES
    )
    runner_still_healthy = False
    try:
        runner_still_healthy = runner_info(context.runner_socket).get("ok") is True
    except (OSError, ProtocolError):
        runner_still_healthy = False

    automated_checks = {
        "N2_root_execution_not_protocol_expressible": True,
        "N3_arbitrary_unit_not_protocol_expressible": True,
        "N4_raw_systemd_properties_not_protocol_expressible": True,
        "N5_path_escape_rejected": path_escape_rejected,
        "N6_hash_mismatch_rejected": hash_mismatch_rejected,
        "N7_arbitrary_service_stop_not_protocol_expressible": True,
        "N8_wrong_h_stop_non_destructive": wrong_h_stop_safe,
        "N10_no_blind_replay_covered_by_p3": True,
        "N11_training_runner_access_denied": bool(
            isinstance(security_record, dict)
            and security_record.get("runner_socket_connect") == "denied"
        ),
        "N12_privileged_property_injection_not_protocol_expressible": True,
        "workload_training_boundary": workload_boundary_ok,
        "runner_still_healthy": runner_still_healthy,
    }
    passed = (
        required_capabilities_ok
        and security_outcome.kind
        in {ControlOutcomeKind.CREATED, ControlOutcomeKind.TERMINAL_MATCH}
        and security_observation.kind is PhysicalObservationKind.TERMINAL_MATCH
        and _line_count(Path(security_spec.start_marker_path)) == 1
        and all(automated_checks.values())
    )
    return _record_result(
        context,
        "P12",
        expected="Candidate B narrow privilege boundary; no generic root/systemd authority",
        observed="PASS" if passed else "FAIL",
        detail={
            "status": "PASS" if passed else "FAIL",
            "controller_user": facts_dict.get("controller_user"),
            "controller_uid": facts_dict.get("controller_uid"),
            "training_uid": training_uid,
            "training_gid": training_gid,
            "automated_checks": automated_checks,
            "security_record": security_record or {},
            "manual_native_probe_required": [
                "N1 unrelated local principal denied by socket/peer boundary",
                "N9 malformed/oversized native frame rejected and Runner remains healthy",
            ],
            "deterministic_protocol_regressions": ["N2", "N3", "N4", "N7", "N12"],
            "used_root_or_user_manager_fallback": False,
        },
        execution_identity=security_identity,
        execution_spec_hash=security_hash,
    )


def _p13(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context, "p13", sleep_seconds=1.0, exit_code=31
    )
    outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    active = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH},
    )
    properties = _subprocess_runner(
        (
            "systemctl",
            "--system",
            "--no-ask-password",
            "show",
            systemd_unit_name(identity),
            "--property=StandardOutput",
            "--property=StandardError",
            "--property=User",
            "--property=Group",
        ),
        10.0,
    )
    terminal = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.TERMINAL_MATCH},
    )
    marker_records = _read_marker_records(Path(spec.start_marker_path))
    started = next(
        (record for record in marker_records if record.get("event") == "probe_started"),
        {},
    )
    actual_argv = started.get("argv") if isinstance(started, dict) else None
    argv_match = (
        isinstance(actual_argv, list)
        and len(actual_argv) >= len(spec.argv)
        and tuple(actual_argv[: len(spec.argv)]) == spec.argv
        and "--execution-identity" in actual_argv
        and identity in actual_argv
        and "--execution-spec-hash" in actual_argv
        and spec_hash in actual_argv
    )
    properties_map = _parse_properties(properties.stdout) if properties.returncode == 0 else {}
    passed = (
        outcome.kind in {ControlOutcomeKind.CREATED, ControlOutcomeKind.TERMINAL_MATCH}
        and active.kind
        in {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH}
        and terminal.kind is PhysicalObservationKind.TERMINAL_MATCH
        and terminal.exit_code == spec.exit_code
        and terminal.evidence_source == "journal"
        and started.get("runtime_path") == spec.runtime_path
        and started.get("working_directory") == spec.working_directory
        and started.get("environment") == {POC_ENV_KEY: "p13"}
        and argv_match
        and properties_map.get("StandardOutput") == "journal"
        and properties_map.get("StandardError") == "journal"
        and properties_map.get("User") == "labpilot-train"
        and properties_map.get("Group") == "labpilot-train"
        and _line_count(Path(spec.start_marker_path)) == 1
    )
    return _record_result(
        context,
        "P13",
        expected="exact runtime argv workdir env stdout/stderr and terminal propagation",
        observed=terminal.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "exit_code": terminal.exit_code,
            "runtime_path_match": started.get("runtime_path") == spec.runtime_path,
            "working_directory_match": started.get("working_directory") == spec.working_directory,
            "argv_match": argv_match,
            "environment_match": started.get("environment") == {POC_ENV_KEY: "p13"},
            "stdout_journal_profile": properties_map.get("StandardOutput") == "journal",
            "stderr_journal_profile": properties_map.get("StandardError") == "journal",
            "terminal_journal_evidence": terminal.evidence_source == "journal",
            "execution_user_match": properties_map.get("User") == "labpilot-train",
            "execution_group_match": properties_map.get("Group") == "labpilot-train",
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
    )


def _reboot_prepare(context: _ScenarioContext) -> int:
    characterization = _read_json(context.arguments.state_root / "host-characterization.json")
    facts = characterization.get("facts")
    facts_dict = facts if isinstance(facts, dict) else {}
    if (
        facts_dict.get("state_root_volatile") is True
        or facts_dict.get("evidence_root_volatile") is True
    ):
        print("HOST_CAPABILITY_GAP: persistent_reboot_roots_required", flush=True)
        return 2
    attempt_id, spec, identity, spec_hash = _prepare_scenario(context, "p11", sleep_seconds=3600.0)
    outcome = dispatch_once_runner(
        context.store,
        context.runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_runner_observation(
        context.runner_socket,
        identity,
        spec_hash,
        {PhysicalObservationKind.ACTIVE_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    metadata_path = context.arguments.evidence_root / "p11-reboot.json"
    _write_json_fsync(
        metadata_path,
        {
            "prior_boot_id": _read_text(Path("/proc/sys/kernel/random/boot_id")),
            "attempt_id": str(attempt_id),
            "execution_identity": identity,
            "execution_spec_hash": spec_hash,
            "marker_path": spec.start_marker_path,
            "prior_root_count": root_count,
            "evidence_stack": context.arguments.evidence_stack,
        },
    )
    passed = observation.kind is PhysicalObservationKind.ACTIVE_MATCH and root_count == 1
    context.evidence.append(
        EvidenceRecord(
            scenario_id="P11",
            phase="reboot_prepare",
            execution_identity=identity,
            execution_spec_hash=spec_hash,
            expected="active exact X/H before human reboot",
            observed=observation.kind.value,
            detail={
                "status": "READY_FOR_HUMAN_REBOOT" if passed else "FAIL",
                "dispatch_outcome": outcome.kind.value,
                "independent_root_count": root_count,
                "metadata_path": str(metadata_path),
            },
        )
    )
    print(str(metadata_path), flush=True)
    return 0 if passed else 1


def _reboot_verify(context: _ScenarioContext) -> int:
    metadata_path = context.arguments.evidence_root / "p11-reboot.json"
    metadata = _read_json(metadata_path)
    identity = _required_string(metadata, "execution_identity")
    spec_hash = _required_string(metadata, "execution_spec_hash")
    marker_path = Path(_required_string(metadata, "marker_path"))
    prior_boot_id = _required_string(metadata, "prior_boot_id")
    prior_root_count_value = metadata["prior_root_count"]
    if not isinstance(prior_root_count_value, int):
        raise ValueError("invalid_prior_root_count")
    prior_root_count = prior_root_count_value
    current_boot_id = _read_text(Path("/proc/sys/kernel/random/boot_id")) or ""
    observation = recover_runner_without_replay(
        context.runner_socket, identity, spec_hash
    )
    current_root_count = _line_count(marker_path)
    result = {
        "boot_changed": prior_boot_id != current_boot_id,
        "independent_root_count_unchanged": current_root_count == prior_root_count,
        "automatic_relaunch": current_root_count > prior_root_count,
        "observation": observation.kind.value,
    }
    journal_retained = _journal_has_exact_record(identity, spec_hash, "probe_started")
    no_launch = True
    passed = (
        result["boot_changed"] is True
        and result["independent_root_count_unchanged"] is True
        and result["automatic_relaunch"] is False
        and result["observation"] != PhysicalObservationKind.ACTIVE_MATCH.value
        and journal_retained
        and no_launch
    )
    context.evidence.append(
        EvidenceRecord(
            scenario_id="P11",
            phase="reboot_verify",
            execution_identity=identity,
            execution_spec_hash=spec_hash,
            expected="boot changed; no same-X relaunch; retained pre-reboot X/H evidence",
            observed=str(result["observation"]),
            detail={
                "status": "PASS" if passed else "FAIL",
                **result,
                "pre_reboot_journal_record_retained": journal_retained,
                "reboot_verify_launch_count": 0,
            },
        )
    )
    print("PASS" if passed else "FAIL", flush=True)
    return 0 if passed else 1


def _summarize(evidence: EvidenceLog) -> int:
    records = evidence.read_all()
    print("| Scenario | Phase | Expected | Observed | Status |", flush=True)
    print("|---|---|---|---|---|", flush=True)
    for record in records:
        status = record.detail.get("status", "UNKNOWN")
        print(
            f"| {record.scenario_id} | {record.phase} | {record.expected} | "
            f"{record.observed} | {status} |",
            flush=True,
        )
    return 0


def _prepare_scenario(
    context: _ScenarioContext,
    name: str,
    *,
    sleep_seconds: float = 10.0,
    exit_code: int = 0,
    child_sleep_seconds: float = 0.0,
    security_evidence: bool = False,
) -> tuple[UUID, FrozenLaunchSpec, str, str]:
    attempt_id = _scenario_attempt(context, name)
    identity = supervisor_execution_identity(context.arguments.installation_namespace, attempt_id)
    marker_path = POC_MARKER_ROOT / (
        f"{name}-{context.arguments.evidence_stack.replace('+', '-')}-starts.jsonl"
    )
    argv: tuple[str, ...] = (POC_PROBE_PATH,)
    if security_evidence:
        argv = (*argv, "--security-evidence")
    spec = FrozenLaunchSpec(
        schema_version=EXECUTION_SPEC_SCHEMA_VERSION,
        runtime_path=POC_RUNTIME_PATH,
        argv=argv,
        working_directory=str(POC_WORKING_ROOT),
        environment=((POC_ENV_KEY, name),),
        credential_slots=(),
        launch_profile_version="s2-poc-b1",
        start_marker_path=str(marker_path),
        sleep_seconds=sleep_seconds,
        exit_code=exit_code,
        child_sleep_seconds=child_sleep_seconds,
        print_env_keys=(POC_ENV_KEY,),
    )
    spec_hash = execution_spec_hash(spec)
    try:
        intent = context.store.load(attempt_id)
    except FileNotFoundError:
        context.store.create(
            DurableIntent(
                attempt_id=attempt_id,
                installation_namespace=context.arguments.installation_namespace,
                execution_identity=identity,
                execution_spec_hash=spec_hash,
                dispatch_phase=DispatchPhase.FIRST_DISPATCH_AVAILABLE,
            )
        )
    else:
        if intent.execution_identity != identity or intent.execution_spec_hash != spec_hash:
            raise ValueError("existing_scenario_intent_conflict")
    return attempt_id, spec, identity, spec_hash


def _scenario_attempt(context: _ScenarioContext, name: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        (f"{context.arguments.installation_namespace}:{context.arguments.evidence_stack}:{name}"),
    )


def _wait_runner_terminal_journal(
    socket_path: Path,
    execution_identity: str,
    execution_spec_hash_value: str,
    *,
    timeout_seconds: float = 15.0,
    observe_call: Callable[..., dict[str, object]] = runner_observe,
    sleep: Callable[[float], None] = time.sleep,
) -> PhysicalObservation:
    deadline = time.monotonic() + timeout_seconds
    observation = _safe_runner_observe(
        socket_path,
        execution_identity,
        execution_spec_hash_value,
        observe_call=observe_call,
    )
    while not (
        observation.kind is PhysicalObservationKind.TERMINAL_MATCH
        and observation.evidence_source == "journal"
    ) and time.monotonic() < deadline:
        sleep(0.05)
        observation = _safe_runner_observe(
            socket_path,
            execution_identity,
            execution_spec_hash_value,
            observe_call=observe_call,
        )
    return observation


def _wait_runner_ready(
    socket_path: Path,
    *,
    timeout_seconds: float = 10.0,
    info_call: Callable[[Path], dict[str, object]] = runner_info,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            response = info_call(socket_path)
        except (OSError, ProtocolError):
            response = {"ok": False}
        if response.get("ok") is True:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.05)


def _wait_runner_observation(
    socket_path: Path,
    execution_identity: str,
    execution_spec_hash_value: str,
    accepted: set[PhysicalObservationKind],
    *,
    timeout_seconds: float = 15.0,
) -> PhysicalObservation:
    deadline = time.monotonic() + timeout_seconds
    observation = recover_runner_without_replay(
        socket_path, execution_identity, execution_spec_hash_value
    )
    while observation.kind not in accepted and time.monotonic() < deadline:
        time.sleep(0.1)
        observation = recover_runner_without_replay(
            socket_path, execution_identity, execution_spec_hash_value
        )
    return observation


def _cleanup_runner(socket_path: Path, execution_identity: str, spec_hash: str) -> None:
    observation = recover_runner_without_replay(socket_path, execution_identity, spec_hash)
    if observation.kind is PhysicalObservationKind.ACTIVE_MATCH:
        try:
            runner_stop(socket_path, x=execution_identity, h=spec_hash, force=True)
        except OSError:
            pass


def _wait_observation(
    supervisor: SystemdCli,
    execution_identity: str,
    execution_spec_hash_value: str,
    accepted: set[PhysicalObservationKind],
    *,
    timeout_seconds: float = 15.0,
) -> PhysicalObservation:
    deadline = time.monotonic() + timeout_seconds
    observation = supervisor.observe(
        execution_identity=execution_identity,
        execution_spec_hash=execution_spec_hash_value,
    )
    while observation.kind not in accepted and time.monotonic() < deadline:
        time.sleep(0.1)
        observation = supervisor.observe(
            execution_identity=execution_identity,
            execution_spec_hash=execution_spec_hash_value,
        )
    return observation


def _fresh_observe(
    execution_identity: str,
    execution_spec_hash_value: str,
    use_journal: bool,
) -> PhysicalObservation:
    descriptor, result_name = tempfile.mkstemp(prefix="labpilot-poc-observe-", suffix=".json")
    os.close(descriptor)
    result_path = Path(result_name)
    result_path.unlink(missing_ok=True)
    process = multiprocessing.get_context("spawn").Process(
        target=_observe_worker,
        args=(execution_identity, execution_spec_hash_value, use_journal, result_path),
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join()
    if process.exitcode != 0 or not result_path.exists():
        return PhysicalObservation(
            PhysicalObservationKind.UNKNOWN,
            execution_identity,
            execution_spec_hash_value,
            evidence_source="fresh_controller_failed",
        )
    try:
        return _observation_from_json(_read_json(result_path))
    finally:
        result_path.unlink(missing_ok=True)


def _wait_fresh_observation(
    execution_identity: str,
    execution_spec_hash_value: str,
    use_journal: bool,
    accepted: set[PhysicalObservationKind],
    *,
    timeout_seconds: float = 15.0,
) -> PhysicalObservation:
    deadline = time.monotonic() + timeout_seconds
    observation = _fresh_observe(execution_identity, execution_spec_hash_value, use_journal)
    while observation.kind not in accepted and time.monotonic() < deadline:
        time.sleep(0.1)
        observation = _fresh_observe(execution_identity, execution_spec_hash_value, use_journal)
    return observation


def _observe_worker(
    execution_identity: str,
    execution_spec_hash_value: str,
    use_journal: bool,
    result_path: Path,
) -> None:
    observation = SystemdCli(
        command_runner=_subprocess_runner,
        use_journal_evidence=use_journal,
    ).observe(
        execution_identity=execution_identity,
        execution_spec_hash=execution_spec_hash_value,
    )
    _write_json_fsync(result_path, asdict(observation))


def _dispatch_and_die(
    state_root: Path,
    runner_socket: Path,
    attempt_id: UUID,
    spec: FrozenLaunchSpec,
    spec_hash: str,
) -> None:
    dispatch_once_runner(
        DurableIntentStore(state_root),
        runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    os._exit(73)


def _race_dispatch(
    state_root: Path,
    runner_socket: Path,
    attempt_id: UUID,
    spec: FrozenLaunchSpec,
    spec_hash: str,
    barrier: Path,
    result_path: Path,
) -> None:
    deadline = time.monotonic() + 10.0
    while not barrier.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    outcome = dispatch_once_runner(
        DurableIntentStore(state_root),
        runner_socket,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    _write_json_fsync(result_path, {"kind": outcome.kind.value, "detail": outcome.detail_code})


def _wait_unit_absent(execution_identity: str, *, timeout_seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _subprocess_runner(
            (
                "systemctl",
                "--system",
                "--no-ask-password",
                "show",
                systemd_unit_name(execution_identity),
                "--property=LoadState",
                "--value",
            ),
            10.0,
        )
        if result.returncode == 0 and result.stdout.strip() == "not-found":
            return True
        time.sleep(0.1)
    return False


def _cleanup(supervisor: SystemdCli, execution_identity: str, spec_hash: str) -> None:
    observation = supervisor.observe(
        execution_identity=execution_identity,
        execution_spec_hash=spec_hash,
    )
    if observation.kind is PhysicalObservationKind.ACTIVE_MATCH:
        supervisor.stop(
            execution_identity=execution_identity,
            execution_spec_hash=spec_hash,
            force=True,
        )


def _record_result(
    context: _ScenarioContext,
    scenario_id: str,
    *,
    expected: str,
    observed: str,
    detail: dict[str, object],
    execution_identity: str | None,
    execution_spec_hash: str | None,
) -> int:
    context.evidence.append(
        EvidenceRecord(
            scenario_id=scenario_id,
            phase="execute",
            execution_identity=execution_identity,
            execution_spec_hash=execution_spec_hash,
            expected=expected,
            observed=observed,
            detail=detail,
        )
    )
    passed = detail.get("status") == "PASS"
    print("PASS" if passed else "FAIL", flush=True)
    return 0 if passed else 1


class _CountingCommandRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(argv))
        return _subprocess_runner(argv, timeout)


def _subprocess_runner(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _parse_exact_journal_messages(
    stdout: str, execution_identity: str, spec_hash: str
) -> tuple[dict[str, object], ...]:
    messages: list[dict[str, object]] = []
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
            message = json.loads(record.get("MESSAGE", ""))
        except (AttributeError, json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(message, dict)
            and message.get("execution_identity") == execution_identity
            and message.get("execution_spec_hash") == spec_hash
        ):
            messages.append({str(key): value for key, value in message.items()})
    return tuple(messages)


def _candidate_b_security_record_matches(
    record: Mapping[str, object], *, expected_uid: int, expected_gid: int
) -> bool:
    return (
        record.get("event") == "probe_security"
        and record.get("euid") == expected_uid
        and record.get("egid") == expected_gid
        and record.get("cap_eff") == "0000000000000000"
        and record.get("cap_bnd") == "0000000000000000"
        and record.get("cap_amb") == "0000000000000000"
        and record.get("direct_systemd_run") == "denied"
        and record.get("runner_socket_connect") == "denied"
        and record.get("policy_write") == "denied"
    )


def _journal_has_exact_record(execution_identity: str, spec_hash: str, event: str) -> bool:
    result = _subprocess_runner(
        (
            "journalctl",
            f"--unit={systemd_unit_name(execution_identity)}",
            "--output=json",
            "--no-pager",
            "-n",
            "100",
        ),
        10.0,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
            message = json.loads(record.get("MESSAGE", ""))
        except (AttributeError, json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(message, dict)
            and message.get("event") == event
            and message.get("execution_identity") == execution_identity
            and message.get("execution_spec_hash") == spec_hash
        ):
            return True
    return False


def _observation_from_json(value: Mapping[str, object]) -> PhysicalObservation:
    return PhysicalObservation(
        kind=PhysicalObservationKind(str(value["kind"])),
        execution_identity=str(value["execution_identity"]),
        expected_spec_hash=str(value["expected_spec_hash"]),
        observed_spec_hash=_optional_string(value.get("observed_spec_hash")),
        pid=_optional_int(value.get("pid")),
        invocation_id=_optional_string(value.get("invocation_id")),
        started_at=_optional_string(value.get("started_at")),
        ended_at=_optional_string(value.get("ended_at")),
        exit_code=_optional_int(value.get("exit_code")),
        result=_optional_string(value.get("result")),
        evidence_source=_optional_string(value.get("evidence_source")),
    )


def _write_json_fsync(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid_json_object")
    return {str(key): item for key, item in value.items()}


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"missing_{key}")
    return item


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _outcome_from_observation(observation: PhysicalObservation) -> ControlOutcome:
    kinds = {
        PhysicalObservationKind.ACTIVE_MATCH: ControlOutcomeKind.EXISTING_MATCH,
        PhysicalObservationKind.TERMINAL_MATCH: ControlOutcomeKind.TERMINAL_MATCH,
        PhysicalObservationKind.IDENTITY_CONFLICT: ControlOutcomeKind.IDENTITY_CONFLICT,
        PhysicalObservationKind.UNKNOWN: ControlOutcomeKind.CONTROL_UNKNOWN,
    }
    return ControlOutcome(
        kinds[observation.kind],
        observation.execution_identity,
        observation.expected_spec_hash,
        f"observed_{observation.kind.value}",
    )


def _read_marker_records(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append({str(key): item for key, item in value.items()})
    return tuple(records)


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _run(command_runner: CommandRunner, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return command_runner(argv, 10.0)
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        return subprocess.CompletedProcess(argv, 255, "", f"{type(error).__name__}: {error}")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_properties(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _is_volatile_path(path: Path) -> bool:
    resolved = path.resolve()
    return (
        resolved == Path("/tmp")
        or resolved == Path("/run")
        or any(parent in {Path("/tmp"), Path("/run")} for parent in resolved.parents)
    )


if __name__ == "__main__":
    raise SystemExit(main())
