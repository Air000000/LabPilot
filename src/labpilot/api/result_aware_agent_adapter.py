from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy import Text, func, literal, select, tuple_
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.result_context import (
    FollowUpRunStore,
    ResultContext,
    ResultContextSource,
    ResultTrialCondition,
)
from labpilot.agent_runtime.result_history import (
    MAX_HISTORY_BATCHES,
    MAX_HISTORY_INPUT_BYTES,
    MAX_HISTORY_TRIALS,
    HistoryBatchEvidence,
    HistoryContext,
    HistoryDesign,
    HistoryExcludedTrial,
    HistoryMetric,
    HistoryOmission,
    HistorySelectedTrial,
    HistoryValidation,
    HistoryWarning,
)
from labpilot.agent_runtime.source_binding import (
    MAX_SOURCE_BINDING_BYTES,
    SourceBinding,
    SourceIdentity,
)
from labpilot.agent_runtime.trial_facts import freeze_trial_facts
from labpilot.agent_runtime.types import AgentRunDetails, AgentRuntimeError
from labpilot.api.source_adoption_evidence import read_adoption_evidence
from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.common.hashing import namespaced_sha256
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.models import (
    RepositoryRegistrationRow,
    RepositorySnapshotRow,
    SourceAdoptionRow,
)
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.proposal_models import (
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
)
from labpilot.experiments.proposal_service import _payload_from_json
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    ValidationReportRow,
)


