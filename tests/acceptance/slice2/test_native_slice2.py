from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import inspect
import json
import multiprocessing
import queue
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest

EXPECTED_SCENARIOS = {f"F{number:02d}" for number in range(1, 23)}
EXPECTED_V2_MATRIX = tuple(f"V2-A{number}" for number in range(1, 21))


def _native_config(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "version": 1,
        "dedicated_candidate_b_host": True,
        "expected_hostname": "candidate-b-vm",
        "repository_root": "/srv/labpilot/repository",
        "workspace_root": "/srv/labpilot/workspaces",
        "runner_policy_source": "/root/labpilot/runner-policy.json",
        "evidence_root": "/srv/labpilot/evidence",
        "dataset_root": "/srv/labpilot/datasets",
        "database_url_env": "LABPILOT_SLICE2_ACCEPTANCE_DATABASE_URL",
        "api_base_url": "http://127.0.0.1:8000",
        "compose_project": "labpilot-task5",
        "python_profile": "python-3.12",
    }
    value.update(changes)
    return value


def _runner_info(
    *, runner_version: str, launch_profile_version: str, policy_identity: str
) -> dict[str, object]:
    return {
        "version": "labpilot-runner:v1",
        "operation": "INFO",
        "outcome": "ok",
        "result": {
            "runner_version": runner_version,
            "policy_identity": policy_identity,
            "workspace_root": "/srv/labpilot/workspaces",
            "execution_user": "labpilot-train",
            "execution_group": "labpilot-train",
            "launch_profile_version": launch_profile_version,
            "allowed_local_dataset_roots": ["/srv/labpilot/datasets"],
            "max_runtime_seconds": 3600,
            "boot_id": "e" * 32,
            "readiness_reason": "ready",
            "runtime_profiles": [
                {
                    "python_profile": "python-3.12",
                    "runtime_path": "/usr/bin/python3",
                    "environment": {"PATH": "/usr/bin:/bin"},
                }
            ],
        },
    }


def _v2_a15_execution_hash(tested_sha: str) -> str:
    return hashlib.sha256(
        f"labpilot-v2-matrix\0{tested_sha}\0V2-A15\0h".encode()
    ).hexdigest()


def test_scenario_registry_is_exact_and_unknown_names_fail_closed() -> None:
    from tests.acceptance.slice2.scenarios import (
        SCENARIOS,
        ScenarioSelectionError,
        select_scenarios,
    )

    assert set(SCENARIOS) == EXPECTED_SCENARIOS
    assert [scenario.name for scenario in select_scenarios("F01,F22")] == [
        "F01",
        "F22",
    ]
    with pytest.raises(ScenarioSelectionError, match="unknown scenario"):
        select_scenarios("F01,F23")


def test_v2_profile_matrix_registry_is_exact_closed_and_not_formal() -> None:
    from tests.acceptance.slice2.candidate_b_v2_fixtures import (
        V2_MATRIX_ROWS,
        V2MatrixIsolation,
        select_v2_profile_matrix,
    )
    from tests.acceptance.slice2.scenarios import SCENARIOS

    assert tuple(V2_MATRIX_ROWS) == EXPECTED_V2_MATRIX
    assert select_v2_profile_matrix("V2-A1-V2-A20") == EXPECTED_V2_MATRIX
    assert set(V2_MATRIX_ROWS).isdisjoint(SCENARIOS)
    assert {V2_MATRIX_ROWS[row] for row in ("V2-A1", "V2-A15", "V2-A20")} == {
        V2MatrixIsolation.NON_DESTRUCTIVE_FINAL_STACK_CHECK
    }
    assert all(
        isolation
        is V2MatrixIsolation.ISOLATED_TEST_ADMIN_FIXTURE
        for row, isolation in V2_MATRIX_ROWS.items()
        if row not in {"V2-A1", "V2-A15", "V2-A20"}
    )
    for selection in (
        "V2-A1",
        "V2-A1,V2-A2",
        "V2-A1-V2-A19",
        "V2-A1-V2-A20,V2-A20",
        "F01",
    ):
        with pytest.raises(ValueError, match="profile matrix"):
            select_v2_profile_matrix(selection)


def test_v2_a11_uses_real_missing_receipt_unit_without_reporter_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2.native_preflight import CommandResult

    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        return CommandResult(tuple(argv), b"", b"")

    monkeypatch.setattr(fixtures, "run_command", run)
    lease = fixtures.create_missing_receipt_fixture(_native_config(), "a" * 40)

    launch = next(command for command in calls if command[0] == "systemd-run")
    assert lease.row == "V2-A11"
    assert lease.units == (
        next(value[7:] for value in launch if value.startswith("--unit=")) + ".service",
    )
    assert "ExecStopPost" not in " ".join(launch)
    assert "labpilot-terminal-receipt-v1.py" not in " ".join(launch)
    assert "--property=Description=labpilot:" not in " ".join(launch)
    assert any(value.startswith("--description=labpilot:") for value in launch)


