import json
import multiprocessing
import os
import pwd
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
import tests.poc.s2_runner.probe as probe_module
import tests.poc.s2_runner.scenario_runner as scenario_module
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
from tests.poc.s2_runner.runner_protocol import ProtocolError
from tests.poc.s2_runner.scenario_runner import (
    REQUIRED_CANDIDATE_B_CAPABILITIES,
    REQUIRED_SYSTEMD_PROFILE_CAPABILITIES,
    ScenarioSupervisor,
    characterize_candidate_b_host,
    characterize_host,
    dispatch_once,
    dispatch_once_runner,
    evaluate_capabilities,
    parse_args,
    reboot_verify,
    recover_runner_without_replay,
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
    started = next(
        json.loads(line)
        for line in captured.out.splitlines()
        if '"event":"probe_started"' in line
    )
    assert started["runtime_path"]
    assert started["working_directory"] == os.getcwd()
    assert isinstance(started["argv"], list)
    assert '"event":"probe_stderr"' in captured.err
    assert X in captured.err


def test_probe_security_evidence_emits_only_bounded_fixed_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "probe-starts.jsonl"
    monkeypatch.setattr(
        probe_module,
        "security_facts",
        lambda: {
            "euid": 2102,
            "egid": 2102,
            "cap_eff": "0000000000000000",
            "cap_bnd": "0000000000000000",
            "cap_amb": "0000000000000000",
            "direct_systemd_run": "denied",
            "runner_socket_connect": "denied",
            "policy_write": "denied",
        },
    )

    result = probe_main(
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
            "0",
            "--security-evidence",
        ]
    )

    assert result == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    security = next(record for record in records if record.get("event") == "probe_security")
    marker_record = json.loads(marker.read_text(encoding="utf-8").strip())
    assert marker_record["security"] == {
        "euid": 2102,
        "egid": 2102,
        "cap_eff": "0000000000000000",
        "cap_bnd": "0000000000000000",
        "cap_amb": "0000000000000000",
        "direct_systemd_run": "denied",
        "runner_socket_connect": "denied",
        "policy_write": "denied",
    }
    assert set(security) == {
        "event",
        "execution_identity",
        "execution_spec_hash",
        "euid",
        "egid",
        "cap_eff",
        "cap_bnd",
        "cap_amb",
        "direct_systemd_run",
        "runner_socket_connect",
        "policy_write",
    }




def test_probe_start_marker_records_only_requested_non_secret_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "probe-starts.jsonl"
    monkeypatch.setenv("LABPILOT_POC_ENV", "p13")

    assert probe_main(
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
            "0",
            "--print-env",
            "LABPILOT_POC_ENV",
        ]
    ) == 0

    marker_record = json.loads(marker.read_text(encoding="utf-8").strip())
    assert marker_record["environment"] == {"LABPILOT_POC_ENV": "p13"}
    assert len(marker.read_text(encoding="utf-8").splitlines()) == 1
    capsys.readouterr()

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


