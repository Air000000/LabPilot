"""Independent integration contract; lives outside the candidate source tree."""

import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import scipy.optimize

ROOT = Path("/workspace")
sys.path.insert(0, str(ROOT / "experiments"))
adapter = importlib.import_module("labpilot_linear")
generate = importlib.import_module("make_labpilot_linear_dataset").generate


def digest(data):
    return hashlib.sha256(data).hexdigest()


def arguments(tmp_path, **updates):
    directory = tmp_path / "data"
    identity = generate(directory)
    values = dict(
        dataset=directory,
        dataset_manifest_sha256=identity,
        output_dir=tmp_path / "output",
        lambda1=0.05,
        seed=7,
        max_iter=100,
        h_tol=1e-8,
        rho_max=1e16,
        w_threshold=0.3,
    )
    values.update(updates)
    return SimpleNamespace(**values)


def rehash(args):
    path = args.dataset / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["files_sha256"] = {
        name: digest((args.dataset / name).read_bytes())
        for name in ("X.csv", "B_true.csv", "W_true.csv")
    }
    path.write_text(json.dumps(manifest, sort_keys=True))
    args.dataset_manifest_sha256 = digest(path.read_bytes())


def forbid_fit(*args, **kwargs):
    pytest.fail("invalid input reached fitting")


def test_generator_contract_and_no_overwrite(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    assert generate(first) == generate(second)
    assert {p.name for p in first.iterdir()} == {
        "X.csv",
        "B_true.csv",
        "W_true.csv",
        "manifest.json",
    }
    assert all(p.read_bytes() == (second / p.name).read_bytes() for p in first.iterdir())
    manifest = json.loads((first / "manifest.json").read_bytes())
    X = np.loadtxt(first / "X.csv", delimiter=",")
    W = np.loadtxt(first / "W_true.csv", delimiter=",")
    assert X.shape == (200, 5) and np.isfinite(X).all()
    expected = np.zeros((5, 5))
    expected[0, 1], expected[0, 2], expected[1, 3], expected[2, 3], expected[3, 4] = (
        0.8,
        -1.1,
        1,
        0.9,
        -1.2,
    )
    np.testing.assert_array_equal(W, expected)
    assert manifest["generation"]["data_seed"] == 7
    for name, sha in manifest["files_sha256"].items():
        assert digest((first / name).read_bytes()) == sha
    for name, sha in manifest["source_files_sha256"].items():
        assert digest((ROOT / name).read_bytes()) == sha
    with pytest.raises(ValueError, match="new"):
        generate(first)


@pytest.mark.parametrize("penalty", [0.05, 0.1])
def test_real_solver_metrics_and_lineage(tmp_path, monkeypatch, penalty):
    args = arguments(tmp_path, lambda1=penalty, seed=19)
    original, observed = adapter.linear.notears_linear, {}

    def spy(X, **kwargs):
        observed.update(kwargs)
        assert X.shape == (200, 5)
        return original(X, **kwargs)

    monkeypatch.setattr(adapter.linear, "notears_linear", spy)
    metrics = adapter.run(args)
    assert observed == dict(
        lambda1=penalty, loss_type="l2", max_iter=100, h_tol=1e-8, rho_max=1e16, w_threshold=0.3
    )
    assert set(p.name for p in args.output_dir.iterdir()) == {
        "W_est.csv",
        "result.json",
        "provenance.json",
    }
    W = np.loadtxt(args.output_dir / "W_est.csv", delimiter=",")
    B = np.loadtxt(args.dataset / "B_true.csv", delimiter=",")
    np.testing.assert_array_equal(
        W, original(np.loadtxt(args.dataset / "X.csv", delimiter=","), **observed)
    )
    assert metrics == adapter.utils.count_accuracy(B, W != 0)
    assert set(metrics) == {"shd", "fdr", "tpr", "fpr", "nnz"}
    assert json.loads((args.output_dir / "result.json").read_bytes()) == {
        "schema_version": "v1",
        "metrics": metrics,
    }
    provenance = json.loads((args.output_dir / "provenance.json").read_bytes())
    assert provenance["trial_seed"] == 19 and provenance["data_seed"] == 7
    assert provenance["dataset_manifest_sha256"] == args.dataset_manifest_sha256
    assert provenance["controls"] == observed
    assert provenance["notears_linear_file"] == str(ROOT / "notears/linear.py")
    assert provenance["W_est_sha256"] == digest((args.output_dir / "W_est.csv").read_bytes())
    assert all(
        provenance["environment"]["threads"][name] == "1"
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    )
    for name, sha in provenance["source_files_sha256"].items():
        assert digest((ROOT / name).read_bytes()) == sha


def test_fpr_above_one_and_poor_result_are_retained(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    B = np.array([[0, 1, 0], [0, 0, 1], [0, 0, 0]])
    X = np.arange(12).reshape(4, 3)
    for name, value in (("X.csv", X), ("B_true.csv", B), ("W_true.csv", B)):
        np.savetxt(args.dataset / name, value, delimiter=",")
    path = args.dataset / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["shape"] = [4, 3]
    path.write_text(json.dumps(manifest))
    rehash(args)
    monkeypatch.setattr(
        adapter.linear,
        "notears_linear",
        lambda *a, **k: B.T + np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0]]),
    )
    assert adapter.run(args) == {"fdr": 1, "tpr": 0, "fpr": 3, "shd": 3, "nnz": 3}
    assert (args.output_dir / "result.json").exists()


