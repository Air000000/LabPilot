from __future__ import annotations

from dataclasses import fields, replace

import pytest

from labpilot.execution import types as execution


def _launch_spec() -> execution.FrozenLaunchSpec:
    workspace = "/srv/labpilot/attempts/33333333333343338333333333333333"
    return execution.FrozenLaunchSpec(
        schema_version="v1",
        workspace_path=workspace,
        source_manifest_sha256="b" * 64,
        python_profile="python-3.12",
        runtime_path="/opt/runtimes/python-3.12/bin/python",
        train_entrypoint_relative_path="train.py",
        workdir_relative_path=".",
        argv=("--lr", "0.1", "--output-dir", f"{workspace}/runtime/output"),
        dataset_reference_type="uri",
        dataset_argument="s3://dataset/v1",
        dataset_reference_hash="c" * 64,
        environment=(
            ("CUDA_VISIBLE_DEVICES", "0"),
            ("PATH", "/opt/runtimes/python-3.12/bin:/usr/bin"),
        ),
        timeout_seconds=3600,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        launch_profile_version="candidate-b-v1",
        launch_policy_identity="d" * 64,
    )


def _observed_environment(*, python_version: str) -> execution.ObservedEnvironment:
    return execution.ObservedEnvironment(
        schema_version="v1",
        environment_expectation_hash="e" * 64,
        python_profile="python-3.12",
        runtime_path="/opt/runtimes/python-3.12/bin/python",
        python_version=python_version,
        framework_versions=(
            execution.ObservedFrameworkVersion("torch", "2.8.0"),
            execution.ObservedFrameworkVersion("xformers", None),
        ),
        cuda_runtime_version=12080,
        cuda_device_count=1,
        probe_issue_codes=(),
    )


def test_frozen_launch_spec_hash_exact_golden_vector() -> None:
    assert execution.frozen_launch_spec_hash(_launch_spec()) == (
        "de171b437b06d85c9a6111765f7b2da3f9e0c1fab1f6b4700f1a17d2061188ff"
    )


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("schema_version", "v2"),
        ("workspace_path", "/srv/labpilot/attempts/other"),
        ("source_manifest_sha256", "a" * 64),
        ("python_profile", "python-3.13"),
        ("runtime_path", "/opt/runtimes/python-3.13/bin/python"),
        ("train_entrypoint_relative_path", "other.py"),
        ("workdir_relative_path", "src"),
        ("argv", ("--lr", "0.2")),
        ("dataset_reference_type", "local_path"),
        ("dataset_argument", "/datasets/v1"),
        ("dataset_reference_hash", "f" * 64),
        ("environment", (("PATH", "/usr/bin"),)),
        ("timeout_seconds", 3599),
        ("execution_user", "other-user"),
        ("execution_group", "other-group"),
        ("launch_profile_version", "candidate-b-v2"),
        ("launch_policy_identity", "f" * 64),
    ],
)
def test_every_frozen_launch_field_participates_in_hash(
    field_name: str,
    changed_value: object,
) -> None:
    original = _launch_spec()
    assert execution.frozen_launch_spec_hash(
        replace(original, **{field_name: changed_value})
    ) != execution.frozen_launch_spec_hash(original)


def test_observed_environment_is_not_a_frozen_launch_hash_field() -> None:
    before = _observed_environment(python_version="3.12.13")
    after = _observed_environment(python_version="3.12.14")

    assert before != after
    assert "observed_environment" not in {
        field.name for field in fields(execution.FrozenLaunchSpec)
    }
    assert execution.frozen_launch_spec_hash(
        _launch_spec()
    ) == execution.frozen_launch_spec_hash(_launch_spec())


def test_identity_conflict_is_not_unknown() -> None:
    assert execution.AttemptStatus.IDENTITY_CONFLICT is not execution.AttemptStatus.UNKNOWN
    assert (
        execution.AttemptStatus.IDENTITY_CONFLICT.value
        != execution.AttemptStatus.UNKNOWN.value
    )


def test_terminal_unclassified_needs_no_invented_exit_code() -> None:
    observation = execution.TerminalObservation(
        terminal_kind=execution.TerminalKind.PROCESS_TERMINAL_UNCLASSIFIED,
        supervisor_result="failed",
    )

    assert observation.exit_code is None
    assert observation.termination_signal is None


def test_classified_terminal_observation_requires_its_exact_cause() -> None:
    with pytest.raises(ValueError, match="exit_code"):
        execution.TerminalObservation(terminal_kind=execution.TerminalKind.PROCESS_EXITED)
    with pytest.raises(ValueError, match="termination_signal"):
        execution.TerminalObservation(
            terminal_kind=execution.TerminalKind.PROCESS_SIGNALED
        )