def test_p2_waits_for_probe_marker_after_active_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    state_root = tmp_path / "state"
    evidence_root = tmp_path / "evidence"
    context = scenario_module._ScenarioContext(
        arguments=scenario_module.CliArguments(
            state_root=state_root,
            evidence_root=evidence_root,
            installation_namespace="native-test",
            evidence_stack="systemd+journal",
            command="p2",
        ),
        store=DurableIntentStore(state_root / "intents"),
        evidence=EvidenceLog(evidence_root / "records.jsonl"),
        runner_socket=Path("/run/labpilot-s2-runner-poc/control.sock"),
    )
    monkeypatch.setattr(scenario_module, "POC_MARKER_ROOT", marker_root)
    monkeypatch.setattr(scenario_module, "POC_WORKING_ROOT", tmp_path / "workspace")

    def dispatch(*args: object, **kwargs: object) -> ControlOutcome:
        del args, kwargs
        attempt_id = scenario_module._scenario_attempt(context, "p2")
        identity = supervisor_execution_identity("native-test", attempt_id)
        intent = context.store.load(attempt_id)
        context.store.mark_dispatch_may_have_occurred(attempt_id)
        return ControlOutcome(
            ControlOutcomeKind.CREATED,
            identity,
            intent.execution_spec_hash,
            "created",
        )

    def observe(
        socket_path: Path,
        execution_identity: str,
        execution_spec_hash_value: str,
        accepted: set[PhysicalObservationKind],
        *,
        timeout_seconds: float = 15.0,
    ) -> PhysicalObservation:
        del socket_path, accepted, timeout_seconds
        marker = marker_root / "p2-systemd-journal-starts.jsonl"

        def write_marker() -> None:
            marker.write_text('{"event":"probe_started"}\n', encoding="utf-8")

        threading.Timer(0.05, write_marker).start()
        return PhysicalObservation(
            PhysicalObservationKind.ACTIVE_MATCH,
            execution_identity,
            execution_spec_hash_value,
            execution_spec_hash_value,
            evidence_source="systemd_unit",
        )

    monkeypatch.setattr(scenario_module, "dispatch_once_runner", dispatch)
    monkeypatch.setattr(scenario_module, "_wait_runner_observation", observe)
    monkeypatch.setattr(scenario_module, "_cleanup_runner", lambda *args: None)

    assert scenario_module._p2(context) == 0


def test_p3_ack_response_loss_waits_for_probe_marker_after_active_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    state_root = tmp_path / "state"
    evidence_root = tmp_path / "evidence"
    context = scenario_module._ScenarioContext(
        arguments=scenario_module.CliArguments(
            state_root=state_root,
            evidence_root=evidence_root,
            installation_namespace="native-test",
            evidence_stack="systemd+journal",
            command="p3",
        ),
        store=DurableIntentStore(state_root / "intents"),
        evidence=EvidenceLog(evidence_root / "records.jsonl"),
        runner_socket=Path("/run/labpilot-s2-runner-poc/control.sock"),
    )
    monkeypatch.setattr(scenario_module, "POC_MARKER_ROOT", marker_root)
    monkeypatch.setattr(scenario_module, "POC_WORKING_ROOT", tmp_path / "workspace")

    after_attempt = scenario_module._scenario_attempt(context, "p3-ack-after")
    after_identity = supervisor_execution_identity("native-test", after_attempt)
    lost_attempt = scenario_module._scenario_attempt(context, "p3-ack-response-lost")
    lost_identity = supervisor_execution_identity("native-test", lost_attempt)

    def dispatch(
        store: DurableIntentStore,
        socket_path: Path,
        attempt_id: UUID,
        spec: FrozenLaunchSpec,
        execution_spec_hash_value: str,
        creation_evidence: PositiveCreationEvidence,
        **kwargs: object,
    ) -> ControlOutcome:
        del store, socket_path, spec, creation_evidence, kwargs
        return ControlOutcome(
            ControlOutcomeKind.CONTROL_UNKNOWN,
            supervisor_execution_identity("native-test", attempt_id),
            execution_spec_hash_value,
            "injected_control_unknown",
        )

    def observe(
        socket_path: Path,
        execution_identity: str,
        execution_spec_hash_value: str,
        accepted: set[PhysicalObservationKind],
        *,
        timeout_seconds: float = 15.0,
    ) -> PhysicalObservation:
        del socket_path, accepted, timeout_seconds
        if execution_identity == after_identity:
            marker = marker_root / "p3-ack-after-systemd-journal-starts.jsonl"

            def write_after_marker() -> None:
                marker.write_text('{"event":"probe_started"}\n', encoding="utf-8")

            threading.Timer(0.05, write_after_marker).start()
        elif execution_identity == lost_identity:
            marker = marker_root / "p3-ack-response-lost-systemd-journal-starts.jsonl"

            def write_marker() -> None:
                marker.write_text('{"event":"probe_started"}\n', encoding="utf-8")

            threading.Timer(0.05, write_marker).start()
        return PhysicalObservation(
            PhysicalObservationKind.ACTIVE_MATCH,
            execution_identity,
            execution_spec_hash_value,
            execution_spec_hash_value,
            evidence_source="systemd_unit",
        )

    monkeypatch.setattr(scenario_module, "dispatch_once_runner", dispatch)
    monkeypatch.setattr(scenario_module, "_wait_runner_ready", lambda *args: True)
    monkeypatch.setattr(
        scenario_module,
        "recover_runner_without_replay",
        lambda *args: PhysicalObservation(
            PhysicalObservationKind.UNKNOWN,
            args[1],
            args[2],
        ),
    )
    monkeypatch.setattr(scenario_module, "_wait_runner_observation", observe)
    monkeypatch.setattr(scenario_module, "_cleanup_runner", lambda *args: None)

    assert scenario_module._p3(context) == 0


