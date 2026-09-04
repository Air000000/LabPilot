from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.results_validation.validator import canonical_decimal_text

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_TRAINING_FIXTURE = (
    _REPOSITORY_ROOT / "tests" / "acceptance" / "slice2" / "training_fixture" / "train.py"
)


def _run_fixture(
    tmp_path: Path,
    *,
    score: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output_dir = tmp_path / "workspace" / "runtime" / "output"
    output_dir.mkdir(parents=True)
    command = [
        sys.executable,
        str(_TRAINING_FIXTURE),
        "--seed",
        "7",
        "--value",
        "baseline",
        "--dataset",
        "demo-dataset",
        "--output-dir",
        str(output_dir),
        "--duration",
        "0",
        "--exit-code",
        "0",
    ]
    if score is not None:
        command.extend(("--score", score))
    return (
        subprocess.run(command, check=False, capture_output=True, text=True),
        output_dir,
    )


def _oracle(output_dir: Path) -> dict[str, object]:
    return json.loads((output_dir / "workload-test-oracle.json").read_text())


def test_training_fixture_without_score_preserves_slice2_oracle(tmp_path: Path) -> None:
    result, output_dir = _run_fixture(tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (output_dir / "result.json").exists()
    oracle = _oracle(output_dir)
    assert oracle["schema_version"] == "v1"
    assert oracle["seed"] == 7
    assert oracle["value"] == "baseline"
    assert oracle["dataset"] == "demo-dataset"
    assert oracle["output_dir"] == str(output_dir)
    assert oracle["start_count"] == 1
    assert oracle["natural_exit"] is True
    assert oracle["exit_code"] == 0
    assert oracle["child_pid"] is None
    assert isinstance(oracle["source_sha256"], str)
    assert isinstance(oracle["pid"], int)


@pytest.mark.parametrize(
    ("score", "expected_numeric", "expected_bytes"),
    (
        ("0.8200", "0.82", b'{"schema_version":"v1","metrics":{"score":0.82}}\n'),
        ("0.1000", "0.1", b'{"schema_version":"v1","metrics":{"score":0.1}}\n'),
        ("-0.000", "0", b'{"schema_version":"v1","metrics":{"score":0}}\n'),
        ("1E+3", "1000", b'{"schema_version":"v1","metrics":{"score":1000}}\n'),
    ),
)
def test_training_fixture_score_writes_canonical_result_json(
    tmp_path: Path,
    score: str,
    expected_numeric: str,
    expected_bytes: bytes,
) -> None:
    result, output_dir = _run_fixture(tmp_path, score=score)

    assert canonical_decimal_text(Decimal(score)) == expected_numeric
    assert result.returncode == 0, result.stderr
    oracle = _oracle(output_dir)
    assert oracle["schema_version"] == "v1"
    assert oracle["seed"] == 7
    assert oracle["value"] == "baseline"
    assert oracle["dataset"] == "demo-dataset"
    assert oracle["start_count"] == 1
    assert oracle["natural_exit"] is True
    assert oracle["exit_code"] == 0
    assert oracle["child_pid"] is None
    assert "score" not in oracle
    assert (output_dir / "result.json").read_bytes() == expected_bytes


@pytest.mark.parametrize("score", ("1E+10001", "1" * 1001))
def test_training_fixture_rejects_nonzero_score_outside_project_canonical_limits(
    tmp_path: Path,
    score: str,
) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_decimal_text(Decimal(score))

    result, output_dir = _run_fixture(tmp_path, score=score)

    assert result.returncode != 0
    assert "unrecognized arguments: --score" not in result.stderr
    assert not (output_dir / "workload-test-oracle.json").exists()
    assert not (output_dir / "result.json").exists()


def test_training_fixture_accepts_zero_before_project_exponent_limit(tmp_path: Path) -> None:
    score = "0E+10001"

    assert canonical_decimal_text(Decimal(score)) == "0"
    result, output_dir = _run_fixture(tmp_path, score=score)

    assert result.returncode == 0, result.stderr
    assert (output_dir / "result.json").read_bytes() == (
        b'{"schema_version":"v1","metrics":{"score":0}}\n'
    )


@pytest.mark.parametrize("score", ("NaN", "Infinity", "-Infinity"))
def test_training_fixture_rejects_nonfinite_score_before_side_effects(
    tmp_path: Path,
    score: str,
) -> None:
    result, output_dir = _run_fixture(tmp_path, score=score)

    assert result.returncode != 0
    assert "unrecognized arguments: --score" not in result.stderr
    assert not (output_dir / "result.json").exists()
    assert not (output_dir / "workload-test-oracle.json").exists()


def test_training_fixture_score_does_not_change_slice2_oracle(tmp_path: Path) -> None:
    baseline_result, baseline_output = _run_fixture(tmp_path / "without-score")
    score_result, score_output = _run_fixture(tmp_path / "with-score", score="0.82")

    assert baseline_result.returncode == score_result.returncode == 0
    baseline_oracle = _oracle(baseline_output)
    score_oracle = _oracle(score_output)
    for field in (
        "schema_version",
        "seed",
        "value",
        "dataset",
        "start_count",
        "natural_exit",
        "exit_code",
        "child_pid",
        "source_sha256",
    ):
        assert score_oracle[field] == baseline_oracle[field]
    assert "score" not in score_oracle
    assert (score_output / "result.json").read_bytes() == (
        b'{"schema_version":"v1","metrics":{"score":0.82}}\n'
    )
