from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

EXPECTED_SCENARIOS = {f"F{number:02d}" for number in range(1, 23)}


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


def test_evidence_package_has_only_the_authorized_names() -> None:
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


def test_unknown_verdict_is_sealed_before_administrative_cleanup(tmp_path: Path) -> None:
    from tests.acceptance.slice2.oracles import EvidenceWriter
    from tests.acceptance.slice2.scenarios import administrative_cleanup_record

    writer = EvidenceWriter(tmp_path)
    digest = writer.seal_verdict(
        {
            "scenario": "F14",
            "outcome": "pass",
            "domain_status": "unknown",
        }
    )
    verdict_bytes = (tmp_path / "verdict.json").read_bytes()
    cleanup = administrative_cleanup_record("stop exact test unit")

    assert digest == hashlib.sha256(verdict_bytes).hexdigest()
    assert cleanup["domain_terminal_evidence"] is False
    assert (tmp_path / "verdict.json").read_bytes() == verdict_bytes
    with pytest.raises(RuntimeError, match="sealed"):
        writer.write_json("verdict.json", {"outcome": "rewritten"})


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
    )
    path = tmp_path / "scenario.json"
    metadata.save(path)

    assert F18Metadata.load(path) == metadata


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
    script_path = (
        Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    )
    spec = importlib.util.spec_from_file_location("slice2_native_acceptance", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls: list[object] = []
    with pytest.raises(module.NativeOptInRequired, match="explicit"):
        module.main([], environ={}, execute=calls.append)
    assert calls == []


def test_native_entrypoint_cli_fails_closed_before_import_or_host_action() -> None:
    script_path = (
        Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    )
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
    script_path = (
        Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    )
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
    script_path = (
        Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    )
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
    script_path = (
        Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    )
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
    script_path = (
        Path(__file__).parents[3] / "scripts" / "run_slice2_native_acceptance.py"
    )
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
