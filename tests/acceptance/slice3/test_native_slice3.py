from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _native_smoke() -> object:
    path = Path(__file__).with_name("native_smoke.py")
    spec = importlib.util.spec_from_file_location("task12_native_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Task12 native smoke module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SMOKE = _native_smoke()
OPT_IN_ENV = _SMOKE.OPT_IN_ENV
NativeOptInRequired = _SMOKE.NativeOptInRequired
NativeSmokeResult = _SMOKE.NativeSmokeResult
Cardinalities = _SMOKE.Cardinalities
run_from_environment = _SMOKE.run_from_environment
_IDENTITY_A = "labpilot-v1-" + "a" * 64 + "-" + "b" * 32
_IDENTITY_B = "labpilot-v1-" + "c" * 64 + "-" + "d" * 32


def _cleanup_smoke(tmp_path: Path) -> object:
    return _SMOKE.NativeSlice3Smoke(
        SimpleNamespace(api_base_url="http://test", workspace_root=tmp_path), "a" * 40
    )


def test_native_smoke_requires_explicit_opt_in() -> None:
    with pytest.raises(NativeOptInRequired, match="explicit opt-in"):
        run_from_environment({})


def test_cleanup_discovers_untracked_attempt_from_owned_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batch, trial, attempt = uuid4(), uuid4(), uuid4()
    smoke = _cleanup_smoke(tmp_path)
    smoke._batches.append(batch)
    smoke.api = SimpleNamespace(
        get_json=lambda path: {
            f"/batches/{batch}": {
                "trial_ids": [str(trial)],
                "presentation_status": "succeeded",
            },
            f"/trials/{trial}": {"attempt_ids": [str(attempt)]},
            f"/attempts/{attempt}": {"supervisor_execution_identity": _IDENTITY_A},
        }[path]
    )
    cleaned: list[object] = []
    monkeypatch.setattr(smoke, "_cleanup_attempt", cleaned.append)

    smoke._cleanup()

    assert [item.attempt_id for item in cleaned] == [attempt]
    assert smoke._attempts[attempt].workspace == tmp_path / attempt.hex


def test_attempt_identity_drift_fails_closed(tmp_path: Path) -> None:
    smoke = _cleanup_smoke(tmp_path)
    attempt = uuid4()

    assert smoke._track_attempt(attempt, {"supervisor_execution_identity": _IDENTITY_A}) is True
    with pytest.raises(_SMOKE.NativeSmokeFailure, match="identity changed"):
        smoke._track_attempt(attempt, {"supervisor_execution_identity": _IDENTITY_B})


def test_unknown_systemd_state_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    smoke = _cleanup_smoke(tmp_path)
    attempt = uuid4()
    assert smoke._track_attempt(attempt, {"supervisor_execution_identity": _IDENTITY_A}) is True
    monkeypatch.setattr(
        _SMOKE,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=b"ActiveState=unfamiliar\n"),
    )

    with pytest.raises(_SMOKE.NativeSmokeFailure, match="unsafe"):
        smoke._systemd_unit_active(smoke._attempts[attempt])


@pytest.mark.skipif(
    os.environ.get(OPT_IN_ENV) != "1",
    reason="native Candidate-B acceptance requires explicit operator opt-in",
)
def test_native_slice3_result_loop() -> None:
    _assert_native_result(run_from_environment())


def _assert_native_result(result: NativeSmokeResult) -> None:
    assert len(result.trial_ids) == 2
    assert len(result.attempt_ids) == 2
    assert all(
        outcome["terminal_kind"] == "process_exited" and outcome["exit_code"] == 0
        for outcome in result.attempt_outcomes.values()
    )
    valid, invalid = (result.validation[attempt] for attempt in result.attempt_ids)
    assert valid["verdict"] == "valid"
    assert invalid["verdict"] == "invalid"
    assert [reason["code"] for reason in invalid["reasons"]] == ["METRIC_BELOW_THRESHOLD"]
    assert result.selections[result.trial_ids[0]] is not None
    assert result.selections[result.trial_ids[1]] is None
    assert result.summary["presentation_status"] == "complete_with_invalid_results"
    assert result.summary["aggregates"]["score"] == {"count": 1, "mean": "0.82"}
    included = result.summary["included"]
    excluded = result.summary["excluded"]
    assert {item["trial_id"] for item in included} == {result.trial_ids[0]}
    assert {item["trial_id"] for item in excluded} == {result.trial_ids[1]}
    assert not ({item["trial_id"] for item in included} & {item["trial_id"] for item in excluded})
    assert all(
        oracle["start_count"] == 1 and oracle["natural_exit"] is False
        for oracle in result.recovery["active_oracles_before_restart"].values()
    )
    assert all(
        oracle["start_count"] == 1 and oracle["natural_exit"] is True and oracle["exit_code"] == 0
        for oracle in result.recovery["settled_oracles_after_recovery"].values()
    )
    assert all(
        oracle["start_count"] == 1 and oracle["natural_exit"] is True and oracle["exit_code"] == 0
        for oracle in result.recovery["settled_oracles_after_second_restart"].values()
    )
    assert all(
        set(oracle) == {"start_count", "natural_exit", "exit_code"}
        for values in (
            result.recovery["active_oracles_before_restart"],
            result.recovery["settled_oracles_after_recovery"],
            result.recovery["settled_oracles_after_second_restart"],
        )
        for oracle in values.values()
    )
    expected = Cardinalities(artifacts=4, metrics=2, reports=2, selections=1, summaries=1)
    assert result.cardinalities_before_restart == expected
    assert result.cardinalities_after_restart == expected
    assert result.security["traversal_rejected"]["finding_codes"] == ["artifact_path_invalid"]
    assert result.security["traversal_rejected"]["unrelated_blockers"] == []
    assert result.security["pre_processing_malformed"]["selection"] is None
    assert result.security["pre_processing_symlink"]["selection"] is None
    assert result.security["pre_processing_malformed"]["result_processing_not_started"] is True
    assert result.security["pre_processing_symlink"]["result_processing_not_started"] is True
    assert set(result.security["pre_processing_malformed"]["oracle"]) == {
        "start_count",
        "natural_exit",
        "exit_code",
    }
    assert set(result.security["pre_processing_symlink"]["oracle"]) == {
        "start_count",
        "natural_exit",
        "exit_code",
    }
    assert result.security["post_validation_mutation"]["availability"] == "corrupted"
    assert result.security["sse_redaction"]["redacted"] is True
    assert result.security["sse_redaction"]["artifact_names"].count("workload-oracle") == 2
