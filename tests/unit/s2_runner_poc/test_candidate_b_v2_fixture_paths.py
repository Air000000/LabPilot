from pathlib import Path

import pytest

from tests.acceptance.slice2 import candidate_b_v2_fixtures as fixtures


class _DummyTrain:
    pw_uid = 995
    pw_gid = 982


def _config(tmp_path: Path) -> dict[str, object]:
    return {
        "evidence_root": str(tmp_path / "evidence"),
        "repository_root": str(tmp_path),
    }


def test_v2_a2_scratch_evidence_is_lease_owned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[Path] = []

    def capture_prepare(path: Path) -> None:
        captured.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    monkeypatch.setattr(fixtures, "_prepare_probe_evidence", capture_prepare)
    monkeypatch.setattr(fixtures, "_install_matrix_reporter", lambda _r, evidence, **_: captured.append(evidence))
    monkeypatch.setattr(fixtures, "_reset_failed_if_loaded", lambda _unit: None)
    monkeypatch.setattr(fixtures, "run_command", lambda *_args, **_kwargs: type("R", (), {"stdout": b""})())
    monkeypatch.setattr(fixtures.pwd, "getpwnam", lambda _name: _DummyTrain())
    monkeypatch.setattr(fixtures, "_read_json", lambda _path: {
        "uid": 995,
        "gid": 982,
        "no_new_privs": 1,
        "cap_prm": "0000000000000000",
        "cap_eff": "0000000000000000",
        "cap_amb": "0000000000000000",
        "environment": {},
    })
    monkeypatch.setattr(fixtures, "_receipt_records", lambda _unit: [{"_UID": "0"}])

    fixtures._execute_security_row(_config(tmp_path), "a" * 40)

    assert captured
    assert all("candidate-b-v2-a2-aaaaaaaaaaaa" in str(path) for path in captured)
    assert all("fixtures/V2-A2" not in str(path) for path in captured)


def test_v2_a3_scratch_evidence_is_lease_owned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[Path] = []

    def capture_prepare(path: Path) -> None:
        captured.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    monkeypatch.setattr(fixtures, "_prepare_probe_evidence", capture_prepare)
    monkeypatch.setattr(fixtures, "_install_matrix_reporter", lambda _r, evidence, **_: captured.append(evidence))
    monkeypatch.setattr(fixtures, "_reset_failed_if_loaded", lambda _unit: None)
    monkeypatch.setattr(fixtures, "run_command", lambda *_args, **_kwargs: type("R", (), {"stdout": b""})())
    monkeypatch.setattr(fixtures, "_receipt_records", lambda _unit: [])
    monkeypatch.setattr(fixtures, "_properties", lambda _value: {"Result": "success", "ExecMainCode": "exited", "ExecMainStatus": "0"})

    fixtures._execute_failure_neutrality_row(_config(tmp_path), "a" * 40)

    assert len(captured) == 4
    assert all("candidate-b-v2-a3-aaaaaaaaaaaa" in str(path) for path in captured)
    assert all("fixtures/V2-A3" not in str(path) for path in captured)
