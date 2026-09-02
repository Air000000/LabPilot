from collections.abc import Mapping
from decimal import Decimal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from labpilot.common.canonical_json import canonical_json_bytes
from labpilot.execution.types import ObservedEnvironment
from labpilot.experiments.proposal_types import (
    DatasetReference,
    EnvironmentExpectation,
    ValidationRule,
    environment_expectation_hash,
)
from labpilot.results_validation.extractor import EXTRACTOR_VERSION
from labpilot.results_validation.types import (
    ValidationEvaluation,
    ValidationFinding,
    ValidationVerdict,
)

EXTRACTION_PROFILE_VERSION = EXTRACTOR_VERSION
VALIDATOR_VERSION = "slice3-validator-v1"
SUMMARY_PROTOCOL_VERSION = "slice3-summary-v1"

_FRAMEWORK_PROBE_ISSUES = frozenset({"PROBE_FAILED", "PROBE_OUTPUT_INVALID"})


def validate(
    *,
    validation_rules: tuple[ValidationRule, ...],
    dataset_reference: DatasetReference,
    environment_expectation: EnvironmentExpectation,
    observed_environment: ObservedEnvironment | None,
    available_artifact_names: frozenset[str],
    metric_values: Mapping[str, Decimal],
) -> ValidationEvaluation:
    errors: list[ValidationFinding] = []
    invalid: list[ValidationFinding] = []
    inconclusive: list[ValidationFinding] = []
    warnings: list[ValidationFinding] = []

    _validate_dataset(dataset_reference, errors, warnings)
    _validate_rules(
        validation_rules,
        available_artifact_names,
        metric_values,
        errors,
        invalid,
    )
    _validate_environment(
        environment_expectation,
        observed_environment,
        errors,
        invalid,
        inconclusive,
    )

    reasons = tuple(sorted((*errors, *invalid, *inconclusive), key=_finding_key))
    ordered_warnings = tuple(sorted(warnings, key=_finding_key))
    verdict = (
        ValidationVerdict.ERROR
        if errors
        else ValidationVerdict.INVALID
        if invalid
        else ValidationVerdict.INCONCLUSIVE
        if inconclusive
        else ValidationVerdict.VALID
    )
    return ValidationEvaluation(
        verdict=verdict,
        reasons=reasons,
        warnings=ordered_warnings,
    )


def _validate_dataset(
    dataset: DatasetReference,
    errors: list[ValidationFinding],
    warnings: list[ValidationFinding],
) -> None:
    if dataset.reproducibility_level == "strict":
        if not dataset.stable_identity:
            errors.append(_finding("VALIDATION_INPUT_INVALID", "dataset"))
    elif dataset.reproducibility_level == "best_effort":
        if not dataset.stable_identity:
            warnings.append(_finding("DATASET_BEST_EFFORT_REPRODUCIBILITY", "dataset"))
    else:
        errors.append(_finding("VALIDATION_INPUT_INVALID", "dataset"))


def _validate_rules(
    rules: tuple[ValidationRule, ...],
    artifacts: frozenset[str],
    metrics: Mapping[str, Decimal],
    errors: list[ValidationFinding],
    invalid: list[ValidationFinding],
) -> None:
    for rule in rules:
        if rule.kind == "artifact_exists":
            if rule.subject not in artifacts:
                invalid.append(_finding("REQUIRED_ARTIFACT_MISSING", rule.subject))
            continue

        value = metrics.get(rule.subject)
        if value is None:
            invalid.append(_finding("METRIC_MISSING", rule.subject))
            continue
        if type(value) is not Decimal:
            errors.append(_finding("VALIDATION_INPUT_INVALID", rule.subject))
            continue
        if not value.is_finite():
            invalid.append(_finding("METRIC_NONFINITE", rule.subject))
            continue
        if rule.kind == "metric_finite":
            continue

        threshold = rule.threshold
        if type(threshold) is not Decimal or not threshold.is_finite():
            errors.append(_finding("VALIDATION_INPUT_INVALID", rule.subject))
            continue
        if rule.kind == "metric_gte" and value < threshold:
            invalid.append(
                _finding(
                    "METRIC_BELOW_THRESHOLD",
                    rule.subject,
                    actual=canonical_decimal_text(value),
                    threshold=canonical_decimal_text(threshold),
                )
            )
        elif rule.kind == "metric_lte" and value > threshold:
            invalid.append(
                _finding(
                    "METRIC_ABOVE_THRESHOLD",
                    rule.subject,
                    actual=canonical_decimal_text(value),
                    threshold=canonical_decimal_text(threshold),
                )
            )
        elif rule.kind not in {"metric_gte", "metric_lte"}:
            errors.append(_finding("VALIDATION_INPUT_INVALID", rule.subject))