class SqlResultAwareAgentAdapter(FollowUpRunStore, ResultContextSource):
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        agent_repository: SqlAgentRepository,
        candidate_store: ContentAddressedStore | None = None,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._agent_repository = agent_repository
        self._candidate_store = candidate_store

    async def create_bound_run(self, run: AgentRunDetails) -> AgentRunDetails:
        summary_id = run.source_batch_summary_revision_id
        if summary_id is None:
            raise AgentRuntimeError("source_result_lineage_invalid")
        async with UnitOfWork(self._session_context_factory) as uow:
            initial = await uow.session.get(BatchSummaryRevisionRow, summary_id)
            if initial is None:
                raise AgentRuntimeError("source_summary_not_found")
            batch = await uow.session.scalar(
                select(BatchRow).where(BatchRow.batch_id == initial.batch_id).with_for_update()
            )
            if batch is None:
                raise AgentRuntimeError("source_batch_not_found")
            summary = await uow.session.scalar(
                select(BatchSummaryRevisionRow).where(
                    BatchSummaryRevisionRow.batch_summary_revision_id == summary_id
                )
            )
            if summary is None or summary.batch_id != batch.batch_id:
                raise AgentRuntimeError("source_result_lineage_invalid")
            latest = await uow.session.scalar(
                select(func.max(BatchSummaryRevisionRow.revision_number)).where(
                    BatchSummaryRevisionRow.batch_id == batch.batch_id
                )
            )
            if latest is None or summary.revision_number != latest:
                raise AgentRuntimeError("source_summary_not_latest")
            version = await uow.session.get(ExperimentVersionRow, batch.experiment_version_id)
            if version is None:
                raise AgentRuntimeError("source_result_lineage_invalid")
            proposal = await uow.session.get(ProposalRevisionRow, version.proposal_revision_id)
            experiment = await uow.session.get(ExperimentRow, version.experiment_id)
            snapshot = await uow.session.get(RepositorySnapshotRow, run.repository_snapshot_id)
            if proposal is None or experiment is None or snapshot is None:
                raise AgentRuntimeError("source_result_lineage_invalid")
            registration = await uow.session.get(
                RepositoryRegistrationRow, snapshot.repository_registration_id
            )
            if registration is None:
                raise AgentRuntimeError("source_result_lineage_invalid")
            if (
                batch.project_id != experiment.project_id
                or experiment.project_id != registration.project_id
                or registration.project_id != run.project_id
            ):
                raise AgentRuntimeError("source_project_mismatch")
            if not snapshot.is_clean:
                raise AgentRuntimeError("source_snapshot_not_clean")
            if run.source_adoption_id is None:
                if (
                    run.repository_snapshot_id != version.repository_snapshot_id
                    or snapshot.repository_snapshot_identity != version.repository_snapshot_identity
                ):
                    raise AgentRuntimeError("source_snapshot_mismatch")
            else:
                await self._source_binding(uow.session, run, for_model=True)
            if (
                proposal.experiment_id != experiment.experiment_id
                or version.experiment_id != experiment.experiment_id
            ):
                raise AgentRuntimeError("source_result_lineage_invalid")
            source_context = await _load_source_context(uow.session, summary_id)
            history = await _capture_history_context(
                uow.session,
                project_id=run.project_id,
                source_batch=batch,
                source_version=version,
                source_summary=summary,
                source_context=source_context,
            )
            details = await self._agent_repository.add_run_in_uow(
                uow, replace(run, history_context=history.model_dump(mode="json"))
            )
            await uow.commit()
        return details

    async def load_source_binding(self, run: AgentRunDetails, *, for_model: bool) -> SourceBinding:
        async with self._session_context_factory() as session:
            return await self._source_binding(session, run, for_model=for_model)

    async def _source_binding(
        self, session: AsyncSession, run: AgentRunDetails, *, for_model: bool
    ) -> SourceBinding:
        if run.source_adoption_id is None or run.source_batch_summary_revision_id is None:
            raise AgentRuntimeError("source_adoption_binding_invalid")
        source = await _load_source_context(session, run.source_batch_summary_revision_id)
        adoption = await session.get(SourceAdoptionRow, run.source_adoption_id)
        target = await session.get(RepositorySnapshotRow, run.repository_snapshot_id)
        base = await session.get(
            RepositorySnapshotRow, source.source_proposal.repository_snapshot_id
        )
        project = await session.get(ProjectRow, run.project_id)
        if (
            adoption is None
            or target is None
            or base is None
            or project is None
            or adoption.project_id != run.project_id
            or adoption.base_snapshot_id != base.repository_snapshot_id
            or adoption.target_snapshot_id != target.repository_snapshot_id
            or base.repository_snapshot_id == target.repository_snapshot_id
            or base.repository_snapshot_identity
            != source.source_proposal.repository_snapshot_identity
            or not base.is_clean
            or not target.is_clean
            or base.repository_registration_id != target.repository_registration_id
        ):
            raise AgentRuntimeError("source_adoption_binding_invalid")
        version = await session.get(ExperimentVersionRow, source.source_experiment_version_id)
        if (
            version is None
            or version.experiment_id != source.experiment_id
            or version.repository_snapshot_id != base.repository_snapshot_id
            or version.repository_snapshot_identity != base.repository_snapshot_identity
        ):
            raise AgentRuntimeError("source_adoption_binding_invalid")
        registration = await session.get(
            RepositoryRegistrationRow, target.repository_registration_id
        )
        experiment = await session.get(ExperimentRow, source.experiment_id)
        if (
            registration is None
            or registration.project_id != run.project_id
            or experiment is None
            or experiment.project_id != run.project_id
        ):
            raise AgentRuntimeError("source_adoption_binding_invalid")
        identity = namespaced_sha256(
            "labpilot.source-adoption.v1",
            {
                "project_id": str(run.project_id),
                "package_ref": adoption.package_ref,
                "target_snapshot_id": str(target.repository_snapshot_id),
                "target_snapshot_identity": target.repository_snapshot_identity,
                "base_snapshot_id": str(base.repository_snapshot_id),
                "base_snapshot_identity": base.repository_snapshot_identity,
            },
        )
        if identity != adoption.adoption_identity:
            raise AgentRuntimeError("source_adoption_binding_invalid")
        if for_model and (
            project.status != "active"
            or registration.status != "active"
            or project.primary_repository_registration_id != registration.repository_registration_id
        ):
            raise AgentRuntimeError("source_adoption_not_active")
        evidence = None
        if for_model:
            if self._candidate_store is None:
                raise AgentRuntimeError("source_adoption_evidence_unconfigured")
            evidence = await read_adoption_evidence(
                self._candidate_store,
                package_ref=adoption.package_ref,
                project_id=str(run.project_id),
                base_id=str(base.repository_snapshot_id),
                base_identity=base.repository_snapshot_identity,
            )
        binding = SourceBinding(
            source_adoption_id=adoption.source_adoption_id,
            adoption_identity=adoption.adoption_identity,
            project_id=run.project_id,
            experiment_id=source.experiment_id,
            source_batch_summary_revision_id=run.source_batch_summary_revision_id,
            result_source=SourceIdentity(
                repository_snapshot_id=base.repository_snapshot_id,
                repository_snapshot_identity=base.repository_snapshot_identity,
            ),
            proposal_target=SourceIdentity(
                repository_snapshot_id=target.repository_snapshot_id,
                repository_snapshot_identity=target.repository_snapshot_identity,
            ),
            package_ref=adoption.package_ref,
            evidence=evidence,
        )
        if (
            len(canonical_json_bytes(cast(CanonicalValue, binding.model_dump(mode="json"))))
            > MAX_SOURCE_BINDING_BYTES
        ):
            raise AgentRuntimeError("source_adoption_evidence_too_large")
        return binding

    async def load(self, source_batch_summary_revision_id: UUID) -> ResultContext:
        async with self._session_context_factory() as session:
            return await _load_source_context(session, source_batch_summary_revision_id)


