from __future__ import annotations

from collections.abc import Iterable
from typing import cast
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy import func, select

from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.result_context import (
    FollowUpRunStore,
    ResultContext,
    ResultContextSource,
    ResultTrialCondition,
)
from labpilot.agent_runtime.types import AgentRunDetails, AgentRuntimeError
from labpilot.context.models import RepositoryRegistrationRow, RepositorySnapshotRow
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.execution.models import BatchRow, TrialRow
from labpilot.experiments.proposal_models import (
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
)
from labpilot.experiments.proposal_service import _payload_from_json
from labpilot.results_validation.models import BatchSummaryRevisionRow


class SqlResultAwareAgentAdapter(FollowUpRunStore, ResultContextSource):
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        agent_repository: SqlAgentRepository,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._agent_repository = agent_repository

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
            if (
                run.repository_snapshot_id != version.repository_snapshot_id
                or snapshot.repository_snapshot_identity != version.repository_snapshot_identity
            ):
                raise AgentRuntimeError("source_snapshot_mismatch")
            if (
                proposal.experiment_id != experiment.experiment_id
                or version.experiment_id != experiment.experiment_id
            ):
                raise AgentRuntimeError("source_result_lineage_invalid")
            details = await self._agent_repository.add_run_in_uow(uow, run)
            await uow.commit()
        return details

    async def load(self, source_batch_summary_revision_id: UUID) -> ResultContext:
        async with self._session_context_factory() as session:
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
                        resolved_parameters=cast(
                            dict[str, JsonValue], trial.resolved_parameters_json
                        ),
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