def test_v2_a12_uses_real_root_post_stop_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2.native_preflight import CommandResult

    calls: list[tuple[str, ...]] = []
    installed: list[tuple[Path, bytes]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        return CommandResult(tuple(argv), b"", b"")

    monkeypatch.setattr(fixtures, "run_command", run)
    monkeypatch.setattr(
        fixtures,
        "_install_fixed_proof_asset",
        lambda path, content: installed.append((path, content)),
    )

    malformed = fixtures.create_trusted_receipt_fixture(
        _native_config(), "a" * 40, "malformed"
    )
    duplicate = fixtures.create_trusted_receipt_fixture(
        _native_config(), "a" * 40, "duplicate"
    )

    launches = [command for command in calls if command[0] == "systemd-run"]
    assert {malformed.row, duplicate.row} == {"V2-A12"}
    assert len(installed) == 2
    assert len(launches) == 2
    assert all("ExecStopPost=-+/usr/bin/python3 -I -B" in " ".join(argv) for argv in launches)
    assert all("journalctl" not in argv for argv in launches)
    assert all("_SYSTEMD_UNIT" not in " ".join(argv) for argv in launches)


def test_v2_a15_is_prepared_before_v2_install_without_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2.native_preflight import CommandResult

    calls: list[tuple[str, ...]] = []
    execution_hash = _v2_a15_execution_hash("a" * 40)

    def run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        if argv[0] == "systemctl" and "show" in argv:
            exec_start = "3600" if argv[2] == "labpilot-runner.service" else "21600"
            output = (
                "ActiveState=active\n"
                "MainPID=4242\n"
                "InvocationID=11111111111111111111111111111111\n"
                f"Description=labpilot:{execution_hash}\n"
                "Type=exec\n"
                "User=labpilot-train\n"
                "Group=labpilot-train\n"
                "Restart=no\n"
                "KillMode=control-group\n"
                "NoNewPrivileges=yes\n"
                "AmbientCapabilities=\n"
                "CapabilityBoundingSet=\n"
                "StandardOutput=journal\n"
                "StandardError=journal\n"
                "Environment=LABPILOT_PROFILE_VERSION=candidate-b-v1\n"
                f"ExecStart=/usr/bin/sleep {exec_start}\n"
            )
            return CommandResult(
                tuple(argv),
                output.encode(),
                b"",
            )
        if argv[0] == "journalctl":
            return CommandResult(
                tuple(argv),
                b'{"_BOOT_ID":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","JOB_RESULT":"done","JOB_TYPE":"start"}\n',
                b"",
            )
        return CommandResult(tuple(argv), b"", b"")

    monkeypatch.setattr(fixtures, "run_command", run)
    monkeypatch.setattr(
        fixtures,
        "_runner_request",
        lambda *_args: _runner_info(
            runner_version="candidate-b-v1",
            launch_profile_version="candidate-b-v1",
            policy_identity="d" * 64,
        ),
    )
    lease = fixtures.prepare_v1_compatibility_before_v2_install(
        _native_config(evidence_root=str(tmp_path)), "a" * 40
    )

    assert lease.row == "V2-A15"
    assert any(command[0] == "systemd-run" for command in calls)
    launch = next(command for command in calls if command[0] == "systemd-run")
    assert launch[-2:] == ("/usr/bin/sleep", "21600")
    encoded = "\n".join(" ".join(command) for command in calls)
    assert "ExecStopPost" not in encoded
    assert "install_labpilot_runner_host.sh" not in encoded
    assert "candidate-b-v1" in encoded


def test_v2_a15_preparation_seals_real_v1_lifecycle_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2.native_preflight import CommandResult

    calls: list[tuple[str, ...]] = []
    boot_id = "b" * 32
    invocation_id = "c" * 32
    execution_hash = _v2_a15_execution_hash("a" * 40)

    def run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        if argv[:2] == ("systemctl", "show") and "--property=LoadState" in argv:
            output = "LoadState=not-found\n"
        elif argv[:2] == ("systemctl", "show"):
            exec_start = "3600" if argv[2] == "labpilot-runner.service" else "21600"
            output = (
                "ActiveState=active\n"
                "MainPID=4242\n"
                f"InvocationID={invocation_id}\n"
                f"Description=labpilot:{execution_hash}\n"
                "Type=exec\n"
                "User=labpilot-train\n"
                "Group=labpilot-train\n"
                "Restart=no\n"
                "KillMode=control-group\n"
                "NoNewPrivileges=yes\n"
                "AmbientCapabilities=\n"
                "CapabilityBoundingSet=\n"
                "StandardOutput=journal\n"
                "StandardError=journal\n"
                "Environment=LABPILOT_PROFILE_VERSION=candidate-b-v1\n"
                f"ExecStart=/usr/bin/sleep {exec_start}\n"
            )
        elif argv[0] == "journalctl":
            output = json.dumps(
                {
                    "_BOOT_ID": boot_id,
                    "JOB_RESULT": "done",
                    "JOB_TYPE": "start",
                }
            ) + "\n"
        else:
            output = ""
        return CommandResult(tuple(argv), output.encode(), b"")

    monkeypatch.setattr(fixtures, "run_command", run)
    monkeypatch.setattr(
        fixtures,
        "_runner_request",
        lambda *_args: _runner_info(
            runner_version="candidate-b-v1",
            launch_profile_version="candidate-b-v1",
            policy_identity="d" * 64,
        ),
    )

    lease = fixtures.prepare_v1_compatibility_before_v2_install(
        _native_config(evidence_root=str(tmp_path)), "a" * 40
    )

    prepared = json.loads((lease.proof_root / "v2-a15-prepared.json").read_text())
    assert set(prepared) == {
        "boot_id",
        "execution_identity",
        "execution_spec_hash",
        "invocation_id",
        "main_pid",
        "pre_v2_installation_identity",
        "profile",
        "start_cardinality",
        "tested_sha",
        "unit",
        "unit_properties",
    }
    assert prepared["tested_sha"] == "a" * 40
    assert prepared["main_pid"] == 4242
    assert prepared["invocation_id"] == invocation_id
    assert prepared["boot_id"] == boot_id
    assert prepared["start_cardinality"] == 1
    assert prepared["unit_properties"] == {
        "AmbientCapabilities": "",
        "CapabilityBoundingSet": "",
        "Description": f"labpilot:{execution_hash}",
        "Environment": "LABPILOT_PROFILE_VERSION=candidate-b-v1",
        "ExecStart": "/usr/bin/sleep 21600",
        "Group": "labpilot-train",
        "KillMode": "control-group",
        "NoNewPrivileges": "yes",
        "Restart": "no",
        "StandardError": "journal",
        "StandardOutput": "journal",
        "Type": "exec",
        "User": "labpilot-train",
    }
    assert prepared["pre_v2_installation_identity"] == {
        "runner_info": {
            "boot_id": "e" * 32,
            "launch_profile_version": "candidate-b-v1",
            "policy_identity": "d" * 64,
            "readiness_reason": "ready",
            "runner_version": "candidate-b-v1",
        },
        "runner_service": {
            "ExecStart": "/usr/bin/sleep 3600",
            "InvocationID": invocation_id,
        },
    }
    assert any(command[0] == "journalctl" for command in calls)


def test_v2_a15_post_install_verification_has_no_phase_i_compose_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2.native_preflight import CommandResult

    unit = "labpilot-v1-" + "a" * 64 + "-" + "b" * 32 + ".service"
    prepared = {
        "boot_id": "b" * 32,
        "execution_identity": unit.removesuffix(".service"),
        "execution_spec_hash": "c" * 64,
        "invocation_id": "d" * 32,
        "main_pid": 4242,
        "pre_v2_installation_identity": {
            "runner_info": {
                "boot_id": "e" * 32,
                "launch_profile_version": "candidate-b-v1",
                "policy_identity": "e" * 64,
                "readiness_reason": "ready",
                "runner_version": "candidate-b-v1",
            },
            "runner_service": {
                "ExecStart": "/usr/bin/sleep 3600",
                "InvocationID": "d" * 32,
            },
        },
        "profile": "candidate-b-v1",
        "start_cardinality": 1,
        "tested_sha": "a" * 40,
        "unit": unit,
        "unit_properties": {
            "AmbientCapabilities": "",
            "CapabilityBoundingSet": "",
            "Description": "labpilot:" + "c" * 64,
            "Environment": "LABPILOT_PROFILE_VERSION=candidate-b-v1",
            "ExecStart": "/usr/bin/sleep 21600",
            "Group": "labpilot-train",
            "KillMode": "control-group",
            "NoNewPrivileges": "yes",
            "Restart": "no",
            "StandardError": "journal",
            "StandardOutput": "journal",
            "Type": "exec",
            "User": "labpilot-train",
        },
    }
    prepared_path = tmp_path / "v2-a15-prepared.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        if argv[0] == "docker":
            raise AssertionError("Phase H must not require a Compose container")
        if argv[:2] == ("git", "-C") and argv[-2:] == ("rev-parse", "HEAD"):
            output = "a" * 40 + "\n"
        elif argv[:2] == ("git", "-C") and argv[-2:] == ("status", "--short"):
            output = ""
        elif argv[:2] == ("systemctl", "show"):
            output = (
                "ActiveState=active\n"
                "ExecStart=/opt/labpilot-runner/app/.venv/bin/python\n"
                "InvocationID="
                + "f" * 32
                + "\n"
            )
        elif argv[0] == "journalctl":
            output = json.dumps({"JOB_RESULT": "done", "JOB_TYPE": "start"}) + "\n"
        else:
            output = ""
        return CommandResult(tuple(argv), output.encode(), b"")

    class Manifest:
        reporter_sha256 = "f" * 64

    class Policy:
        policy_identity = "e" * 64
        runtime_profiles = (type("Profile", (), {"name": "python-3.12"})(),)

    monkeypatch.setattr(fixtures, "run_command", run)
    monkeypatch.setattr(fixtures, "validate_candidate_b_v2_deployment", Manifest)
    monkeypatch.setattr(fixtures, "load_runner_policy", lambda _path: Policy())
    observations = iter(("active_match", "terminal_match"))

    def runner_request(_row: str, operation: str, *_args: str) -> dict[str, object]:
        if operation == "INFO":
            return _runner_info(
                runner_version="candidate-b-v2",
                launch_profile_version="candidate-b-v2",
                policy_identity="e" * 64,
            )
        if operation == "STOP":
            return {"outcome": "ok", "result": {}}
        return {"outcome": "ok", "result": {"kind": next(observations)}}

    monkeypatch.setattr(fixtures, "_runner_request", runner_request)

    verified = fixtures.verify_v1_compatibility_after_v2_install(
        _native_config(repository_root="/srv/labpilot/repository"),
        fixtures.V2FixtureLease("V2-A15", (unit,), tmp_path, None, False),
    )

    assert verified["start_cardinality"] == 1
    assert (tmp_path / "v2-a15-verified.json").is_file()
    assert all(command[0] != "docker" for command in calls)


def test_v2_a18_uses_mount_namespace_and_fixed_installer_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2.native_preflight import CommandResult

    calls: list[tuple[str, ...]] = []
    hashes = iter(("a" * 64, "a" * 64))
    monkeypatch.setattr(fixtures, "_host_reporter_sha256", lambda: next(hashes))
    monkeypatch.setattr(fixtures, "_prepare_a18_fixture", lambda *args: None)

    def run(argv: tuple[str, ...], **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        return CommandResult(
            tuple(argv),
            b'{"host_reporter_unchanged":true,"validator_failed_closed":true}\n',
            b"",
        )

    monkeypatch.setattr(fixtures, "run_command", run)
    result = fixtures.run_isolated_installer_drift_fixture(
        _native_config(
            repository_root=str(Path(__file__).parents[3]),
            evidence_root=str(tmp_path),
        ),
        "a" * 40,
    )

    assert result["validator_failed_closed"] is True
    assert calls == [
        (
            "bash",
            str(
                Path(__file__).parents[3]
                / "tests/acceptance/slice2/fixtures/a18_installer_drift_probe.sh"
            ),
            str(tmp_path / "candidate-b-v2/a18-aaaaaaaaaaaa"),
            str(Path(__file__).parents[3]),
        )
    ]


@pytest.mark.parametrize("_profile_matrix_keyword", [None], ids=["profile_matrix"])
def test_matrix_cleanup_requires_equal_final_stack_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _profile_matrix_keyword: None,
) -> None:
    del _profile_matrix_keyword
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2 import native_campaign

    base = fixtures.FinalStackFingerprint(
        tested_sha="a" * 40,
        reporter_sha256="b" * 64,
        policy_identity="c" * 64,
        runner_exec_start="fixed",
        runner_active_state="active",
        runner_invocation_id="d" * 32,
        compose_container_ids=(("api", "container"),),
        compose_image_ids=(("api", "image"),),
    )
    changed = fixtures.FinalStackFingerprint(
        tested_sha=base.tested_sha,
        reporter_sha256=base.reporter_sha256,
        policy_identity=base.policy_identity,
        runner_exec_start=base.runner_exec_start,
        runner_active_state=base.runner_active_state,
        runner_invocation_id="e" * 32,
        compose_container_ids=base.compose_container_ids,
        compose_image_ids=base.compose_image_ids,
    )
    fingerprints = iter((base, base, changed))
    cleaned: list[fixtures.V2FixtureLease] = []
    lease = fixtures.V2FixtureLease(
        "V2-A2", ("labpilot-v2-a2.service",), None, None, False
    )
    monkeypatch.setattr(
        native_campaign,
        "capture_final_stack_fingerprint",
        lambda config: next(fingerprints),
    )
    monkeypatch.setattr(
        native_campaign,
        "select_v2_profile_matrix",
        lambda selection: ("V2-A2",),
    )
    monkeypatch.setattr(
        native_campaign,
        "execute_candidate_b_v2_row",
        lambda row, config, sha: ({"row": row}, lease),
    )
    monkeypatch.setattr(
        native_campaign,
        "cleanup_candidate_b_v2_fixture",
        lambda config, value: cleaned.append(value),
    )

    with pytest.raises(RuntimeError, match="fingerprint"):
        native_campaign.run_profile_matrix(
            "V2-A1-V2-A20",
            _native_config(evidence_root=str(tmp_path)),
        )

    assert cleaned == [lease]


def test_profile_matrix_evidence_binds_the_frozen_tested_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
    from tests.acceptance.slice2 import native_campaign

    fingerprint = fixtures.FinalStackFingerprint(
        tested_sha="a" * 40,
        reporter_sha256="b" * 64,
        policy_identity="c" * 64,
        runner_exec_start="fixed",
        runner_active_state="active",
        runner_invocation_id="d" * 32,
        compose_container_ids=(("api", "container"),),
        compose_image_ids=(("api", "image"),),
    )
    monkeypatch.setattr(
        native_campaign,
        "select_v2_profile_matrix",
        lambda selection: ("V2-A1",),
    )
    monkeypatch.setattr(
        native_campaign,
        "capture_final_stack_fingerprint",
        lambda config: fingerprint,
    )
    monkeypatch.setattr(
        native_campaign,
        "execute_candidate_b_v2_row",
        lambda row, config, sha: ({"tested_sha": sha}, None),
    )

    native_campaign.run_profile_matrix(
        "V2-A1-V2-A20", _native_config(evidence_root=str(tmp_path))
    )

    evidence = json.loads(
        (
            tmp_path
            / "candidate-b-v2/profile-matrix"
            / ("a" * 40)
            / "V2-A1/scenario.json"
        ).read_text()
    )
    assert evidence["row"] == "V2-A1"
    assert evidence["tested_sha"] == "a" * 40
    assert evidence["evidence"] == {"tested_sha": "a" * 40}


def test_all_scenarios_have_concrete_distinct_driver_bindings() -> None:
    from tests.acceptance.slice2.scenarios import SCENARIO_DRIVERS

    assert set(SCENARIO_DRIVERS) == EXPECTED_SCENARIOS
    assert {driver.__name__ for driver in SCENARIO_DRIVERS.values()} == {
        f"run_f{number:02d}" for number in range(1, 23)
    }


def test_scenario_dispatch_executes_the_bound_procedure() -> None:
    from tests.acceptance.slice2.scenarios import dispatch_native_scenario

    observed: list[object] = []

    class Runtime:
        def execute(self, procedure: object) -> None:
            observed.append(procedure)

    for number in range(1, 23):
        dispatch_native_scenario(
            f"F{number:02d}", Runtime(), phase="prepare" if number == 18 else "run"
        )

    assert [procedure.scenario for procedure in observed] == [
        f"F{number:02d}" for number in range(1, 23)
    ]
    assert all(procedure.operations for procedure in observed)
    assert all(procedure.obligations for procedure in observed)
    assert all(type(procedure.operations) is tuple for procedure in observed)
    assert all(type(procedure.obligations) is tuple for procedure in observed)
    assert all(procedure.phase in {"run", "prepare", "verify"} for procedure in observed)


def test_f18_driver_binds_prepare_and_verify_but_rejects_run() -> None:
    from tests.acceptance.slice2.scenarios import dispatch_native_scenario

    phases: list[str] = []

    class Runtime:
        def execute(self, procedure: object) -> None:
            phases.append(procedure.phase)

    dispatch_native_scenario("F18", Runtime(), phase="prepare")
    dispatch_native_scenario("F18", Runtime(), phase="verify")
    assert phases == ["prepare", "verify"]
    with pytest.raises(ValueError, match="F18"):
        dispatch_native_scenario("F18", Runtime(), phase="run")


def test_native_scenario_placeholder_is_removed() -> None:
    from tests.acceptance.slice2 import scenarios

    assert "native scenario drivers are not bound" not in inspect.getsource(scenarios)


def test_native_worker_registry_is_exact_and_unknown_operation_fails_closed() -> None:
    from tests.acceptance.slice2.native_worker import (
        WORKER_OPERATIONS,
        NativeWorkerSelectionError,
        select_worker_operation,
    )
    from tests.acceptance.slice2.scenarios import scenario_operation_names

    expected = {
        (scenario, operation)
        for scenario in EXPECTED_SCENARIOS
        for operation in ("initialize", "snapshot", *scenario_operation_names(scenario))
    }
    assert set(WORKER_OPERATIONS) == expected
    calls: list[object] = []
    with pytest.raises(NativeWorkerSelectionError):
        select_worker_operation("F01", "shell", before_select=calls.append)
    assert calls == []


def test_native_worker_fresh_interpreter_registers_complete_model_metadata() -> None:
    code = (
        "import json\n"
        "import tests.acceptance.slice2.native_worker\n"
        "from labpilot.db.base import Base\n"
        "from labpilot.execution.models import BatchRow\n"
        "required = {'batches', 'experiment_versions', 'projects'}\n"
        "targets = sorted(f'{fk.column.table.name}.{fk.column.name}' "
        "for fk in BatchRow.__table__.foreign_keys)\n"
        "print(json.dumps({'foreign_key_targets': targets, "
        "'required_tables': sorted(required & set(Base.metadata.tables))}))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "foreign_key_targets": [
            "experiment_versions.experiment_version_id",
            "projects.project_id",
        ],
        "required_tables": ["batches", "experiment_versions", "projects"],
    }