async def _load_source_context(
    session: AsyncSession, source_batch_summary_revision_id: UUID
) -> ResultContext:
    summary = await session.get(BatchSummaryRevisionRow, source_batch_summary_revision_id)
    if summary is None:
        raise AgentRuntimeError("source_summary_not_found")
    batch = await session.get(BatchRow, summary.batch_id)
    if batch is None:
        raise AgentRuntimeError("source_result_lineage_invalid")
    version = await session.get(ExperimentVersionRow, batch.experiment_version_id)
    if version is None:
        raise AgentRuntimeError("source_result_lineage_invalid")
    proposal = await session.get(ProposalRevisionRow, version.proposal_revision_id)
    experiment = await session.get(ExperimentRow, version.experiment_id)
    if proposal is None or experiment is None:
        raise AgentRuntimeError("source_result_lineage_invalid")
    if (
        proposal.experiment_id != experiment.experiment_id
        or version.experiment_id != experiment.experiment_id
    ):
        raise AgentRuntimeError("source_result_lineage_invalid")
    trials = tuple(
        (
            await session.scalars(
                select(TrialRow)
                .where(TrialRow.batch_id == batch.batch_id)
                .order_by(TrialRow.trial_ordinal)
            )
        ).all()
    )
    included = _json_objects(summary.included_result_refs_json)
    excluded = _json_objects(summary.excluded_reasons_json)
    warnings = _json_objects(summary.warning_json.get("warnings", []))
    _require_exact_trial_coverage(trials, included=included, excluded=excluded)
    try:
        return ResultContext(
            experiment_id=experiment.experiment_id,
            source_experiment_version_id=version.experiment_version_id,
            source_version_number=version.version_number,
            source_proposal_revision_id=proposal.proposal_revision_id,
            source_batch_id=batch.batch_id,
            source_batch_summary_revision_id=summary.batch_summary_revision_id,
            source_summary_revision_number=summary.revision_number,
            presentation_status=("complete_with_invalid_results" if excluded else "complete"),
            source_proposal=_payload_from_json(proposal.payload_json),
            trials=tuple(
                ResultTrialCondition(
                    trial_id=trial.trial_id,
                    trial_ordinal=trial.trial_ordinal,
                    resolved_parameters=cast(dict[str, JsonValue], trial.resolved_parameters_json),
                    seed=trial.seed,
                )
                for trial in trials
            ),
            included_results=cast(tuple[dict[str, JsonValue], ...], included),
            excluded_results=cast(tuple[dict[str, JsonValue], ...], excluded),
            warnings=cast(tuple[dict[str, JsonValue], ...], warnings),
            aggregates=cast(dict[str, JsonValue], summary.aggregates_json),
        )
    except (TypeError, ValidationError, ValueError) as exc:
        raise AgentRuntimeError("source_result_lineage_invalid") from exc


