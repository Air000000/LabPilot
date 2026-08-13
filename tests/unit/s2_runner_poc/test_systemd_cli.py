import json
import subprocess
from collections.abc import Sequence
from typing import cast
from uuid import UUID

from tests.poc.s2_runner.contract import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    ControlOutcomeKind,
    FrozenLaunchSpec,
    PhysicalObservationKind,
    PositiveCreationEvidence,
    execution_spec_hash,
    supervisor_execution_identity,
    systemd_unit_name,
)
from tests.poc.s2_runner.systemd_cli import CommandRunner, SystemdCli

X = supervisor_execution_identity(
    "native-installation", UUID("12345678-1234-5678-1234-567812345678")
)


def _spec(**changes: object) -> FrozenLaunchSpec:
    values: dict[str, object] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "runtime_path": "/opt/runtime/python3.12",
        "argv": ("-m", "tests.poc.s2_runner.probe"),
        "working_directory": "/srv/labpilot",
        "environment": (("VISIBLE_B", "two"), ("VISIBLE_A", "one")),
        "credential_slots": (),
        "execution_user": "labpilot-train",
        "start_marker_path": "/var/lib/labpilot/start.jsonl",
        "sleep_seconds": 5.0,
        "exit_code": 17,
        "child_sleep_seconds": 2.0,
        "print_env_keys": ("VISIBLE_B", "VISIBLE_A"),
        "collect_after_exit": True,
    }
    values.update(changes)
    return FrozenLaunchSpec(**values)  # type: ignore[arg-type]


def _result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class ScriptedRunner:
    def __init__(self, *results: subprocess.CompletedProcess[str] | BaseException) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
        assert not isinstance(argv, str)
        assert timeout > 0
        self.calls.append(tuple(argv))
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _absent() -> subprocess.CompletedProcess[str]:
    return _result(stdout="LoadState=not-found\n")


def _unit_state(
    *,
    spec_hash: str,
    active_state: str = "active",
    sub_state: str = "running",
    result: str = "success",
    exit_code: int = 0,
) -> subprocess.CompletedProcess[str]:
    return _result(
        stdout=(
            "LoadState=loaded\n"
            f"ActiveState={active_state}\n"
            f"SubState={sub_state}\n"
            f"Description=labpilot-poc:{spec_hash}\n"
            f"Result={result}\n"
            f"MainPID={'4312' if active_state == 'active' else '0'}\n"
            "InvocationID=0123456789abcdef\n"
            f"ExecMainStatus={exit_code}\n"
            "ActiveEnterTimestamp=Wed 2026-08-13 10:00:00 CST\n"
            "InactiveEnterTimestamp=Wed 2026-08-13 10:00:10 CST\n"
        )
    )


def _cli(runner: ScriptedRunner, *, journal: bool = False) -> SystemdCli:
    return SystemdCli(command_runner=cast(CommandRunner, runner), use_journal_evidence=journal)


def test_hash_mismatch_performs_zero_commands() -> None:
    runner = ScriptedRunner()

    try:
        _cli(runner).launch(
            execution_identity=X,
            execution_spec_hash="0" * 64,
            spec=_spec(),
            creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
        )
    except ValueError as error:
        assert str(error) == "execution_spec_hash_mismatch"
    else:
        raise AssertionError("hash mismatch accepted")

    assert runner.calls == []


def test_launch_requires_positive_creation_evidence() -> None:
    runner = ScriptedRunner()
    spec = _spec()

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=cast(PositiveCreationEvidence, "not-proof"),
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_REJECTED
    assert runner.calls == []


def test_launch_uses_only_hashed_runtime_path() -> None:
    runner = ScriptedRunner(_absent(), _result())
    spec = _spec(runtime_path="/frozen/runtime/python")

    _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    launch = runner.calls[-1]
    command_start = launch.index("--") + 1
    assert launch[command_start] == "/frozen/runtime/python"
    assert "/usr/bin/python" not in launch


