"""Build an offline NOTEARS proposal JSON; never connect, verify, approve or execute."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

from labpilot.api.schemas.experiments import ProposalPayloadRequest

METRICS = ("shd", "fdr", "tpr", "fpr", "nnz")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def build_proposal(
    manifest_path: Path,
    *,
    snapshot_id: UUID,
    snapshot_identity: str,
    dataset_path: str,
    numpy_version: str,
    scipy_version: str,
    igraph_version: str,
) -> ProposalPayloadRequest:
    if not snapshot_id.int or re.fullmatch(r"[0-9a-f]{64}", snapshot_identity) is None:
        raise ValueError("exact new Snapshot binding required")
    dataset = Path(dataset_path)
    if not dataset.is_absolute() or ".." in dataset.parts:
        raise ValueError("absolute published dataset path required")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("regular manifest required")
    with manifest_path.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("manifest too large")
    manifest = json.loads(raw, object_pairs_hook=_unique_pairs)
    if (
        manifest.get("schema_version") != "notears-linear-dataset-v1"
        or manifest.get("shape") != [200, 5]
        or manifest.get("generation", {}).get("data_seed") != 7
        or set(manifest.get("files_sha256", {})) != {"X.csv", "B_true.csv", "W_true.csv"}
    ):
        raise ValueError("fixed dataset contract mismatch")
    for name, expected in manifest["files_sha256"].items():
        file = manifest_path.parent / name
        if file.is_symlink() or not file.is_file():
            raise ValueError("regular data files required")
        with file.open("rb") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("dataset file hash mismatch")
    framework_requirements = []
    for name, version in (
        ("numpy", numpy_version),
        ("scipy", scipy_version),
        ("igraph", igraph_version),
    ):
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version) is None:
            raise ValueError("exact observed release versions required")
        framework_requirements.append(f"{name}=={version}")
    digest = hashlib.sha256(raw).hexdigest()
    artifacts = [
        {"name": name, "relative_path": file, "required": True}
        for name, file in (
            ("result", "result.json"),
            ("weights", "W_est.csv"),
            ("provenance", "provenance.json"),
        )
    ]
    rules = [{"kind": "artifact_exists", "subject": a["name"]} for a in artifacts]
    rules += [{"kind": "metric_finite", "subject": name} for name in METRICS]
    rules += [{"kind": "metric_gte", "subject": name, "threshold": "0"} for name in METRICS]
    rules += [{"kind": "metric_lte", "subject": name, "threshold": "1"} for name in ("fdr", "tpr")]
    return ProposalPayloadRequest.model_validate(
        {
            "goal": (
                "Compare lambda1=0.05 and 0.1 on one frozen 200x5 Gaussian SEM dataset; "
                "retain all protocol-valid results. This is a fixed integration benchmark, "
                "not statistical superiority evidence."
            ),
            "experiment_type": "parameter_sweep",
            "repository_snapshot_id": snapshot_id,
            "repository_snapshot_identity": snapshot_identity,
            "train_entrypoint": "experiments/labpilot_linear.py",
            "eval_entrypoint": None,
            "workdir": ".",
            "parameter_matrix": {"lambda1": [0.05, 0.1]},
            "seeds": [7],
            "dataset_reference": {
                "reference_type": "local_path",
                "path_or_uri": dataset_path,
                "stable_identity": digest,
                "reproducibility_level": "strict",
            },
            "environment_expectation": {
                "python_profile": "notears-cpu-v1",
                "framework_requirements": framework_requirements,
                "device_requirement": None,
                "cuda_requirement": None,
            },
            "metrics": [
                {"name": name, "source_artifact": "result", "split": None} for name in METRICS
            ],
            "artifacts": artifacts,
            "validation_rules": rules,
            "timeout_seconds": 60,
            "max_retries": 0,
            "train_argv_template": [
                "--dataset",
                "{dataset}",
                "--dataset-manifest-sha256",
                digest,
                "--output-dir",
                "{output_dir}",
                "--lambda1",
                "{parameter:lambda1}",
                "--seed",
                "{seed}",
                "--max_iter",
                "100",
                "--h_tol",
                "1e-8",
                "--rho_max",
                "1e16",
                "--w_threshold",
                "0.3",
            ],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--snapshot-id", type=UUID, required=True)
    parser.add_argument("--snapshot-identity", required=True)
    parser.add_argument("--dataset-path", required=True)
    for name in ("numpy", "scipy", "igraph"):
        parser.add_argument(f"--{name}-version", required=True)
    args = parser.parse_args()
    payload = build_proposal(
        args.manifest,
        snapshot_id=args.snapshot_id,
        snapshot_identity=args.snapshot_identity,
        dataset_path=args.dataset_path,
        numpy_version=args.numpy_version,
        scipy_version=args.scipy_version,
        igraph_version=args.igraph_version,
    )
    payload.to_domain()  # Existing domain contract; this is not formal Verification.
    print(payload.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
