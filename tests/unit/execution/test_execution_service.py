from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from labpilot.execution.ports import ExecutionInputError
from labpilot.execution.types import FrozenExecutionSpec, frozen_launch_spec_hash


def _execution_spec() -> FrozenExecutionSpec:
    return FrozenExecutionSpec(
        schema_version="v1",
        experiment_version_id=UUID("10000000-0000-4000-8000-000000000001"),
        experiment_version_identity="a" * 64,
        trial_identity="b" * 64,
        train_entrypoint_relative_path="train.py",
        workdir_relative_path=".",
        python_profile="python-3.12",
        argv=("--literal", "{output_dir}", "--output", "{output_dir}"),
        output_dir_arg_indexes=(3,),
        dataset_reference_type="local_path",
        dataset_argument="/datasets/main",
        dataset_reference_hash="c" * 64,
        environment_expectation_hash="d" * 64,
        framework_requirements=("torch==2.8",),
        framework_distribution_names=("torch",),
        device_requirement=None,
        cuda_requirement=None,
        timeout_seconds=300,
    )


def test_launch_spec_substitutes_only_proven_output_indexes(tmp_path: Path) -> None:
    from labpilot.execution.execution_service import build_frozen_launch_spec

    workspace = tmp_path / "attempt"
    spec = build_frozen_launch_spec(
        _execution_spec(),
        workspace_path=workspace,
        source_manifest_sha256="e" * 64,
        runtime_path="/opt/python/bin/python",
        environment=(("PATH", "/opt/python/bin:/usr/bin"),),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        launch_profile_version="candidate-b-v1",
        launch_policy_identity="f" * 64,
    )

    assert spec.argv == (
        "--literal",
        "{output_dir}",
        "--output",
        str(workspace / "runtime" / "output"),
    )


def test_changed_reporter_bytes_change_policy_identity_and_rebuilt_h(
    tmp_path: Path,
) -> None:
    from labpilot.execution.execution_service import build_frozen_launch_spec
    from labpilot.execution.runner_policy import (
        candidate_b_v2_profile_manifest,
        runner_policy_identity,
    )

    policy_value = {
        "launch_profile_version": "candidate-b-v2",
        "runtime_profiles": {},
    }
    first_identity = runner_policy_identity(
        policy_value,
        candidate_b_v2_profile_manifest("a" * 64),
    )
    changed_identity = runner_policy_identity(
        policy_value,
        candidate_b_v2_profile_manifest("b" * 64),
    )
    common = {
        "workspace_path": tmp_path / "attempt",
        "source_manifest_sha256": "e" * 64,
        "runtime_path": "/opt/python/bin/python",
        "environment": (),
        "execution_user": "labpilot-train",
        "execution_group": "labpilot-train",
        "launch_profile_version": "candidate-b-v2",
    }

    first = build_frozen_launch_spec(
        _execution_spec(),
        **common,
        launch_policy_identity=first_identity,
    )
    changed = build_frozen_launch_spec(
        _execution_spec(),
        **common,
        launch_policy_identity=changed_identity,
    )

    assert first_identity != changed_identity
    assert frozen_launch_spec_hash(first) != frozen_launch_spec_hash(changed)


@pytest.mark.parametrize(
    "logical",
    [
        replace(_execution_spec(), argv=("x" * 8193,), output_dir_arg_indexes=()),
        replace(_execution_spec(), argv=("x",) * 257, output_dir_arg_indexes=()),
    ],
)
def test_launch_spec_rechecks_final_exec_budget(
    tmp_path: Path, logical: FrozenExecutionSpec
) -> None:
    from labpilot.execution.execution_service import build_frozen_launch_spec

    with pytest.raises(ExecutionInputError, match="EXECUTION_ARGV_UNSUPPORTED"):
        build_frozen_launch_spec(
            logical,
            workspace_path=tmp_path / "attempt",
            source_manifest_sha256="e" * 64,
            runtime_path="/opt/python/bin/python",
            environment=(),
            execution_user="labpilot-train",
            execution_group="labpilot-train",
            launch_profile_version="candidate-b-v1",
            launch_policy_identity="f" * 64,
        )


def test_startup_reconciliation_requires_start_id() -> None:
    from labpilot.execution.execution_service import (
        AttemptExecutionService,
        ReconciliationReason,
    )

    service = AttemptExecutionService(object(), object(), workspace_root=Path("/attempts"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="coordinator_start_id"):
        service.validate_reconciliation(
            ReconciliationReason.STARTUP, coordinator_start_id=None
        )


@pytest.mark.asyncio
async def test_startup_stop_requires_start_id_before_loading_attempt() -> None:
    from labpilot.execution.execution_service import (
        AttemptExecutionService,
        ReconciliationReason,
    )

    service = AttemptExecutionService(object(), object(), workspace_root=Path("/attempts"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="coordinator_start_id"):
        await service.request_stop(uuid4(), reason=ReconciliationReason.STARTUP)


@pytest.mark.asyncio
async def test_periodic_stop_rejects_start_id_before_loading_attempt() -> None:
    from labpilot.execution.execution_service import (
        AttemptExecutionService,
        ReconciliationReason,
    )

    service = AttemptExecutionService(object(), object(), workspace_root=Path("/attempts"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="coordinator_start_id"):
        await service.request_stop(
            uuid4(),
            reason=ReconciliationReason.PERIODIC,
            coordinator_start_id=uuid4(),
        )