def test_native_worker_fixture_profiles_cover_capacity_child_and_timeout() -> None:
    from tests.acceptance.slice2.native_worker import fixture_profile

    assert fixture_profile("F03").trial_count == 6
    assert fixture_profile("F11").spawn_child is True
    timeout = fixture_profile("F21")
    assert timeout.timeout_seconds == 1
    assert timeout.duration_seconds > timeout.timeout_seconds


def test_native_worker_state_root_is_exact_and_cannot_escape() -> None:
    from tests.acceptance.slice2.native_worker import worker_state_root

    root = Path("/srv/labpilot/attempts")
    assert worker_state_root(root, "a" * 40, "F01") == (
        root / ".task5-acceptance" / ("a" * 40) / "F01"
    )
    with pytest.raises(ValueError):
        worker_state_root(Path("relative"), "a" * 40, "F01")
    with pytest.raises(ValueError):
        worker_state_root(root, "A" * 40, "F01")


def test_workload_oracle_flock_symmetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2 import native_worker
    from tests.acceptance.slice2.native_worker import NativeWorkerRuntime

    workspace = tmp_path / "attempt"
    output = workspace / "runtime" / "output"
    output.mkdir(parents=True)
    path = output / "workload-test-oracle.json"
    path.write_bytes(b'{"source_sha256":"old","start_count":1}\n')
    replacement = {"source_sha256": "b" * 64, "start_count": 1}
    encoded = json.dumps(replacement, separators=(",", ":"), sort_keys=True).encode() + b"\n"

    context = multiprocessing.get_context("fork")
    writer_truncated = context.Event()
    writer_release = context.Event()

    def write_replacement() -> None:
        import fcntl as writer_fcntl

        with path.open("r+b") as handle:
            writer_fcntl.flock(handle.fileno(), writer_fcntl.LOCK_EX)
            handle.truncate(0)
            handle.flush()
            writer_truncated.set()
            if not writer_release.wait(timeout=5):
                raise RuntimeError("test writer release timed out")
            handle.write(encoded)
            handle.flush()
            writer_fcntl.flock(handle.fileno(), writer_fcntl.LOCK_UN)

    writer = context.Process(target=write_replacement)
    writer.start()
    assert writer_truncated.wait(timeout=5)

    real_flock = fcntl.flock
    observations: queue.Queue[tuple[str, object]] = queue.Queue()

    def observed_flock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_SH:
            observations.put(("lock-sh-requested", None))
        real_flock(fd, operation)

    monkeypatch.setattr(native_worker, "fcntl", fcntl, raising=False)
    monkeypatch.setattr(fcntl, "flock", observed_flock)

    def read_oracle() -> None:
        try:
            value = NativeWorkerRuntime._workload_oracle(workspace)
        except BaseException as error:
            observations.put(("reader-failed", error))
        else:
            observations.put(("reader-returned", value))

    reader = threading.Thread(target=read_oracle, daemon=True)
    try:
        reader.start()
        assert observations.get(timeout=5) == ("lock-sh-requested", None)
        writer_release.set()
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert observations.get(timeout=1) == ("reader-returned", replacement)
    finally:
        writer_release.set()
        reader.join(timeout=5)
        writer.join(timeout=5)
        if writer.is_alive():
            writer.terminate()
            writer.join()
    assert writer.exitcode == 0