def test_launch_uses_system_manager_exec_profile_and_structured_argv() -> None:
    runner = ScriptedRunner(_absent(), _result())
    spec = _spec()
    spec_hash = execution_spec_hash(spec)

    _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=spec_hash,
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    launch = runner.calls[-1]
    for expected in (
        "--system",
        "--no-ask-password",
        f"--unit={systemd_unit_name(X)}",
        f"--description=labpilot-poc:{spec_hash}",
        "--service-type=exec",
        "--expand-environment=no",
        "--property=Restart=no",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "--property=KillMode=control-group",
        "--uid=labpilot-train",
        "--working-directory=/srv/labpilot",
        "--collect",
    ):
        assert expected in launch


def test_launch_does_not_use_user_manager_or_shell() -> None:
    runner = ScriptedRunner(_absent(), _result())
    spec = _spec()

    _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert all("--user" not in call for call in runner.calls)
    assert all(not isinstance(call, str) for call in runner.calls)


def test_launch_uses_only_non_secret_frozen_environment() -> None:
    runner = ScriptedRunner(_absent(), _result())
    spec = _spec()

    _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    launch = runner.calls[-1]
    assert "--setenv=VISIBLE_A=one" in launch
    assert "--setenv=VISIBLE_B=two" in launch
    assert not any(value.startswith("--setenv=HOME=") for value in launch)


def test_launch_sets_exact_h_description_metadata() -> None:
    runner = ScriptedRunner(_absent(), _result())
    spec = _spec()
    spec_hash = execution_spec_hash(spec)

    _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=spec_hash,
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert f"--description=labpilot-poc:{spec_hash}" in runner.calls[-1]


def test_active_matching_x_and_description_h_becomes_active_match() -> None:
    spec_hash = execution_spec_hash(_spec())
    observation = _cli(ScriptedRunner(_unit_state(spec_hash=spec_hash))).observe(
        execution_identity=X, execution_spec_hash=spec_hash
    )

    assert observation.kind is PhysicalObservationKind.ACTIVE_MATCH
    assert observation.observed_spec_hash == spec_hash
    assert observation.pid == 4312


def test_same_x_different_description_h_becomes_identity_conflict() -> None:
    spec_hash = execution_spec_hash(_spec())
    observation = _cli(ScriptedRunner(_unit_state(spec_hash="b" * 64))).observe(
        execution_identity=X, execution_spec_hash=spec_hash
    )

    assert observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT
    assert observation.observed_spec_hash == "b" * 64


def test_preflight_present_observes_and_never_creates() -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    runner = ScriptedRunner(_result(stdout="LoadState=loaded\n"), _unit_state(spec_hash=spec_hash))

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=spec_hash,
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.EXISTING_MATCH
    assert not any(call[0] == "systemd-run" for call in runner.calls)


def test_preflight_confirmed_absent_with_positive_evidence_may_create() -> None:
    spec = _spec()
    runner = ScriptedRunner(_absent(), _result())

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=PositiveCreationEvidence.NEVER_ACCEPTED_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.CREATED
    assert sum(call[0] == "systemd-run" for call in runner.calls) == 1


def test_preflight_confirmed_absent_without_positive_evidence_never_creates() -> None:
    runner = ScriptedRunner()
    spec = _spec()

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=cast(PositiveCreationEvidence, object()),
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_REJECTED
    assert runner.calls == []


def test_preflight_lookup_unknown_never_creates() -> None:
    spec = _spec()
    runner = ScriptedRunner(_result(returncode=1, stderr="dbus unavailable"))

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
    assert not any(call[0] == "systemd-run" for call in runner.calls)


def test_not_found_without_journal_is_unknown() -> None:
    spec_hash = execution_spec_hash(_spec())
    observation = _cli(ScriptedRunner(_absent())).observe(
        execution_identity=X, execution_spec_hash=spec_hash
    )

    assert observation.kind is PhysicalObservationKind.UNKNOWN


def test_dispatch_nonzero_observes_before_returning() -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    runner = ScriptedRunner(_absent(), _result(returncode=1), _unit_state(spec_hash=spec_hash))

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=spec_hash,
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.EXISTING_MATCH
    assert [call[0] for call in runner.calls] == ["systemctl", "systemd-run", "systemctl"]


