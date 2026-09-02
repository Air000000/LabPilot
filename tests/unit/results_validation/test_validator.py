import importlib
from decimal import Decimal

import pytest

from labpilot.execution.types import ObservedEnvironment, ObservedFrameworkVersion
from labpilot.experiments.proposal_types import (
    DatasetReference,
    EnvironmentExpectation,
    ValidationRule,
    environment_expectation_hash,
)


def _validator():
    return importlib.import_module("labpilot.results_validation.validator")


def _dataset(
    *,
    level: str = "strict",
    stable_identity: str | None = "sha256:dataset",
) -> DatasetReference:
    return DatasetReference(
        reference_type="uri",
        path_or_uri="s3://datasets/example",
        stable_identity=stable_identity,
        reproducibility_level=level,  # type: ignore[arg-type]
    )


def _expectation(
    *,
    python_profile: str = "python-3.12",
    frameworks: tuple[str, ...] = (),
    device: str | None = None,
    cuda: str | None = None,
) -> EnvironmentExpectation:
    return EnvironmentExpectation(
        python_profile=python_profile,
        framework_requirements=frameworks,
        device_requirement=device,
        cuda_requirement=cuda,
    )


def _observation(
    expectation: EnvironmentExpectation,
    *,
    environment_hash: str | None = None,
    python_profile: str | None = None,
    frameworks: tuple[ObservedFrameworkVersion, ...] = (),
    cuda_runtime_version: int | None = None,
    cuda_device_count: int | None = None,
    probe_issue_codes: tuple[str, ...] = (),
) -> ObservedEnvironment:
    return ObservedEnvironment(
        schema_version="v1",
        environment_expectation_hash=environment_hash
        if environment_hash is not None
        else environment_expectation_hash(expectation),
        python_profile=python_profile if python_profile is not None else expectation.python_profile,
        runtime_path="/opt/runtimes/python/bin/python",
        python_version="3.12.9",
        framework_versions=frameworks,
        cuda_runtime_version=cuda_runtime_version,
        cuda_device_count=cuda_device_count,
        probe_issue_codes=probe_issue_codes,
    )


def _evaluate(
    *,
    rules: tuple[ValidationRule, ...] = (),
    dataset: DatasetReference | None = None,
    expectation: EnvironmentExpectation | None = None,
    observation: ObservedEnvironment | None = None,
    artifacts: frozenset[str] = frozenset(),
    metrics: dict[str, Decimal] | None = None,
):
    resolved_expectation = expectation or _expectation()
    resolved_observation = observation or _observation(resolved_expectation)
    return _validator().validate(
        validation_rules=rules,
        dataset_reference=dataset or _dataset(),
        environment_expectation=resolved_expectation,
        observed_environment=resolved_observation,
        available_artifact_names=artifacts,
        metric_values=metrics or {},
    )


def _codes(findings) -> tuple[str, ...]:
    return tuple(finding.code for finding in findings)


def test_versions_are_frozen() -> None:
    validator = _validator()

    assert validator.EXTRACTION_PROFILE_VERSION == "result-json-v1"
    assert validator.VALIDATOR_VERSION == "slice3-validator-v1"
    assert validator.SUMMARY_PROTOCOL_VERSION == "slice3-summary-v1"


def test_artifact_exists_rule_uses_the_frozen_rule_even_for_optional_artifacts() -> None:
    rule = ValidationRule("artifact_exists", "optional-output")

    present = _evaluate(rules=(rule,), artifacts=frozenset({"optional-output"}))
    missing = _evaluate(rules=(rule,))

    assert present.verdict.value == "valid"
    assert missing.verdict.value == "invalid"
    assert _codes(missing.reasons) == ("REQUIRED_ARTIFACT_MISSING",)
    assert missing.reasons[0].subject == "optional-output"