def test_p3_recovery_observes_before_any_possible_replay() -> None:
    spec_hash = execution_spec_hash(_spec())
    supervisor = FakeSupervisor(
        PhysicalObservation(PhysicalObservationKind.ACTIVE_MATCH, X, spec_hash, spec_hash)
    )

    observation = recover_without_replay(_supervisor(supervisor), X, spec_hash)

    assert observation.kind is PhysicalObservationKind.ACTIVE_MATCH
    assert supervisor.observe_calls == 1
    assert supervisor.launch_calls == 0


def test_p4_waits_for_probe_marker_after_concurrent_dispatch_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    context = scenario_module._ScenarioContext(
        arguments=scenario_module.CliArguments(
            state_root=state_root,
            evidence_root=evidence_root,
            installation_namespace="native-test",
            evidence_stack="systemd+journal",
            command="p4",
        ),
        store=DurableIntentStore(state_root / "intents"),
        evidence=EvidenceLog(evidence_root / "records.jsonl"),
        runner_socket=Path("/run/labpilot-s2-runner-poc/control.sock"),
    )
    monkeypatch.setattr(scenario_module, "POC_MARKER_ROOT", marker_root)
    monkeypatch.setattr(scenario_module, "POC_WORKING_ROOT", tmp_path / "workspace")

    class FakeProcess:
        def __init__(self, args: tuple[object, ...]) -> None:
            self._args = args
            self.exitcode: int | None = None

        def start(self) -> None:
            result_path = cast(Path, self._args[-1])
            kind = "created" if result_path.name.endswith("0.json") else "existing_match"
            result_path.write_text(
                json.dumps({"kind": kind, "detail": "native-test"}),
                encoding="utf-8",
            )
            self.exitcode = 0

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

        def terminate(self) -> None:
            raise AssertionError("fake process should already be complete")

    class FakeSpawnContext:
        def Process(self, *, target: object, args: tuple[object, ...]) -> FakeProcess:
            del target
            return FakeProcess(args)

    monkeypatch.setattr(
        multiprocessing,
        "get_context",
        lambda method: FakeSpawnContext(),
    )

    def observe(
        socket_path: Path,
        execution_identity: str,
        execution_spec_hash_value: str,
        accepted: set[PhysicalObservationKind],
        *,
        timeout_seconds: float = 15.0,
    ) -> PhysicalObservation:
        del socket_path, accepted, timeout_seconds
        marker = marker_root / "p4-systemd-journal-starts.jsonl"

        def write_marker() -> None:
            marker.write_text('{"event":"probe_started"}\n', encoding="utf-8")

        threading.Timer(0.05, write_marker).start()
        return PhysicalObservation(
            PhysicalObservationKind.ACTIVE_MATCH,
            execution_identity,
            execution_spec_hash_value,
            execution_spec_hash_value,
            evidence_source="systemd_unit",
        )

    monkeypatch.setattr(scenario_module, "_wait_runner_observation", observe)
    monkeypatch.setattr(scenario_module, "_cleanup_runner", lambda *args: None)

    assert scenario_module._p4(context) == 0

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