def test_concurrent_race_loser_reconciles_existing_x_without_fallback_unit() -> None:
    spec = _spec()
    spec_hash = execution_spec_hash(spec)
    runner = ScriptedRunner(_absent(), _result(returncode=1), _unit_state(spec_hash=spec_hash))

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=spec_hash,
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.EXISTING_MATCH
    unit_arguments = [part for call in runner.calls for part in call if part.startswith("--unit=")]
    assert set(unit_arguments) == {f"--unit={systemd_unit_name(X)}"}


def test_journal_exact_x_h_terminal_record_can_be_terminal_match() -> None:
    spec_hash = execution_spec_hash(_spec())
    record = json.dumps(
        {
            "MESSAGE": json.dumps(
                {
                    "event": "probe_terminal",
                    "execution_identity": X,
                    "execution_spec_hash": spec_hash,
                    "exit_code": 17,
                    "timestamp": "2026-08-13T02:00:10+00:00",
                }
            )
        }
    )
    runner = ScriptedRunner(_absent(), _result(stdout=f"{record}\n"))
    observation = _cli(runner, journal=True).observe(
        execution_identity=X,
        execution_spec_hash=spec_hash,
    )

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.exit_code == 17
    assert observation.evidence_source == "journal"


def test_journal_different_h_is_identity_conflict() -> None:
    spec_hash = execution_spec_hash(_spec())
    record = json.dumps(
        {
            "MESSAGE": json.dumps(
                {
                    "event": "probe_terminal",
                    "execution_identity": X,
                    "execution_spec_hash": "b" * 64,
                    "exit_code": 0,
                }
            )
        }
    )
    runner = ScriptedRunner(_absent(), _result(stdout=f"{record}\n"))
    observation = _cli(runner, journal=True).observe(
        execution_identity=X,
        execution_spec_hash=spec_hash,
    )

    assert observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT


def test_generic_launch_error_is_control_unknown_not_non_acceptance() -> None:
    spec = _spec()
    runner = ScriptedRunner(_absent(), TimeoutError("injected timeout"), _absent())

    outcome = _cli(runner).launch(
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        creation_evidence=PositiveCreationEvidence.FIRST_DISPATCH_PROVEN,
    )

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN


def test_stop_active_match_issues_control_and_returns_control_accepted() -> None:
    spec_hash = execution_spec_hash(_spec())
    runner = ScriptedRunner(_unit_state(spec_hash=spec_hash), _result())

    outcome = _cli(runner).stop(execution_identity=X, execution_spec_hash=spec_hash, force=False)

    assert outcome.kind is ControlOutcomeKind.CONTROL_ACCEPTED
    assert runner.calls[-1] == (
        "systemctl",
        "--system",
        "--no-ask-password",
        "stop",
        systemd_unit_name(X),
    )


def test_stop_terminal_match_is_noop() -> None:
    spec_hash = execution_spec_hash(_spec())
    runner = ScriptedRunner(
        _unit_state(spec_hash=spec_hash, active_state="inactive", sub_state="dead", exit_code=17)
    )

    outcome = _cli(runner).stop(execution_identity=X, execution_spec_hash=spec_hash, force=False)

    assert outcome.kind is ControlOutcomeKind.TERMINAL_MATCH
    assert len(runner.calls) == 1


def test_stop_identity_conflict_never_issues_destructive_control() -> None:
    spec_hash = execution_spec_hash(_spec())
    runner = ScriptedRunner(_unit_state(spec_hash="b" * 64))

    outcome = _cli(runner).stop(execution_identity=X, execution_spec_hash=spec_hash, force=True)

    assert outcome.kind is ControlOutcomeKind.IDENTITY_CONFLICT
    assert len(runner.calls) == 1


def test_stop_unknown_never_issues_destructive_control() -> None:
    spec_hash = execution_spec_hash(_spec())
    runner = ScriptedRunner(_absent())

    outcome = _cli(runner).stop(execution_identity=X, execution_spec_hash=spec_hash, force=False)

    assert outcome.kind is ControlOutcomeKind.CONTROL_UNKNOWN
    assert len(runner.calls) == 1