@pytest.mark.parametrize("value", (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")))
def test_metric_finite_rejects_nonfinite_decimal(value: Decimal) -> None:
    evaluation = _evaluate(
        rules=(ValidationRule("metric_finite", "score"),),
        metrics={"score": value},
    )

    assert evaluation.verdict.value == "invalid"
    assert _codes(evaluation.reasons) == ("METRIC_NONFINITE",)


def test_metric_finite_reports_a_missing_metric() -> None:
    evaluation = _evaluate(rules=(ValidationRule("metric_finite", "score"),))

    assert evaluation.verdict.value == "invalid"
    assert _codes(evaluation.reasons) == ("METRIC_MISSING",)
    assert evaluation.reasons[0].subject == "score"


@pytest.mark.parametrize(
    ("kind", "value", "threshold", "verdict", "reason_code"),
    (
        ("metric_gte", Decimal("0.82"), Decimal("0.80"), "valid", None),
        ("metric_gte", Decimal("0.80"), Decimal("0.80"), "valid", None),
        ("metric_gte", Decimal("0.61"), Decimal("0.80"), "invalid", "METRIC_BELOW_THRESHOLD"),
        ("metric_lte", Decimal("0.61"), Decimal("0.80"), "valid", None),
        ("metric_lte", Decimal("0.80"), Decimal("0.80"), "valid", None),
        ("metric_lte", Decimal("0.82"), Decimal("0.80"), "invalid", "METRIC_ABOVE_THRESHOLD"),
    ),
)
def test_threshold_rules_compare_decimals_exactly(
    kind: str,
    value: Decimal,
    threshold: Decimal,
    verdict: str,
    reason_code: str | None,
) -> None:
    evaluation = _evaluate(
        rules=(ValidationRule(kind, "score", threshold),),  # type: ignore[arg-type]
        metrics={"score": value},
    )

    assert evaluation.verdict.value == verdict
    assert _codes(evaluation.reasons) == (() if reason_code is None else (reason_code,))


def test_threshold_reason_details_render_decimal_with_the_canonical_encoder() -> None:
    evaluation = _evaluate(
        rules=(ValidationRule("metric_gte", "score", Decimal("0.800")),),
        metrics={"score": Decimal("0.6100")},
    )

    assert dict(evaluation.reasons[0].details) == {
        "actual": "0.61",
        "threshold": "0.8",
    }


def test_best_effort_dataset_without_stable_identity_warns_without_changing_valid_verdict() -> None:
    evaluation = _evaluate(dataset=_dataset(level="best_effort", stable_identity=None))

    assert evaluation.verdict.value == "valid"
    assert _codes(evaluation.warnings) == ("DATASET_BEST_EFFORT_REPRODUCIBILITY",)


def test_corrupt_strict_dataset_input_fails_closed() -> None:
    dataset = object.__new__(DatasetReference)
    object.__setattr__(dataset, "reference_type", "uri")
    object.__setattr__(dataset, "path_or_uri", "s3://datasets/example")
    object.__setattr__(dataset, "stable_identity", None)
    object.__setattr__(dataset, "reproducibility_level", "strict")

    evaluation = _evaluate(dataset=dataset)

    assert evaluation.verdict.value == "error"
    assert _codes(evaluation.reasons) == ("VALIDATION_INPUT_INVALID",)


def test_environment_binding_conflict_precedes_profile_and_probe_insufficiency() -> None:
    expectation = _expectation(frameworks=("torch>=2",))
    observation = _observation(
        expectation,
        environment_hash="wrong",
        python_profile="python-3.11",
        frameworks=(ObservedFrameworkVersion("torch", None),),
        probe_issue_codes=("PROBE_FAILED",),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "error"
    assert "ENVIRONMENT_BINDING_CONFLICT" in _codes(evaluation.reasons)


def test_known_python_profile_mismatch_precedes_probe_insufficiency() -> None:
    expectation = _expectation(frameworks=("torch>=2",))
    observation = _observation(
        expectation,
        python_profile="python-3.11",
        frameworks=(ObservedFrameworkVersion("torch", None),),
        probe_issue_codes=("PROBE_FAILED",),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "invalid"
    assert "PYTHON_PROFILE_MISMATCH" in _codes(evaluation.reasons)


def test_missing_environment_observation_is_inconclusive() -> None:
    expectation = _expectation()

    evaluation = _validator().validate(
        validation_rules=(),
        dataset_reference=_dataset(),
        environment_expectation=expectation,
        observed_environment=None,
        available_artifact_names=frozenset(),
        metric_values={},
    )

    assert evaluation.verdict.value == "inconclusive"
    assert _codes(evaluation.reasons) == ("ENVIRONMENT_OBSERVATION_INSUFFICIENT",)


@pytest.mark.parametrize(
    ("observed_version", "verdict", "reason_code"),
    (
        ("2.7.0", "valid", None),
        ("1.13", "invalid", "FRAMEWORK_VERSION_MISMATCH"),
        (None, "invalid", "FRAMEWORK_MISSING"),
    ),
)
def test_framework_requirement_uses_packaging_versions(
    observed_version: str | None,
    verdict: str,
    reason_code: str | None,
) -> None:
    expectation = _expectation(frameworks=("torch>=2,<3",))
    observation = _observation(
        expectation,
        frameworks=(ObservedFrameworkVersion("Torch", observed_version),),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == verdict
    assert _codes(evaluation.reasons) == (() if reason_code is None else (reason_code,))


@pytest.mark.parametrize(
    ("requirement", "observed_version", "verdict", "reason_code"),
    (
        ("torch>=1,<3", "2.0rc1", "invalid", "FRAMEWORK_VERSION_MISMATCH"),
        ("torch>=2.0rc1,<3", "2.0rc2", "valid", None),
    ),
)
def test_framework_requirement_preserves_standard_prerelease_semantics(
    requirement: str,
    observed_version: str,
    verdict: str,
    reason_code: str | None,
) -> None:
    expectation = _expectation(frameworks=(requirement,))
    observation = _observation(
        expectation,
        frameworks=(ObservedFrameworkVersion("torch", observed_version),),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == verdict
    assert _codes(evaluation.reasons) == (() if reason_code is None else (reason_code,))


def test_normal_observation_without_required_framework_entry_is_invalid_input() -> None:
    expectation = _expectation(frameworks=("torch>=2",))
    observation = _observation(expectation)

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "error"
    assert _codes(evaluation.reasons) == ("VALIDATION_INPUT_INVALID",)


@pytest.mark.parametrize(
    "requirement",
    (
        "torch @ https://example.invalid/torch.whl",
        "torch>=2; python_version >= '3.12'",
        "not a valid requirement @@@",
    ),
)
def test_frozen_framework_requirements_without_deterministic_semantics_are_input_errors(
    requirement: str,
) -> None:
    expectation = _expectation(frameworks=(requirement,))
    observation = _observation(
        expectation,
        frameworks=(ObservedFrameworkVersion("torch", "2.7.0"),),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "error"
    assert _codes(evaluation.reasons) == ("VALIDATION_INPUT_INVALID",)


def test_invalid_observed_framework_version_fails_closed() -> None:
    expectation = _expectation(frameworks=("torch>=2",))
    observation = _observation(
        expectation,
        frameworks=(ObservedFrameworkVersion("torch", "not-a-version"),),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "error"
    assert _codes(evaluation.reasons) == ("VALIDATION_INPUT_INVALID",)


def test_probe_failure_keeps_framework_missing_evidence_inconclusive() -> None:
    expectation = _expectation(frameworks=("torch>=2",))
    observation = _observation(
        expectation,
        frameworks=(ObservedFrameworkVersion("torch", None),),
        probe_issue_codes=("PROBE_FAILED",),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "inconclusive"
    assert _codes(evaluation.reasons) == ("ENVIRONMENT_OBSERVATION_INSUFFICIENT",)


def test_canonical_framework_name_collision_is_an_input_error() -> None:
    expectation = _expectation(frameworks=("torch>=2",))
    observation = _observation(
        expectation,
        frameworks=(
            ObservedFrameworkVersion("Torch", "2.7.0"),
            ObservedFrameworkVersion("torch", "2.7.0"),
        ),
    )

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "error"
    assert _codes(evaluation.reasons) == ("VALIDATION_INPUT_INVALID",)


@pytest.mark.parametrize(
    ("device_count", "verdict", "reason_code"),
    (
        (1, "valid", None),
        (0, "invalid", "CUDA_DEVICE_MISMATCH"),
        (None, "inconclusive", "ENVIRONMENT_OBSERVATION_INSUFFICIENT"),
    ),
)
def test_cuda_device_requirement_distinguishes_known_and_insufficient_evidence(
    device_count: int | None,
    verdict: str,
    reason_code: str | None,
) -> None:
    expectation = _expectation(device="cuda")
    observation = _observation(expectation, cuda_device_count=device_count)

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == verdict
    assert _codes(evaluation.reasons) == (() if reason_code is None else (reason_code,))


@pytest.mark.parametrize(
    ("requirement", "raw_version", "verdict", "reason_code"),
    (
        ("12.8", 12080, "valid", None),
        ("12.8", 12070, "invalid", "CUDA_RUNTIME_MISMATCH"),
        ("12.8", None, "inconclusive", "ENVIRONMENT_OBSERVATION_INSUFFICIENT"),
        ("not-a-version", 12080, "error", "VALIDATION_INPUT_INVALID"),
    ),
)
def test_cuda_runtime_uses_nvidia_major_minor_encoding(
    requirement: str,
    raw_version: int | None,
    verdict: str,
    reason_code: str | None,
) -> None:
    expectation = _expectation(cuda=requirement)
    observation = _observation(expectation, cuda_runtime_version=raw_version)

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == verdict
    assert _codes(evaluation.reasons) == (() if reason_code is None else (reason_code,))


def test_unknown_device_requirement_is_an_input_error() -> None:
    expectation = _expectation(device="mps")
    observation = _observation(expectation)

    evaluation = _evaluate(expectation=expectation, observation=observation)

    assert evaluation.verdict.value == "error"
    assert _codes(evaluation.reasons) == ("VALIDATION_INPUT_INVALID",)


def test_reasons_are_sorted_and_repeated_evaluation_is_identical() -> None:
    rules = (
        ValidationRule("metric_gte", "zeta", Decimal("1.0")),
        ValidationRule("artifact_exists", "alpha"),
        ValidationRule("metric_finite", "beta"),
    )

    first = _evaluate(rules=rules)
    second = _evaluate(rules=rules)

    assert tuple((reason.code, reason.subject) for reason in first.reasons) == (
        ("METRIC_MISSING", "beta"),
        ("METRIC_MISSING", "zeta"),
        ("REQUIRED_ARTIFACT_MISSING", "alpha"),
    )
    assert first == second
