from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select

from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.trial_outcome import (
    TrialExecutionOutcome,
    TrialExecutionOutcomeService,
    TrialExecutionState,
)
from labpilot.experiments.proposal_models import ExperimentVersionRow
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.payloads import (
    EventJsonObject,
    prepare_runtime_event_payload,
    register_runtime_event_payload_schema,
)
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import ResultStateConflict
from labpilot.results_validation.validator import (
    EXTRACTION_PROFILE_VERSION,
    SUMMARY_PROTOCOL_VERSION,
    VALIDATOR_VERSION,
    canonical_decimal_text,
)

SUMMARY_MEAN_PRECISION = 28
SUMMARY_MEAN_ROUNDING = ROUND_HALF_EVEN
_SUMMARY_EVENT_TYPE = "result.summary_created"


@dataclass(frozen=True, slots=True)
class RefreshSummaryResult:
    batch_id: UUID
    revision_id: UUID | None
    created: bool
    presentation_status: Literal["not_ready", "complete", "complete_with_invalid_results"]


def _validate_summary_payload(payload: EventJsonObject) -> None:
    required = {
        "batch_summary_revision_id",
        "revision_number",
        "presentation_status",
        "included_count",
        "excluded_count",
    }
    if set(payload) != required:
        raise ValueError("summary event payload fields do not match schema")
    if any(
        type(payload[key]) is not str
        for key in ("batch_summary_revision_id", "presentation_status")
    ):
        raise ValueError("summary event identity fields must be strings")
    if payload["presentation_status"] not in {"complete", "complete_with_invalid_results"}:
        raise ValueError("summary event presentation status is invalid")
    if any(
        type(payload[key]) is not int or cast(int, payload[key]) < 0
        for key in ("revision_number", "included_count", "excluded_count")
    ):
        raise ValueError("summary event counts must be nonnegative integers")


register_runtime_event_payload_schema(_SUMMARY_EVENT_TYPE, 1, _validate_summary_payload)


class BatchSummaryService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        trial_execution_outcome_service: TrialExecutionOutcomeService,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._trial_execution_outcome_service = trial_execution_outcome_service

    async def refresh_summary(self, batch_id: UUID) -> RefreshSummaryResult:
        snapshot = await self._trial_execution_outcome_service.snapshot_batch(batch_id)
        if any(outcome.state is TrialExecutionState.UNRESOLVED for outcome in snapshot.outcomes):
            return _not_ready(batch_id)

        async with UnitOfWork(self._session_context_factory) as uow:
            batch = await uow.session.scalar(
                select(BatchRow).where(BatchRow.batch_id == batch_id).with_for_update()
            )
            if batch is None:
                raise ResultStateConflict("batch_not_found")
            if batch.row_version != snapshot.batch_row_version:
                return _not_ready(batch_id)
            trials = tuple(
                (
                    await uow.session.scalars(
                        select(TrialRow)
                        .where(TrialRow.batch_id == batch_id)
                        .order_by(TrialRow.trial_ordinal)
                    )
                ).all()
            )
            trial_ids = tuple(trial.trial_id for trial in trials)
            attempts = (
                tuple(
                    (
                        await uow.session.scalars(
                            select(AttemptRow)
                            .where(AttemptRow.trial_id.in_(trial_ids))
                            .order_by(AttemptRow.trial_id, AttemptRow.attempt_number)
                        )
                    ).all()
                )
                if trial_ids
                else ()
            )
            if tuple(attempt.attempt_id for attempt in attempts) != snapshot.attempt_ids:
                return _not_ready(batch_id)
            version = await uow.session.get(ExperimentVersionRow, batch.experiment_version_id)
            if version is None:
                raise ResultStateConflict("experiment_version_not_found")
            reports = (
                {
                    item.validation_report_id: item
                    for item in (
                        await uow.session.scalars(
                            select(ValidationReportRow).where(
                                ValidationReportRow.attempt_id.in_(
                                    tuple(attempt.attempt_id for attempt in attempts)
                                )
                            )
                        )
                    ).all()
                }
                if attempts
                else {}
            )
            by_attempt: dict[UUID, list[ValidationReportRow]] = defaultdict(list)
            for report in reports.values():
                by_attempt[report.attempt_id].append(report)

            attempts_by_id = {
                attempt.attempt_id: attempt
                for attempt in attempts
            }
            selections = {
                item.trial_id: item
                for item in (
                    await uow.session.scalars(
                        select(TrialResultSelectionRow).where(
                            TrialResultSelectionRow.trial_id.in_(trial_ids)
                        )
                    )
                ).all()
            }
            state = await _build_state(
                uow,
                trials=trials,
                outcomes=snapshot.outcomes,
                reports_by_attempt=by_attempt,
                attempts_by_id=attempts_by_id,
                reports_by_id=reports,
                selections=selections,
                validation_protocol_hash=version.validation_protocol_hash,
            )
            if state is None:
                return _not_ready(batch_id)
            included, excluded, warnings = state
            aggregates = _aggregates(included)
            presentation_status: Literal["complete", "complete_with_invalid_results"] = (
                "complete_with_invalid_results" if excluded else "complete"
            )
            latest = await uow.session.scalar(
                select(BatchSummaryRevisionRow)
                .where(BatchSummaryRevisionRow.batch_id == batch_id)
                .order_by(BatchSummaryRevisionRow.revision_number.desc())
            )
            if latest is not None and _same_summary(
                latest, included, excluded, warnings, aggregates
            ):
                await uow.commit()
                return RefreshSummaryResult(
                    batch_id=batch_id,
                    revision_id=latest.batch_summary_revision_id,
                    created=False,
                    presentation_status=presentation_status,
                )
            revision = BatchSummaryRevisionRow(
                batch_summary_revision_id=uuid4(),
                batch_id=batch_id,
                revision_number=1 if latest is None else latest.revision_number + 1,
                summary_protocol_version=SUMMARY_PROTOCOL_VERSION,
                included_result_refs_json=included,
                excluded_reasons_json=excluded,
                warning_json={"warnings": warnings},
                aggregates_json=aggregates,
            )
            uow.session.add(revision)
            await uow.session.flush()
            await append_runtime_event(
                uow,
                RuntimeEventEnvelope(
                    project_id=batch.project_id,
                    aggregate_type="batch",
                    aggregate_id=str(batch_id),
                    event_type=_SUMMARY_EVENT_TYPE,
                    event_schema_version=1,
                    payload=prepare_runtime_event_payload(
                        event_type=_SUMMARY_EVENT_TYPE,
                        event_schema_version=1,
                        payload={
                            "batch_summary_revision_id": str(revision.batch_summary_revision_id),
                            "revision_number": revision.revision_number,
                            "presentation_status": presentation_status,
                            "included_count": len(included),
                            "excluded_count": len(excluded),
                        },
                    ),
                    occurred_at=None,
                    interaction_correlation_id=None,
                    workflow_correlation_id=None,
                    causation_id=None,
                    actor_type="system",
                    actor_id=None,
                    visibility_class="internal",
                    topic="runtime-events",
                    partition_key=f"batch:{batch_id}",
                ),
                enqueue_outbox=False,
            )
            await uow.commit()
            return RefreshSummaryResult(
                batch_id=batch_id,
                revision_id=revision.batch_summary_revision_id,
                created=True,
                presentation_status=presentation_status,
            )

        raise AssertionError("unreachable")

