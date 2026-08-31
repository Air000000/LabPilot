import json
from pathlib import Path

import pytest
from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures
from tests.acceptance.slice2 import native_campaign


class _DummyTrain:
    pw_uid = 995
    pw_gid = 982


class _CommandResult:
    stdout = b""


def _config(tmp_path: Path) -> dict[str, object]:
    return {
        "evidence_root": str(tmp_path / "evidence"),
        "repository_root": str(tmp_path),
    }


def test_v2_a2_scratch_evidence_is_lease_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tested_sha = "a" * 40
    monkeypatch.setattr(fixtures, "_PROOF_ROOT", tmp_path / "mechanism-proof")
    root = fixtures._PROOF_ROOT / f"candidate-b-v2-a2-{tested_sha[:12]}"
    expected = [
        root / "evidence/main.json",
        root / "evidence/reporter.json",
    ]
    captured: list[Path] = []

    def capture_prepare(path: Path) -> None:
        captured.append(path)

    def capture_reporter(
        _reporter: Path,
        evidence: Path,
        *,
        exit_status: int,
    ) -> None:
        assert exit_status == 0
        captured.append(evidence)

    def read_evidence(path: Path) -> dict[str, object]:
        if path.name == "main.json":
            return {
                "uid": 995,
                "gid": 982,
                "no_new_privs": 1,
                "cap_prm": "0000000000000000",
                "cap_eff": "0000000000000000",
                "cap_amb": "0000000000000000",
                "environment": {
                    "LD_LIBRARY_PATH": str(root / "canary/ld"),
                    "PYTHONPATH": str(root / "canary/python"),
                },
            }
        return {
            "uid": 0,
            "gid": 0,
            "loader_environment_present": False,
        }

    monkeypatch.setattr(fixtures, "_prepare_probe_evidence", capture_prepare)
    monkeypatch.setattr(fixtures, "_install_matrix_reporter", capture_reporter)
    monkeypatch.setattr(fixtures, "_reset_failed_if_loaded", lambda _unit: None)
    monkeypatch.setattr(fixtures, "run_command", lambda *_args, **_kwargs: _CommandResult())
    monkeypatch.setattr(fixtures.pwd, "getpwnam", lambda _name: _DummyTrain())
    monkeypatch.setattr(fixtures, "_read_json", read_evidence)
    monkeypatch.setattr(fixtures, "_receipt_records", lambda _unit: [{"_UID": "0"}])

    config = _config(tmp_path)
    fixtures._execute_security_row(config, tested_sha)

    assert captured == expected
    configured_evidence_root = Path(str(config["evidence_root"]))
    assert all(not path.is_relative_to(configured_evidence_root) for path in captured)


def test_v2_a3_scratch_evidence_is_lease_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tested_sha = "a" * 40
    root = fixtures._PROOF_ROOT / f"candidate-b-v2-a3-{tested_sha[:12]}"
    expected = [
        root / "evidence/exit0-main.json",
        root / "evidence/exit0-reporter.json",
        root / "evidence/exit71-main.json",
        root / "evidence/exit71-reporter.json",
    ]
    captured: list[Path] = []

    def capture_prepare(path: Path) -> None:
        captured.append(path)

    def capture_reporter(
        _reporter: Path,
        evidence: Path,
        *,
        exit_status: int,
    ) -> None:
        assert exit_status in {0, 71}
        captured.append(evidence)

    monkeypatch.setattr(fixtures, "_prepare_probe_evidence", capture_prepare)
    monkeypatch.setattr(fixtures, "_install_matrix_reporter", capture_reporter)
    monkeypatch.setattr(fixtures, "_reset_failed_if_loaded", lambda _unit: None)
    monkeypatch.setattr(fixtures, "run_command", lambda *_args, **_kwargs: _CommandResult())
    monkeypatch.setattr(fixtures, "_receipt_records", lambda _unit: [])
    monkeypatch.setattr(
        fixtures,
        "_properties",
        lambda _value: {
            "Result": "success",
            "ExecMainCode": "exited",
            "ExecMainStatus": "0",
        },
    )
    monkeypatch.setattr(
        fixtures,
        "_read_json",
        lambda _path: {
            "service_result": "success",
            "exit_code": "exited",
            "exit_status": "0",
        },
    )

    config = _config(tmp_path)
    fixtures._execute_failure_neutrality_row(config, tested_sha)

    assert captured == expected
    configured_evidence_root = Path(str(config["evidence_root"]))
    assert all(not path.is_relative_to(configured_evidence_root) for path in captured)


def test_profile_matrix_scenario_evidence_stays_under_configured_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tested_sha = "a" * 40
    row = "V2-A2"
    fingerprint = fixtures.FinalStackFingerprint(
        tested_sha=tested_sha,
        reporter_sha256="b" * 64,
        policy_identity="c" * 64,
        runner_exec_start="/usr/bin/labpilot-runner",
        runner_active_state="active",
        runner_invocation_id="invocation",
        compose_container_ids=(),
        compose_image_ids=(),
    )
    proof_root = fixtures._PROOF_ROOT / f"candidate-b-v2-a2-{tested_sha[:12]}"
    scratch_root = proof_root / "evidence"
    lease = fixtures.V2FixtureLease(row, (), proof_root, None, False)

    monkeypatch.setattr(native_campaign, "select_v2_profile_matrix", lambda _selection: (row,))
    monkeypatch.setattr(
        native_campaign,
        "capture_final_stack_fingerprint",
        lambda _config: fingerprint,
    )
    monkeypatch.setattr(
        native_campaign,
        "execute_candidate_b_v2_row",
        lambda _row, _config, _tested_sha: (
            {"scratch_root": str(scratch_root)},
            lease,
        ),
    )
    monkeypatch.setattr(
        native_campaign,
        "cleanup_candidate_b_v2_fixture",
        lambda _config, _lease: None,
    )

    config = _config(tmp_path)
    native_campaign.run_profile_matrix("V2-A1-V2-A20", config)

    configured_root = Path(str(config["evidence_root"]))
    scenario = (
        configured_root
        / "candidate-b-v2"
        / "profile-matrix"
        / tested_sha
        / row
        / "scenario.json"
    )
    assert scenario.is_file()
    assert not scenario.is_relative_to(fixtures._PROOF_ROOT)
    payload = json.loads(scenario.read_text(encoding="utf-8"))
    assert payload["row"] == row
    assert payload["tested_sha"] == tested_sha
    assert payload["evidence"] == {"scratch_root": str(scratch_root)}
