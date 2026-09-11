from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _module(name: str, filename: str) -> object:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SMOKE = _module("task12_native_smoke_stabilization", "native_smoke.py")
_SUPERVISOR = _module("task12_native_supervisor", "native_supervisor.py")
_SHA = "a" * 40


def _run(tmp_path: Path, invocation: str, child: list[str]) -> int:
    return _SUPERVISOR.run_supervisor(
        invocation_id=invocation,
        evidence_root=tmp_path / "evidence",
        child_argv=child,
        product_candidate_sha=_SHA,
        acceptance_harness_sha=_SHA,
        config_path="/home/air/task12-config.json",
        fixture_path="/home/air/task12-fixture.json",
        systemd_unit_identity=f"labpilot-task12-native-supervisor@{invocation}.service",
    )


def test_running_gate_uses_default_and_validated_override() -> None:
    assert _SMOKE._running_gate_timeout_seconds({}) == 300.0
    assert _SMOKE._running_gate_timeout_seconds(
        {_SMOKE.RUNNING_GATE_TIMEOUT_ENV: "90"}
    ) == 90.0
    for value in ("", "zero", "0", "-1", "nan", "inf"):
        with pytest.raises(_SMOKE.NativeOptInRequired, match="positive finite"):
            _SMOKE._running_gate_timeout_seconds({_SMOKE.RUNNING_GATE_TIMEOUT_ENV: value})


def test_wait_timeout_retains_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    smoke = _SMOKE.NativeSlice3Smoke(
        type("Config", (), {"api_base_url": "http://test", "workspace_root": tmp_path})(), _SHA
    )
    moments = iter((0.0, 1.0))
    monkeypatch.setattr(_SMOKE.time, "monotonic", lambda: next(moments))

    with pytest.raises(_SMOKE.NativeSmokeFailure, match="diagnostic=.*batch_id"):
        smoke._wait_for(
            "running attempts",
            lambda: None,
            1,
            diagnostic=lambda: {"batch_id": "bounded", "trials": []},
        )


def test_supervisor_writes_started_before_fake_child_and_atomic_success(tmp_path: Path) -> None:
    marker = tmp_path / "evidence" / "first" / "started.json"
    code = (
        "from pathlib import Path; import sys; "
        "assert Path(sys.argv[1]).is_file(); print('fake native output')"
    )

    assert _run(tmp_path, "first", [sys.executable, "-c", code, str(marker)]) == 0

    result_path = tmp_path / "evidence" / "first" / "supervisor-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    started = json.loads(marker.read_text(encoding="utf-8"))
    assert started["invocation_id"] == "first"
    assert result["receipt_status"] == "captured"
    assert result["child_exit_code"] == 0
    assert result["child_outcome"] == "returned_normally"
    assert not list(result_path.parent.glob("*.tmp"))
    assert "postgres" not in result_path.read_text(encoding="utf-8").lower()


def test_supervisor_blocks_duplicate_without_retry(tmp_path: Path) -> None:
    invoked = tmp_path / "second-child-ran"
    assert _run(tmp_path, "duplicate", [sys.executable, "-c", "pass"]) == 0
    code = "from pathlib import Path; import sys; Path(sys.argv[1]).touch()"

    with pytest.raises(_SUPERVISOR.SupervisorError, match="already started"):
        _run(tmp_path, "duplicate", [sys.executable, "-c", code, str(invoked)])

    assert not invoked.exists()


def test_supervisor_refuses_secret_shaped_child_argv(tmp_path: Path) -> None:
    with pytest.raises(_SUPERVISOR.SupervisorError, match="credentials"):
        _run(tmp_path, "secret", ["python", "postgresql+psycopg://user:password@host/db"])

    assert not (tmp_path / "evidence" / "secret" / "started.json").exists()


def test_supervisor_propagates_nonzero_fake_child_exit(tmp_path: Path) -> None:
    assert _run(tmp_path, "nonzero", [sys.executable, "-c", "raise SystemExit(7)"]) == 7

    result = json.loads(
        (tmp_path / "evidence" / "nonzero" / "supervisor-result.json").read_text(encoding="utf-8")
    )
    assert result["child_exit_code"] == 7
    assert result["child_outcome"] == "nonzero_exit"
    assert result["receipt_status"] == "captured"


def test_supervisor_service_is_system_scoped_and_carries_no_environment() -> None:
    unit = Path(__file__).with_name("task12-native-supervisor.service").read_text(
        encoding="utf-8"
    )

    assert "User=root" in unit
    assert "ExecStart=" in unit
    assert "systemctl --user" not in unit
    assert "nohup" not in unit
    assert "Environment=" not in unit