def test_p5_waits_for_original_probe_marker_before_counting_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    context = scenario_module._ScenarioContext(
        arguments=scenario_module.CliArguments(
            state_root=state_root,
            evidence_root=evidence_root,
            installation_namespace="native-test",
            evidence_stack="systemd+journal",
            command="p5",
        ),
        store=DurableIntentStore(state_root / "intents"),
        evidence=EvidenceLog(evidence_root / "records.jsonl"),
        runner_socket=Path("/run/labpilot-s2-runner-poc/control.sock"),
    )
    monkeypatch.setattr(scenario_module, "POC_MARKER_ROOT", marker_root)
    monkeypatch.setattr(scenario_module, "POC_WORKING_ROOT", tmp_path / "workspace")

    def dispatch(
        store: DurableIntentStore,
        socket_path: Path,
        attempt_id: UUID,
        spec: FrozenLaunchSpec,
        execution_spec_hash_value: str,
        creation_evidence: PositiveCreationEvidence,
        **kwargs: object,
    ) -> ControlOutcome:
        del store, socket_path, spec, creation_evidence, kwargs
        return ControlOutcome(
            ControlOutcomeKind.CREATED,
            supervisor_execution_identity("native-test", attempt_id),
            execution_spec_hash_value,
            "created",
        )

    def observe(
        socket_path: Path,
        execution_identity: str,
        execution_spec_hash_value: str,
        accepted: set[PhysicalObservationKind],
        *,
        timeout_seconds: float = 15.0,
    ) -> PhysicalObservation:
        del socket_path, accepted, timeout_seconds
        marker = marker_root / "p5-systemd-journal-starts.jsonl"

        def write_marker() -> None:
            marker.write_text('{"event":"probe_started"}\n', encoding="utf-8")

        threading.Timer(0.05, write_marker).start()
        return PhysicalObservation(
            PhysicalObservationKind.ACTIVE_MATCH,
            execution_identity,
            execution_spec_hash_value,
            execution_spec_hash_value,
            evidence_source="systemd_unit",
        )

    monkeypatch.setattr(scenario_module, "dispatch_once_runner", dispatch)
    monkeypatch.setattr(scenario_module, "_wait_runner_observation", observe)
    monkeypatch.setattr(
        scenario_module,
        "runner_launch",
        lambda *args, **kwargs: {
            "ok": True,
            "kind": ControlOutcomeKind.IDENTITY_CONFLICT.value,
            "detail_code": "identity_conflict",
        },
    )
    monkeypatch.setattr(scenario_module, "_cleanup_runner", lambda *args: None)

    assert scenario_module._p5(context) == 0

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


def test_reboot_prepare_waits_for_probe_marker_before_recording_root_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (state_root / "host-characterization.json").write_text(
        json.dumps(
            {
                "facts": {
                    "state_root_volatile": False,
                    "evidence_root_volatile": False,
                }
            }
        ),
        encoding="utf-8",
    )
    context = scenario_module._ScenarioContext(
        arguments=scenario_module.CliArguments(
            state_root=state_root,
            evidence_root=evidence_root,
            installation_namespace="native-test",
            evidence_stack="systemd+journal",
            command="reboot-prepare",
        ),
        store=DurableIntentStore(state_root / "intents"),
        evidence=EvidenceLog(evidence_root / "records.jsonl"),
        runner_socket=Path("/run/labpilot-s2-runner-poc/control.sock"),
    )
    monkeypatch.setattr(scenario_module, "POC_MARKER_ROOT", marker_root)
    monkeypatch.setattr(scenario_module, "POC_WORKING_ROOT", tmp_path / "workspace")

    def dispatch(
        store: DurableIntentStore,
        socket_path: Path,
        attempt_id: UUID,
        spec: FrozenLaunchSpec,
        execution_spec_hash_value: str,
        creation_evidence: PositiveCreationEvidence,
        **kwargs: object,
    ) -> ControlOutcome:
        del store, socket_path, spec, creation_evidence, kwargs
        return ControlOutcome(
            ControlOutcomeKind.CREATED,
            supervisor_execution_identity("native-test", attempt_id),
            execution_spec_hash_value,
            "created",
        )

    def observe(
        socket_path: Path,
        execution_identity: str,
        execution_spec_hash_value: str,
        accepted: set[PhysicalObservationKind],
        *,
        timeout_seconds: float = 15.0,
    ) -> PhysicalObservation:
        del socket_path, accepted, timeout_seconds
        marker = marker_root / "p11-systemd-journal-starts.jsonl"

        def write_marker() -> None:
            marker.write_text('{"event":"probe_started"}\n', encoding="utf-8")

        threading.Timer(0.05, write_marker).start()
        return PhysicalObservation(
            PhysicalObservationKind.ACTIVE_MATCH,
            execution_identity,
            execution_spec_hash_value,
            execution_spec_hash_value,
            evidence_source="systemd_unit",
        )

    monkeypatch.setattr(scenario_module, "dispatch_once_runner", dispatch)
    monkeypatch.setattr(scenario_module, "_wait_runner_observation", observe)

    assert scenario_module._reboot_prepare(context) == 0
    metadata = json.loads((evidence_root / "p11-reboot.json").read_text(encoding="utf-8"))
    assert metadata["prior_root_count"] == 1

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