def test_native_campaign_f01_uses_real_snapshot_fields_and_seals_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance.slice2 import native_campaign
    from tests.acceptance.slice2.native_campaign import NativeCampaign
    from tests.acceptance.slice2.native_preflight import CommandResult
    from tests.acceptance.slice2.scenarios import ScenarioProcedure

    calls: list[str] = []
    execution_identity = f"labpilot-v1-{'e' * 64}-{'f' * 32}"
    unit = f"{execution_identity}.service"

    def command(argv: object, **_kwargs: object) -> CommandResult:
        command = tuple(cast(tuple[str, ...], argv))
        if command[:2] == ("systemctl", "list-units"):
            output = f"{unit} loaded inactive dead LabPilot attempt\n".encode()
        elif command[:2] == ("systemctl", "show"):
            output = b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n"
        elif command[:2] == ("journalctl", "--unit"):
            output = json.dumps({"_SYSTEMD_UNIT": unit, "MESSAGE": "exited"}).encode() + b"\n"
        else:
            raise AssertionError(command)
        return CommandResult(command, output, b"")

    monkeypatch.setattr(native_campaign, "run_command", command)

    def worker(_container: str, scenario: str, operation: str, _sha: str, _profile: str):
        calls.append(operation)
        terminal = operation in {"observe_terminal", "snapshot"}
        return {
            "scenario": scenario,
            "operation": operation,
            "tested_sha": "a" * 40,
            "batch": {"batch_id": "batch", "started": operation != "initialize"},
            "trials": [{"trial_id": "trial", "ordinal": 1}],
            "attempts": [
                {
                    "attempt_id": "attempt",
                    "status": "terminal" if terminal else "running",
                    "execution_identity": execution_identity,
                    "execution_spec_hash": "b" * 64,
                    "source_manifest_sha256": "c" * 64,
                    "prepared_train_sha256": "d" * 64,
                    "observed_environment": {"python_profile": "python-3.12"},
                    "terminal_kind": "process_exited" if terminal else None,
                    "terminal_at": "2026-01-01T00:00:00+00:00" if terminal else None,
                    "exit_code": 0 if terminal else None,
                    "termination_signal": None,
                }
            ],
            "runtime_events": [],
            "workload_oracle": {"start_count": 1, "source_sha256": "d" * 64},
        }

    request = {
        "config": {
            "evidence_root": str(tmp_path.resolve()),
            "workspace_root": str((tmp_path / "workspace").resolve()),
            "python_profile": "python-3.12",
            "compose_project": "labpilot-task5",
        },
        "tested_sha": "a" * 40,
        "phase": "run",
        "database_url": "postgresql+asyncpg://user:pass@127.0.0.1:5432/labpilot",
    }
    runtime = NativeCampaign(
        request,
        preflight={
            "containers": {
                "coordinator": "a1b2c3d4e5f6",
                "postgres": "b1c2d3e4f5a6",
            }
        },
        worker=worker,
        admin=lambda _argv: None,
        campaign_nonce="01234567",
    )
    runtime.execute(
        ScenarioProcedure(
            "F01",
            ("create_real_batch", "start", "dispatch", "observe_terminal"),
            ("exact_source", "observed_environment", "exit_zero", "succeeded"),
        )
    )

    assert calls == [
        "initialize",
        "create_real_batch",
        "start",
        "dispatch",
        "observe_terminal",
        "snapshot",
    ]
    verdicts = list(tmp_path.rglob("verdict.json"))
    assert len(verdicts) == 1
    assert json.loads(verdicts[0].read_text())["verdict"] == "PASS"
    evidence_root = verdicts[0].parent
    assert json.loads((evidence_root / "systemd-exact-unit.json").read_text())["units"] == [
        {
            "execution_identity": execution_identity,
            "listed": True,
            "properties": {
                "ActiveState": "inactive",
                "LoadState": "loaded",
                "Result": "success",
                "SubState": "dead",
            },
            "unit": unit,
        }
    ]
    assert (
        json.loads((evidence_root / "journal-exact-unit.jsonl").read_text())["_SYSTEMD_UNIT"]
        == unit
    )


