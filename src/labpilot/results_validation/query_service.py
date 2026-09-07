from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import func, select

from labpilot.db.uow import SessionContextFactory
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.query_service import (
    AttemptQueryNotFound,
    BatchQueryNotFound,
    TrialQueryNotFound,
)
from labpilot.observability.models import RuntimeEventRow
from labpilot.results_validation.artifact_reader import read_artifact_identity
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import ArtifactIdentity, MissingArtifact
from labpilot.results_validation.validator import canonical_decimal_text

_UNSAFE_ARTIFACT_READER_ERRORS = frozenset({"artifact_path_unsafe", "artifact_not_regular_file"})


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    artifact_id: UUID
    artifact_name: str
    relative_path: str
    content_sha256: str
    byte_size: int
    media_type: str
    availability: str


@dataclass(frozen=True, slots=True)
class MetricObservation:
    metric_observation_id: UUID
    artifact_id: UUID
    metric_name: str
    split: str | None
    numeric_value: str
    extractor_version: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    validation_report_id: UUID
    validation_protocol_hash: str
    extraction_profile_version: str
    validator_version: str
    verdict: str
    reasons: list[object]
    warnings: list[object]


@dataclass(frozen=True, slots=True)
class AttemptResults:
    attempt_id: UUID
    artifacts: tuple[ArtifactResult, ...]
    metrics: tuple[MetricObservation, ...]
    validation_reports: tuple[ValidationReport, ...]


@dataclass(frozen=True, slots=True)
class TrialResultSelection:
    attempt_id: UUID
    validation_report_id: UUID


@dataclass(frozen=True, slots=True)
class TrialResult:
    trial_id: UUID
    selection: TrialResultSelection | None


@dataclass(frozen=True, slots=True)
class BatchSummaryRevision:
    revision_id: UUID
    revision_number: int
    presentation_status: str
    summary_protocol_version: str
    included: list[object]
    excluded: list[object]
    warnings: list[object]
    aggregates: dict[str, object]


@dataclass(frozen=True, slots=True)
class BatchSummary:
    batch_id: UUID
    latest_summary: BatchSummaryRevision | None


@dataclass(frozen=True, slots=True)
class BatchRuntimeEvent:
    aggregate_sequence: int
    event_type: str
    payload_json: dict[str, object]


