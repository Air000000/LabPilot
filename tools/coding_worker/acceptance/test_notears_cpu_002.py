"""Independent NOTEARS-CPU-002 contract, frozen before its first model call."""

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.optimize

sys.path.insert(0, str(Path("/workspace")))
from notears.linear import notears_linear


def valid_data():
    return np.array([[0.0, 1.0], [1.0, 0.0], [2.0, 1.0], [3.0, 2.0]])


@pytest.mark.parametrize(
    "X",
    [
        np.empty((0, 2)),
        np.empty((2, 0)),
        np.array([1.0, 2.0]),
        np.ones((2, 2, 1)),
        np.array([[0.0, np.nan]]),
        np.array([[np.inf, 0.0]]),
        np.array([[-np.inf, 0.0]]),
        np.array([[1.0 + 1.0j, 0.0]]),
        np.array([["invalid", "data"]]),
    ],
    ids=[
        "no-rows",
        "no-columns",
        "vector",
        "rank-three",
        "nan",
        "inf",
        "negative-inf",
        "complex",
        "text",
    ],
)
def test_invalid_data_rejected_before_optimizer(X, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid X reached the optimizer")

    monkeypatch.setattr(scipy.optimize, "minimize", forbidden)
    with pytest.raises(ValueError, match="X"):
        notears_linear(X, lambda1=0.1, loss_type="l2")


@pytest.mark.parametrize(
    "name,value",
    [
        ("lambda1", -0.1),
        ("lambda1", np.nan),
        ("lambda1", np.inf),
        ("lambda1", True),
        ("lambda1", "0.1"),
        ("lambda1", 1j),
        ("max_iter", 0),
        ("max_iter", -1),
        ("max_iter", 1.5),
        ("max_iter", True),
        ("max_iter", np.nan),
        ("max_iter", "2"),
        ("h_tol", -1e-8),
        ("h_tol", np.nan),
        ("h_tol", np.inf),
        ("h_tol", False),
        ("h_tol", "1e-8"),
        ("rho_max", 1.0),
        ("rho_max", 0.0),
        ("rho_max", -1.0),
        ("rho_max", np.nan),
        ("rho_max", np.inf),
        ("rho_max", True),
        ("rho_max", "100"),
        ("w_threshold", -0.1),
        ("w_threshold", np.nan),
        ("w_threshold", np.inf),
        ("w_threshold", False),
        ("w_threshold", "0.3"),
        ("loss_type", "unknown"),
    ],
)
def test_invalid_control_rejected_before_optimizer(name, value, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid control reached the optimizer")

    monkeypatch.setattr(scipy.optimize, "minimize", forbidden)
    kwargs = {"lambda1": 0.1, "loss_type": "l2", name: value}
    with pytest.raises(ValueError, match=name):
        notears_linear(valid_data(), **kwargs)


def test_signature_and_defaults_remain_compatible():
    parameters = inspect.signature(notears_linear).parameters
    assert list(parameters) == [
        "X",
        "lambda1",
        "loss_type",
        "max_iter",
        "h_tol",
        "rho_max",
        "w_threshold",
    ]
    assert [p.default for p in list(parameters.values())[3:]] == [100, 1e-8, 1e16, 0.3]


def test_valid_zero_bounds_and_numpy_scalars_are_supported():
    # rho starts at 1; its cap must exceed 1. Zero regularization/tolerance/threshold are valid.
    actual = notears_linear(
        valid_data(),
        lambda1=np.float64(0),
        loss_type="l2",
        max_iter=np.int64(1),
        h_tol=np.float64(0),
        rho_max=np.float64(1.1),
        w_threshold=np.float64(0),
    )
    assert actual.shape == (2, 2) and np.isfinite(actual).all()
    assert np.all(np.diag(actual) == 0)


def test_single_sample_and_single_variable_remain_supported():
    actual = notears_linear(np.array([[1.0]]), lambda1=0, loss_type="l2", max_iter=1)
    np.testing.assert_array_equal(actual, np.zeros((1, 1)))
