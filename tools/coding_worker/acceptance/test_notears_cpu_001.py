"""Frozen NOTEARS-CPU-001 evaluator. Candidate source is always /workspace."""

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path("/workspace")
sys.path.insert(0, str(ROOT))


def inputs():
    rng = np.random.default_rng(723)
    a = rng.normal(size=128)
    l2 = np.column_stack(
        [a, 1.2 * a + 0.2 * rng.normal(size=128), -0.7 * a + 0.3 * rng.normal(size=128)]
    )
    a = rng.binomial(1, 0.5, size=128)
    b = rng.binomial(1, 1 / (1 + np.exp(-(3 * a - 1.5))))
    c = rng.binomial(1, 1 / (1 + np.exp(-(2 * b - 1))))
    logistic = np.column_stack([a, b, c]).astype(float)
    a = rng.poisson(1.0, size=128)
    b = rng.poisson(np.exp(0.5 * a))
    c = rng.poisson(np.exp(0.3 * a))
    return {"l2": l2, "logistic": logistic, "poisson": np.column_stack([a, b, c]).astype(float)}


def test_library_import_has_no_working_directory_side_effects(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", "import notears.linear; print('import_ok')"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import_ok"
    assert list(tmp_path.iterdir()) == []


def test_cli_exposes_solver_controls():
    result = subprocess.run(
        [sys.executable, str(ROOT / "bin/notears_linear"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert result.returncode == 0, result.stderr
    for flag in ("--max_iter", "--h_tol", "--rho_max", "--w_threshold"):
        assert flag in result.stdout


@pytest.mark.parametrize("loss_type", ["l2", "logistic", "poisson"])
def test_original_head_numerical_regression(loss_type):
    from notears.linear import notears_linear

    expected = json.loads(Path(__file__).with_name("reference.json").read_text())
    W = notears_linear(
        inputs()[loss_type], lambda1=0.05, loss_type=loss_type, max_iter=30, w_threshold=0.1
    )
    assert W.shape == (3, 3) and np.isfinite(W).all()
    assert np.count_nonzero(W) > 0
    assert np.all(np.diag(W) == 0)
    np.testing.assert_allclose(W, expected["matrices"][loss_type], rtol=1e-6, atol=1e-7)


def test_real_cli_matches_api_and_requested_output_path(tmp_path):
    from notears.linear import notears_linear

    X = inputs()["l2"]
    source, target = tmp_path / "data.csv", tmp_path / "actual.csv"
    np.savetxt(source, X, delimiter=",")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin/notears_linear"),
            str(source),
            "--lambda1",
            ".05",
            "--max_iter",
            "30",
            "--h_tol",
            "1e-8",
            "--rho_max",
            "1e16",
            "--w_threshold",
            ".1",
            "--W_path",
            str(target),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert result.returncode == 0, result.stderr
    actual = np.loadtxt(target, delimiter=",")
    expected = notears_linear(X, lambda1=0.05, loss_type="l2", max_iter=30, w_threshold=0.1)
    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-7)
    assert not (tmp_path / "W_est.csv").exists()


def test_cli_forwards_nondefault_values(tmp_path, monkeypatch):
    import notears.linear as linear

    calls = []

    def record(X, **kwargs):
        calls.append(kwargs)
        return np.zeros((X.shape[1], X.shape[1]))

    monkeypatch.setattr(linear, "notears_linear", record)
    source = tmp_path / "data.csv"
    np.savetxt(source, inputs()["l2"], delimiter=",")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(ROOT / "bin/notears_linear"),
            str(source),
            "--lambda1",
            ".2",
            "--max_iter",
            "2",
            "--h_tol",
            ".0001",
            "--rho_max",
            "10000",
            "--w_threshold",
            ".91",
            "--W_path",
            str(tmp_path / "result.csv"),
        ],
    )
    runpy.run_path(str(ROOT / "bin/notears_linear"), run_name="__main__")
    assert calls == [
        {
            "lambda1": 0.2,
            "loss_type": "l2",
            "max_iter": 2,
            "h_tol": 0.0001,
            "rho_max": 10000.0,
            "w_threshold": 0.91,
        }
    ]


def test_explicit_plot_preserves_three_visualizations(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    from notears.visualization import plot_linear_comparison

    def no_interactive_show(*args, **kwargs):
        raise AssertionError("headless plotting must not call show by default")

    monkeypatch.setattr(plt, "show", no_interactive_show)
    W_true = np.array([[0.0, 0.8, 0.0], [0.0, 0.0, 0.6], [0.0, 0.0, 0.0]])
    W_est = np.array([[0.0, 0.7, 0.0], [0.0, 0.0, 0.5], [0.0, 0.0, 0.0]])
    plot_linear_comparison(W_true, W_est, (W_true != 0).astype(int), tmp_path)
    for name in (
        "weight_matrices_comparison.png",
        "dag_structures_comparison.png",
        "edge_weights_distribution.png",
    ):
        assert (tmp_path / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert (tmp_path / name).stat().st_size > 1000
    assert not plt.get_fignums()