@pytest.mark.parametrize("name", ["manifest.json", "X.csv", "B_true.csv", "W_true.csv"])
def test_byte_tampering_rejected_before_fit(tmp_path, monkeypatch, name):
    args = arguments(tmp_path)
    path = args.dataset / name
    path.write_bytes(path.read_bytes() + b"\n")
    monkeypatch.setattr(adapter.linear, "notears_linear", forbid_fit)
    with pytest.raises(ValueError, match="hash mismatch"):
        adapter.run(args)
    assert not (args.output_dir / "result.json").exists()


@pytest.mark.parametrize("case", ["nan", "shape", "cyclic", "nonbinary", "inconsistent"])
def test_rehashed_invalid_data_rejected(tmp_path, monkeypatch, case):
    args = arguments(tmp_path)
    if case in ("nan", "shape"):
        value = np.loadtxt(args.dataset / "X.csv", delimiter=",")
        if case == "nan":
            value[0, 0] = np.nan
        else:
            value = value[:, :-1]
        np.savetxt(args.dataset / "X.csv", value, delimiter=",")
    else:
        B = np.loadtxt(args.dataset / "B_true.csv", delimiter=",")
        W = np.loadtxt(args.dataset / "W_true.csv", delimiter=",")
        if case == "cyclic":
            B[1, 0] = W[1, 0] = 1
        elif case == "nonbinary":
            B[0, 1] = 2
        else:
            W[0, 1] = 0
        np.savetxt(args.dataset / "B_true.csv", B, delimiter=",")
        np.savetxt(args.dataset / "W_true.csv", W, delimiter=",")
    rehash(args)
    monkeypatch.setattr(adapter.linear, "notears_linear", forbid_fit)
    with pytest.raises(ValueError):
        adapter.run(args)
    assert not (args.output_dir / "result.json").exists()


@pytest.mark.parametrize("case", ["directory", "file"])
def test_symlink_input_rejected(tmp_path, monkeypatch, case):
    args = arguments(tmp_path)
    if case == "directory":
        link = tmp_path / "link"
        link.symlink_to(args.dataset, target_is_directory=True)
        args.dataset = link
    else:
        file = args.dataset / "X.csv"
        moved = tmp_path / "X.csv"
        file.rename(moved)
        file.symlink_to(moved)
    monkeypatch.setattr(adapter.linear, "notears_linear", forbid_fit)
    with pytest.raises((ValueError, OSError)):
        adapter.run(args)


