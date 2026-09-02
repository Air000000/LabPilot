from __future__ import annotations

import inspect
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.types import (
    ObservedEnvironment,
    ObservedFrameworkVersion,
    observed_environment_value,
)
from labpilot.experiments.proposal_models import (
    ExperimentVersionRow,
    ProposalRevisionRow,
)
from labpilot.experiments.proposal_service import (
    _dataset_from_json,
    _environment_from_json,
    _payload_from_json,
    _validation_from_json,
)
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
    dataset_reference_hash,
    environment_expectation_hash,
    proposal_payload_hash,
    validation_protocol_hash,
)
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.payloads import (
    EventJsonObject,
    EventJsonValue,
    prepare_runtime_event_payload,
    register_runtime_event_payload_schema,
)
from labpilot.results_validation.artifact_reader import (
    capture_result_json_artifact,
    read_artifact_identity,
)
from labpilot.results_validation.extractor import (
    EXTRACTOR_VERSION,
    extract_result_json_v1,
)
from labpilot.results_validation.models import (
    ArtifactRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import (
    ArtifactIdentity,
    ArtifactSnapshot,
    MissingArtifact,
    ProcessAttemptResult,
    ResultProcessingDeferred,
    ResultStateConflict,
    ValidationEvaluation,
    ValidationFinding,
    ValidationVerdict,
)
from labpilot.results_validation.validator import (
    EXTRACTION_PROFILE_VERSION,
    VALIDATOR_VERSION,
    canonical_decimal_text,
    validate,
)

__all__ = [
    "ProcessAttemptResult",
    "ResultApplicationService",
    "ResultProcessingDeferred",
    "ResultStateConflict",
]

_RESULT_EVENT_TYPES = {
    "result.artifact_registered": {
        "attempt_id",
        "artifact_id",
        "artifact_name",
        "content_sha256",
        "byte_size",
        "media_type",
    },
    "result.validation_completed": {
        "attempt_id",
        "validation_report_id",
        "verdict",
        "reason_codes",
        "warning_codes",
        "metric_names",
    },
    "result.trial_selected": {
        "trial_id",
        "attempt_id",
        "validation_report_id",
    },
}


def _validate_result_payload(event_type: str, payload: EventJsonObject) -> None:
    if set(payload) != _RESULT_EVENT_TYPES[event_type]:
        raise ValueError("result event payload fields do not match schema")
    for key, value in payload.items():
        if key in {"byte_size"}:
            if type(value) is not int or value < 0:
                raise ValueError(f"{key} must be a nonnegative integer")
        elif key in {"reason_codes", "warning_codes", "metric_names"}:
            if type(value) is not list or any(type(item) is not str for item in value):
                raise ValueError(f"{key} must be a string list")
        elif type(value) is not str:
            raise ValueError(f"{key} must be a string")


def _payload_validator(event_type: str) -> Callable[[EventJsonObject], None]:
    def validate_payload(payload: EventJsonObject) -> None:
        _validate_result_payload(event_type, payload)

    return validate_payload


for _event_type in _RESULT_EVENT_TYPES:
    register_runtime_event_payload_schema(
        _event_type,
        1,
        _payload_validator(_event_type),
    )


@dataclass(frozen=True, slots=True)
class _Contract:
    attempt_id: UUID
    trial_id: UUID
    batch_id: UUID
    project_id: UUID
    experiment_version_id: UUID
    proposal_revision_id: UUID
    workspace_path: str
    status: str
    terminal_kind: str | None
    exit_code: int | None
    observed_environment_json: dict[str, object] | None
    proposal_payload_hash: str
    dataset_reference_hash: str
    environment_expectation_hash: str
    validation_protocol_hash: str
    payload: ProposalPayload
    dataset_reference: DatasetReference
    environment_expectation: EnvironmentExpectation
    validation_rules: tuple[ValidationRule, ...]


@dataclass(frozen=True, slots=True)
class _ObservedArtifact:
    declaration: ArtifactDeclaration
    identity: ArtifactIdentity | ArtifactSnapshot | None
    missing: bool
    metric_values: tuple[tuple[MetricDeclaration, Decimal], ...]
    content_error: str | None


@dataclass(frozen=True, slots=True)
class _ProcessingError:
    code: str
    subject: str
    failure: str
    details: tuple[tuple[str, str], ...] = ()


class ResultApplicationService:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory

    async def process_attempt(
        self,
        attempt_id: UUID,
    ) -> ProcessAttemptResult | None:
        async with self._session_context_factory() as session:
            attempt = await session.get(AttemptRow, attempt_id)
            if attempt is None:
                raise ResultStateConflict("attempt_not_found")
            if not _eligible(attempt):
                return None
            contract = await _load_contract(session, attempt_id)

        observations = await _observe_artifacts(contract)

        async with UnitOfWork(self._session_context_factory) as uow:
            current = await _load_contract(uow.session, attempt_id, lock_attempt=True)
            if current != contract:
                raise ResultStateConflict("attempt_authority_changed")

            report = await uow.session.scalar(
                select(ValidationReportRow).where(
                    ValidationReportRow.attempt_id == attempt_id,
                    ValidationReportRow.validation_protocol_hash
                    == contract.validation_protocol_hash,
                    ValidationReportRow.extraction_profile_version
                    == EXTRACTION_PROFILE_VERSION,
                    ValidationReportRow.validator_version == VALIDATOR_VERSION,
                )
            )
            artifact_rows, new_artifacts, errors = await _reconcile_artifacts(
                uow.session,
                contract,
                observations,
                report_exists=report is not None,
            )
            metric_rows, metric_errors = await _reconcile_metrics(
                uow.session,
                observations,
                artifact_rows,
                report_exists=report is not None,
            )
            errors.extend(metric_errors)
            evaluation = (
                _processing_evaluation(errors)
                if errors
                else validate(
                    validation_rules=contract.validation_rules,
                    dataset_reference=contract.dataset_reference,
                    environment_expectation=contract.environment_expectation,
                    observed_environment=_observed_environment(
                        contract.observed_environment_json
                    ),
                    available_artifact_names=frozenset(artifact_rows),
                    metric_values={row.metric_name: row.numeric_value for row in metric_rows},
                )
            )
            snapshot = _input_snapshot(
                contract,
                observations,
                artifact_rows,
                metric_rows,
                errors,
            )
            reason_json = _findings_json("reasons", evaluation.reasons)
            warning_json = _findings_json("warnings", evaluation.warnings)

            new_report = False
            if report is None:
                report = ValidationReportRow(
                    validation_report_id=uuid4(),
                    attempt_id=attempt_id,
                    validation_protocol_hash=contract.validation_protocol_hash,
                    extraction_profile_version=EXTRACTION_PROFILE_VERSION,
                    validator_version=VALIDATOR_VERSION,
                    input_snapshot_json=snapshot,
                    verdict=evaluation.verdict.value,
                    reason_json=reason_json,
                    warning_json=warning_json,
                )
                uow.session.add(report)
                await uow.session.flush()
                new_report = True
            elif (
                report.input_snapshot_json != snapshot
                or report.verdict != evaluation.verdict.value
                or report.reason_json != reason_json
                or report.warning_json != warning_json
            ):
                raise ResultStateConflict("validation_report_conflict")

            selected, new_selection = await _reconcile_selection(
                uow.session,
                contract,
                report,
                evaluation.verdict,
            )
            await _append_creation_events(
                uow,
                contract,
                new_artifacts,
                report if new_report else None,
                evaluation,
                metric_rows,
                report if new_selection else None,
            )
            result = ProcessAttemptResult(
                attempt_id=attempt_id,
                validation_report_id=report.validation_report_id,
                verdict=evaluation.verdict,
                selected=selected,
            )
            await uow.commit()
        return result


def _eligible(attempt: AttemptRow) -> bool:
    return (
        attempt.status == "terminal"
        and attempt.terminal_kind == "process_exited"
        and attempt.exit_code == 0
    )


async def _load_contract(
    session: AsyncSession,
    attempt_id: UUID,
    *,
    lock_attempt: bool = False,
) -> _Contract:
    statement = select(AttemptRow).where(AttemptRow.attempt_id == attempt_id)
    if lock_attempt:
        statement = statement.with_for_update()
    attempt = await session.scalar(statement)
    if attempt is None or not _eligible(attempt):
        raise ResultStateConflict("attempt_authority_changed")
    trial = await session.get(TrialRow, attempt.trial_id)
    batch = await session.get(BatchRow, trial.batch_id) if trial is not None else None
    version = (
        await session.get(ExperimentVersionRow, batch.experiment_version_id)
        if batch is not None
        else None
    )
    revision = (
        await session.get(ProposalRevisionRow, version.proposal_revision_id)
        if version is not None
        else None
    )
    if trial is None or batch is None or version is None or revision is None:
        raise ResultStateConflict("result_contract_missing")
    try:
        payload = _payload_from_json(revision.payload_json)
        dataset = _dataset_from_json(version.dataset_reference_json)
        environment = _environment_from_json(version.environment_expectation_json)
        rules = _validation_from_json(version.validation_protocol_json)
    except (KeyError, TypeError, ValueError) as error:
        raise ResultStateConflict("result_contract_invalid") from error
    if (
        revision.proposal_revision_id != version.proposal_revision_id
        or revision.experiment_id != version.experiment_id
        or revision.payload_hash != version.proposal_payload_hash
        or proposal_payload_hash(payload) != version.proposal_payload_hash
        or payload.dataset_reference != dataset
        or dataset_reference_hash(payload.dataset_reference)
        != version.dataset_reference_hash
        or dataset_reference_hash(dataset) != version.dataset_reference_hash
        or payload.environment_expectation != environment
        or environment_expectation_hash(payload.environment_expectation)
        != version.environment_expectation_hash
        or environment_expectation_hash(environment)
        != version.environment_expectation_hash
        or payload.validation_rules != rules
        or validation_protocol_hash(payload.validation_rules)
        != version.validation_protocol_hash
        or validation_protocol_hash(rules) != version.validation_protocol_hash
    ):
        raise ResultStateConflict("result_contract_binding_mismatch")
    return _Contract(
        attempt_id=attempt.attempt_id,
        trial_id=trial.trial_id,
        batch_id=batch.batch_id,
        project_id=batch.project_id,
        experiment_version_id=version.experiment_version_id,
        proposal_revision_id=revision.proposal_revision_id,
        workspace_path=attempt.workspace_path,
        status=attempt.status,
        terminal_kind=attempt.terminal_kind,
        exit_code=attempt.exit_code,
        observed_environment_json=deepcopy(attempt.observed_environment_json),
        proposal_payload_hash=version.proposal_payload_hash,
        dataset_reference_hash=version.dataset_reference_hash,
        environment_expectation_hash=version.environment_expectation_hash,
        validation_protocol_hash=version.validation_protocol_hash,
        payload=payload,
        dataset_reference=dataset,
        environment_expectation=environment,
        validation_rules=rules,
    )


async def _observe_artifacts(
    contract: _Contract,
) -> tuple[_ObservedArtifact, ...]:
    output_root = Path(contract.workspace_path) / "runtime" / "output"
    by_source: dict[str, tuple[MetricDeclaration, ...]] = {}
    for metric in contract.payload.metrics:
        by_source[metric.source_artifact] = (
            *by_source.get(metric.source_artifact, ()),
            metric,
        )
    observations: list[_ObservedArtifact] = []
    for declaration in contract.payload.artifacts:
        metrics = by_source.get(declaration.name, ())
        media_type = "application/json" if metrics else "application/octet-stream"
        try:
            value: ArtifactIdentity | ArtifactSnapshot | MissingArtifact
            if metrics:
                value = capture_result_json_artifact(
                    output_root,
                    declaration.relative_path,
                    media_type,
                )
            else:
                value = read_artifact_identity(
                    output_root,
                    declaration.relative_path,
                    media_type,
                )
            if inspect.isawaitable(value):
                value = await value
        except ValueError as error:
            token = _content_failure_token(error)
            if token is None:
                raise
            observations.append(
                _ObservedArtifact(declaration, None, False, (), token)
            )
            continue
        if isinstance(value, MissingArtifact):
            observations.append(
                _ObservedArtifact(declaration, None, True, (), None)
            )
            continue
        identity = value
        if not metrics:
            observations.append(
                _ObservedArtifact(declaration, identity, False, (), None)
            )
            continue
        assert isinstance(identity, ArtifactSnapshot)
        try:
            extracted = extract_result_json_v1(
                identity,
                {metric.name for metric in metrics},
            )
        except ValueError as error:
            token = _content_failure_token(error)
            if token is None:
                raise
            observations.append(
                _ObservedArtifact(declaration, identity, False, (), token)
            )
            continue
        observations.append(
            _ObservedArtifact(
                declaration,
                identity,
                False,
                tuple((metric, extracted[metric.name]) for metric in metrics),
                None,
            )
        )
    return tuple(observations)


def _content_failure_token(error: ValueError) -> str | None:
    message = str(error)
    if message == "result_json_oversize":
        return message
    if message.startswith("result_json_"):
        return "result_json_invalid"
    if message in {
        "artifact_path_unsafe",
        "artifact_not_regular_file",
        "artifact_changed_during_read",
    }:
        return message
    return None


async def _reconcile_artifacts(
    session: AsyncSession,
    contract: _Contract,
    observations: tuple[_ObservedArtifact, ...],
    *,
    report_exists: bool,
) -> tuple[dict[str, ArtifactRow], list[ArtifactRow], list[_ProcessingError]]:
    persisted = tuple(
        await session.scalars(
            select(ArtifactRow).where(ArtifactRow.attempt_id == contract.attempt_id)
        )
    )
    by_name = {row.artifact_name: row for row in persisted}
    by_path = {row.relative_path: row for row in persisted}
    reconciled: dict[str, ArtifactRow] = {}
    created: list[ArtifactRow] = []
    errors: list[_ProcessingError] = []
    for observation in observations:
        if observation.content_error is not None:
            errors.append(
                _ProcessingError(
                    "VALIDATION_INPUT_INVALID",
                    observation.declaration.name,
                    observation.content_error,
                )
            )
        identity = observation.identity
        if identity is None:
            continue
        named = by_name.get(observation.declaration.name)
        pathed = by_path.get(observation.declaration.relative_path)
        row = named or pathed
        mismatch = named is not pathed if named is not None and pathed is not None else False
        if row is not None:
            mismatch = mismatch or any(
                (
                    row.attempt_id != contract.attempt_id,
                    row.artifact_name != observation.declaration.name,
                    row.relative_path != observation.declaration.relative_path,
                    row.content_sha256 != identity.content_sha256,
                    row.byte_size != identity.byte_size,
                    row.media_type != identity.media_type,
                )
            )
        if mismatch:
            if report_exists:
                raise ResultStateConflict("artifact_provenance_conflict")
            assert row is not None
            errors.append(
                _ProcessingError(
                    "RESULT_PROVENANCE_CONFLICT",
                    observation.declaration.name,
                    "artifact_identity_conflict",
                    (
                        ("expected_content_sha256", row.content_sha256),
                        ("observed_content_sha256", identity.content_sha256),
                        ("expected_byte_size", str(row.byte_size)),
                        ("observed_byte_size", str(identity.byte_size)),
                    ),
                )
            )
            continue
        if row is None:
            row = ArtifactRow(
                artifact_id=uuid4(),
                attempt_id=contract.attempt_id,
                artifact_name=observation.declaration.name,
                relative_path=observation.declaration.relative_path,
                content_sha256=identity.content_sha256,
                byte_size=identity.byte_size,
                media_type=identity.media_type,
            )
            session.add(row)
            await session.flush()
            created.append(row)
            by_name[row.artifact_name] = row
            by_path[row.relative_path] = row
        reconciled[observation.declaration.name] = row
    return reconciled, created, errors


async def _reconcile_metrics(
    session: AsyncSession,
    observations: tuple[_ObservedArtifact, ...],
    artifacts: dict[str, ArtifactRow],
    *,
    report_exists: bool,
) -> tuple[list[MetricObservationRow], list[_ProcessingError]]:
    reconciled: list[MetricObservationRow] = []
    errors: list[_ProcessingError] = []
    for observation in observations:
        artifact = artifacts.get(observation.declaration.name)
        if artifact is None or observation.content_error is not None:
            continue
        for declaration, value in observation.metric_values:
            row = await session.scalar(
                select(MetricObservationRow).where(
                    MetricObservationRow.artifact_id == artifact.artifact_id,
                    MetricObservationRow.metric_name == declaration.name,
                    MetricObservationRow.extractor_version == EXTRACTOR_VERSION,
                )
            )
            if row is not None and (
                row.artifact_id != artifact.artifact_id
                or row.metric_name != declaration.name
                or row.split != declaration.split
                or row.numeric_value != value
                or row.extractor_version != EXTRACTOR_VERSION
            ):
                if report_exists:
                    raise ResultStateConflict("metric_provenance_conflict")
                errors.append(
                    _ProcessingError(
                        "RESULT_PROVENANCE_CONFLICT",
                        declaration.name,
                        "metric_observation_conflict",
                        (
                            (
                                "expected_numeric_value",
                                canonical_decimal_text(row.numeric_value),
                            ),
                            ("observed_numeric_value", canonical_decimal_text(value)),
                        ),
                    )
                )
                continue
            if row is None:
                row = MetricObservationRow(
                    metric_observation_id=uuid4(),
                    artifact_id=artifact.artifact_id,
                    metric_name=declaration.name,
                    split=declaration.split,
                    numeric_value=value,
                    extractor_version=EXTRACTOR_VERSION,
                )
                session.add(row)
                await session.flush()
            reconciled.append(row)
    return reconciled, errors


def _processing_evaluation(errors: list[_ProcessingError]) -> ValidationEvaluation:
    findings = tuple(
        sorted(
            (
                ValidationFinding(
                    code=error.code,
                    subject=error.subject,
                    details=tuple(
                        sorted((("failure", error.failure), *error.details))
                    ),
                )
                for error in errors
            ),
            key=lambda finding: (finding.code, finding.subject),
        )
    )
    return ValidationEvaluation(ValidationVerdict.ERROR, findings, ())


def _observed_environment(value: dict[str, object] | None) -> ObservedEnvironment | None:
    if value is None:
        return None
    try:
        versions = cast(list[dict[str, object]], value["framework_versions"])
        return ObservedEnvironment(
            schema_version=cast(str, value["schema_version"]),
            environment_expectation_hash=cast(
                str, value["environment_expectation_hash"]
            ),
            python_profile=cast(str, value["python_profile"]),
            runtime_path=cast(str, value["runtime_path"]),
            python_version=cast(str, value["python_version"]),
            framework_versions=tuple(
                ObservedFrameworkVersion(
                    cast(str, item["distribution_name"]),
                    cast(str | None, item["installed_version"]),
                )
                for item in versions
            ),
            cuda_runtime_version=cast(int | None, value["cuda_runtime_version"]),
            cuda_device_count=cast(int | None, value["cuda_device_count"]),
            probe_issue_codes=tuple(cast(list[str], value["probe_issue_codes"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ResultStateConflict("observed_environment_invalid") from error


def _input_snapshot(
    contract: _Contract,
    observations: tuple[_ObservedArtifact, ...],
    artifacts: dict[str, ArtifactRow],
    metrics: list[MetricObservationRow],
    errors: list[_ProcessingError],
) -> dict[str, object]:
    artifact_values: list[dict[str, object]] = []
    for observation in observations:
        row = artifacts.get(observation.declaration.name)
        if row is not None:
            artifact_values.append(
                {
                    "artifact_name": row.artifact_name,
                    "relative_path": row.relative_path,
                    "state": "registered",
                    "artifact_id": str(row.artifact_id),
                    "content_sha256": row.content_sha256,
                    "byte_size": row.byte_size,
                    "media_type": row.media_type,
                }
            )
        else:
            artifact_values.append(
                {
                    "artifact_name": observation.declaration.name,
                    "relative_path": observation.declaration.relative_path,
                    "state": "missing" if observation.missing else "error",
                }
            )
    metric_values = [
        {
            "metric_observation_id": str(row.metric_observation_id),
            "artifact_id": str(row.artifact_id),
            "metric_name": row.metric_name,
            "split": row.split,
            "numeric_value": canonical_decimal_text(row.numeric_value),
            "extractor_version": row.extractor_version,
        }
        for row in sorted(
            metrics,
            key=lambda item: (str(item.artifact_id), item.metric_name, item.split or ""),
        )
    ]
    observed = _observed_environment(contract.observed_environment_json)
    return {
        "schema_version": "v1",
        "experiment_version": {
            "experiment_version_id": str(contract.experiment_version_id),
            "validation_protocol_hash": contract.validation_protocol_hash,
            "validation_rules": [
                _validation_rule_value(rule) for rule in contract.validation_rules
            ],
        },
        "dataset_reference": {
            "reference_type": contract.dataset_reference.reference_type,
            "path_or_uri": contract.dataset_reference.path_or_uri,
            "stable_identity": contract.dataset_reference.stable_identity,
            "reproducibility_level": contract.dataset_reference.reproducibility_level,
        },
        "environment_expectation": {
            "python_profile": contract.environment_expectation.python_profile,
            "framework_requirements": list(
                contract.environment_expectation.framework_requirements
            ),
            "device_requirement": contract.environment_expectation.device_requirement,
            "cuda_requirement": contract.environment_expectation.cuda_requirement,
        },
        "observed_environment": (
            observed_environment_value(observed) if observed is not None else None
        ),
        "artifacts": artifact_values,
        "metrics": metric_values,
        "processing_errors": [_processing_error_value(error) for error in errors],
    }


def _validation_rule_value(rule: ValidationRule) -> dict[str, object]:
    value: dict[str, object] = {"kind": rule.kind, "subject": rule.subject}
    if rule.threshold is not None:
        value["threshold"] = canonical_decimal_text(rule.threshold)
    return value


def _processing_error_value(error: _ProcessingError) -> object:
    if error.code == "VALIDATION_INPUT_INVALID" and not error.details:
        return error.failure
    return {
        "code": error.code,
        "subject": error.subject,
        "details": {"failure": error.failure, **dict(error.details)},
    }


def _findings_json(
    name: str,
    findings: tuple[ValidationFinding, ...],
) -> dict[str, object]:
    return {
        name: [
            {
                "code": finding.code,
                "subject": finding.subject,
                "details": dict(finding.details),
            }
            for finding in findings
        ]
    }


async def _reconcile_selection(
    session: AsyncSession,
    contract: _Contract,
    report: ValidationReportRow,
    verdict: ValidationVerdict,
) -> tuple[bool, bool]:
    if verdict is not ValidationVerdict.VALID:
        return False, False
    trial = await session.scalar(
        select(TrialRow)
        .where(TrialRow.trial_id == contract.trial_id)
        .with_for_update()
    )
    if trial is None or trial.batch_id != contract.batch_id:
        raise ResultStateConflict("trial_authority_changed")
    selection = await session.get(TrialResultSelectionRow, contract.trial_id)
    if selection is None:
        selection = TrialResultSelectionRow(
            trial_id=contract.trial_id,
            attempt_id=contract.attempt_id,
            validation_report_id=report.validation_report_id,
        )
        session.add(selection)
        await session.flush()
        return True, True
    selected_attempt = await session.get(AttemptRow, selection.attempt_id)
    selected_report = await session.get(
        ValidationReportRow, selection.validation_report_id
    )
    if (
        selected_attempt is None
        or selected_report is None
        or selected_attempt.trial_id != contract.trial_id
        or selected_report.attempt_id != selected_attempt.attempt_id
        or selected_report.verdict != ValidationVerdict.VALID.value
    ):
        raise ResultStateConflict("trial_selection_conflict")
    return selection.validation_report_id == report.validation_report_id, False


async def _append_creation_events(
    uow: UnitOfWork,
    contract: _Contract,
    artifacts: list[ArtifactRow],
    new_report: ValidationReportRow | None,
    evaluation: ValidationEvaluation,
    metrics: list[MetricObservationRow],
    new_selection_report: ValidationReportRow | None,
) -> None:
    envelopes: list[RuntimeEventEnvelope] = []
    for artifact in artifacts:
        envelopes.append(
            _event(
                contract,
                "result.artifact_registered",
                {
                    "attempt_id": str(contract.attempt_id),
                    "artifact_id": str(artifact.artifact_id),
                    "artifact_name": artifact.artifact_name,
                    "content_sha256": artifact.content_sha256,
                    "byte_size": artifact.byte_size,
                    "media_type": artifact.media_type,
                },
            )
        )
    if new_report is not None:
        envelopes.append(
            _event(
                contract,
                "result.validation_completed",
                {
                    "attempt_id": str(contract.attempt_id),
                    "validation_report_id": str(new_report.validation_report_id),
                    "verdict": evaluation.verdict.value,
                    "reason_codes": [finding.code for finding in evaluation.reasons],
                    "warning_codes": [finding.code for finding in evaluation.warnings],
                    "metric_names": cast(
                        list[EventJsonValue], sorted(row.metric_name for row in metrics)
                    ),
                },
            )
        )
    if new_selection_report is not None:
        envelopes.append(
            _event(
                contract,
                "result.trial_selected",
                {
                    "trial_id": str(contract.trial_id),
                    "attempt_id": str(contract.attempt_id),
                    "validation_report_id": str(
                        new_selection_report.validation_report_id
                    ),
                },
            )
        )
    for envelope in envelopes:
        await append_runtime_event(uow, envelope, enqueue_outbox=False)


def _event(
    contract: _Contract,
    event_type: str,
    payload: EventJsonObject,
) -> RuntimeEventEnvelope:
    return RuntimeEventEnvelope(
        project_id=contract.project_id,
        aggregate_type="batch",
        aggregate_id=str(contract.batch_id),
        event_type=event_type,
        event_schema_version=1,
        payload=prepare_runtime_event_payload(
            event_type=event_type,
            event_schema_version=1,
            payload=payload,
        ),
        occurred_at=None,
        interaction_correlation_id=None,
        workflow_correlation_id=None,
        causation_id=None,
        actor_type="system",
        actor_id=None,
        visibility_class="internal",
        topic="runtime-events",
        partition_key=f"batch:{contract.batch_id}",
    )