def test_candidate_b_characterize_requires_runner_and_denied_direct_systemd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persistent_journal = tmp_path / "journal"
    persistent_journal.mkdir()
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        "tests.poc.s2_runner.scenario_runner.pwd.getpwuid",
        lambda uid: SimpleNamespace(pw_name="labpilot-controller"),
    )
    monkeypatch.setattr("tests.poc.s2_runner.scenario_runner.os.getuid", lambda: 2101)
    monkeypatch.setattr("tests.poc.s2_runner.scenario_runner.os.getgid", lambda: 2101)
    monkeypatch.setattr(
        "tests.poc.s2_runner.scenario_runner.pwd.getpwnam",
        lambda name: SimpleNamespace(pw_uid=2102 if name == "labpilot-train" else 2101),
    )
    monkeypatch.setattr(
        "tests.poc.s2_runner.scenario_runner.grp.getgrnam",
        lambda name: SimpleNamespace(gr_gid=2102 if name == "labpilot-train" else 2101),
    )

    def command_runner(
        argv: tuple[str, ...] | list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert timeout > 0
        call = tuple(argv)
        calls.append(call)
        if call[:2] == ("systemctl", "--version"):
            return subprocess.CompletedProcess(call, 0, "systemd 259\n", "")
        if call[:2] == ("systemd-run", "--help"):
            return subprocess.CompletedProcess(
                call,
                0,
                "--service-type --expand-environment --uid --gid --working-directory --collect",
                "",
            )
        if call[0] == "systemd-run":
            return subprocess.CompletedProcess(call, 1, "", "Access denied")
        if call[0] == "journalctl" and "--list-boots" in call:
            return subprocess.CompletedProcess(call, 0, "0 boot-id\n-1 old-boot-id\n", "")
        if call[0] == "findmnt":
            return subprocess.CompletedProcess(call, 0, "/ ext4\n", "")
        return subprocess.CompletedProcess(call, 0, "", "")

    result = characterize_candidate_b_host(
        state_root=tmp_path / "state",
        evidence_root=tmp_path / "evidence",
        command_runner=command_runner,
        runner_info_call=lambda socket_path: {
            "ok": True,
            "protocol_version": "s2-runner-poc-v1",
            "runner_version": "s2-runner-poc-b1",
            "policy_identity": "a" * 64,
            "peer_uid": 2101,
            "peer_gid": 2101,
        },
        runner_socket=tmp_path / "control.sock",
        persistent_journal_path=persistent_journal,
        kernel_release="7.0.0-native",
        executable_finder=lambda name: f"/usr/bin/{name}",
    )

    assert result.gap is None
    assert set(result.capabilities) == set(REQUIRED_CANDIDATE_B_CAPABILITIES)
    assert all(result.capabilities.values())
    assert result.facts["controller_user"] == "labpilot-controller"
    assert result.facts["controller_uid"] == 2101
    assert result.facts["controller_gid"] == 2101
    assert result.facts["runner_peer_uid"] == 2101
    assert result.facts["runner_peer_gid"] == 2101
    assert result.capabilities["runner_peer_credentials_match"] is True
    assert result.facts["training_uid"] == 2102
    assert result.facts["training_gid"] == 2102
    assert result.facts["runner_policy_identity"] == "a" * 64
    assert any(call[0] == "systemd-run" and "--system" in call for call in calls)


def test_candidate_b_characterize_does_not_require_controller_journal_read_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persistent_journal = tmp_path / "journal"
    persistent_journal.mkdir()

    monkeypatch.setattr(
        "tests.poc.s2_runner.scenario_runner.pwd.getpwuid",
        lambda uid: SimpleNamespace(pw_name="labpilot-controller"),
    )
    monkeypatch.setattr("tests.poc.s2_runner.scenario_runner.os.getuid", lambda: 2101)
    monkeypatch.setattr("tests.poc.s2_runner.scenario_runner.os.getgid", lambda: 2101)
    monkeypatch.setattr(
        "tests.poc.s2_runner.scenario_runner.pwd.getpwnam",
        lambda name: SimpleNamespace(pw_uid=2102 if name == "labpilot-train" else 2101),
    )
    monkeypatch.setattr(
        "tests.poc.s2_runner.scenario_runner.grp.getgrnam",
        lambda name: SimpleNamespace(gr_gid=2102 if name == "labpilot-train" else 2101),
    )

    def command_runner(
        argv: tuple[str, ...] | list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert timeout > 0
        call = tuple(argv)
        if call[:2] == ("systemctl", "--version"):
            return subprocess.CompletedProcess(call, 0, "systemd 259\n", "")
        if call[:2] == ("systemd-run", "--help"):
            return subprocess.CompletedProcess(
                call,
                0,
                "--service-type --expand-environment --uid --gid --working-directory --collect",
                "",
            )
        if call[0] == "systemd-run":
            return subprocess.CompletedProcess(call, 1, "", "Access denied")
        if call[0] == "journalctl" and "--list-boots" in call:
            return subprocess.CompletedProcess(call, 1, "", "Permission denied")
        if call[0] == "findmnt":
            return subprocess.CompletedProcess(call, 0, "/ ext4\n", "")
        return subprocess.CompletedProcess(call, 0, "", "")

    result = characterize_candidate_b_host(
        state_root=tmp_path / "state",
        evidence_root=tmp_path / "evidence",
        command_runner=command_runner,
        runner_info_call=lambda socket_path: {
            "ok": True,
            "protocol_version": "s2-runner-poc-v1",
            "runner_version": "s2-runner-poc-b1",
            "policy_identity": "a" * 64,
            "peer_uid": 2101,
            "peer_gid": 2101,
        },
        runner_socket=tmp_path / "control.sock",
        persistent_journal_path=persistent_journal,
        kernel_release="7.0.0-native",
        executable_finder=lambda name: f"/usr/bin/{name}",
    )

    assert result.gap is None
    assert result.capabilities["persistent_journal_available"] is True
    assert result.facts["journalctl_controller_returncode"] == 1
    assert result.facts["journalctl_controller_stderr"] == "Permission denied"
    assert result.facts["journal_boots"] == []


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




def test_candidate_b_native_spec_matches_fixed_host_policy(tmp_path: Path) -> None:
    arguments = scenario_module.CliArguments(
        state_root=tmp_path / "state",
        evidence_root=tmp_path / "evidence",
        installation_namespace="native-test",
        evidence_stack="systemd+journal",
        command="p2",
    )
    context = scenario_module._ScenarioContext(
        arguments=arguments,
        store=DurableIntentStore(tmp_path / "state" / "intents"),
        evidence=EvidenceLog(tmp_path / "evidence" / "records.jsonl"),
        runner_socket=Path("/run/labpilot-s2-runner-poc/control.sock"),
    )

    _, spec, _, _ = scenario_module._prepare_scenario(
        context, "native-policy", security_evidence=True
    )

    assert spec.runtime_path == "/usr/bin/python3"
    assert spec.argv == (
        "/opt/labpilot-s2-runner-poc/tests/poc/s2_runner/probe.py",
        "--security-evidence",
    )
    assert spec.working_directory == "/var/lib/labpilot-s2-poc/workspace"
    assert Path(spec.start_marker_path).parent == Path("/var/lib/labpilot-s2-poc/markers")
    assert spec.environment == (("LABPILOT_POC_ENV", "native-policy"),)
    assert spec.print_env_keys == ("LABPILOT_POC_ENV",)

def test_candidate_b_dispatch_persists_uncertainty_before_runner_launch(tmp_path: Path) -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    calls: list[str] = []

    def launch_call(
        socket_path: Path, *, x: str, h: str, spec: FrozenLaunchSpec, fault_point: str = "none"
    ) -> dict[str, object]:
        del socket_path, x, h, spec, fault_point
        assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED
        calls.append("launch")
        return {
            "ok": True,
            "kind": ControlOutcomeKind.CREATED.value,
            "execution_identity": X,
            "execution_spec_hash": spec_hash,
            "detail_code": "created",
        }

    outcome = dispatch_once_runner(
        store,
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        ATTEMPT_ID,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        launch_call=launch_call,
    )

    assert outcome.kind is ControlOutcomeKind.CREATED
    assert calls == ["launch"]


def test_candidate_b_ack_response_loss_has_no_automatic_resend(tmp_path: Path) -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    launch_calls = 0

    def launch_call(
        socket_path: Path, *, x: str, h: str, spec: FrozenLaunchSpec, fault_point: str = "none"
    ) -> dict[str, object]:
        nonlocal launch_calls
        del socket_path, x, h, spec, fault_point
        launch_calls += 1
        raise ConnectionError("response lost")

    outcome = dispatch_once_runner(
        store,
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        ATTEMPT_ID,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        launch_call=launch_call,
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
    assert launch_calls == 1
    assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED




def test_candidate_b_ack_before_dispatch_crash_is_unknown_without_resend(tmp_path: Path) -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    calls: list[str] = []

    def launch_call(
        socket_path: Path, *, x: str, h: str, spec: FrozenLaunchSpec, fault_point: str = "none"
    ) -> dict[str, object]:
        del socket_path, x, h, spec
        calls.append(fault_point)
        raise ProtocolError("truncated_frame")

    outcome = dispatch_once_runner(
        store,
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        ATTEMPT_ID,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        fault_point="before_systemd_dispatch",
        launch_call=launch_call,
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
    assert calls == ["before_systemd_dispatch"]
    assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED


def test_candidate_b_ack_after_possible_acceptance_is_unknown_without_resend(
    tmp_path: Path,
) -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    store = DurableIntentStore(tmp_path)
    store.create(_intent(spec, DispatchPhase.FIRST_DISPATCH_AVAILABLE))
    calls: list[str] = []

    def launch_call(
        socket_path: Path, *, x: str, h: str, spec: FrozenLaunchSpec, fault_point: str = "none"
    ) -> dict[str, object]:
        del socket_path, x, h, spec
        calls.append(fault_point)
        raise ProtocolError("truncated_frame")

    outcome = dispatch_once_runner(
        store,
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        ATTEMPT_ID,
        spec,
        spec_hash,
        PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        fault_point="after_systemd_dispatch_before_response",
        launch_call=launch_call,
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
    assert calls == ["after_systemd_dispatch_before_response"]
    assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED






def test_candidate_b_p7_waits_past_loaded_terminal_until_journal_evidence() -> None:
    responses = iter(
        (
            {
                "ok": True,
                "kind": PhysicalObservationKind.TERMINAL_MATCH.value,
                "observed_spec_hash": "a" * 64,
                "evidence_source": "systemd_unit",
            },
            {
                "ok": True,
                "kind": PhysicalObservationKind.TERMINAL_MATCH.value,
                "observed_spec_hash": "a" * 64,
                "evidence_source": "journal",
            },
        )
    )
    calls = 0

    def observe_call(socket_path: Path, *, x: str, h: str) -> dict[str, object]:
        nonlocal calls
        del socket_path, x, h
        calls += 1
        return next(responses)

    observation = scenario_module._wait_runner_terminal_journal(
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        X,
        "a" * 64,
        timeout_seconds=0.2,
        observe_call=observe_call,
        sleep=lambda _: None,
    )

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.evidence_source == "journal"
    assert calls == 2

def test_candidate_b_wait_runner_ready_retries_info_without_launch() -> None:
    calls = 0

    def info_call(socket_path: Path) -> dict[str, object]:
        nonlocal calls
        del socket_path
        calls += 1
        if calls < 3:
            raise ConnectionRefusedError("runner restarting")
        return {"ok": True}

    assert scenario_module._wait_runner_ready(
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        timeout_seconds=0.2,
        info_call=info_call,
        sleep=lambda _: None,
    ) is True
    assert calls == 3

def test_candidate_b_security_record_requires_exact_training_boundary() -> None:
    record = {
        "event": "probe_security",
        "euid": 2102,
        "egid": 2102,
        "cap_eff": "0000000000000000",
        "cap_bnd": "0000000000000000",
        "cap_amb": "0000000000000000",
        "direct_systemd_run": "denied",
        "runner_socket_connect": "denied",
        "policy_write": "denied",
    }

    assert scenario_module._candidate_b_security_record_matches(
        record, expected_uid=2102, expected_gid=2102
    ) is True
    assert scenario_module._candidate_b_security_record_matches(
        {**record, "cap_bnd": "0000000000000001"}, expected_uid=2102, expected_gid=2102
    ) is False


def test_candidate_b_security_record_accepts_nested_marker_security_payload() -> None:
    nested_security = {
        "euid": 2102,
        "egid": 2102,
        "cap_eff": "0000000000000000",
        "cap_bnd": "0000000000000000",
        "cap_amb": "0000000000000000",
        "direct_systemd_run": "denied",
        "runner_socket_connect": "denied",
        "policy_write": "denied",
    }

    assert scenario_module._candidate_b_security_record_matches(
        nested_security, expected_uid=2102, expected_gid=2102
    ) is True
    assert scenario_module._candidate_b_security_record_matches(
        {**nested_security, "event": "not_probe_security"},
        expected_uid=2102,
        expected_gid=2102,
    ) is False


def test_candidate_b_journal_parser_keeps_only_exact_x_h_json_messages() -> None:
    exact = json.dumps(
        {
            "MESSAGE": json.dumps(
                {
                    "event": "probe_security",
                    "execution_identity": X,
                    "execution_spec_hash": "a" * 64,
                    "euid": 2102,
                }
            )
        }
    )
    wrong_h = json.dumps(
        {
            "MESSAGE": json.dumps(
                {
                    "event": "probe_security",
                    "execution_identity": X,
                    "execution_spec_hash": "b" * 64,
                    "euid": 0,
                }
            )
        }
    )

    messages = scenario_module._parse_exact_journal_messages(
        exact + "\n" + wrong_h + "\n", X, "a" * 64
    )

    assert messages == (
        {
            "event": "probe_security",
            "execution_identity": X,
            "execution_spec_hash": "a" * 64,
            "euid": 2102,
        },
    )

def test_candidate_b_recovery_observes_without_launch() -> None:
    observe_calls = 0

    def observe_call(socket_path: Path, *, x: str, h: str) -> dict[str, object]:
        nonlocal observe_calls
        del socket_path
        observe_calls += 1
        return {
            "ok": True,
            "kind": PhysicalObservationKind.ACTIVE_MATCH.value,
            "execution_identity": x,
            "execution_spec_hash": h,
            "observed_spec_hash": h,
            "pid": 1234,
            "invocation_id": "abcd",
            "started_at": None,
            "ended_at": None,
            "exit_code": None,
            "result": None,
            "evidence_source": "systemd_unit",
        }

    observation = recover_runner_without_replay(
        Path("/run/labpilot-s2-runner-poc/control.sock"),
        X,
        "a" * 64,
        observe_call=observe_call,
    )

    assert observation.kind is PhysicalObservationKind.ACTIVE_MATCH
    assert observe_calls == 1