def _not_ready(batch_id: UUID) -> RefreshSummaryResult:
    return RefreshSummaryResult(batch_id, None, False, "not_ready")


async def _build_state(
    uow: UnitOfWork,
    *,
    trials: tuple[TrialRow, ...],
    outcomes: tuple[TrialExecutionOutcome, ...],
    reports_by_attempt: dict[UUID, list[ValidationReportRow]],
    attempts_by_id: dict[UUID, AttemptRow],
    reports_by_id: dict[UUID, ValidationReportRow],
    selections: dict[UUID, TrialResultSelectionRow],
    validation_protocol_hash: str,
) -> tuple[list[object], list[object], list[object]] | None:
    outcome_by_trial = {outcome.trial_id: outcome for outcome in outcomes}
    if set(outcome_by_trial) != {trial.trial_id for trial in trials}:
        raise ResultStateConflict("execution_snapshot_trial_mismatch")
    included: list[object] = []
    excluded: list[object] = []
    warnings: list[object] = []
    for trial in trials:
        outcome = outcome_by_trial[trial.trial_id]
        selection = selections.get(trial.trial_id)
        if outcome.state is TrialExecutionState.SUCCESSFUL:
            if outcome.successful_attempt_id is None:
                raise ResultStateConflict("successful_attempt_missing")
            successful_attempt = attempts_by_id.get(outcome.successful_attempt_id)
            if (
                successful_attempt is None
                or successful_attempt.trial_id != trial.trial_id
            ):
                raise ResultStateConflict("successful_attempt_trial_mismatch")
            candidates = [
                report
                for report in reports_by_attempt.get(outcome.successful_attempt_id, [])
                if report.validation_protocol_hash == validation_protocol_hash
                and report.extraction_profile_version == EXTRACTION_PROFILE_VERSION
                and report.validator_version == VALIDATOR_VERSION
            ]
            if not candidates:
                return None
            if len(candidates) != 1:
                raise ResultStateConflict("active_validation_report_ambiguous")
            report = candidates[0]
            warnings.extend(_warnings(trial, outcome.successful_attempt_id, report))
            if report.verdict == "valid":
                if selection is None or not _valid_selection(
                    selection, trial, outcome.successful_attempt_id, report, attempts_by_id
                ):
                    raise ResultStateConflict("valid_result_selection_missing_or_invalid")
                metrics = await _included_metrics(uow, report, outcome.successful_attempt_id)
                included.append(
                    {
                        "trial_id": str(trial.trial_id),
                        "trial_ordinal": trial.trial_ordinal,
                        "selected_attempt_id": str(outcome.successful_attempt_id),
                        "validation_report_id": str(report.validation_report_id),
                        "metrics": metrics,
                    }
                )
            elif report.verdict in {"invalid", "inconclusive", "error"}:
                if selection is not None:
                    _validate_historical_selection(
                        selection,
                        trial,
                        outcome.successful_attempt_id,
                        report,
                        reports_by_id,
                        attempts_by_id,
                    )
                excluded.append(_validation_exclusion(trial, report))
            else:
                raise ResultStateConflict("validation_report_verdict_invalid")
        elif outcome.state in {
            TrialExecutionState.EXHAUSTED_EXECUTION_FAILURE,
            TrialExecutionState.CANCELLED,
        }:
            if selection is not None:
                raise ResultStateConflict("execution_exclusion_has_selection")
            excluded.append(
                {
                    "trial_id": str(trial.trial_id),
                    "trial_ordinal": trial.trial_ordinal,
                    "source": "execution",
                    "exclusion_code": (
                        "EXECUTION_EXHAUSTED"
                        if outcome.state is TrialExecutionState.EXHAUSTED_EXECUTION_FAILURE
                        else "EXECUTION_CANCELLED"
                    ),
                }
            )
        else:
            raise ResultStateConflict("unsettled_execution_reached_summary")
    warnings.sort(key=_warning_key)
    return included, excluded, warnings


