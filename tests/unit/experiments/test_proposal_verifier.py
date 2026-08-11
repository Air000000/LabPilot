from dataclasses import replace
from uuid import UUID

import pytest

from labpilot.experiments.proposal_ports import (
    ApprovalSourceState,
    ProposalPathFact,
    ProposalSnapshotState,
)
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
)
from labpilot.experiments.proposal_verifier import ProposalVerifier

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_PROJECT_ID = UUID("11111111-1111-1111-1111-111111111112")
SNAPSHOT_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_SNAPSHOT_ID = UUID("22222222-2222-2222-2222-222222222223")
SNAPSHOT_IDENTITY = "a" * 64


class FakeRepositoryFacts:
    def __init__(self) -> None:
        self.snapshot: ProposalSnapshotState | Exception = ProposalSnapshotState(
            repository_snapshot_id=SNAPSHOT_ID,
            repository_snapshot_identity=SNAPSHOT_IDENTITY,
            project_id=PROJECT_ID,
            is_clean=True,
        )
        self.paths: dict[str, ProposalPathFact] = {
            "train.py": ProposalPathFact("train.py", "regular", "100644"),
            "eval.py": ProposalPathFact("eval.py", "regular", "100644"),
        }
        self.directories = {"src"}
        self.approval_source_state = self._raise_if_called

    async def snapshot_state(self, repository_snapshot_id: UUID) -> ProposalSnapshotState:
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot

    async def path_fact(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> ProposalPathFact | None:
        return self.paths.get(relative_path)

    async def directory_exists(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> bool:
        return relative_path in self.directories

    async def _raise_if_called(
        self,
        repository_snapshot_id: UUID,
    ) -> ApprovalSourceState:
        raise AssertionError("approval_source_state must not be called by verifier")


def _dataset(
    *,
    stable_identity: str | None = "dataset:v1",
    reproducibility_level: str = "strict",
) -> DatasetReference:
    value = DatasetReference(
        reference_type="uri",
        path_or_uri="s3://datasets/example",
        stable_identity=stable_identity,
        reproducibility_level="best_effort",
    )
    object.__setattr__(value, "reproducibility_level", reproducibility_level)
    return value


def _payload(**changes: object) -> ProposalPayload:
    payload = ProposalPayload(
        goal="Reproduce the baseline",
        experiment_type="reproduction",
        repository_snapshot_id=SNAPSHOT_ID,
        repository_snapshot_identity=SNAPSHOT_IDENTITY,
        train_entrypoint="train.py",
        eval_entrypoint="eval.py",
        workdir=".",
        parameter_matrix={"learning_rate": (0.1,)},
        seeds=(7,),
        dataset_reference=_dataset(),
        environment_expectation=EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=("torch==2.7.0",),
            device_requirement=None,
            cuda_requirement=None,
        ),
        metrics=(MetricDeclaration("accuracy", "model", "test"),),
        artifacts=(ArtifactDeclaration("model", "out/model.pt", True),),
        validation_rules=(
            ValidationRule("artifact_exists", "model"),
            ValidationRule("metric_finite", "accuracy"),
        ),
        timeout_seconds=3600,
        max_retries=1,
    )
    for field, value in changes.items():
        object.__setattr__(payload, field, value)
    return payload


async def _verify(
    payload: ProposalPayload,
    fake: FakeRepositoryFacts | None = None,
):
    return await ProposalVerifier(fake or FakeRepositoryFacts()).verify(
        project_id=PROJECT_ID,
        payload=payload,
    )


def _codes(result: object) -> set[str]:
    return {finding.code for finding in result.findings}  # type: ignore[attr-defined]


async def test_valid_proposal_passes_without_live_approval_readiness() -> None:
    fake = FakeRepositoryFacts()
    fake.approval_source_state = fake._raise_if_called

    result = await _verify(_payload(), fake)

    assert result.passed is True
    assert result.findings == ()


async def test_missing_snapshot_fails() -> None:
    fake = FakeRepositoryFacts()
    fake.snapshot = RuntimeError("snapshot_not_found")

    result = await _verify(_payload(), fake)

    assert result.passed is False
    assert "snapshot_missing" in _codes(result)


async def test_wrong_snapshot_fails() -> None:
    fake = FakeRepositoryFacts()
    fake.snapshot = replace(fake.snapshot, repository_snapshot_id=OTHER_SNAPSHOT_ID)  # type: ignore[arg-type]

    result = await _verify(_payload(), fake)

    assert "snapshot_id_mismatch" in _codes(result)


async def test_snapshot_project_mismatch_fails() -> None:
    fake = FakeRepositoryFacts()
    fake.snapshot = replace(fake.snapshot, project_id=OTHER_PROJECT_ID)  # type: ignore[arg-type]

    result = await _verify(_payload(), fake)

    assert "snapshot_project_mismatch" in _codes(result)


async def test_stored_dirty_snapshot_fails() -> None:
    fake = FakeRepositoryFacts()
    fake.snapshot = replace(fake.snapshot, is_clean=False)  # type: ignore[arg-type]

    result = await _verify(_payload(), fake)

    assert "snapshot_dirty" in _codes(result)


async def test_snapshot_identity_mismatch_fails() -> None:
    fake = FakeRepositoryFacts()
    fake.snapshot = replace(fake.snapshot, repository_snapshot_identity="b" * 64)  # type: ignore[arg-type]

    result = await _verify(_payload(), fake)

    assert "snapshot_identity_mismatch" in _codes(result)


@pytest.mark.parametrize("entrypoint", ["train_entrypoint", "eval_entrypoint"])
@pytest.mark.parametrize("fact", [None, ProposalPathFact("entry.py", "symlink", "120000")])
async def test_entrypoint_must_exist_and_be_regular(
    entrypoint: str,
    fact: ProposalPathFact | None,
) -> None:
    fake = FakeRepositoryFacts()
    path = "train.py" if entrypoint == "train_entrypoint" else "eval.py"
    if fact is None:
        fake.paths.pop(path)
    else:
        fake.paths[path] = replace(fact, relative_path=path)

    result = await _verify(_payload(), fake)

    assert f"{entrypoint}_invalid" in _codes(result)


async def test_eval_entrypoint_is_optional() -> None:
    result = await _verify(_payload(eval_entrypoint=None))

    assert result.passed is True


@pytest.mark.parametrize("workdir", ["/tmp", "../escape", "a/../b", "a\\b", "bad\x00dir"])
async def test_workdir_must_be_safe(workdir: str) -> None:
    result = await _verify(_payload(workdir=workdir))

    assert "workdir_invalid" in _codes(result)


async def test_dot_workdir_means_repository_root() -> None:
    fake = FakeRepositoryFacts()
    fake.directories.clear()

    result = await _verify(_payload(workdir="."), fake)

    assert result.passed is True


async def test_non_root_workdir_must_exist() -> None:
    result = await _verify(_payload(workdir="missing"))

    assert "workdir_missing" in _codes(result)


@pytest.mark.parametrize("seeds", [(), tuple(range(21)), (1, 1), (-1,)])
async def test_seed_limits_uniqueness_and_non_negative_values(seeds: tuple[int, ...]) -> None:
    result = await _verify(_payload(seeds=seeds))

    assert "seeds_invalid" in _codes(result)


async def test_parameter_matrix_allows_zero_keys_as_one_planned_trial() -> None:
    result = await _verify(_payload(parameter_matrix={}))

    assert result.passed is True


async def test_parameter_matrix_rejects_more_than_sixteen_keys() -> None:
    result = await _verify(
        _payload(parameter_matrix={f"p{index}": (index,) for index in range(17)})
    )

    assert "parameter_key_count" in _codes(result)


@pytest.mark.parametrize("key", ["", " \t", "bad\ud800key"])
async def test_parameter_keys_must_be_non_blank_valid_utf8(key: str) -> None:
    result = await _verify(_payload(parameter_matrix={key: (1,)}))

    assert "parameter_key_invalid" in _codes(result)


@pytest.mark.parametrize("values", [(), tuple(range(21))])
async def test_parameter_value_list_must_have_one_to_twenty_values(
    values: tuple[int, ...],
) -> None:
    result = await _verify(_payload(parameter_matrix={"p": values}))

    assert "parameter_value_count" in _codes(result)


async def test_expanded_trial_count_must_not_exceed_twenty() -> None:
    result = await _verify(
        _payload(parameter_matrix={"a": tuple(range(5)), "b": tuple(range(5))})
    )

    assert "trial_count_invalid" in _codes(result)


@pytest.mark.parametrize("experiment_type", ["parameter_sweep", "comparison", "ablation"])
async def test_multi_trial_experiment_types_require_multiple_parameter_combinations(
    experiment_type: str,
) -> None:
    result = await _verify(_payload(experiment_type=experiment_type))

    assert "experiment_trial_requirement" in _codes(result)


async def test_stability_requires_two_distinct_seeds() -> None:
    result = await _verify(_payload(experiment_type="stability", seeds=(1,)))

    assert "stability_seed_requirement" in _codes(result)


async def test_best_effort_dataset_without_identity_is_warning_only() -> None:
    result = await _verify(
        _payload(
            dataset_reference=_dataset(
                stable_identity=None,
                reproducibility_level="best_effort",
            )
        )
    )

    assert result.passed is True
    assert [(finding.code, finding.severity) for finding in result.findings] == [
        ("dataset_best_effort", "warning")
    ]


async def test_strict_dataset_without_identity_fails() -> None:
    result = await _verify(
        _payload(dataset_reference=_dataset(stable_identity=None, reproducibility_level="strict"))
    )

    assert "dataset_identity_required" in _codes(result)


async def test_artifact_names_must_be_unique() -> None:
    result = await _verify(
        _payload(
            artifacts=(
                ArtifactDeclaration("model", "out/model.pt", True),
                ArtifactDeclaration("model", "out/model-2.pt", False),
            )
        )
    )

    assert "artifact_name_duplicate" in _codes(result)


async def test_required_artifact_path_must_be_safe() -> None:
    artifact = ArtifactDeclaration("model", "out/model.pt", True)
    object.__setattr__(artifact, "relative_path", "../model.pt")

    result = await _verify(_payload(artifacts=(artifact,)))

    assert "artifact_path_invalid" in _codes(result)


async def test_required_artifact_needs_validation_coverage() -> None:
    result = await _verify(
        _payload(validation_rules=(ValidationRule("metric_finite", "accuracy"),))
    )

    assert "artifact_validation_missing" in _codes(result)


async def test_metric_names_must_be_unique() -> None:
    result = await _verify(
        _payload(
            metrics=(
                MetricDeclaration("accuracy", "model", "test"),
                MetricDeclaration("accuracy", "model", "train"),
            )
        )
    )

    assert "metric_name_duplicate" in _codes(result)


async def test_metric_must_reference_declared_artifact() -> None:
    result = await _verify(
        _payload(metrics=(MetricDeclaration("accuracy", "missing", "test"),))
    )

    assert "metric_artifact_missing" in _codes(result)


async def test_metric_needs_validation_coverage() -> None:
    result = await _verify(
        _payload(validation_rules=(ValidationRule("artifact_exists", "model"),))
    )

    assert "metric_validation_missing" in _codes(result)


@pytest.mark.parametrize("timeout_seconds", [0, 86401])
async def test_timeout_must_be_between_one_and_86400(timeout_seconds: int) -> None:
    result = await _verify(_payload(timeout_seconds=timeout_seconds))

    assert "timeout_invalid" in _codes(result)


@pytest.mark.parametrize("max_retries", [-1, 4])
async def test_max_retries_must_be_between_zero_and_three(max_retries: int) -> None:
    result = await _verify(_payload(max_retries=max_retries))

    assert "max_retries_invalid" in _codes(result)


async def test_findings_and_result_hash_are_deterministic_for_mapping_order() -> None:
    first = _payload(parameter_matrix={"": (1,), "bad\ud800key": (1,)}, timeout_seconds=0)
    second = _payload(
        parameter_matrix={"bad\ud800key": (1,), "": (1,)},
        timeout_seconds=0,
    )

    first_result = await _verify(first)
    second_result = await _verify(second)

    assert first_result.findings == second_result.findings
    assert first_result.result_hash == second_result.result_hash
    assert first_result.findings == tuple(
        sorted(
            first_result.findings,
            key=lambda finding: (
                finding.severity,
                finding.code,
                finding.field or "",
                finding.message,
            ),
        )
    )