async def _capture_history_context(
    session: AsyncSession,
    *,
    project_id: UUID,
    source_batch: BatchRow,
    source_version: ExperimentVersionRow,
    source_summary: BatchSummaryRevisionRow,
    source_context: ResultContext,
) -> HistoryContext:
    latest = (
        select(
            BatchSummaryRevisionRow.batch_summary_revision_id.label("summary_id"),
            BatchSummaryRevisionRow.batch_id,
            func.row_number()
            .over(
                partition_by=BatchSummaryRevisionRow.batch_id,
                order_by=BatchSummaryRevisionRow.revision_number.desc(),
            )
            .label("rank"),
        )
        .join(BatchRow, BatchRow.batch_id == BatchSummaryRevisionRow.batch_id)
        .join(
            ExperimentVersionRow,
            BatchRow.experiment_version_id == ExperimentVersionRow.experiment_version_id,
        )
        .where(
            BatchRow.project_id == project_id,
            ExperimentVersionRow.experiment_id == source_version.experiment_id,
            ExperimentVersionRow.version_number <= source_version.version_number,
            BatchRow.batch_id != source_batch.batch_id,
            tuple_(BatchRow.created_at, BatchRow.batch_id)
            < tuple_(literal(source_batch.created_at), literal(source_batch.batch_id)),
        )
        .subquery()
    )
    candidates = (
        await session.execute(
            select(BatchRow.batch_id, latest.c.summary_id)
            .join(
                ExperimentVersionRow,
                BatchRow.experiment_version_id == ExperimentVersionRow.experiment_version_id,
            )
            .join(latest, latest.c.batch_id == BatchRow.batch_id)
            .where(latest.c.rank == 1)
            .order_by(
                ExperimentVersionRow.version_number.desc(),
                BatchRow.created_at.desc(),
                BatchRow.batch_id.desc(),
            )
            .limit(MAX_HISTORY_BATCHES + 1)
        )
    ).all()
    values: dict[str, object] = {
        "project_id": project_id,
        "experiment_id": source_version.experiment_id,
        "source_batch_id": source_batch.batch_id,
        "source_batch_summary_revision_id": source_summary.batch_summary_revision_id,
        "source_version_number": source_version.version_number,
        "source_batch_created_at": source_batch.created_at,
        "batches": [],
        "omissions": [],
        "has_more_candidates": len(candidates) > MAX_HISTORY_BATCHES,
        "coverage_limited": len(candidates) > MAX_HISTORY_BATCHES,
    }
    batches: list[HistoryBatchEvidence] = []
    omissions: list[HistoryOmission] = []
    for batch_id, summary_id in candidates[:MAX_HISTORY_BATCHES]:
        count = await session.scalar(
            select(func.count()).select_from(TrialRow).where(TrialRow.batch_id == batch_id)
        )
        # Inspect JSON sizes on the server before hydrating any candidate JSON.
        size = await session.scalar(
            select(
                func.octet_length(sql_cast(BatchSummaryRevisionRow.included_result_refs_json, Text))
                + func.octet_length(sql_cast(BatchSummaryRevisionRow.excluded_reasons_json, Text))
                + func.octet_length(sql_cast(BatchSummaryRevisionRow.warning_json, Text))
                + func.octet_length(sql_cast(ProposalRevisionRow.payload_json, Text))
                + func.octet_length(sql_cast(ExperimentVersionRow.dataset_reference_json, Text))
                + func.octet_length(
                    sql_cast(ExperimentVersionRow.environment_expectation_json, Text)
                )
                + func.octet_length(sql_cast(ExperimentVersionRow.validation_protocol_json, Text))
                + select(
                    func.coalesce(
                        func.sum(
                            func.octet_length(sql_cast(TrialRow.resolved_parameters_json, Text))
                        ),
                        0,
                    )
                )
                .where(TrialRow.batch_id == batch_id)
                .scalar_subquery()
            )
            .select_from(BatchSummaryRevisionRow)
            .join(BatchRow, BatchRow.batch_id == BatchSummaryRevisionRow.batch_id)
            .join(
                ExperimentVersionRow,
                ExperimentVersionRow.experiment_version_id == BatchRow.experiment_version_id,
            )
            .join(
                ProposalRevisionRow,
                ProposalRevisionRow.proposal_revision_id
                == ExperimentVersionRow.proposal_revision_id,
            )
            .where(BatchSummaryRevisionRow.batch_summary_revision_id == summary_id)
        )
        if count is None or size is None:
            raise AgentRuntimeError("history_context_invalid")
        if count > MAX_HISTORY_TRIALS or size > MAX_HISTORY_INPUT_BYTES:
            omissions.append(
                HistoryOmission(
                    batch_id=batch_id,
                    summary_revision_id=summary_id,
                    reason="trial_limit" if count > MAX_HISTORY_TRIALS else "input_bytes_limit",
                )
            )
        else:
            candidate = await _history_batch(session, batch_id, summary_id)
            try:
                HistoryContext.model_validate(
                    {
                        **values,
                        "batches": [*batches, candidate],
                        "trial_facts": freeze_trial_facts(source_context, (*batches, candidate)),
                        "omissions": omissions,
                        "coverage_limited": bool(values["has_more_candidates"] or omissions),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                if not isinstance(exc, ValidationError) or not any(
                    error.get("ctx", {}).get("error")
                    and str(error["ctx"]["error"]) == "history_context_too_large"
                    for error in exc.errors()
                ):
                    raise AgentRuntimeError("history_context_invalid") from exc
                omissions.append(
                    HistoryOmission(
                        batch_id=batch_id,
                        summary_revision_id=summary_id,
                        reason="history_bytes_limit",
                    )
                )
            else:
                batches.append(candidate)
    # At most three removals: later omission metadata must also fit the envelope.
    for _ in range(MAX_HISTORY_BATCHES + 1):
        try:
            return HistoryContext.model_validate(
                {
                    **values,
                    "batches": batches,
                    "trial_facts": freeze_trial_facts(source_context, tuple(batches)),
                    "omissions": omissions,
                    "coverage_limited": bool(values["has_more_candidates"] or omissions),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            if (
                not isinstance(exc, ValidationError)
                or not batches
                or not any(
                    str(error.get("ctx", {}).get("error")) == "history_context_too_large"
                    for error in exc.errors()
                )
            ):
                raise AgentRuntimeError("history_context_invalid") from exc
            removed = batches.pop()
            omissions.append(
                HistoryOmission(
                    batch_id=removed.batch_id,
                    summary_revision_id=removed.summary_revision_id,
                    reason="history_bytes_limit",
                )
            )
    raise AgentRuntimeError("history_context_invalid")


async def _history_batch(
    session: AsyncSession, batch_id: UUID, summary_id: UUID
) -> HistoryBatchEvidence:
    try:
        return await _history_batch_checked(session, batch_id, summary_id)
    except (TypeError, ValueError, KeyError) as exc:
        raise AgentRuntimeError("history_context_invalid") from exc


async def _history_batch_checked(
    session: AsyncSession, batch_id: UUID, summary_id: UUID
) -> HistoryBatchEvidence:
    batch = await session.get(BatchRow, batch_id)
    summary = await session.get(
        BatchSummaryRevisionRow,
        summary_id,
        options=(
            load_only(
                BatchSummaryRevisionRow.batch_id,
                BatchSummaryRevisionRow.revision_number,
                BatchSummaryRevisionRow.summary_protocol_version,
                BatchSummaryRevisionRow.included_result_refs_json,
                BatchSummaryRevisionRow.excluded_reasons_json,
                BatchSummaryRevisionRow.warning_json,
            ),
        ),
    )
    if batch is None or summary is None or summary.batch_id != batch_id:
        raise AgentRuntimeError("history_context_invalid")
    version = await session.get(
        ExperimentVersionRow,
        batch.experiment_version_id,
        options=(
            load_only(
                ExperimentVersionRow.experiment_id,
                ExperimentVersionRow.version_number,
                ExperimentVersionRow.proposal_revision_id,
                ExperimentVersionRow.repository_snapshot_id,
                ExperimentVersionRow.repository_snapshot_identity,
                ExperimentVersionRow.dataset_reference_json,
                ExperimentVersionRow.dataset_reference_hash,
                ExperimentVersionRow.environment_expectation_json,
                ExperimentVersionRow.environment_expectation_hash,
                ExperimentVersionRow.validation_protocol_json,
                ExperimentVersionRow.validation_protocol_hash,
            ),
        ),
    )
    if version is None:
        raise AgentRuntimeError("history_context_invalid")
    proposal = await session.get(ProposalRevisionRow, version.proposal_revision_id)
    experiment = await session.get(ExperimentRow, version.experiment_id)
    if (
        proposal is None
        or experiment is None
        or proposal.experiment_id != experiment.experiment_id
        or experiment.project_id != batch.project_id
    ):
        raise AgentRuntimeError("history_context_invalid")
    payload = _payload_from_json(proposal.payload_json)
    if (
        payload.repository_snapshot_id != version.repository_snapshot_id
        or payload.repository_snapshot_identity != version.repository_snapshot_identity
    ):
        raise AgentRuntimeError("history_context_invalid")
    trials = tuple(
        (
            await session.scalars(
                select(TrialRow)
                .where(TrialRow.batch_id == batch_id)
                .order_by(TrialRow.trial_ordinal)
            )
        ).all()
    )
    included, excluded = (
        _json_objects(summary.included_result_refs_json),
        _json_objects(summary.excluded_reasons_json),
    )
    _require_exact_trial_coverage(trials, included=included, excluded=excluded)
    outcomes = {UUID(str(item["trial_id"])): item for item in (*included, *excluded)}
    evidence: list[HistorySelectedTrial | HistoryExcludedTrial] = []
    report_attempts: dict[UUID, UUID] = {}
    for trial in trials:
        item = outcomes[trial.trial_id]
        if item["trial_ordinal"] != trial.trial_ordinal:
            raise AgentRuntimeError("history_context_invalid")
        common = {
            "trial_id": trial.trial_id,
            "trial_ordinal": trial.trial_ordinal,
            "resolved_parameters": trial.resolved_parameters_json,
            "seed": trial.seed,
        }
        validation = None
        report = None
        if "validation_report_id" in item:
            report = await session.get(
                ValidationReportRow,
                UUID(str(item["validation_report_id"])),
                options=(
                    load_only(
                        ValidationReportRow.attempt_id,
                        ValidationReportRow.verdict,
                        ValidationReportRow.validation_protocol_hash,
                        ValidationReportRow.extraction_profile_version,
                        ValidationReportRow.validator_version,
                    ),
                ),
            )
            attempt = (
                None
                if report is None
                else await session.get(
                    AttemptRow, report.attempt_id, options=(load_only(AttemptRow.trial_id),)
                )
            )
            if (
                report is None
                or attempt is None
                or attempt.trial_id != trial.trial_id
                or report.validation_protocol_hash != version.validation_protocol_hash
            ):
                raise AgentRuntimeError("history_context_invalid")
            validation = HistoryValidation(
                validation_report_id=report.validation_report_id,
                validation_protocol_hash=report.validation_protocol_hash,
                extraction_profile_version=report.extraction_profile_version,
                validator_version=report.validator_version,
            )
            report_attempts[report.validation_report_id] = report.attempt_id
        if "selected_attempt_id" in item:
            if (
                report is None
                or validation is None
                or report.verdict != "valid"
                or str(report.attempt_id) != item["selected_attempt_id"]
            ):
                raise AgentRuntimeError("history_context_invalid")
            metrics: list[HistoryMetric] = []
            for metric in _json_objects(item["metrics"]):
                observation = await session.get(
                    MetricObservationRow,
                    UUID(str(metric["metric_observation_id"])),
                    options=(
                        load_only(
                            MetricObservationRow.artifact_id,
                            MetricObservationRow.metric_name,
                            MetricObservationRow.split,
                            MetricObservationRow.extractor_version,
                        ),
                    ),
                )
                artifact = await session.get(ArtifactRow, UUID(str(metric["artifact_id"])))
                if (
                    observation is None
                    or artifact is None
                    or observation.artifact_id != artifact.artifact_id
                    or artifact.attempt_id != report.attempt_id
                    or observation.metric_name != metric["metric_name"]
                    or observation.split != metric["split"]
                ):
                    raise AgentRuntimeError("history_context_invalid")
                metrics.append(
                    HistoryMetric(
                        metric_name=observation.metric_name,
                        split=observation.split,
                        numeric_value=cast(str, metric["numeric_value"]),
                        artifact_id=artifact.artifact_id,
                        metric_observation_id=observation.metric_observation_id,
                        extractor_version=observation.extractor_version,
                    )
                )
            evidence.append(
                HistorySelectedTrial(
                    **common,
                    outcome="selected_valid",
                    selected_attempt_id=report.attempt_id,
                    validation=validation,
                    metrics=tuple(metrics),
                )
            )
        else:
            code = item["exclusion_code"]
            verdicts = {
                "VALIDATION_INVALID": "invalid",
                "VALIDATION_INCONCLUSIVE": "inconclusive",
                "VALIDATION_ERROR": "error",
            }
            if (code in verdicts and (report is None or report.verdict != verdicts[code])) or (
                code not in verdicts and report is not None
            ):
                raise AgentRuntimeError("history_context_invalid")
            evidence.append(
                HistoryExcludedTrial(
                    **common,
                    outcome="excluded",
                    exclusion_code=code,
                    validation=validation,
                    validation_reason_codes=item.get("validation_reason_codes", []),
                )
            )
    warnings = tuple(
        HistoryWarning.model_validate(item)
        for item in _json_objects(summary.warning_json.get("warnings", []))
    )
    if any(
        report_attempts.get(warning.validation_report_id) != warning.attempt_id
        for warning in warnings
    ):
        raise AgentRuntimeError("history_context_invalid")
    return HistoryBatchEvidence(
        project_id=batch.project_id,
        experiment_id=version.experiment_id,
        experiment_version_id=version.experiment_version_id,
        version_number=version.version_number,
        proposal_revision_id=version.proposal_revision_id,
        batch_id=batch.batch_id,
        batch_created_at=batch.created_at,
        summary_revision_id=summary_id,
        summary_revision_number=summary.revision_number,
        summary_protocol_version=summary.summary_protocol_version,
        trial_count=len(trials),
        design=HistoryDesign(
            repository_snapshot_id=version.repository_snapshot_id,
            repository_snapshot_identity=version.repository_snapshot_identity,
            dataset_reference_hash=version.dataset_reference_hash,
            dataset_reference=version.dataset_reference_json,
            environment_expectation_hash=version.environment_expectation_hash,
            environment_expectation=version.environment_expectation_json,
            validation_protocol_hash=version.validation_protocol_hash,
            train_entrypoint=payload.train_entrypoint,
            eval_entrypoint=payload.eval_entrypoint,
            workdir=payload.workdir,
            train_argv_template=payload.train_argv_template,
            metrics=payload.metrics,
            artifacts=payload.artifacts,
            validation_rules=version.validation_protocol_json,
        ),
        trial_evidence=tuple(evidence),
        warnings=warnings,
    )


def _json_objects(values: object) -> tuple[dict[str, object], ...]:
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise AgentRuntimeError("source_result_lineage_invalid")
    return tuple(dict(cast(dict[str, object], value)) for value in values)


def _require_exact_trial_coverage(
    trials: Iterable[TrialRow],
    *,
    included: tuple[dict[str, object], ...],
    excluded: tuple[dict[str, object], ...],
) -> None:
    expected = {trial.trial_id for trial in trials}
    covered: list[UUID] = []
    for result in (*included, *excluded):
        trial_id = result.get("trial_id")
        if not isinstance(trial_id, str):
            raise AgentRuntimeError("source_result_lineage_invalid")
        try:
            covered.append(UUID(trial_id))
        except ValueError as exc:
            raise AgentRuntimeError("source_result_lineage_invalid") from exc
    if len(covered) != len(expected) or set(covered) != expected:
        raise AgentRuntimeError("source_result_lineage_invalid")