class ResultQueryService:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory

    async def get_attempt_results(self, attempt_id: UUID) -> AttemptResults:
        async with self._session_context_factory() as session:
            attempt = await session.get(AttemptRow, attempt_id)
            if attempt is None:
                raise AttemptQueryNotFound(attempt_id)
            artifacts = tuple(
                (
                    await session.scalars(
                        select(ArtifactRow)
                        .where(ArtifactRow.attempt_id == attempt_id)
                        .order_by(ArtifactRow.artifact_name, ArtifactRow.artifact_id)
                    )
                ).all()
            )
            metrics = tuple(
                (
                    await session.scalars(
                        select(MetricObservationRow)
                        .join(
                            ArtifactRow,
                            MetricObservationRow.artifact_id == ArtifactRow.artifact_id,
                        )
                        .where(ArtifactRow.attempt_id == attempt_id)
                        .order_by(
                            MetricObservationRow.metric_name,
                            func.coalesce(MetricObservationRow.split, ""),
                            MetricObservationRow.artifact_id,
                            MetricObservationRow.metric_observation_id,
                        )
                    )
                ).all()
            )
            reports = tuple(
                (
                    await session.scalars(
                        select(ValidationReportRow)
                        .where(ValidationReportRow.attempt_id == attempt_id)
                        .order_by(
                            ValidationReportRow.created_at,
                            ValidationReportRow.validation_report_id,
                        )
                    )
                ).all()
            )

        output_root = Path(attempt.workspace_path) / "runtime" / "output"
        return AttemptResults(
            attempt_id=attempt_id,
            artifacts=tuple(_artifact_result(artifact, output_root) for artifact in artifacts),
            metrics=tuple(
                MetricObservation(
                    metric_observation_id=metric.metric_observation_id,
                    artifact_id=metric.artifact_id,
                    metric_name=metric.metric_name,
                    split=metric.split,
                    numeric_value=canonical_decimal_text(metric.numeric_value),
                    extractor_version=metric.extractor_version,
                )
                for metric in sorted(
                    metrics,
                    key=lambda metric: (
                        metric.metric_name,
                        metric.split or "",
                        str(metric.artifact_id),
                        str(metric.metric_observation_id),
                    ),
                )
            ),
            validation_reports=tuple(
                ValidationReport(
                    validation_report_id=report.validation_report_id,
                    validation_protocol_hash=report.validation_protocol_hash,
                    extraction_profile_version=report.extraction_profile_version,
                    validator_version=report.validator_version,
                    verdict=report.verdict,
                    reasons=list(cast(list[object], report.reason_json.get("reasons", []))),
                    warnings=list(cast(list[object], report.warning_json.get("warnings", []))),
                )
                for report in reports
            ),
        )

    async def get_trial_result(self, trial_id: UUID) -> TrialResult:
        async with self._session_context_factory() as session:
            trial = await session.get(TrialRow, trial_id)
            if trial is None:
                raise TrialQueryNotFound(trial_id)
            selection = await session.scalar(
                select(TrialResultSelectionRow).where(TrialResultSelectionRow.trial_id == trial_id)
            )
        if selection is None:
            return TrialResult(trial_id=trial_id, selection=None)
        return TrialResult(
            trial_id=trial_id,
            selection=TrialResultSelection(
                attempt_id=selection.attempt_id,
                validation_report_id=selection.validation_report_id,
            ),
        )

    async def get_batch_summary(self, batch_id: UUID) -> BatchSummary:
        async with self._session_context_factory() as session:
            batch = await session.get(BatchRow, batch_id)
            if batch is None:
                raise BatchQueryNotFound(batch_id)
            revision = await session.scalar(
                select(BatchSummaryRevisionRow)
                .where(BatchSummaryRevisionRow.batch_id == batch_id)
                .order_by(BatchSummaryRevisionRow.revision_number.desc())
                .limit(1)
            )
        if revision is None:
            return BatchSummary(batch_id=batch_id, latest_summary=None)
        excluded = list(revision.excluded_reasons_json)
        return BatchSummary(
            batch_id=batch_id,
            latest_summary=BatchSummaryRevision(
                revision_id=revision.batch_summary_revision_id,
                revision_number=revision.revision_number,
                presentation_status=("complete_with_invalid_results" if excluded else "complete"),
                summary_protocol_version=revision.summary_protocol_version,
                included=list(revision.included_result_refs_json),
                excluded=excluded,
                warnings=list(cast(list[object], revision.warning_json.get("warnings", []))),
                aggregates=dict(revision.aggregates_json),
            ),
        )

    async def ensure_batch_exists(self, batch_id: UUID) -> None:
        async with self._session_context_factory() as session:
            if await session.get(BatchRow, batch_id) is None:
                raise BatchQueryNotFound(batch_id)


    async def list_batch_events_after(
        self, batch_id: UUID, *, after_sequence: int
    ) -> tuple[BatchRuntimeEvent, ...]:
        async with self._session_context_factory() as session:
            events = tuple(
                (
                    await session.scalars(
                        select(RuntimeEventRow)
                        .where(
                            RuntimeEventRow.aggregate_type == "batch",
                            RuntimeEventRow.aggregate_id == str(batch_id),
                            RuntimeEventRow.aggregate_sequence > after_sequence,
                        )
                        .order_by(RuntimeEventRow.aggregate_sequence)
                        .limit(100)
                    )
                ).all()
            )
        return tuple(
            BatchRuntimeEvent(
                aggregate_sequence=event.aggregate_sequence,
                event_type=event.event_type,
                payload_json=dict(event.payload_json),
            )
            for event in events
        )


def _artifact_result(artifact: ArtifactRow, output_root: Path) -> ArtifactResult:
    try:
        observed = read_artifact_identity(output_root, artifact.relative_path, artifact.media_type)
    except ValueError as error:
        if str(error) not in _UNSAFE_ARTIFACT_READER_ERRORS:
            raise
        availability = "corrupted"
    else:
        availability = _availability(artifact, observed)
    return ArtifactResult(
        artifact_id=artifact.artifact_id,
        artifact_name=artifact.artifact_name,
        relative_path=artifact.relative_path,
        content_sha256=artifact.content_sha256,
        byte_size=artifact.byte_size,
        media_type=artifact.media_type,
        availability=availability,
    )


def _availability(artifact: ArtifactRow, observed: ArtifactIdentity | MissingArtifact) -> str:
    if isinstance(observed, MissingArtifact):
        return "missing"
    return "available" if observed.content_sha256 == artifact.content_sha256 else "corrupted"