def _valid_selection(
    selection: TrialResultSelectionRow,
    trial: TrialRow,
    successful_attempt_id: UUID,
    report: ValidationReportRow,
    attempts_by_id: dict[UUID, AttemptRow],
) -> bool:
    selected_attempt = attempts_by_id.get(selection.attempt_id)
    return (
        selection.trial_id == trial.trial_id
        and selection.attempt_id == successful_attempt_id
        and selection.validation_report_id == report.validation_report_id
        and report.attempt_id == selection.attempt_id
        and report.verdict == "valid"
        and selected_attempt is not None
        and selected_attempt.trial_id == trial.trial_id
    )


def _validate_historical_selection(
    selection: TrialResultSelectionRow,
    trial: TrialRow,
    successful_attempt_id: UUID,
    current_report: ValidationReportRow,
    reports_by_id: dict[UUID, ValidationReportRow],
    attempts_by_id: dict[UUID, AttemptRow],
) -> None:
    if selection.validation_report_id == current_report.validation_report_id:
        raise ResultStateConflict("nonvalid_current_report_selected")
    report = reports_by_id.get(selection.validation_report_id)
    if (
        not _valid_selection(
            selection,
            trial,
            successful_attempt_id,
            report,
            attempts_by_id,
        )
        if report is not None
        else True
    ):
        raise ResultStateConflict("historical_selection_invalid")


def _validation_exclusion(trial: TrialRow, report: ValidationReportRow) -> dict[str, object]:
    code = {
        "invalid": "VALIDATION_INVALID",
        "inconclusive": "VALIDATION_INCONCLUSIVE",
        "error": "VALIDATION_ERROR",
    }[report.verdict]
    return {
        "trial_id": str(trial.trial_id),
        "trial_ordinal": trial.trial_ordinal,
        "source": "validation",
        "exclusion_code": code,
        "validation_report_id": str(report.validation_report_id),
        "validation_reason_codes": _reason_codes(report),
    }


def _reason_codes(report: ValidationReportRow) -> list[str]:
    reasons = report.reason_json.get("reasons") if type(report.reason_json) is dict else None
    if type(reasons) is not list or any(
        type(item) is not dict or type(item.get("code")) is not str for item in reasons
    ):
        raise ResultStateConflict("validation_reasons_invalid")
    return sorted(item["code"] for item in reasons)


def _warnings(
    trial: TrialRow, attempt_id: UUID, report: ValidationReportRow
) -> list[dict[str, object]]:
    raw = report.warning_json.get("warnings") if type(report.warning_json) is dict else None
    if type(raw) is not list:
        raise ResultStateConflict("validation_warnings_invalid")
    rendered: list[dict[str, object]] = []
    for item in raw:
        if (
            type(item) is not dict
            or type(item.get("code")) is not str
            or type(item.get("subject")) is not str
            or type(item.get("details")) is not dict
        ):
            raise ResultStateConflict("validation_warning_invalid")
        try:
            canonical_json_bytes(cast(CanonicalValue, item["details"]))
        except ValueError as error:
            raise ResultStateConflict("validation_warning_details_invalid") from error
        rendered.append(
            {
                "trial_id": str(trial.trial_id),
                "trial_ordinal": trial.trial_ordinal,
                "attempt_id": str(attempt_id),
                "validation_report_id": str(report.validation_report_id),
                "code": item["code"],
                "subject": item["subject"],
                "details": item["details"],
            }
        )
    return rendered


