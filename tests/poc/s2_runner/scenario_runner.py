import argparse
import json
import multiprocessing
import os
import platform
import pwd
import shutil
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
from tests.poc.s2_runner.systemd_cli import CommandRunner, SystemdCli

ScenarioSupervisor = SystemdCli

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
    supervisor: SystemdCli


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    arguments.state_root.mkdir(parents=True, exist_ok=True)
    arguments.evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = EvidenceLog(arguments.evidence_root / "s2-runner-poc.jsonl")
    if arguments.command == "characterize":
        execution_user = pwd.getpwuid(os.getuid()).pw_name
        characterization = characterize_host(
            state_root=arguments.state_root,
            evidence_root=arguments.evidence_root,
            execution_user=execution_user,
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
                expected="complete frozen systemd profile available noninteractively",
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
        supervisor=SystemdCli(
            command_runner=_subprocess_runner,
            use_journal_evidence=arguments.evidence_stack == "systemd+journal",
        ),
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
    outcome = dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_fresh_observation(
        identity,
        spec_hash,
        context.arguments.evidence_stack == "systemd+journal",
        {PhysicalObservationKind.ACTIVE_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = observation.kind is PhysicalObservationKind.ACTIVE_MATCH and root_count == 1
    _cleanup(context.supervisor, identity, spec_hash)
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
    attempt_id, spec, identity, spec_hash = _prepare_scenario(context, "p3", sleep_seconds=20.0)
    process = multiprocessing.get_context("spawn").Process(
        target=_dispatch_and_die,
        args=(context.arguments.state_root / "intents", attempt_id, spec, spec_hash),
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join()
    observation = _wait_fresh_observation(
        identity,
        spec_hash,
        context.arguments.evidence_stack == "systemd+journal",
        {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH},
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = (
        process.exitcode == 73
        and observation.kind
        in {PhysicalObservationKind.ACTIVE_MATCH, PhysicalObservationKind.TERMINAL_MATCH}
        and root_count == 1
    )
    _cleanup(context.supervisor, identity, spec_hash)
    return _record_result(
        context,
        "P3",
        expected="ACTIVE_MATCH or TERMINAL_MATCH after accepted-before-ack death",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "controller_exit_code": process.exitcode,
            "independent_root_count": root_count,
        },
        execution_identity=identity,
        execution_spec_hash=spec_hash,
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
    observation = _wait_fresh_observation(
        identity,
        spec_hash,
        context.arguments.evidence_stack == "systemd+journal",
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
    _cleanup(context.supervisor, identity, spec_hash)
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
    dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    _wait_observation(
        context.supervisor,
        identity,
        spec_hash,
        {PhysicalObservationKind.ACTIVE_MATCH},
    )
    conflicting_spec = replace(spec, exit_code=19)
    conflicting_hash = execution_spec_hash(conflicting_spec)
    outcome = context.supervisor.launch(
        execution_identity=identity,
        execution_spec_hash=conflicting_hash,
        spec=conflicting_spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    root_count = _line_count(Path(spec.start_marker_path))
    passed = outcome.kind is ControlOutcomeKind.IDENTITY_CONFLICT and root_count == 1
    _cleanup(context.supervisor, identity, spec_hash)
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
    dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_fresh_observation(
        identity,
        spec_hash,
        context.arguments.evidence_stack == "systemd+journal",
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
        context, "p7", sleep_seconds=0.1, exit_code=23, collect_after_exit=True
    )
    dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    unit_gone = _wait_unit_absent(identity)
    observation = _fresh_observe(
        identity,
        spec_hash,
        context.arguments.evidence_stack == "systemd+journal",
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
        dispatch_once(
            context.store,
            context.supervisor,
            attempt_id,
            spec,
            spec_hash,
            PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        )
        time.sleep(stop_delay)
        stop_outcome = context.supervisor.stop(
            execution_identity=identity,
            execution_spec_hash=spec_hash,
            force=False,
        )
        observation = _wait_observation(
            context.supervisor,
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
    use_journal = context.arguments.evidence_stack == "systemd+journal"
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context,
        "p10",
        sleep_seconds=0.2,
        exit_code=29,
        collect_after_exit=use_journal,
    )
    dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    time.sleep(1.0)
    observation = _wait_fresh_observation(
        identity,
        spec_hash,
        use_journal,
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
    passed = capabilities_dict.get("noninteractive_system_manager_authorization") is True
    return _record_result(
        context,
        "P12",
        expected="narrow noninteractive system-manager permission boundary",
        observed="PASS" if passed else "FAIL",
        detail={
            "status": "PASS" if passed else "FAIL",
            "controller_user": facts_dict.get("controller_user"),
            "controller_uid": facts_dict.get("controller_uid"),
            "execution_user": facts_dict.get("execution_user"),
            "execution_uid": facts_dict.get("execution_uid"),
            "manager_scope": "system",
            "used_root_or_user_manager_fallback": False,
        },
        execution_identity=None,
        execution_spec_hash=None,
    )


def _p13(context: _ScenarioContext) -> int:
    attempt_id, spec, identity, spec_hash = _prepare_scenario(
        context, "p13", sleep_seconds=0.2, exit_code=31
    )
    dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_observation(
        context.supervisor,
        identity,
        spec_hash,
        {PhysicalObservationKind.TERMINAL_MATCH},
    )
    journal = _subprocess_runner(
        (
            "journalctl",
            f"--unit={systemd_unit_name(identity)}",
            "--no-pager",
            "-n",
            "100",
            "--output=cat",
        ),
        10.0,
    )
    output = journal.stdout[-16000:]
    properties = _subprocess_runner(
        (
            "systemctl",
            "--system",
            "--no-ask-password",
            "show",
            systemd_unit_name(identity),
            "--property=ExecStart",
            "--property=WorkingDirectory",
            "--property=Environment",
        ),
        10.0,
    )
    expected_environment = "LABPILOT_POC_SCENARIO=p13"
    environment_log_match = '"LABPILOT_POC_SCENARIO":"p13"' in output
    passed = (
        observation.kind is PhysicalObservationKind.TERMINAL_MATCH
        and str(spec.exit_code) in output
        and environment_log_match
        and '"event":"probe_started"' in output
        and '"event":"probe_stderr"' in output
        and spec.runtime_path in properties.stdout
        and spec.working_directory in properties.stdout
        and expected_environment in properties.stdout
    )
    return _record_result(
        context,
        "P13",
        expected="exact runtime argv workdir env stdout terminal propagation",
        observed=observation.kind.value,
        detail={
            "status": "PASS" if passed else "FAIL",
            "exit_code": observation.exit_code,
            "runtime_path_match": spec.runtime_path in properties.stdout,
            "working_directory_match": spec.working_directory in properties.stdout,
            "environment_match": (
                environment_log_match and expected_environment in properties.stdout
            ),
            "stdout_match": '"event":"probe_started"' in output,
            "stderr_match": '"event":"probe_stderr"' in output,
            "bounded_log_tail": output,
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
    outcome = dispatch_once(
        context.store,
        context.supervisor,
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    observation = _wait_observation(
        context.supervisor,
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
    counting = _CountingCommandRunner()
    supervisor = SystemdCli(command_runner=counting, use_journal_evidence=True)
    result = reboot_verify(
        supervisor,
        execution_identity=identity,
        execution_spec_hash=spec_hash,
        prior_boot_id=prior_boot_id,
        current_boot_id=current_boot_id,
        marker_path=marker_path,
        prior_root_count=prior_root_count,
    )
    journal_retained = _journal_has_exact_record(identity, spec_hash, "probe_started")
    no_launch = not any(call[0] == "systemd-run" for call in counting.calls)
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
                "reboot_verify_launch_count": sum(
                    call[0] == "systemd-run" for call in counting.calls
                ),
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
    collect_after_exit: bool = False,
) -> tuple[UUID, FrozenLaunchSpec, str, str]:
    attempt_id = _scenario_attempt(context, name)
    identity = supervisor_execution_identity(context.arguments.installation_namespace, attempt_id)
    marker_path = context.arguments.evidence_root / (
        f"{name}-{context.arguments.evidence_stack.replace('+', '-')}-starts.jsonl"
    )
    spec = FrozenLaunchSpec(
        schema_version=EXECUTION_SPEC_SCHEMA_VERSION,
        runtime_path=str(Path(sys.executable).resolve()),
        argv=("-m", "tests.poc.s2_runner.probe"),
        working_directory=str(Path.cwd().resolve()),
        environment=(("LABPILOT_POC_SCENARIO", name),),
        credential_slots=(),
        execution_user=pwd.getpwuid(os.getuid()).pw_name,
        start_marker_path=str(marker_path),
        sleep_seconds=sleep_seconds,
        exit_code=exit_code,
        child_sleep_seconds=child_sleep_seconds,
        print_env_keys=("LABPILOT_POC_SCENARIO",),
        collect_after_exit=collect_after_exit,
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
    attempt_id: UUID,
    spec: FrozenLaunchSpec,
    spec_hash: str,
) -> None:
    dispatch_once(
        DurableIntentStore(state_root),
        SystemdCli(command_runner=_subprocess_runner, use_journal_evidence=False),
        attempt_id,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    os._exit(73)


def _race_dispatch(
    state_root: Path,
    attempt_id: UUID,
    spec: FrozenLaunchSpec,
    spec_hash: str,
    barrier: Path,
    result_path: Path,
) -> None:
    deadline = time.monotonic() + 10.0
    while not barrier.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    outcome = dispatch_once(
        DurableIntentStore(state_root),
        SystemdCli(command_runner=_subprocess_runner, use_journal_evidence=False),
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