def test_unexpected_worker_failure_writes_exact_diagnostic_schema(
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2.native_campaign import NativeCampaign
    from tests.acceptance.slice2.native_preflight import NativeCommandFailure
    from tests.acceptance.slice2.scenarios import ScenarioProcedure

    configured_secret = "opaque-configured-value"
    stdout = f"worker exposed {configured_secret}".encode()
    stderr = b"safe worker failure\n"

    def worker(*_args: object) -> dict[str, object]:
        raise NativeCommandFailure("python3", "b" * 64, 17, stdout, stderr)

    campaign = NativeCampaign(
        {
            "config": {
                "evidence_root": str(tmp_path.resolve()),
                "workspace_root": str((tmp_path / "workspace").resolve()),
                "python_profile": "python-3.12",
                "compose_project": "labpilot-task5",
            },
            "tested_sha": "a" * 40,
            "database_url": configured_secret,
        },
        preflight={
            "containers": {
                "coordinator": "a1b2c3d4e5f6",
                "postgres": "b1c2d3e4f5a6",
            }
        },
        worker=worker,
        admin=lambda _argv: None,
        campaign_nonce="01234567",
    )

    with pytest.raises(NativeCommandFailure):
        campaign.execute(ScenarioProcedure("F01", (), ()))

    evidence = json.loads(next(tmp_path.rglob("worker-failure.json")).read_text())
    assert evidence == {
        "schema_version": 1,
        "scenario": "F01",
        "operation": "python3",
        "argv_sha256": "b" * 64,
        "return_code": 17,
        "stdout": {
            "byte_count": len(stdout),
            "sha256": hashlib.sha256(stdout).hexdigest(),
            "truncated": False,
            "excerpt": "[REDACTED]",
        },
        "stderr": {
            "byte_count": len(stderr),
            "sha256": hashlib.sha256(stderr).hexdigest(),
            "truncated": False,
            "excerpt": "safe worker failure\n",
        },
        "unexpected_worker_failure": True,
    }
    verdict = json.loads(next(tmp_path.rglob("verdict.json")).read_text())
    assert verdict["verdict"] == "STOPPED"
    assert "unexpected_worker_failure" not in verdict


def test_coordinator_down_commands_run_while_container_remains_exec_capable(
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2.native_campaign import NativeCampaign
    from tests.acceptance.slice2.scenarios import ScenarioProcedure

    events: list[object] = []

    def worker(_container: str, scenario: str, operation: str, sha: str, _profile: str):
        events.append(("worker", operation))
        attempts = []
        if operation in {"scan", "snapshot"}:
            attempts = [
                {
                    "attempt_id": "attempt",
                    "status": "terminal",
                    "execution_identity": "X",
                    "terminal_kind": "process_exited",
                    "terminal_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        return {
            "scenario": scenario,
            "operation": operation,
            "tested_sha": sha,
            "attempts": attempts,
            "runtime_events": [],
            "workload_oracles": [],
        }

    campaign = NativeCampaign(
        {
            "config": {
                "evidence_root": str(tmp_path.resolve()),
                "workspace_root": str((tmp_path / "workspace").resolve()),
                "python_profile": "python-3.12",
                "compose_project": "labpilot-task5",
            },
            "tested_sha": "a" * 40,
            "database_url": "postgresql+asyncpg://user:pass@db.example/labpilot",
        },
        preflight={"containers": {"coordinator": "a1b2c3d4e5f6", "postgres": "b1c2d3e4f5a6"}},
        worker=worker,
        admin=events.append,
    )
    campaign.execute(
        ScenarioProcedure(
            "F04",
            ("stop_coordinator", "persist_start", "start_coordinator", "scan"),
            ("zero_attempt_while_down", "single_normal_dispatch"),
        )
    )

    persist = events.index(("worker", "persist_start"))
    stopped = max(
        index
        for index, event in enumerate(events[:persist])
        if isinstance(event, tuple) and any("SIGSTOP" in part for part in event)
    )
    restarted = next(
        index
        for index, event in enumerate(events[persist + 1 :], persist + 1)
        if isinstance(event, tuple) and event[-2:] == ("restart", "coordinator")
    )
    assert stopped < persist < restarted < events.index(("worker", "scan"))
    assert not any(
        isinstance(event, tuple) and event[-2:] == ("stop", "coordinator")
        for event in events[stopped + 1 : persist]
    )


def test_faults_cannot_manufacture_physical_observations() -> None:
    from tests.acceptance.slice2 import faults

    source = inspect.getsource(faults)
    assert "systemd_supervisor" not in source
    assert "ACTIVE_MATCH" not in source
    assert "TERMINAL_MATCH" not in source
    assert not hasattr(faults, "PhysicalObservation")


def test_delegate_then_drop_delegates_before_response_loss() -> None:
    from tests.acceptance.slice2.faults import (
        ResponseLostAfterDelegate,
        delegate_then_drop,
    )

    events: list[object] = []

    def delegate(request: object) -> dict[str, object]:
        events.extend(("delegated", request))
        return {"real": "response"}

    with pytest.raises(ResponseLostAfterDelegate) as raised:
        delegate_then_drop(delegate, {"operation": "LAUNCH"})

    assert events == ["delegated", {"operation": "LAUNCH"}]
    assert raised.value.delegate_completed is True
    assert raised.value.response == {"real": "response"}


def test_crash_after_observe_requires_a_real_delegated_result() -> None:
    from tests.acceptance.slice2.faults import CrashAfterDelegate, crash_after_delegate

    events: list[str] = []

    def observe() -> dict[str, object]:
        events.append("observed")
        return {"kind": "unknown"}

    with pytest.raises(CrashAfterDelegate) as raised:
        crash_after_delegate(observe)

    assert events == ["observed"]
    assert raised.value.delegate_completed is True
    assert raised.value.result == {"kind": "unknown"}


def test_pre_send_fault_never_calls_runner() -> None:
    from tests.acceptance.slice2.faults import ConnectionPrevented, prevent_connection

    calls: list[str] = []

    def runner() -> None:
        calls.append("runner")

    with pytest.raises(ConnectionPrevented) as raised:
        prevent_connection()
        runner()

    assert raised.value.request_bytes_sent == 0
    assert calls == []


def test_native_worker_fault_runner_preserves_real_delegation_boundaries() -> None:
    from tests.acceptance.slice2.native_worker import _CrashBoundary, _FaultRunner

    from labpilot.execution.runner_protocol import RunnerOperation

    calls: list[object] = []

    class Delegate:
        def request(self, request: object) -> dict[str, object]:
            calls.append(request)
            return {"outcome": "ok"}

    class Request:
        def __init__(self, operation: object) -> None:
            self.operation = operation

    launch = Request(RunnerOperation.LAUNCH)
    with pytest.raises(_CrashBoundary):
        _FaultRunner(Delegate(), "crash_before_launch").request(launch)
    assert calls == []

    with pytest.raises(ConnectionError, match="dropped real LAUNCH"):
        _FaultRunner(Delegate(), "drop_launch").request(launch)
    assert calls == [launch]


def test_native_worker_crash_boundary_has_distinct_process_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance.slice2 import native_worker

    def crash(_request: object) -> dict[str, object]:
        raise native_worker._CrashBoundary("deliberate crash boundary")

    monkeypatch.setitem(native_worker.WORKER_OPERATIONS, ("F05", "crash_before_runner"), crash)

    assert (
        native_worker.main(
            [
                "--scenario",
                "F05",
                "--operation",
                "crash_before_runner",
                "--tested-sha",
                "a" * 40,
                "--python-profile",
                "python-3.12",
            ]
        )
        == 86
    )


def test_status_86_remains_expected_without_worker_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2 import native_campaign
    from tests.acceptance.slice2.native_campaign import NativeCampaign
    from tests.acceptance.slice2.native_preflight import NativeCommandFailure

    def failed(_argv: object, **_kwargs: object) -> object:
        raise NativeCommandFailure("python3", "b" * 64, 86, b"", b"")

    monkeypatch.setattr(native_campaign, "run_command", failed)
    campaign = NativeCampaign(
        {
            "config": {
                "evidence_root": str(tmp_path.resolve()),
                "workspace_root": str((tmp_path / "workspace").resolve()),
                "python_profile": "python-3.12",
                "compose_project": "labpilot-task5",
            },
            "tested_sha": "a" * 40,
            "database_url": "postgresql+asyncpg://user:pass@db.example:5432/labpilot",
        },
        preflight={"containers": {"coordinator": "a1b2c3d4e5f6", "postgres": "b1c2d3e4f5a6"}},
    )

    assert (
        campaign._run_worker("a1b2c3d4e5f6", "F05", "crash_before_runner", "a" * 40, "python-3.12")[
            "process_exit_boundary"
        ]
        == 86
    )
    assert not list(tmp_path.rglob("worker-failure.json"))
    with pytest.raises(NativeCommandFailure):
        campaign._run_worker("a1b2c3d4e5f6", "F05", "snapshot", "a" * 40, "python-3.12")


def test_f20_barrier_delegates_only_after_host_confirms_runner_stopped(
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2.native_worker import _FaultRunner

    from labpilot.execution.runner_protocol import RunnerOperation

    events: list[str] = []

    class Delegate:
        def request(self, _request: object) -> dict[str, object]:
            events.append("delegate")
            return {"outcome": "ok"}

    class Request:
        operation = RunnerOperation.LAUNCH

    def host() -> None:
        ready = tmp_path / ".f20-launch-boundary"
        deadline = time.monotonic() + 2
        while not ready.is_file():
            if time.monotonic() >= deadline:
                raise AssertionError("worker did not reach launch boundary")
            time.sleep(0.01)
        events.append("runner_stopped")
        (tmp_path / ".f20-runner-stopped").write_text("runner_stopped\n")

    watcher = threading.Thread(target=host)
    watcher.start()
    _FaultRunner(Delegate(), "barrier_prevent_launch", tmp_path).request(Request())
    watcher.join()

    assert events == ["runner_stopped", "delegate"]


def test_bounded_polling_stops_on_success_and_records_last_fact() -> None:
    from tests.acceptance.slice2.oracles import BoundedPollTimeout, poll_until

    facts = iter(({"status": "pending"}, {"status": "running"}))
    times = iter((10.0, 10.1, 10.2, 11.1))
    backoffs: list[float] = []
    result = poll_until(
        lambda: next(facts),
        lambda fact: fact["status"] == "running",
        timeout_seconds=1.0,
        interval_seconds=0.1,
        monotonic=lambda: next(times),
        backoff=backoffs.append,
    )
    assert result == {"status": "running"}
    assert backoffs == [0.1]

    times = iter((20.0, 20.0, 21.0))
    with pytest.raises(BoundedPollTimeout) as raised:
        poll_until(
            lambda: {"status": "pending"},
            lambda _fact: False,
            timeout_seconds=1.0,
            interval_seconds=0.1,
            monotonic=lambda: next(times),
            backoff=lambda _seconds: None,
        )
    assert raised.value.last_observed == {"status": "pending"}


def test_evidence_writer_bounds_journal_database_and_environment(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import (
        EvidenceBoundsError,
        EvidenceLimits,
        EvidenceWriter,
    )

    writer = EvidenceWriter(
        tmp_path,
        limits=EvidenceLimits(
            max_file_bytes=4096,
            max_records=2,
            max_database_rows=1,
            max_environment_items=1,
        ),
    )
    with pytest.raises(EvidenceBoundsError, match="record limit"):
        writer.write_jsonl(
            "journal-exact-unit.jsonl",
            ({"MESSAGE": str(number)} for number in range(3)),
        )
    with pytest.raises(EvidenceBoundsError, match="database row limit"):
        writer.write_json("db-before.json", {"rows": [{"id": 1}, {"id": 2}]})
    with pytest.raises(EvidenceBoundsError, match="environment item limit"):
        writer.write_json(
            "observed-environment.json",
            {"environment": {"python": "3.12", "cuda": "none"}},
        )


@pytest.mark.parametrize(
    "payload",
    (
        {"api_token": "not-allowed"},
        {"nested": {"database_url": "postgresql://user:pass@host/db"}},
        {"header": "Bearer abcdefghijklmnopqrstuvwxyz"},
    ),
)
def test_evidence_writer_excludes_secrets(tmp_path: Path, payload: object) -> None:
    from tests.acceptance.slice2.oracles import EvidenceSecretError, EvidenceWriter

    with pytest.raises(EvidenceSecretError):
        EvidenceWriter(tmp_path).write_json("runner-info.json", payload)


def test_worker_failure_is_the_only_added_authorized_evidence_name() -> None:
    from tests.acceptance.slice2.oracles import EVIDENCE_FILES

    assert EVIDENCE_FILES == {
        "scenario.json",
        "db-before.json",
        "db-after.json",
        "runner-info.json",
        "runner-service-effective.json",
        "observed-environment.json",
        "physical-observations.jsonl",
        "runtime-events.jsonl",
        "systemd-exact-unit.json",
        "journal-exact-unit.jsonl",
        "workload-test-oracle.json",
        "worker-failure.json",
        "verdict.json",
    }


def test_installation_identity_is_stable_across_saved_scenario_phases(
    tmp_path: Path,
) -> None:
    from tests.acceptance.slice2.scenarios import ScenarioState

    state = ScenarioState.create(
        scenario="F06",
        tested_commit="a" * 40,
        installation_seed="acceptance-host-one",
    )
    state.save(tmp_path / "scenario.json")
    resumed = ScenarioState.load(tmp_path / "scenario.json")

    assert resumed.installation_identity == state.installation_identity
    assert resumed.for_phase("verify").installation_identity == state.installation_identity
    assert resumed.tested_commit == "a" * 40


def _verdict(**changes: object) -> object:
    from tests.acceptance.slice2.oracles import ScenarioVerdict

    value: dict[str, object] = {
        "schema_version": 1,
        "scenario": "F01",
        "tested_sha": "a" * 40,
        "verdict": "PASS",
        "reason_code": "obligations_satisfied",
    }
    value.update(changes)
    return ScenarioVerdict(**value)


@pytest.mark.parametrize(
    "changes",
    (
        {"verdict": "pass"},
        {"verdict": "UNKNOWN"},
        {"verdict": "IDENTITY_CONFLICT"},
        {"scenario": "F00"},
        {"scenario": "F23"},
        {"schema_version": True},
        {"schema_version": "1"},
        {"tested_sha": "A" * 40},
        {"tested_sha": "a" * 39},
        {"reason_code": ""},
        {"reason_code": "UPPER_CASE"},
        {"reason_code": "punctuation-not-allowed"},
        {"reason_code": "a" * 81},
    ),
)
def test_scenario_verdict_rejects_invalid_contract_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _verdict(**changes)


def test_scenario_verdict_requires_the_authoritative_verdict_field() -> None:
    from tests.acceptance.slice2.oracles import ScenarioVerdict

    with pytest.raises(TypeError):
        ScenarioVerdict(
            schema_version=1,
            scenario="F01",
            tested_sha="a" * 40,
            reason_code="obligations_satisfied",
        )


@pytest.mark.parametrize("alias", ("outcome", "passing_verdict", "status", "success", "passed"))
def test_scenario_verdict_rejects_top_level_verdict_aliases(alias: str) -> None:
    from tests.acceptance.slice2.oracles import ScenarioVerdict

    value = {
        "schema_version": 1,
        "scenario": "F01",
        "tested_sha": "a" * 40,
        "verdict": "PASS",
        "reason_code": "obligations_satisfied",
        alias: True,
    }
    with pytest.raises(TypeError):
        ScenarioVerdict(**value)


@pytest.mark.parametrize("verdict", ("PASS", "FAIL", "STOPPED"))
def test_seal_verdict_writes_exact_schema_and_canonical_bytes(tmp_path: Path, verdict: str) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter

    writer = EvidenceWriter(tmp_path)
    digest = writer.seal_verdict(_verdict(verdict=verdict))
    path = tmp_path / "verdict.json"
    expected = (
        b'{"administrative_cleanup_is_domain_evidence":false,'
        b'"reason_code":"obligations_satisfied","scenario":"F01",'
        b'"schema_version":1,"sealed":true,'
        + b'"tested_sha":"'
        + b"a" * 40
        + b'","verdict":"'
        + verdict.encode("ascii")
        + b'"}'
    )

    assert path.read_bytes() == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert digest == hashlib.sha256(expected).hexdigest()
    assert set(json.loads(expected)) == {
        "schema_version",
        "scenario",
        "tested_sha",
        "verdict",
        "reason_code",
        "sealed",
        "administrative_cleanup_is_domain_evidence",
    }


def test_seal_verdict_accepts_only_the_closed_value_object(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter

    with pytest.raises(TypeError):
        EvidenceWriter(tmp_path).seal_verdict(  # type: ignore[arg-type]
            {
                "schema_version": 1,
                "scenario": "F01",
                "tested_sha": "a" * 40,
                "verdict": "PASS",
                "reason_code": "obligations_satisfied",
            }
        )


def test_same_writer_cannot_reseal_verdict(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter

    writer = EvidenceWriter(tmp_path)
    writer.seal_verdict(_verdict())

    with pytest.raises((FileExistsError, RuntimeError)):
        writer.seal_verdict(_verdict(verdict="FAIL", reason_code="invariant_violated"))


def test_new_writer_cannot_replace_or_reseal_verdict(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter

    EvidenceWriter(tmp_path).seal_verdict(_verdict())
    original = (tmp_path / "verdict.json").read_bytes()

    with pytest.raises((FileExistsError, RuntimeError)):
        EvidenceWriter(tmp_path).seal_verdict(
            _verdict(verdict="FAIL", reason_code="invariant_violated")
        )

    assert (tmp_path / "verdict.json").read_bytes() == original


def test_generic_json_write_cannot_create_or_replace_verdict(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter

    writer = EvidenceWriter(tmp_path)
    with pytest.raises(ValueError, match="seal_verdict"):
        writer.write_json("verdict.json", {"verdict": "PASS"})

    writer.seal_verdict(_verdict())
    with pytest.raises(ValueError, match="seal_verdict"):
        EvidenceWriter(tmp_path).write_json("verdict.json", {"verdict": "FAIL"})


def test_existing_verdict_symlink_is_not_followed_or_replaced(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_bytes(b"outside")
    (tmp_path / "verdict.json").symlink_to(outside)

    with pytest.raises((FileExistsError, RuntimeError)):
        EvidenceWriter(tmp_path).seal_verdict(_verdict())

    assert outside.read_bytes() == b"outside"
    assert (tmp_path / "verdict.json").is_symlink()


def test_unknown_domain_state_has_pass_verdict_and_survives_cleanup(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter
    from tests.acceptance.slice2.scenarios import administrative_cleanup_record

    writer = EvidenceWriter(tmp_path)
    writer.write_jsonl(
        "physical-observations.jsonl",
        ({"domain_state": "UNKNOWN", "terminal_at": None},),
    )
    digest = writer.seal_verdict(
        _verdict(scenario="F14", verdict="PASS", reason_code="unknown_invariants_satisfied")
    )
    verdict_bytes = (tmp_path / "verdict.json").read_bytes()
    cleanup = administrative_cleanup_record("stop exact test unit")

    assert json.loads(verdict_bytes)["verdict"] == "PASS"
    assert "UNKNOWN" not in verdict_bytes.decode("ascii")
    assert digest == hashlib.sha256(verdict_bytes).hexdigest()
    assert cleanup["domain_terminal_evidence"] is False
    assert (tmp_path / "verdict.json").read_bytes() == verdict_bytes


def test_scenario_isolation_names_are_exact_and_disjoint(tmp_path: Path) -> None:
    from tests.acceptance.slice2.scenarios import ScenarioIsolation

    first = ScenarioIsolation.create(
        root=tmp_path,
        scenario="F01",
        tested_sha="a" * 40,
        campaign_nonce="01234567",
    )
    second = ScenarioIsolation.create(
        root=tmp_path,
        scenario="F02",
        tested_sha="a" * 40,
        campaign_nonce="01234567",
    )

    assert first.database_name == "labpilot_task5_f01_aaaaaaa_01234567"
    assert first.workspace == tmp_path / "F01-aaaaaaa-01234567" / "workspace"
    assert first.evidence == tmp_path / "F01-aaaaaaa-01234567" / "evidence"
    assert len({first.workspace, first.evidence, second.workspace, second.evidence}) == 4


def test_capacity_and_fifo_oracles_enforce_four_and_no_bypass() -> None:
    from tests.acceptance.slice2.oracles import (
        OracleViolation,
        require_capacity_at_most_four,
        require_fifo_dispatch,
    )

    require_capacity_at_most_four(("running", "unknown", "identity_conflict", "launch_pending"))
    require_fifo_dispatch(("A", "B", "C"), ("A", "B"))

    with pytest.raises(OracleViolation, match="capacity"):
        require_capacity_at_most_four(("running",) * 5)
    with pytest.raises(OracleViolation, match="FIFO"):
        require_fifo_dispatch(("A", "B", "C"), ("A", "C"))


def test_workload_start_count_oracle_requires_exactly_one() -> None:
    from tests.acceptance.slice2.oracles import OracleViolation, require_single_start

    require_single_start({"start_count": 1})
    for count in (0, 2):
        with pytest.raises(OracleViolation, match="start_count"):
            require_single_start({"start_count": count})


def test_unknown_and_identity_conflict_oracles_remain_distinct_and_consume_slots() -> None:
    from tests.acceptance.slice2.oracles import (
        OracleViolation,
        require_identity_conflict,
        require_unknown_slot,
    )

    require_unknown_slot(
        {"status": "unknown", "terminal_at": None, "retry_count": 0, "launch_count": 1}
    )
    require_identity_conflict(
        {
            "status": "identity_conflict",
            "terminal_at": None,
            "stop_count": 0,
            "retry_count": 0,
            "launch_count": 1,
        }
    )

    with pytest.raises(OracleViolation, match="UNKNOWN"):
        require_unknown_slot(
            {"status": "unknown", "terminal_at": "now", "retry_count": 0, "launch_count": 1}
        )
    with pytest.raises(OracleViolation, match="IDENTITY_CONFLICT"):
        require_identity_conflict(
            {
                "status": "unknown",
                "terminal_at": None,
                "stop_count": 0,
                "retry_count": 0,
                "launch_count": 1,
            }
        )


def test_cancel_natural_race_preserves_physical_terminal_truth() -> None:
    from tests.acceptance.slice2.oracles import OracleViolation, require_natural_terminal_preserved

    truth = {"terminal_kind": "process_exited", "exit_code": 0, "termination_signal": None}
    require_natural_terminal_preserved(truth, dict(truth))
    with pytest.raises(OracleViolation, match="natural terminal"):
        require_natural_terminal_preserved(
            truth,
            {"terminal_kind": "process_signaled", "exit_code": None, "termination_signal": 15},
        )


def test_startup_barrier_oracle_rejects_dispatch_before_all_reconciliation() -> None:
    from tests.acceptance.slice2.oracles import OracleViolation, require_startup_barrier_order

    require_startup_barrier_order(
        ("reconcile:A", "audit:A", "reconcile:B", "audit:B", "barrier_complete", "dispatch:C"),
        expected_attempts=("A", "B"),
    )
    with pytest.raises(OracleViolation, match="startup barrier"):
        require_startup_barrier_order(
            ("reconcile:A", "audit:A", "dispatch:C", "reconcile:B", "audit:B", "barrier_complete"),
            expected_attempts=("A", "B"),
        )


def test_f18_metadata_requires_journal_locator_and_different_boot_id(tmp_path: Path) -> None:
    from tests.acceptance.slice2.scenarios import F18Metadata, require_f18_boot_change

    metadata = F18Metadata(
        scenario="F18",
        tested_commit="b" * 40,
        installation_identity="labpilot-acceptance:stable",
        database_identity="task5-f18-db",
        database_location="candidate-b-host",
        attempt_id="4e5ec2ce-d7a4-4ed2-916c-c26362210b5b",
        execution_identity=f"labpilot-v1-{'c' * 64}-{'d' * 32}",
        execution_spec_hash="e" * 64,
        pre_reboot_status="running",
        boot_id="11111111-1111-1111-1111-111111111111",
        runner_policy_identity="f" * 64,
        journal_evidence_locator="unit=labpilot-v1-c.service;cursor=s=bounded",
    )
    path = tmp_path / "f18.json"
    metadata.save(path)
    assert F18Metadata.load(path) == metadata
    require_f18_boot_change(metadata, "22222222-2222-2222-2222-222222222222")
    with pytest.raises(ValueError, match="boot ID"):
        require_f18_boot_change(metadata, metadata.boot_id)


@pytest.mark.parametrize(
    "change",
    (
        {"tested_commit": "A" * 40},
        {"pre_reboot_status": "terminal"},
        {"boot_id": "not-a-boot-id"},
        {"runner_policy_identity": "short"},
        {"journal_evidence_locator": ""},
    ),
)
def test_f18_metadata_rejects_invalid_persisted_state(
    tmp_path: Path, change: dict[str, object]
) -> None:
    value: dict[str, object] = {
        "scenario": "F18",
        "tested_commit": "b" * 40,
        "installation_identity": "labpilot-acceptance:stable",
        "database_identity": "task5-f18-db",
        "database_location": "candidate-b-host",
        "attempt_id": "4e5ec2ce-d7a4-4ed2-916c-c26362210b5b",
        "execution_identity": f"labpilot-v1-{'c' * 64}-{'d' * 32}",
        "execution_spec_hash": "e" * 64,
        "pre_reboot_status": "running",
        "boot_id": "11111111-1111-1111-1111-111111111111",
        "runner_policy_identity": "f" * 64,
        "journal_evidence_locator": "unit=labpilot-v1-c.service;cursor=s=bounded",
    }
    value.update(change)
    path = tmp_path / "f18-invalid.json"
    path.write_text(json.dumps(value))

    from tests.acceptance.slice2.scenarios import F18Metadata

    with pytest.raises(ValueError):
        F18Metadata.load(path)

    value.update({"tested_commit": "b" * 40, "unexpected": "field"})
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="schema"):
        F18Metadata.load(path)


def test_runtime_max_and_observe_before_uow_oracles_require_physical_ordering() -> None:
    from tests.acceptance.slice2.oracles import (
        OracleViolation,
        require_observe_before_uow_crash_order,
        require_runtime_max_termination,
    )

    require_runtime_max_termination(
        {"Result": "timeout", "RuntimeMaxUSec": "2000000", "ExecMainCode": "2"}
    )
    require_observe_before_uow_crash_order(
        ("observe_returned", "crash_before_uow_commit", "restart", "observe_returned", "uow_commit")
    )
    with pytest.raises(OracleViolation, match="RuntimeMaxSec"):
        require_runtime_max_termination({"Result": "success", "RuntimeMaxUSec": "infinity"})
    with pytest.raises(OracleViolation, match="observe-before-UoW"):
        require_observe_before_uow_crash_order(
            ("observe_returned", "uow_commit", "crash_before_uow_commit", "restart")
        )


def test_f18_metadata_round_trip_preserves_exact_reboot_identity(tmp_path: Path) -> None:
    from tests.acceptance.slice2.scenarios import F18Metadata

    metadata = F18Metadata(
        scenario="F18",
        tested_commit="b" * 40,
        installation_identity="labpilot-acceptance:stable",
        database_identity="task5-f18-db",
        database_location="candidate-b-host",
        attempt_id="4e5ec2ce-d7a4-4ed2-916c-c26362210b5b",
        execution_identity=f"labpilot-v1-{'c' * 64}-{'d' * 32}",
        execution_spec_hash="e" * 64,
        pre_reboot_status="running",
        boot_id="11111111-1111-1111-1111-111111111111",
        runner_policy_identity="f" * 64,
        journal_evidence_locator="unit=labpilot-v1-c.service;cursor=s=bounded",
    )
    path = tmp_path / "scenario.json"
    metadata.save(path)

    assert F18Metadata.load(path) == metadata


def test_f18_campaign_prepare_persists_identity_without_verdict_or_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.acceptance.slice2 import native_campaign
    from tests.acceptance.slice2.native_campaign import NativeCampaign
    from tests.acceptance.slice2.native_preflight import CommandResult
    from tests.acceptance.slice2.scenarios import ScenarioProcedure

    calls: list[str] = []
    command_calls: list[tuple[str, ...]] = []
    post_reboot = False
    execution_identity = f"labpilot-v1-{'b' * 64}-{'c' * 32}"

    def worker(_container: str, scenario: str, operation: str, sha: str, _profile: str):
        calls.append(operation)
        return {
            "scenario": scenario,
            "operation": operation,
            "tested_sha": sha,
            "installation_identity": "slice2-task5:stable",
            "batch": {"batch_id": "batch", "started": operation != "initialize"},
            "trials": [{"trial_id": "trial", "ordinal": 1}],
            "attempts": [
                {
                    "attempt_id": "4e5ec2ce-d7a4-4ed2-916c-c26362210b5b",
                    "status": "unknown" if post_reboot else "running",
                    "execution_identity": execution_identity,
                    "execution_spec_hash": "d" * 64,
                    "observed_environment": {"python_profile": "python-3.12"},
                    "terminal_at": None,
                }
            ],
            "runtime_events": [
                {
                    "event_type": "attempt.physical_started_observed",
                    "aggregate_id": "4e5ec2ce-d7a4-4ed2-916c-c26362210b5b",
                }
            ],
            "workload_oracles": [{"start_count": 1}],
        }

    def command(argv: object, **_kwargs: object) -> CommandResult:
        command = tuple(cast(tuple[str, ...], argv))
        command_calls.append(command)
        output = b"-- cursor: s=bounded\n" if "--show-cursor" in command else b""
        return CommandResult(command, output, b"")

    monkeypatch.setattr(native_campaign, "run_command", command)
    request = {
        "config": {
            "evidence_root": str((tmp_path / "evidence").resolve()),
            "workspace_root": str((tmp_path / "workspace").resolve()),
            "python_profile": "python-3.12",
            "compose_project": "labpilot-task5",
        },
        "tested_sha": "a" * 40,
        "database_url": "postgresql+asyncpg://user:pass@db.example:5432/labpilot",
    }
    preflight = {
        "containers": {"coordinator": "a1b2c3d4e5f6", "postgres": "b1c2d3e4f5a6"},
        "runner_info": {
            "result": {
                "boot_id": "11111111-1111-1111-1111-111111111111",
                "policy_identity": "e" * 64,
            }
        },
    }
    campaign = NativeCampaign(
        request,
        preflight=preflight,
        worker=worker,
        admin=lambda _argv: None,
    )
    campaign.execute(
        ScenarioProcedure(
            "F18",
            ("persist_pre_reboot_identity", "verify_new_boot", "observe_old_x"),
            ("different_boot_id", "no_same_x_resurrection", "allowed_post_reboot_truth"),
            phase="prepare",
        )
    )

    assert calls == ["initialize", "persist_pre_reboot_identity", "snapshot"]
    scenario_files = list((tmp_path / "evidence").rglob("scenario.json"))
    assert len(scenario_files) == 1
    assert json.loads(scenario_files[0].read_text())["execution_identity"] == execution_identity
    assert not list((tmp_path / "evidence").rglob("verdict.json"))

    post_reboot = True
    preflight["runner_info"]["result"]["boot_id"] = "22222222-2222-2222-2222-222222222222"
    campaign.execute(
        ScenarioProcedure(
            "F18",
            ("persist_pre_reboot_identity", "verify_new_boot", "observe_old_x"),
            ("different_boot_id", "no_same_x_resurrection", "allowed_post_reboot_truth"),
            phase="verify",
        )
    )

    assert calls[-3:] == ["verify_new_boot", "observe_old_x", "snapshot"]
    verdict = json.loads(next((tmp_path / "evidence").rglob("verdict.json")).read_text())
    assert verdict["verdict"] == "PASS"
    assert (
        sum(
            command[:2] == ("journalctl", "--unit") and "--output=json" in command
            for command in command_calls
        )
        == 2
    )


def test_f18_verify_without_reboot_seals_stopped_verdict(tmp_path: Path) -> None:
    from dataclasses import asdict

    from tests.acceptance.slice2.native_campaign import NativeCampaign
    from tests.acceptance.slice2.oracles import EvidenceWriter
    from tests.acceptance.slice2.scenarios import F18Metadata, ScenarioIsolation, ScenarioProcedure

    sha = "a" * 40
    boot_id = "11111111-1111-1111-1111-111111111111"
    isolation = ScenarioIsolation.create(
        root=tmp_path,
        scenario="F18",
        tested_sha=sha,
        campaign_nonce=sha[:8],
    )
    EvidenceWriter(isolation.evidence).write_json(
        "scenario.json",
        asdict(
            F18Metadata(
                scenario="F18",
                tested_commit=sha,
                installation_identity="installation",
                database_identity="b" * 64,
                database_location="db.example/labpilot",
                attempt_id="4e5ec2ce-d7a4-4ed2-916c-c26362210b5b",
                execution_identity=f"labpilot-v1-{'e' * 64}-{'f' * 32}",
                execution_spec_hash="c" * 64,
                pre_reboot_status="running",
                boot_id=boot_id,
                runner_policy_identity="d" * 64,
                journal_evidence_locator="unit=exact.service;cursor=s=bounded",
            )
        ),
    )
    campaign = NativeCampaign(
        {
            "config": {
                "evidence_root": str(tmp_path.resolve()),
                "workspace_root": str((tmp_path / "workspace").resolve()),
                "python_profile": "python-3.12",
                "compose_project": "labpilot-task5",
            },
            "tested_sha": sha,
            "database_url": "postgresql+asyncpg://user:pass@db.example/labpilot",
        },
        preflight={
            "containers": {"coordinator": "a1b2c3d4e5f6", "postgres": "b1c2d3e4f5a6"},
            "runner_info": {"result": {"boot_id": boot_id}},
        },
        worker=lambda *_args: {},
        admin=lambda _argv: None,
    )

    with pytest.raises(ValueError, match="different boot ID"):
        campaign.execute(
            ScenarioProcedure(
                "F18",
                ("persist_pre_reboot_identity", "verify_new_boot", "observe_old_x"),
                ("different_boot_id", "no_same_x_resurrection", "allowed_post_reboot_truth"),
                phase="verify",
            )
        )

    verdict = json.loads((isolation.evidence / "verdict.json").read_text())
    assert verdict["verdict"] == "STOPPED"
    assert verdict["reason_code"] == "verify_stopped"


def test_training_fixture_writes_only_bounded_runtime_output(tmp_path: Path) -> None:
    from tests.acceptance.slice2.training_fixture.train import main

    output = tmp_path / "attempt" / "runtime" / "output"
    output.mkdir(parents=True)
    assert (
        main(
            [
                "--seed",
                "7",
                "--value",
                "0.25",
                "--dataset",
                "dataset-A",
                "--output-dir",
                str(output),
                "--duration",
                "0",
                "--exit-code",
                "0",
            ]
        )
        == 0
    )
    oracle = json.loads((output / "workload-test-oracle.json").read_text())
    assert oracle["seed"] == 7
    assert oracle["value"] == "0.25"
    assert oracle["dataset"] == "dataset-A"
    assert oracle["start_count"] == 1
    assert not list(tmp_path.glob("workload-test-oracle.json"))


def test_native_entrypoint_is_import_safe_and_requires_explicit_opt_in() -> None:
    script_path = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_native_acceptance", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls: list[object] = []
    with pytest.raises(module.NativeOptInRequired, match="explicit"):
        module.main([], environ={}, execute=calls.append)
    assert calls == []


def test_native_entrypoint_cli_fails_closed_before_import_or_host_action() -> None:
    script_path = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--scenario", "F01"],
        capture_output=True,
        text=True,
        check=False,
        env={},
    )

    assert result.returncode != 0
    assert "explicit opt-in requires" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_native_configuration_accepts_only_the_exact_authorized_schema(
    tmp_path: Path,
) -> None:
    script_path = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_native_config", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = (tmp_path / "config.json").resolve()
    expected = _native_config()
    config.write_text(json.dumps(expected))

    assert module.load_config(config) == expected

    config.write_text(json.dumps(_native_config(unexpected=True)))
    with pytest.raises(module.NativeOptInRequired, match="unknown|schema"):
        module.load_config(config)


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"version": 2}, "version"),
        ({"dedicated_candidate_b_host": False}, "dedicated"),
        ({"workspace_root": "relative/workspaces"}, "absolute"),
        ({"database_url_env": "NOT-AN-ENV-NAME"}, "environment variable"),
        ({"api_base_url": "http://user:password@127.0.0.1:8000"}, "API URL"),
        ({"api_base_url": "http://[invalid"}, "API URL"),
    ),
)
def test_native_configuration_rejects_invalid_authority_values(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    script_path = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_native_values", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = (tmp_path / "config.json").resolve()
    config.write_text(json.dumps(_native_config(**change)))

    with pytest.raises(module.NativeOptInRequired, match=message):
        module.load_config(config)


def test_native_configuration_requires_absolute_regular_non_symlink_file(
    tmp_path: Path,
) -> None:
    script_path = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_native_path", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(module.NativeOptInRequired, match="absolute"):
        module.load_config(Path("relative-config.json"))

    target = (tmp_path / "target.json").resolve()
    target.write_text(json.dumps(_native_config()))
    link = tmp_path / "config.json"
    link.symlink_to(target)
    with pytest.raises(module.NativeOptInRequired, match="symlink|regular"):
        module.load_config(link.absolute())


@pytest.mark.parametrize(
    ("actual_hostname", "is_wsl", "message"),
    (
        ("wrong-host", False, "hostname"),
        ("candidate-b-vm", True, "WSL"),
    ),
)
def test_native_authority_rejects_wrong_host_and_wsl_before_execution(
    tmp_path: Path,
    actual_hostname: str,
    is_wsl: bool,
    message: str,
) -> None:
    script_path = Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    spec = importlib.util.spec_from_file_location("slice2_native_host", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = (tmp_path / "config.json").resolve()
    config.write_text(json.dumps(_native_config()))
    calls: list[object] = []
    database_variable = "LABPILOT_SLICE2_ACCEPTANCE_DATABASE_URL"

    with pytest.raises(module.NativeOptInRequired, match=message):
        module.main(
            ["--scenario", "F01"],
            environ={
                module.CONFIG_ENV: str(config),
                database_variable: "postgresql+asyncpg://redacted-authority",
            },
            execute=calls.append,
            hostname=lambda: actual_hostname,
            wsl_detector=lambda: is_wsl,
        )

    assert calls == []
