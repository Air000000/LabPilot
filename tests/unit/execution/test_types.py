import hashlib
import importlib
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest


def _types():
    return importlib.import_module("labpilot.execution.types")


def test_trial_identity_exact_golden_vector() -> None:
    execution = _types()

    identity = execution.trial_identity(
        experiment_version_identity="a" * 64,
        resolved_parameters={
            "batch_size": 16,
            "enabled": True,
            "learning_rate": 0.1,
            "note": None,
        },
        seed=7,
    )

    assert identity == "81f5af3dfedc5be1aa8edf20cbb78362ba88f96d590dc4a22eda9f8b8fd9ea14"


def test_installation_digest_and_supervisor_identity_exact_golden_vector() -> None:
    execution = _types()
    attempt_id = UUID("33333333-3333-4333-8333-333333333333")

    digest = execution.installation_digest("labpilot-prod-01")
    identity = execution.supervisor_execution_identity(
        installation_identity="labpilot-prod-01",
        attempt_id=attempt_id,
    )

    assert digest == "55f402ae7fcfb369905d882c585b64e8a5a66dd92fa3d0377b1e7d0aa18f1fb9"
    assert identity == (
        "labpilot-v1-55f402ae7fcfb369905d882c585b64e8a5a66dd92fa3d0377b1e7d0aa18f1fb9-"
        "33333333333343338333333333333333"
    )


def test_attempt_workspace_path_is_exact_and_uses_uuid_hex() -> None:
    execution = _types()

    path = execution.attempt_workspace_path(
        Path("/srv/labpilot/attempts"),
        UUID("33333333-3333-4333-8333-333333333333"),
    )

    assert path == "/srv/labpilot/attempts/33333333333343338333333333333333"


def test_source_manifest_identity_exact_golden_vector() -> None:
    execution = _types()
    manifest = execution.SourceManifest(
        schema_version="v1",
        repository_snapshot_id=UUID("22222222-2222-4222-8222-222222222222"),
        repository_snapshot_identity="b" * 64,
        entries=(
            execution.SourceManifestEntry(
                relative_path="README.md",
                file_type="regular",
                file_mode="100644",
                content_sha256=hashlib.sha256(b"hello\n").hexdigest(),
            ),
            execution.SourceManifestEntry(
                relative_path="bin/train",
                file_type="regular",
                file_mode="100755",
                content_sha256=hashlib.sha256(b"#!/bin/sh\n").hexdigest(),
            ),
        ),
    )

    assert execution.source_manifest_hash(manifest) == (
        "455987c5d2ea1a8c69be60f69e7a690719a9b821b5f0d14f0b18fb1596b1e875"
    )


def test_frozen_execution_spec_identity_exact_golden_vector() -> None:
    execution = _types()
    spec = execution.FrozenExecutionSpec(
        schema_version="v1",
        experiment_version_id=UUID("11111111-1111-4111-8111-111111111111"),
        experiment_version_identity="a" * 64,
        trial_identity="c" * 64,
        train_entrypoint_relative_path="train.py",
        workdir_relative_path=".",
        python_profile="python-3.12",
        argv=(
            "--lr",
            "0.1",
            "--seed",
            "7",
            "--dataset",
            "s3://dataset/v1",
            "--output-dir",
            "{output_dir}",
        ),
        output_dir_arg_indexes=(7,),
        dataset_reference_type="uri",
        dataset_argument="s3://dataset/v1",
        dataset_reference_hash="d" * 64,
        environment_expectation_hash="e" * 64,
        framework_requirements=("torch==2.7.0",),
        framework_distribution_names=("torch",),
        device_requirement="cuda",
        cuda_requirement="12.8",
        timeout_seconds=3600,
    )

    identity = execution.frozen_execution_spec_hash(spec)

    assert identity == (
        "aae3a70d772f59f38eb952c15de85b653e6b4310715b24a16283458c1fcd16d0"
    )
    assert execution.frozen_execution_spec_hash(
        replace(spec, output_dir_arg_indexes=())
    ) != identity
    for indexes in ((-1,), (7, 7), (7, 6), (8,)):
        with pytest.raises(ValueError):
            replace(spec, output_dir_arg_indexes=indexes)
