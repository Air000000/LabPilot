from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from labpilot.execution.ports import ExecutionInputError
from labpilot.execution.types import VersionExecutionSemantics

VERSION_ID = UUID("10000000-0000-4000-8000-000000000001")


def _semantics() -> VersionExecutionSemantics:
    return VersionExecutionSemantics(
        project_id=UUID("20000000-0000-4000-8000-000000000001"),
        experiment_version_id=VERSION_ID,
        experiment_version_identity="a" * 64,
        proposal_revision_id=UUID("30000000-0000-4000-8000-000000000001"),
        proposal_payload_hash="b" * 64,
        repository_snapshot_id=UUID("40000000-0000-4000-8000-000000000001"),
        repository_snapshot_identity="c" * 64,
        train_entrypoint="train.py",
        workdir=".",
        train_argv_template=(
            "--name",
            "{parameter:name}",
            "--rate",
            "{parameter:rate}",
            "--enabled",
            "{parameter:enabled}",
            "--empty",
            "{parameter:empty}",
            "--seed",
            "{seed}",
            "--dataset",
            "{dataset}",
            "--output",
            "{output_dir}",
        ),
        parameter_matrix={
            "name": ("demo",),
            "rate": (0.25,),
            "enabled": (True,),
            "empty": (None,),
        },
        seeds=(7,),
        dataset_reference_type="local_path",
        dataset_path_or_uri="/datasets/main",
        dataset_reference_hash="d" * 64,
        environment_expectation_hash="e" * 64,
        python_profile="python-3.12",
        framework_requirements=("torch==2.8.0", "scikit-learn >= 1.7"),
        device_requirement="cpu",
        cuda_requirement=None,
        artifact_relative_paths=("model.bin",),
        timeout_seconds=300,
        max_retries=1,
    )


def test_build_frozen_spec_renders_only_approved_logical_tokens() -> None:
    from labpilot.execution.preparation_service import build_frozen_execution_spec

    spec = build_frozen_execution_spec(
        _semantics(),
        trial_identity="f" * 64,
        resolved_parameters={"name": "demo", "rate": 0.25, "enabled": True, "empty": None},
        seed=7,
    )

    assert spec.argv == (
        "--name", "demo", "--rate", "0.25", "--enabled", "true",
        "--empty", "null", "--seed", "7", "--dataset", "/datasets/main",
        "--output", "{output_dir}",
    )
    assert spec.output_dir_arg_indexes == (13,)
    assert spec.framework_requirements == ("torch==2.8.0", "scikit-learn >= 1.7")
    assert spec.framework_distribution_names == ("torch", "scikit-learn")
    assert spec.environment_expectation_hash == "e" * 64


def test_output_dir_provenance_ignores_parameter_value_collision() -> None:
    from labpilot.execution.preparation_service import build_frozen_execution_spec

    spec = build_frozen_execution_spec(
        replace(
            _semantics(),
            train_argv_template=(
                "--name", "{parameter:name}", "--output", "{output_dir}"
            ),
        ),
        trial_identity="f" * 64,
        resolved_parameters={"name": "{output_dir}"},
        seed=7,
    )

    assert spec.argv == (
        "--name", "{output_dir}", "--output", "{output_dir}"
    )
    assert spec.output_dir_arg_indexes == (3,)


def test_output_dir_provenance_ignores_dataset_value_collision() -> None:
    from labpilot.execution.preparation_service import build_frozen_execution_spec

    spec = build_frozen_execution_spec(
        replace(
            _semantics(),
            dataset_path_or_uri="{output_dir}",
            train_argv_template=("{dataset}", "{output_dir}"),
        ),
        trial_identity="f" * 64,
        resolved_parameters={},
        seed=7,
    )

    assert spec.argv == ("{output_dir}", "{output_dir}")
    assert spec.output_dir_arg_indexes == (1,)


def test_output_dir_provenance_retains_multiple_real_slots() -> None:
    from labpilot.execution.preparation_service import build_frozen_execution_spec

    spec = build_frozen_execution_spec(
        replace(
            _semantics(),
            train_argv_template=("{output_dir}", "--copy", "{output_dir}"),
        ),
        trial_identity="f" * 64,
        resolved_parameters={},
        seed=7,
    )

    assert spec.argv == ("{output_dir}", "--copy", "{output_dir}")
    assert spec.output_dir_arg_indexes == (0, 2)


@pytest.mark.parametrize(
    ("semantics", "parameters", "reason_code"),
    [
        (
            replace(_semantics(), train_argv_template=("{parameter:name}",) * 257),
            {"name": "x", "rate": 0.25, "enabled": True, "empty": None},
            "EXECUTION_ARGV_UNSUPPORTED",
        ),
        (
            replace(_semantics(), framework_requirements=(">=2",)),
            {"name": "x", "rate": 0.25, "enabled": True, "empty": None},
            "EXECUTION_ENVIRONMENT_UNSUPPORTED",
        ),
        (
            _semantics(),
            {"name": "x" * 8193, "rate": 0.25, "enabled": True, "empty": None},
            "EXECUTION_ARGV_UNSUPPORTED",
        ),
    ],
)
def test_build_frozen_spec_rejects_unlaunchable_inputs(
    semantics: VersionExecutionSemantics,
    parameters: dict[str, object],
    reason_code: str,
) -> None:
    from labpilot.execution.preparation_service import build_frozen_execution_spec

    with pytest.raises(ExecutionInputError) as captured:
        build_frozen_execution_spec(
            semantics,
            trial_identity="f" * 64,
            resolved_parameters=parameters,
            seed=7,
        )

    assert captured.value.reason_code == reason_code


def test_workspace_permissions_make_source_read_only_and_output_writable(tmp_path: Path) -> None:
    from labpilot.execution.preparation_service import set_workspace_permissions

    source = tmp_path / "source"
    output = tmp_path / "runtime" / "output"
    source.mkdir()
    output.mkdir(parents=True)
    regular = source / "regular.py"
    executable = source / "run.sh"
    regular.write_text("pass\n")
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    set_workspace_permissions(tmp_path)

    assert regular.stat().st_mode & 0o222 == 0
    assert executable.stat().st_mode & 0o222 == 0
    assert executable.stat().st_mode & 0o111
    assert source.stat().st_mode & 0o222 == 0
    assert output.stat().st_mode & 0o220 == 0o220