def _warning_key(item: object) -> tuple[object, ...]:
    value = cast(dict[str, object], item)
    return (
        value["trial_ordinal"],
        value["code"],
        value["subject"],
        canonical_json_bytes(cast(CanonicalValue, value["details"])),
    )


async def _included_metrics(
    uow: UnitOfWork, report: ValidationReportRow, successful_attempt_id: UUID
) -> list[dict[str, object]]:
    raw_metrics = (
        report.input_snapshot_json.get("metrics")
        if type(report.input_snapshot_json) is dict
        else None
    )
    if type(raw_metrics) is not list:
        raise ResultStateConflict("validation_metric_snapshot_invalid")
    rendered: list[dict[str, object]] = []
    seen: set[UUID] = set()
    for item in raw_metrics:
        if type(item) is not dict:
            raise ResultStateConflict("validation_metric_snapshot_invalid")
        try:
            raw_metric_id = item["metric_observation_id"]
            raw_artifact_id = item["artifact_id"]
            metric_name = item["metric_name"]
            split = item["split"]
            raw_numeric_value = item["numeric_value"]
            extractor_version = item["extractor_version"]
        except KeyError as error:
            raise ResultStateConflict("validation_metric_snapshot_invalid") from error
        if (
            type(raw_metric_id) is not str
            or type(raw_artifact_id) is not str
            or type(metric_name) is not str
            or (split is not None and type(split) is not str)
            or type(raw_numeric_value) is not str
            or type(extractor_version) is not str
        ):
            raise ResultStateConflict("validation_metric_snapshot_invalid")
        try:
            metric_id = UUID(raw_metric_id)
            artifact_id = UUID(raw_artifact_id)
            value = Decimal(raw_numeric_value)
        except (DecimalException, TypeError, ValueError) as error:
            raise ResultStateConflict("validation_metric_snapshot_invalid") from error
        if (
            metric_id in seen
            or not value.is_finite()
            or raw_numeric_value != canonical_decimal_text(value)
        ):
            raise ResultStateConflict("validation_metric_snapshot_invalid")
        seen.add(metric_id)
        metric = await uow.session.get(MetricObservationRow, metric_id)
        artifact = await uow.session.get(ArtifactRow, artifact_id)
        if (
            metric is None
            or artifact is None
            or metric.artifact_id != artifact_id
            or artifact.attempt_id != successful_attempt_id
            or metric.metric_name != metric_name
            or metric.split != split
            or metric.extractor_version != extractor_version
            or metric.extractor_version != EXTRACTION_PROFILE_VERSION
            or canonical_decimal_text(metric.numeric_value) != canonical_decimal_text(value)
        ):
            raise ResultStateConflict("validation_metric_snapshot_conflict")
        rendered.append(
            {
                "metric_observation_id": str(metric_id),
                "artifact_id": str(artifact_id),
                "metric_name": metric_name,
                "split": split,
                "numeric_value": canonical_decimal_text(metric.numeric_value),
            }
        )
    rendered.sort(key=lambda item: (item["metric_name"], item["split"] or "", item["artifact_id"]))
    return rendered


def _aggregates(included: list[object]) -> dict[str, object]:
    values: dict[str, list[Decimal]] = defaultdict(list)
    for result in included:
        metrics = cast(list[object], cast(dict[str, object], result)["metrics"])
        for metric in metrics:
            item = cast(dict[str, object], metric)
            values[cast(str, item["metric_name"])].append(Decimal(cast(str, item["numeric_value"])))
    return {name: _mean_json(tuple(values[name])) for name in sorted(values)}


def _mean_json(values: tuple[Decimal, ...]) -> dict[str, object]:
    with localcontext(Context(prec=SUMMARY_MEAN_PRECISION, rounding=SUMMARY_MEAN_ROUNDING)):
        mean = sum(values, Decimal(0)) / Decimal(len(values))
    return {"count": len(values), "mean": canonical_decimal_text(mean)}


def _same_summary(
    revision: BatchSummaryRevisionRow,
    included: list[object],
    excluded: list[object],
    warnings: list[object],
    aggregates: dict[str, object],
) -> bool:
    return (
        revision.summary_protocol_version == SUMMARY_PROTOCOL_VERSION
        and revision.included_result_refs_json == included
        and revision.excluded_reasons_json == excluded
        and revision.warning_json == {"warnings": warnings}
        and revision.aggregates_json == aggregates
    )
