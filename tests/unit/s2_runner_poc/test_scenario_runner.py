import os
import pwd
import subprocess
from collections.abc import Callable
from pathlib import Path
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
    PositiveCreationEvidence,
    execution_spec_hash,
    supervisor_execution_identity,
)
from tests.poc.s2_runner.durable_state import (
    DispatchPhase,
    DurableIntent,
    DurableIntentStore,
)
from tests.poc.s2_runner.evidence import EvidenceLog, EvidenceRecord
from tests.poc.s2_runner.probe import main as probe_main
from tests.poc.s2_runner.scenario_runner import (
    REQUIRED_SYSTEMD_PROFILE_CAPABILITIES,
    ScenarioSupervisor,
    characterize_host,
    dispatch_once,
    evaluate_capabilities,
    parse_args,
    reboot_verify,
    recover_without_replay,
    run_after_capability_gate,
    stop_and_observe,
)

ATTEMPT_ID = UUID("12345678-aaaa-bbbb-cccc-1234567890ab")
NAMESPACE = "native-test-installation"
X = supervisor_execution_identity(NAMESPACE, ATTEMPT_ID)


def _spec(**changes: object) -> FrozenLaunchSpec:
    values: dict[str, object] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "runtime_path": "/usr/bin/python3.12",
        "argv": ("-m", "tests.poc.s2_runner.probe"),
        "working_directory": "/srv/labpilot",
        "environment": (("VISIBLE", "yes"),),
        "credential_slots": (),
        "launch_profile_version": "s2-poc-b1",
        "start_marker_path": "/var/lib/labpilot/start.jsonl",
        "sleep_seconds": 1.0,
        "exit_code": 0,
        "child_sleep_seconds": 0.0,
        "print_env_keys": ("VISIBLE",),
    }
    values.update(changes)
    return FrozenLaunchSpec(**values)  # type: ignore[arg-type]


def _intent(spec: FrozenLaunchSpec, phase: DispatchPhase) -> DurableIntent:
    return DurableIntent(
        attempt_id=ATTEMPT_ID,
        installation_namespace=NAMESPACE,
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        dispatch_phase=phase,
    )


class FakeSupervisor:
    def __init__(self, observation: PhysicalObservation | None = None) -> None:
        self.observation = observation
        self.launch_calls = 0
        self.observe_calls = 0
        self.stop_calls = 0
        self.on_launch: Callable[[], None] | None = None

    def launch(
        self,
        *,
        execution_identity: str,
        execution_spec_hash: str,
        spec: FrozenLaunchSpec,
        creation_evidence: PositiveCreationEvidence,
    ) -> ControlOutcome:
        self.launch_calls += 1
        if self.on_launch is not None:
            self.on_launch()
        return ControlOutcome(
            ControlOutcomeKind.CREATED,
            execution_identity,
            execution_spec_hash,
            creation_evidence.value,
        )

    def observe(self, *, execution_identity: str, execution_spec_hash: str) -> PhysicalObservation:
        self.observe_calls += 1
        return self.observation or PhysicalObservation(
            PhysicalObservationKind.UNKNOWN,
            execution_identity,
            execution_spec_hash,
        )

    def stop(
        self, *, execution_identity: str, execution_spec_hash: str, force: bool
    ) -> ControlOutcome:
        self.stop_calls += 1
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_ACCEPTED,
            execution_identity,
            execution_spec_hash,
            "accepted",
        )


def _supervisor(fake: FakeSupervisor) -> ScenarioSupervisor:
    return cast(ScenarioSupervisor, fake)


def test_probe_start_marker_is_one_independent_root_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "probe-starts.jsonl"

    assert (
        probe_main(
            [
                "--execution-identity",
                X,
                "--execution-spec-hash",
                "a" * 64,
                "--start-marker",
                str(marker),
                "--sleep-seconds",
                "0",
                "--exit-code",
                "0",
                "--child-sleep-seconds",
                "0.01",
            ]
        )
        == 0
    )

    records = marker.read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    assert '"event":"probe_started"' in records[0]
    assert X in records[0]
    captured = capsys.readouterr()
    assert '"event":"probe_stderr"' in captured.err
    assert X in captured.err


