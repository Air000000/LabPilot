from types import SimpleNamespace
from uuid import UUID

from labpilot.execution.types import VersionExecutionSemantics


def _semantics() -> VersionExecutionSemantics:
    return VersionExecutionSemantics(
        project_id=UUID("10000000-0000-4000-8000-000000000001"),
        experiment_version_id=UUID("20000000-0000-4000-8000-000000000002"),
        experiment_version_identity="a" * 64,
        proposal_revision_id=UUID("30000000-0000-4000-8000-000000000003"),
        proposal_payload_hash="b" * 64,
        repository_snapshot_id=UUID("40000000-0000-4000-8000-000000000004"),
        repository_snapshot_identity="c" * 64,
        train_entrypoint="train.py",
        workdir=".",
        train_argv_template=("{seed}",),
        parameter_matrix={"β": (2, 1), "a": ("second", "first")},
        seeds=(9, 7),
        dataset_reference_type="uri",
        dataset_path_or_uri="dataset:v1",
        dataset_reference_hash="d" * 64,
        environment_expectation_hash="e" * 64,
        python_profile="python-3.12",
        framework_requirements=(),
        device_requirement=None,
        cuda_requirement=None,
        artifact_relative_paths=(),
        timeout_seconds=30,
        max_retries=1,
    )


def test_expand_trials_orders_utf8_keys_then_values_then_seeds() -> None:
    from labpilot.execution.batch_service import expand_trial_inputs

    expanded = expand_trial_inputs(_semantics())

    assert [(item.resolved_parameters, item.seed) for item in expanded] == [
        ({"a": "second", "β": 2}, 9),
        ({"a": "second", "β": 2}, 7),
        ({"a": "second", "β": 1}, 9),
        ({"a": "second", "β": 1}, 7),
        ({"a": "first", "β": 2}, 9),
        ({"a": "first", "β": 2}, 7),
        ({"a": "first", "β": 1}, 9),
        ({"a": "first", "β": 1}, 7),
    ]
    assert [item.trial_ordinal for item in expanded] == list(range(1, 9))


def test_retry_allowlist_is_finite_and_exact() -> None:
    from labpilot.execution.batch_service import is_retryable_infrastructure_failure

    retryable = {
        "PREPARATION_TEMPORARY_IO",
        "RUNNER_UNAVAILABLE_BEFORE_SEND",
        "PHYSICAL_PREEMPTION_PROVEN",
    }
    for code in retryable:
        assert is_retryable_infrastructure_failure(
            SimpleNamespace(
                preparation_failure_code=code,
                execution_issue_code=None,
            )
        )
    for code in {
        "PROCESS_TERMINAL_UNCLASSIFIED",
        "PHYSICAL_STATE_UNKNOWN",
        "IDENTITY_CONFLICT",
        "OOM",
        None,
    }:
        assert not is_retryable_infrastructure_failure(
            SimpleNamespace(
                preparation_failure_code=code,
                execution_issue_code=None,
            )
        )

