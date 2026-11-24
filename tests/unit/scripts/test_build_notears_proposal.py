import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from scripts.build_notears_proposal import METRICS, build_proposal


def dataset(tmp_path: Path) -> Path:
    files = {
        "X.csv": ("0,1,2,3,4\n" * 200).encode(),
        "B_true.csv": ("0,0,0,0,0\n" * 5).encode(),
        "W_true.csv": ("0,0,0,0,0\n" * 5).encode(),
    }
    for name, value in files.items():
        (tmp_path / name).write_bytes(value)
    manifest = {
        "schema_version": "notears-linear-dataset-v1",
        "shape": [200, 5],
        "generation": {"data_seed": 7},
        "files_sha256": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def build(path: Path, **updates):
    values = dict(
        snapshot_id=UUID("12345678-1234-1234-1234-123456789abc"),
        snapshot_identity="a" * 64,
        dataset_path="/var/lib/labpilot/datasets/notears-linear-v1/frozen",
        numpy_version="2.5.4",
        scipy_version="1.18.1",
        igraph_version="1.0.0",
    )
    values.update(updates)
    return build_proposal(path, **values)


def test_proposal_binds_one_manifest_and_preserves_scientific_comparison(tmp_path):
    path = dataset(tmp_path)
    request = build(path)
    payload = request.to_domain()
    identity = hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload.dataset_reference.stable_identity == identity
    argv = payload.train_argv_template
    assert argv is not None and argv[argv.index("--dataset-manifest-sha256") + 1] == identity
    assert {"{dataset}", "{output_dir}", "{parameter:lambda1}", "{seed}"} <= set(argv)
    assert payload.parameter_matrix == {"lambda1": (0.05, 0.1)} and payload.seeds == (7,)
    assert payload.timeout_seconds == 60 and payload.max_retries == 0
    assert {metric.name for metric in payload.metrics} == set(METRICS)
    assert payload.environment_expectation.device_requirement is None
    assert payload.environment_expectation.cuda_requirement is None
    assert set(payload.environment_expectation.framework_requirements) == {
        "numpy==2.5.4",
        "scipy==1.18.1",
        "igraph==1.0.0",
    }
    assert {r.subject for r in payload.validation_rules if r.kind == "metric_lte"} == {"fdr", "tpr"}
    assert {a.name for a in payload.artifacts if a.required} == {"result", "weights", "provenance"}
    # Pure construction; no Settings, database, API, provider or Runner client.
    assert request.model_validate_json(request.model_dump_json()).to_domain() == payload


@pytest.mark.parametrize("name", ["X.csv", "B_true.csv", "W_true.csv"])
def test_constructor_rejects_bytes_not_matching_manifest(tmp_path, name):
    path = dataset(tmp_path)
    (tmp_path / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        build(path)


@pytest.mark.parametrize(
    "updates",
    [
        {"snapshot_id": UUID(int=0)},
        {"snapshot_identity": "unknown"},
        {"dataset_path": "relative"},
        {"dataset_path": "/dataset/../other"},
        {"numpy_version": ">=2"},
    ],
)
def test_constructor_requires_concrete_bindings(tmp_path, updates):
    with pytest.raises(ValueError):
        build(dataset(tmp_path), **updates)


def test_constructor_rejects_symlink_data(tmp_path):
    path = dataset(tmp_path)
    original = tmp_path / "X.csv"
    original.rename(tmp_path / "other.csv")
    original.symlink_to(tmp_path / "other.csv")
    with pytest.raises(ValueError, match="regular"):
        build(path)


def test_constructor_rejects_duplicate_manifest_key(tmp_path):
    path = dataset(tmp_path)
    path.write_bytes(b'{"shape":[1,1],' + path.read_bytes()[1:])
    with pytest.raises(ValueError, match="duplicate"):
        build(path)