def _validate_environment(
    expectation: EnvironmentExpectation,
    observed: ObservedEnvironment | None,
    errors: list[ValidationFinding],
    invalid: list[ValidationFinding],
    inconclusive: list[ValidationFinding],
) -> None:
    if observed is None:
        inconclusive.append(_finding("ENVIRONMENT_OBSERVATION_INSUFFICIENT", "environment"))
        return

    try:
        expected_hash = environment_expectation_hash(expectation)
    except ValueError:
        errors.append(_finding("VALIDATION_INPUT_INVALID", "environment"))
        return
    if observed.environment_expectation_hash != expected_hash:
        errors.append(_finding("ENVIRONMENT_BINDING_CONFLICT", "environment"))
        return
    if observed.python_profile != expectation.python_profile:
        invalid.append(_finding("PYTHON_PROFILE_MISMATCH", "python_profile"))

    observed_frameworks = _canonical_frameworks(observed, errors)
    if observed_frameworks is None:
        return
    _validate_frameworks(
        expectation.framework_requirements,
        observed_frameworks,
        observed.probe_issue_codes,
        errors,
        invalid,
        inconclusive,
    )
    _validate_cuda(
        expectation,
        observed,
        errors,
        invalid,
        inconclusive,
    )


def _canonical_frameworks(
    observed: ObservedEnvironment,
    errors: list[ValidationFinding],
) -> dict[str, str | None] | None:
    frameworks: dict[str, str | None] = {}
    for entry in observed.framework_versions:
        if type(entry.distribution_name) is not str or type(entry.installed_version) not in {
            str,
            type(None),
        }:
            errors.append(_finding("VALIDATION_INPUT_INVALID", "framework"))
            return None
        name = canonicalize_name(entry.distribution_name)
        if name in frameworks:
            errors.append(_finding("VALIDATION_INPUT_INVALID", "framework"))
            return None
        frameworks[name] = entry.installed_version
    return frameworks


def _validate_frameworks(
    requirements: tuple[str, ...],
    frameworks: Mapping[str, str | None],
    probe_issue_codes: tuple[str, ...],
    errors: list[ValidationFinding],
    invalid: list[ValidationFinding],
    inconclusive: list[ValidationFinding],
) -> None:
    for frozen_requirement in requirements:
        try:
            requirement = Requirement(frozen_requirement)
        except InvalidRequirement:
            errors.append(_finding("VALIDATION_INPUT_INVALID", frozen_requirement))
            continue
        if requirement.marker is not None or requirement.url is not None:
            errors.append(_finding("VALIDATION_INPUT_INVALID", requirement.name))
            continue

        name = canonicalize_name(requirement.name)
        installed = frameworks.get(name)
        if installed is None:
            if _FRAMEWORK_PROBE_ISSUES.intersection(probe_issue_codes) or name not in frameworks:
                inconclusive.append(_finding("ENVIRONMENT_OBSERVATION_INSUFFICIENT", name))
            else:
                invalid.append(_finding("FRAMEWORK_MISSING", name))
            continue
        try:
            version = Version(installed)
        except InvalidVersion:
            errors.append(_finding("VALIDATION_INPUT_INVALID", name))
            continue
        if not requirement.specifier.contains(version, prereleases=True):
            invalid.append(_finding("FRAMEWORK_VERSION_MISMATCH", name))


def _validate_cuda(
    expectation: EnvironmentExpectation,
    observed: ObservedEnvironment,
    errors: list[ValidationFinding],
    invalid: list[ValidationFinding],
    inconclusive: list[ValidationFinding],
) -> None:
    if expectation.device_requirement not in {None, "cuda"}:
        errors.append(_finding("VALIDATION_INPUT_INVALID", "device_requirement"))
    elif expectation.device_requirement == "cuda":
        if observed.cuda_device_count is None:
            inconclusive.append(
                _finding("ENVIRONMENT_OBSERVATION_INSUFFICIENT", "cuda_device_count")
            )
        elif observed.cuda_device_count <= 0:
            invalid.append(_finding("CUDA_DEVICE_MISMATCH", "cuda"))

    if expectation.cuda_requirement is None:
        return
    try:
        required_version = Version(expectation.cuda_requirement)
    except InvalidVersion:
        errors.append(_finding("VALIDATION_INPUT_INVALID", "cuda_requirement"))
        return
    raw_version = observed.cuda_runtime_version
    if raw_version is None:
        inconclusive.append(
            _finding("ENVIRONMENT_OBSERVATION_INSUFFICIENT", "cuda_runtime_version")
        )
        return
    if type(raw_version) is not int or raw_version < 0:
        errors.append(_finding("VALIDATION_INPUT_INVALID", "cuda_runtime_version"))
        return
    observed_version = Version(f"{raw_version // 1000}.{(raw_version % 1000) // 10}")
    if observed_version != required_version:
        invalid.append(_finding("CUDA_RUNTIME_MISMATCH", "cuda_runtime_version"))


def canonical_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal must be finite")
    return canonical_json_bytes(value).decode("utf-8")


def _finding(code: str, subject: str, **details: str) -> ValidationFinding:
    return ValidationFinding(code=code, subject=subject, details=tuple(sorted(details.items())))


def _finding_key(finding: ValidationFinding) -> tuple[str, str]:
    return finding.code, finding.subject