@pytest.mark.parametrize(
    "name,value",
    [
        ("seed", -1),
        ("lambda1", -0.1),
        ("lambda1", np.nan),
        ("max_iter", 0),
        ("rho_max", 1),
        ("w_threshold", -1),
    ],
)
def test_bad_controls_fail_before_optimizer(tmp_path, monkeypatch, name, value):
    args = arguments(tmp_path, **{name: value})
    monkeypatch.setattr(scipy.optimize, "minimize", forbid_fit)
    with pytest.raises(ValueError):
        adapter.run(args)
    assert not (args.output_dir / "result.json").exists()


@pytest.mark.parametrize("case", ["nan", "shape", "diagonal", "cyclic"])
def test_bad_solver_result_does_not_publish_success(tmp_path, monkeypatch, case):
    args = arguments(tmp_path)
    W = np.zeros((5, 5))
    if case == "nan":
        W[0, 1] = np.nan
    elif case == "shape":
        W = W[:4]
    elif case == "diagonal":
        W[0, 0] = 1
    else:
        W[0, 1] = W[1, 0] = 1
    monkeypatch.setattr(adapter.linear, "notears_linear", lambda *a, **k: W)
    with pytest.raises(ValueError, match="estimated graph"):
        adapter.run(args)
    assert not (args.output_dir / "result.json").exists()


def test_output_reuse_rejected_without_overwriting(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    args.output_dir.mkdir()
    previous = args.output_dir / "result.json"
    previous.write_text("old attempt")
    monkeypatch.setattr(adapter.linear, "notears_linear", forbid_fit)
    with pytest.raises(ValueError, match="empty"):
        adapter.run(args)
    assert previous.read_text() == "old attempt"


def test_cli_runs_with_readonly_source_and_external_workdir(tmp_path):
    args = arguments(tmp_path)
    files = [p for p in ROOT.rglob("*") if p.is_file()]
    before = {p: (digest(p.read_bytes()), p.stat().st_mode & 0o777) for p in files}
    directories = [ROOT, *[p for p in ROOT.rglob("*") if p.is_dir()]]
    directory_modes = {p: p.stat().st_mode & 0o777 for p in directories}
    try:
        for p, mode in directory_modes.items():
            p.chmod(mode & ~0o222)
        for p, (_, mode) in before.items():
            p.chmod(mode & ~0o222)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "experiments/labpilot_linear.py"),
                "--dataset",
                str(args.dataset),
                "--dataset-manifest-sha256",
                args.dataset_manifest_sha256,
                "--output-dir",
                str(args.output_dir),
                "--lambda1",
                "0.1",
                "--seed",
                "7",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, result.stderr
        assert (args.output_dir / "result.json").exists()
    finally:
        for p, mode in directory_modes.items():
            p.chmod(mode)
        for p, (_, mode) in before.items():
            p.chmod(mode)
    assert all(digest(p.read_bytes()) == sha for p, (sha, _) in before.items())


def test_source_cannot_be_selected_as_output(tmp_path, monkeypatch):
    args = arguments(tmp_path, output_dir=ROOT / "forbidden-output")
    monkeypatch.setattr(adapter.linear, "notears_linear", forbid_fit)
    with pytest.raises(ValueError, match="outside source"):
        adapter.run(args)
    assert not args.output_dir.exists()


def test_duplicate_manifest_key_rejected_before_fit(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    path = args.dataset / "manifest.json"
    path.write_bytes(b'{"schema_version":"duplicate",' + path.read_bytes()[1:])
    args.dataset_manifest_sha256 = digest(path.read_bytes())
    monkeypatch.setattr(adapter.linear, "notears_linear", forbid_fit)
    with pytest.raises(ValueError, match="duplicate"):
        adapter.run(args)


def test_partial_artifact_write_never_publishes_success(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    monkeypatch.setattr(adapter.linear, "notears_linear", lambda *a, **k: np.zeros((5, 5)))
    original = Path.write_bytes

    def fail_provenance(path, value):
        if path == args.output_dir / "provenance.json":
            raise OSError("injected artifact write failure")
        return original(path, value)

    monkeypatch.setattr(Path, "write_bytes", fail_provenance)
    with pytest.raises(OSError, match="artifact write failure"):
        adapter.run(args)
    assert not (args.output_dir / "result.json").exists()