def test_cli_accepts_exact_public_scenario_commands(tmp_path: Path) -> None:
    commands = (
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
    prefix = [
        "--state-root",
        str(tmp_path / "state"),
        "--evidence-root",
        str(tmp_path / "evidence"),
        "--installation-namespace",
        "native-test",
        "--evidence-stack",
        "systemd+journal",
    ]

    assert tuple(parse_args([*prefix, command]).command for command in commands) == commands


def test_creation_path_has_no_call_without_positive_evidence(tmp_path: Path) -> None:
    spec = _spec()
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    supervisor = FakeSupervisor()

    outcome = dispatch_once(
        store,
        _supervisor(supervisor),
        ATTEMPT_ID,
        spec,
        execution_spec_hash(spec),
        cast(PositiveCreationEvidence, "missing"),
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_REJECTED
    assert supervisor.launch_calls == 0
    assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.FIRST_DISPATCH_AVAILABLE


def test_dispatch_phase_is_persisted_before_launch_call(tmp_path: Path) -> None:
    spec = _spec()
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    supervisor = FakeSupervisor()
    supervisor.on_launch = lambda: assert_dispatch_may_have_occurred(store)

    dispatch_once(
        store,
        _supervisor(supervisor),
        ATTEMPT_ID,
        spec,
        execution_spec_hash(spec),
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert supervisor.launch_calls == 1


def assert_dispatch_may_have_occurred(store: DurableIntentStore) -> None:
    assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED


def test_p3_recovery_observes_before_any_possible_replay() -> None:
    spec_hash = execution_spec_hash(_spec())
    supervisor = FakeSupervisor(
        PhysicalObservation(PhysicalObservationKind.ACTIVE_MATCH, X, spec_hash, spec_hash)
    )

    observation = recover_without_replay(_supervisor(supervisor), X, spec_hash)

    assert observation.kind is PhysicalObservationKind.ACTIVE_MATCH
    assert supervisor.observe_calls == 1
    assert supervisor.launch_calls == 0


def test_p4_concurrent_same_x_h_never_generates_fallback_identity(tmp_path: Path) -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    supervisor = FakeSupervisor(
        PhysicalObservation(PhysicalObservationKind.ACTIVE_MATCH, X, spec_hash, spec_hash)
    )

    first = dispatch_once(
        store,
        _supervisor(supervisor),
        ATTEMPT_ID,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )
    second = dispatch_once(
        store,
        _supervisor(supervisor),
        ATTEMPT_ID,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert first.kind is ControlOutcomeKind.CREATED
    assert second.kind is ControlOutcomeKind.EXISTING_MATCH
    assert supervisor.launch_calls == 1
    assert supervisor.observe_calls == 1


def test_p5_same_x_different_h_never_stops_or_relaunches() -> None:
    spec_hash = execution_spec_hash(_spec())
    supervisor = FakeSupervisor(
        PhysicalObservation(PhysicalObservationKind.IDENTITY_CONFLICT, X, spec_hash, "b" * 64)
    )

    observation = recover_without_replay(_supervisor(supervisor), X, spec_hash)

    assert observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT
    assert supervisor.launch_calls == 0
    assert supervisor.stop_calls == 0


def test_p8_pre_dispatch_validation_can_prove_never_accepted(tmp_path: Path) -> None:
    spec = _spec()
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    supervisor = FakeSupervisor()

    outcome = dispatch_once(
        store,
        _supervisor(supervisor),
        ATTEMPT_ID,
        spec,
        "0" * 64,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE
    assert outcome.detail_code == PositiveCreationEvidence.NEVER_ACCEPTED_PROVEN.value
    assert supervisor.launch_calls == supervisor.observe_calls == 0


def test_p8_not_found_after_possible_dispatch_is_unknown(tmp_path: Path) -> None:
    spec = _spec()
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED))
    supervisor = FakeSupervisor()

    outcome = dispatch_once(
        store,
        _supervisor(supervisor),
        ATTEMPT_ID,
        spec,
        execution_spec_hash(spec),
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
    assert supervisor.observe_calls == 1
    assert supervisor.launch_calls == 0


def test_p9_does_not_claim_cancel_caused_exit_without_evidence() -> None:
    spec_hash = execution_spec_hash(_spec())
    terminal = PhysicalObservation(
        PhysicalObservationKind.TERMINAL_MATCH,
        X,
        spec_hash,
        spec_hash,
        exit_code=0,
    )
    supervisor = FakeSupervisor(terminal)

    facts = stop_and_observe(_supervisor(supervisor), X, spec_hash, force=False)

    assert facts == {"stop_requested": True, "terminal_observed": True}
    assert "cancel_caused_exit" not in facts


def test_reboot_verify_never_calls_launch(tmp_path: Path) -> None:
    marker = tmp_path / "starts.jsonl"
    marker.write_text("one\n", encoding="utf-8")
    supervisor = FakeSupervisor()

    result = reboot_verify(
        _supervisor(supervisor),
        execution_identity=X,
        execution_spec_hash="a" * 64,
        prior_boot_id="boot-before",
        current_boot_id="boot-after",
        marker_path=marker,
        prior_root_count=1,
    )

    assert result["boot_changed"] is True
    assert result["independent_root_count_unchanged"] is True
    assert supervisor.launch_calls == 0
    assert supervisor.observe_calls == 1


def test_reboot_verify_does_not_invent_lost_observation_kind(tmp_path: Path) -> None:
    marker = tmp_path / "starts.jsonl"
    marker.write_text("one\n", encoding="utf-8")

    result = reboot_verify(
        _supervisor(FakeSupervisor()),
        execution_identity=X,
        execution_spec_hash="a" * 64,
        prior_boot_id="boot-before",
        current_boot_id="boot-after",
        marker_path=marker,
        prior_root_count=1,
    )

    assert result["observation"] == PhysicalObservationKind.UNKNOWN.value
    assert "lost" not in {kind.value for kind in PhysicalObservationKind}


def test_characterize_checks_entire_required_systemd_profile() -> None:
    all_present = {name: True for name in REQUIRED_SYSTEMD_PROFILE_CAPABILITIES}

    assert evaluate_capabilities(all_present) is None
    for missing in REQUIRED_SYSTEMD_PROFILE_CAPABILITIES:
        values = dict(all_present)
        values[missing] = False
        assert evaluate_capabilities(values) == f"HOST_CAPABILITY_GAP: {missing}"


def test_characterize_exercises_frozen_profile_with_structured_commands(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    current_user = pwd.getpwuid(os.getuid()).pw_name

    def command_runner(
        argv: tuple[str, ...] | list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert not isinstance(argv, str)
        assert timeout > 0
        call = tuple(argv)
        calls.append(call)
        if call[:2] == ("systemctl", "--version"):
            return subprocess.CompletedProcess(call, 0, "systemd 257\n", "")
        if call[0] == "systemd-run":
            return subprocess.CompletedProcess(call, 0, "Running as unit: poc.service\n", "")
        if call[0] == "journalctl" and "--list-boots" in call:
            return subprocess.CompletedProcess(call, 0, "0 boot-id\n-1 old-boot-id\n", "")
        if call[0] == "journalctl" and "--disk-usage" in call:
            return subprocess.CompletedProcess(call, 0, "Archived journals take up 8.0M.\n", "")
        if call[0] == "journalctl":
            return subprocess.CompletedProcess(call, 0, "poc-stdout\npoc-stderr\n", "")
        if call[0] == "findmnt":
            return subprocess.CompletedProcess(call, 0, "/ ext4\n", "")
        if "--property=Version" in call:
            return subprocess.CompletedProcess(call, 0, "Version=257\n", "")
        if "--property=LoadState" in call and "--value" in call:
            return subprocess.CompletedProcess(call, 0, "not-found\n", "")
        if "show" in call:
            return subprocess.CompletedProcess(
                call,
                0,
                (
                    "LoadState=loaded\nType=exec\nDescription=labpilot-poc-characterize\n"
                    f"User={current_user}\nWorkingDirectory={tmp_path}\n"
                    "Environment=LABPILOT_POC_CHARACTERIZE=ok\nRestart=no\n"
                    "KillMode=control-group\nStandardOutput=journal\nStandardError=journal\n"
                ),
                "",
            )
        return subprocess.CompletedProcess(call, 0, "", "")

    result = characterize_host(
        state_root=tmp_path,
        evidence_root=tmp_path,
        execution_user=current_user,
        command_runner=command_runner,
        kernel_release="6.12.0-native",
        executable_finder=lambda name: f"/usr/bin/{name}",
    )

    assert result.gap is None
    assert all(result.capabilities.values())
    launch = next(call for call in calls if call[0] == "systemd-run")
    for required in (
        "--system",
        "--no-ask-password",
        "--service-type=exec",
        "--expand-environment=no",
        "--property=Restart=no",
        "--property=KillMode=control-group",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        f"--uid={current_user}",
        f"--working-directory={tmp_path}",
        "--setenv=LABPILOT_POC_CHARACTERIZE=ok",
        "--collect",
    ):
        assert required in launch
    assert any(call[:4] == ("systemctl", "--system", "--no-ask-password", "stop") for call in calls)


def test_characterize_capability_gap_stops_before_scenarios() -> None:
    called = False

    def scenario() -> str:
        nonlocal called
        called = True
        return "ran"

    capabilities = {name: True for name in REQUIRED_SYSTEMD_PROFILE_CAPABILITIES}
    capabilities["system_manager_reachable"] = False

    result = run_after_capability_gate(capabilities, scenario)

    assert result == "HOST_CAPABILITY_GAP: system_manager_reachable"
    assert called is False


def test_evidence_writer_rejects_secret_shaped_keys(tmp_path: Path) -> None:
    log = EvidenceLog(tmp_path / "evidence.jsonl")
    record = EvidenceRecord(
        scenario_id="P0",
        phase="characterize",
        execution_identity=None,
        execution_spec_hash=None,
        expected="secret-free detail",
        observed="rejected",
        detail={"nested": [{"Authorization": "must-not-write"}]},
    )

    with pytest.raises(ValueError, match="secret_shaped_evidence_key"):
        log.append(record)

    assert not (tmp_path / "evidence.jsonl").exists()
