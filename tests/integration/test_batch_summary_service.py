from __future__ import annotations

import asyncio
import importlib
from copy import deepcopy
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.models import AttemptRow, BatchRow
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import ResultStateConflict
from tests.integration.test_result_processing_service import _application, _seed_result_attempt


def _summary_module():
    return importlib.import_module("labpilot.results_validation.summary_service")


def _summary_service(summary: object, session_context_factory: object, outcomes: object):
    return summary.BatchSummaryService(
        session_context_factory,
        trial_execution_outcome_service=outcomes,
    )


def _outcomes(session_context_factory: object) -> TrialExecutionOutcomeService:
    return TrialExecutionOutcomeService(
        session_context_factory,
        version_semantics=VersionExecutionSemanticsAdapter(session_context_factory),
    )


async def _state(database: object, batch_id: UUID) -> dict[str, tuple[object, ...]]:
    async with database.session() as session:
        revisions = tuple(
            await session.scalars(
                select(BatchSummaryRevisionRow)
                .where(BatchSummaryRevisionRow.batch_id == batch_id)
                .order_by(BatchSummaryRevisionRow.revision_number)
            )
        )
        events = tuple(
            await session.scalars(
                select(RuntimeEventRow).where(
                    RuntimeEventRow.aggregate_type == "batch",
                    RuntimeEventRow.aggregate_id == str(batch_id),
                    RuntimeEventRow.event_type == "result.summary_created",
                )
            )
        )
        outboxes = tuple(
            await session.scalars(
                select(OutboxRecordRow)
                .join(RuntimeEventRow, OutboxRecordRow.event_id == RuntimeEventRow.event_id)
                .where(RuntimeEventRow.event_type == "result.summary_created")
            )
        )
    return {"revisions": revisions, "events": events, "outboxes": outboxes}


async def _process(database: object, attempt_id: UUID) -> None:
    service = importlib.import_module("labpilot.results_validation.service")
    await _application(service, database.session).process_attempt(attempt_id)


async def _add_second_failed_attempt(database: object, first_attempt_id: UUID) -> UUID:
    async with database.session() as session:
        first = await session.get(AttemptRow, first_attempt_id)
        assert first is not None
        attempt_id = uuid4()
        session.add(
            AttemptRow(
                attempt_id=attempt_id,
                trial_id=first.trial_id,
                attempt_number=first.attempt_number + 1,
                status="terminal",
                supervisor_execution_identity=f"summary-retry-{attempt_id}",
                workspace_path=first.workspace_path,
                frozen_execution_spec_json=deepcopy(first.frozen_execution_spec_json),
                frozen_execution_spec_hash=first.frozen_execution_spec_hash,
                frozen_launch_spec_json=deepcopy(first.frozen_launch_spec_json),
                execution_spec_hash=first.execution_spec_hash,
                launch_intent_committed_at=first.launch_intent_committed_at,
                observed_environment_json=deepcopy(first.observed_environment_json),
                observed_environment_recorded_at=first.observed_environment_recorded_at,
                physical_started_at=first.physical_started_at,
                physical_ended_at=first.physical_ended_at,
                terminal_kind="process_exited",
                exit_code=1,
                terminal_at=first.terminal_at,
            )
        )
        await session.commit()
    return attempt_id


@pytest.mark.integration
async def test_unresolved_execution_is_not_ready_without_revision_or_event(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async with system.database.session() as session:
            attempt = await session.get(AttemptRow, attempt_id)
            assert attempt is not None
            attempt.status, attempt.terminal_kind = "running", None
            attempt.exit_code, attempt.terminal_at = None, None
            await session.commit()
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert (result.presentation_status, result.revision_id, result.created) == (
        "not_ready",
        None,
        False,
    )
    assert state["revisions"] == state["events"] == ()


@pytest.mark.integration
async def test_successful_execution_without_active_report_is_not_ready(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        _workspace,
    ):
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert (result.presentation_status, result.revision_id, result.created) == (
        "not_ready",
        None,
        False,
    )
    assert state["revisions"] == state["events"] == ()


@pytest.mark.integration
async def test_valid_selected_result_persists_included_metrics_aggregate_and_event(
    isolated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = _summary_module()
    calls: list[tuple[object, bool]] = []
    original = summary.append_runtime_event

    async def append_spy(uow: object, envelope: object, *, enqueue_outbox: bool = True):
        calls.append((envelope, enqueue_outbox))
        return await original(uow, envelope, enqueue_outbox=enqueue_outbox)

    monkeypatch.setattr(summary, "append_runtime_event", append_spy)
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    revision = state["revisions"][0]
    item = revision.included_result_refs_json[0]
    metric = item["metrics"][0]
    assert result.presentation_status == "complete" and result.created is True
    assert result.revision_id == revision.batch_summary_revision_id
    assert revision.revision_number == 1
    assert item["trial_id"] == str(trial_id) and item["trial_ordinal"] == 1
    assert item["selected_attempt_id"] == str(attempt_id)
    assert metric["metric_name"] == "score" and metric["split"] == "test"
    assert metric["numeric_value"] == "0.82"
    assert set(metric) == {
        "metric_observation_id",
        "artifact_id",
        "metric_name",
        "split",
        "numeric_value",
    }
    assert revision.aggregates_json == {"score": {"count": 1, "mean": "0.82"}}
    assert len(state["events"]) == 1 and state["outboxes"] == ()
    event = state["events"][0]
    assert (event.aggregate_type, event.aggregate_id, event.event_type) == (
        "batch",
        str(batch_id),
        "result.summary_created",
    )
    assert event.actor_type == "system" and event.actor_id is None
    assert event.visibility_class == "internal"
    assert event.payload_json == {
        "batch_summary_revision_id": str(revision.batch_summary_revision_id),
        "revision_number": 1,
        "presentation_status": "complete",
        "included_count": 1,
        "excluded_count": 0,
    }
    assert [(item.topic, item.partition_key, outbox) for item, outbox in calls] == [
        ("runtime-events", f"batch:{batch_id}", False)
    ]


@pytest.mark.integration
@pytest.mark.parametrize("mode", ("missing", "wrong_attempt"))
async def test_valid_current_report_requires_exact_current_selection(
    isolated_database_url: str, tmp_path: Path, mode: str
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            selection = await session.get(TrialResultSelectionRow, trial_id)
            assert selection is not None
            if mode == "missing":
                await session.delete(selection)
            else:
                other_attempt_id = uuid4()
                current = await session.get(AttemptRow, attempt_id)
                assert current is not None
                session.add(
                    AttemptRow(
                        attempt_id=other_attempt_id,
                        trial_id=trial_id,
                        attempt_number=2,
                        status="terminal",
                        supervisor_execution_identity=f"summary-{other_attempt_id}",
                        workspace_path=current.workspace_path,
                        terminal_kind="process_exited",
                        exit_code=1,
                        terminal_at=current.terminal_at,
                    )
                )
                selection.attempt_id = other_attempt_id
            await session.commit()
        with pytest.raises(ResultStateConflict):
            await _summary_service(
                summary, system.database.session, _outcomes(system.database.session)
            ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert state["revisions"] == ()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("output", "forced_verdict", "code"),
    (
        (b'{"schema_version":"v1","metrics":{"score":0.61}}', None, "VALIDATION_INVALID"),
        (
            b'{"schema_version":"v1","metrics":{"score":0.82,"score":0.61}}',
            None,
            "VALIDATION_ERROR",
        ),
    ),
)
async def test_settled_validation_verdicts_create_stable_exclusions(
    isolated_database_url: str,
    tmp_path: Path,
    output: bytes,
    forced_verdict: str | None,
    code: str,
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=output) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        if forced_verdict is not None:
            async with system.database.session() as session:
                report = await session.scalar(
                    select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
                )
                assert report is not None
                selection = await session.get(TrialResultSelectionRow, trial_id)
                assert selection is not None
                await session.delete(selection)
                report.verdict = forced_verdict
                report.reason_json = {
                    "reasons": [
                        {
                            "code": "ENVIRONMENT_OBSERVATION_INSUFFICIENT",
                            "subject": "environment",
                            "details": {},
                        }
                    ]
                }
                await session.commit()
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    exclusion = state["revisions"][0].excluded_reasons_json[0]
    assert result.presentation_status == "complete_with_invalid_results"
    assert exclusion["source"] == "validation" and exclusion["exclusion_code"] == code
    assert exclusion["validation_reason_codes"] == sorted(exclusion["validation_reason_codes"])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("cancelled", "expected"),
    ((False, "EXECUTION_EXHAUSTED"), (True, "EXECUTION_CANCELLED")),
)
async def test_settled_execution_outcomes_create_stable_exclusions(
    isolated_database_url: str, tmp_path: Path, cancelled: bool, expected: str
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async with system.database.session() as session:
            attempt, batch = (
                await session.get(AttemptRow, attempt_id),
                await session.get(BatchRow, batch_id),
            )
            assert attempt is not None and batch is not None
            attempt.exit_code = 1
            if cancelled:
                batch.cancel_requested_at = datetime.now(UTC)
            await session.commit()
        if not cancelled:
            await _add_second_failed_attempt(system.database, attempt_id)
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    exclusion = state["revisions"][0].excluded_reasons_json[0]
    assert result.presentation_status == "complete_with_invalid_results"
    assert exclusion == {
        "trial_id": exclusion["trial_id"],
        "trial_ordinal": 1,
        "source": "execution",
        "exclusion_code": expected,
    }


@pytest.mark.integration
async def test_selection_on_exhausted_trial_is_conflict_not_exclusion(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            attempt = await session.get(AttemptRow, attempt_id)
            assert attempt is not None
            attempt.exit_code = 1
            await session.commit()
        await _add_second_failed_attempt(system.database, attempt_id)
        with pytest.raises(ResultStateConflict):
            await _summary_service(
                summary, system.database.session, _outcomes(system.database.session)
            ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert state["revisions"] == ()


@pytest.mark.integration
async def test_consumed_validation_warnings_preserve_provenance(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path, best_effort_dataset=True) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            report_id = report.validation_report_id
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert result.presentation_status == "complete"
    assert state["revisions"][0].warning_json == {
        "warnings": [
            {
                "trial_id": str(trial_id),
                "trial_ordinal": 1,
                "attempt_id": str(attempt_id),
                "validation_report_id": str(report_id),
                "code": "DATASET_BEST_EFFORT_REPRODUCIBILITY",
                "subject": "dataset",
                "details": {},
            }
        ]
    }


@pytest.mark.integration
async def test_snapshot_is_the_metric_set_authority_and_row_conflicts_fail_closed(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            artifact = await session.scalar(
                select(ArtifactRow).where(ArtifactRow.attempt_id == attempt_id)
            )
            assert artifact is not None
            session.add(
                MetricObservationRow(
                    metric_observation_id=uuid4(),
                    artifact_id=artifact.artifact_id,
                    metric_name="extra",
                    split="test",
                    numeric_value=Decimal("999"),
                    extractor_version="result-json-v1",
                )
            )
            await session.commit()
        await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)
        assert state["revisions"][0].aggregates_json == {"score": {"count": 1, "mean": "0.82"}}
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            snapshot = deepcopy(report.input_snapshot_json)
            snapshot["metrics"][0]["numeric_value"] = "0.81"
            report.input_snapshot_json = snapshot
            await session.commit()
        with pytest.raises(ResultStateConflict):
            await _summary_service(
                summary, system.database.session, _outcomes(system.database.session)
            ).refresh_summary(batch_id)


@pytest.mark.integration
async def test_nonterminating_mean_is_fixed_28_digit_round_half_even_and_context_independent() -> (
    None
):
    summary = _summary_module()
    previous = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_HALF_EVEN))
        assert summary._mean_json((Decimal("0"), Decimal("0"), Decimal("1"))) == {
            "count": 3,
            "mean": "0.3333333333333333333333333333",
        }
    finally:
        setcontext(previous)


@pytest.mark.integration
async def test_unchanged_refresh_is_noop_and_historical_revision_is_not_current_when_unready(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        service = _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        )
        first, second = (
            await service.refresh_summary(batch_id),
            await service.refresh_summary(batch_id),
        )
        async with system.database.session() as session:
            attempt = await session.get(AttemptRow, attempt_id)
            assert attempt is not None
            attempt.status, attempt.terminal_kind = "running", None
            attempt.exit_code, attempt.terminal_at = None, None
            await session.commit()
        current = await service.refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert first.created is True
    assert second.created is False and second.revision_id == first.revision_id
    assert current.presentation_status == "not_ready" and current.revision_id is None
    assert len(state["revisions"]) == len(state["events"]) == 1


@pytest.mark.integration
@pytest.mark.parametrize("stale_attempt_ids", (False, True))
async def test_stale_row_version_or_attempt_ids_is_not_ready_without_writes(
    isolated_database_url: str, tmp_path: Path, stale_attempt_ids: bool
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        _workspace,
    ):
        from dataclasses import replace

        await _process(system.database, _attempt_id)
        real_snapshot = await _outcomes(system.database.session).snapshot_batch(batch_id)
        snapshot = replace(
            real_snapshot,
            attempt_ids=() if stale_attempt_ids else real_snapshot.attempt_ids,
            batch_row_version=(
                real_snapshot.batch_row_version
                if stale_attempt_ids
                else real_snapshot.batch_row_version + 1
            ),
        )

        class StaleOutcomes:
            async def snapshot_batch(self, observed_batch_id: UUID):
                assert observed_batch_id == batch_id
                return snapshot

        result = await _summary_service(
            summary, system.database.session, StaleOutcomes()
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert (result.presentation_status, result.revision_id, result.created) == (
        "not_ready",
        None,
        False,
    )
    assert state["revisions"] == state["events"] == ()


@pytest.mark.integration
async def test_concurrent_refresh_has_one_immutable_revision_and_one_event(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        left = _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        )
        right = _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        )
        first, second = await asyncio.gather(
            left.refresh_summary(batch_id), right.refresh_summary(batch_id)
        )
        state = await _state(system.database, batch_id)

    assert {first.created, second.created} == {False, True}
    assert first.revision_id == second.revision_id
    assert len(state["revisions"]) == len(state["events"]) == 1


@pytest.mark.integration
async def test_invalid_current_report_selected_by_the_same_trial_fails_closed(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
    ) as (system, batch_id, trial_id, attempt_id, _workspace):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            session.add(
                TrialResultSelectionRow(
                    trial_id=trial_id,
                    attempt_id=attempt_id,
                    validation_report_id=report.validation_report_id,
                )
            )
            await session.commit()
        with pytest.raises(ResultStateConflict):
            await _summary_service(
                summary, system.database.session, _outcomes(system.database.session)
            ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert state["revisions"] == ()


@pytest.mark.integration
async def test_included_metrics_and_aggregates_have_explicit_deterministic_order(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            artifacts = [
                ArtifactRow(
                    artifact_id=uuid4(),
                    attempt_id=attempt_id,
                    artifact_name=name,
                    relative_path=f"{name}.json",
                    content_sha256="0" * 64,
                    byte_size=0,
                    media_type="application/json",
                )
                for name in ("zeta", "alpha")
            ]
            session.add_all(artifacts)
            metrics = [
                MetricObservationRow(
                    metric_observation_id=uuid4(),
                    artifact_id=artifact.artifact_id,
                    metric_name=name,
                    split="test",
                    numeric_value=value,
                    extractor_version="result-json-v1",
                )
                for artifact, name, value in (
                    (artifacts[0], "zeta", Decimal("2")),
                    (artifacts[1], "alpha", Decimal("1")),
                )
            ]
            session.add_all(metrics)
            snapshot = deepcopy(report.input_snapshot_json)
            # Deliberately reverse the desired output order.
            snapshot["metrics"] = [
                *snapshot["metrics"],
                *[
                    {
                        "metric_observation_id": str(metric.metric_observation_id),
                        "artifact_id": str(metric.artifact_id),
                        "metric_name": metric.metric_name,
                        "split": metric.split,
                        "numeric_value": str(metric.numeric_value),
                        "extractor_version": metric.extractor_version,
                    }
                    for metric in reversed(metrics)
                ],
            ]
            report.input_snapshot_json = snapshot
            await session.commit()
        await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    rendered = state["revisions"][0].included_result_refs_json[0]["metrics"]
    assert [(item["metric_name"], item["split"], item["artifact_id"]) for item in rendered] == [
        (item["metric_name"], item["split"], item["artifact_id"])
        for item in sorted(
            rendered,
            key=lambda item: (
                item["metric_name"],
                item["split"] or "",
                item["artifact_id"],
            ),
        )
    ]
    assert [(item["metric_name"], item["split"]) for item in rendered] == [
        ("alpha", "test"),
        ("score", "test"),
        ("zeta", "test"),
    ]
    assert sorted(state["revisions"][0].aggregates_json) == ["alpha", "score", "zeta"]
    assert state["revisions"][0].aggregates_json == {
        "alpha": {"count": 1, "mean": "1"},
        "score": {"count": 1, "mean": "0.82"},
        "zeta": {"count": 1, "mean": "2"},
    }


@pytest.mark.integration
async def test_current_invalid_report_appends_revision_after_historical_v0_valid_selection(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    from labpilot.experiments.proposal_models import ExperimentVersionRow
    from labpilot.results_validation.validator import (
        EXTRACTION_PROFILE_VERSION,
        SUMMARY_PROTOCOL_VERSION,
    )

    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
    ) as (system, batch_id, trial_id, attempt_id, _workspace):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
            assert batch is not None
            version = await session.get(ExperimentVersionRow, batch.experiment_version_id)
            current_report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert version is not None and current_report is not None
            assert current_report.verdict == "invalid"
            historical_report_id, historical_revision_id = uuid4(), uuid4()
            historical_report = ValidationReportRow(
                validation_report_id=historical_report_id,
                attempt_id=attempt_id,
                validation_protocol_hash=version.validation_protocol_hash,
                extraction_profile_version=EXTRACTION_PROFILE_VERSION,
                validator_version="slice3-validator-v0",
                input_snapshot_json=deepcopy(current_report.input_snapshot_json),
                verdict="valid",
                reason_json={"reasons": []},
                warning_json={"warnings": []},
            )
            historical_metrics = [
                {
                    key: metric[key]
                    for key in (
                        "metric_observation_id",
                        "artifact_id",
                        "metric_name",
                        "split",
                        "numeric_value",
                    )
                }
                for metric in current_report.input_snapshot_json["metrics"]
            ]
            historical_included = [
                {
                    "trial_id": str(trial_id),
                    "trial_ordinal": 1,
                    "selected_attempt_id": str(attempt_id),
                    "validation_report_id": str(historical_report_id),
                    "metrics": historical_metrics,
                }
            ]
            session.add(historical_report)
            await session.flush()
            session.add(
                TrialResultSelectionRow(
                    trial_id=trial_id,
                    attempt_id=attempt_id,
                    validation_report_id=historical_report_id,
                )
            )
            session.add(
                BatchSummaryRevisionRow(
                    batch_summary_revision_id=historical_revision_id,
                    batch_id=batch_id,
                    revision_number=1,
                    summary_protocol_version=SUMMARY_PROTOCOL_VERSION,
                    included_result_refs_json=historical_included,
                    excluded_reasons_json=[],
                    warning_json={"warnings": []},
                    aggregates_json={"score": {"count": 1, "mean": "0.61"}},
                )
            )
            await session.commit()
        historical_structural_state = (
            deepcopy(historical_included),
            [],
            {"warnings": []},
            {"score": {"count": 1, "mean": "0.61"}},
        )
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert result.created is True
    assert result.presentation_status == "complete_with_invalid_results"
    assert result.revision_id != historical_revision_id
    assert len(state["revisions"]) == 2
    historical, current = state["revisions"]
    assert historical.batch_summary_revision_id == historical_revision_id
    assert (
        historical.included_result_refs_json,
        historical.excluded_reasons_json,
        historical.warning_json,
        historical.aggregates_json,
    ) == historical_structural_state
    assert current.revision_number == 2 and current.included_result_refs_json == []
    assert current.excluded_reasons_json == [
        {
            "trial_id": str(trial_id),
            "trial_ordinal": 1,
            "source": "validation",
            "exclusion_code": "VALIDATION_INVALID",
            "validation_report_id": str(current_report.validation_report_id),
            "validation_reason_codes": ["METRIC_BELOW_THRESHOLD"],
        }
    ]
    assert len(state["events"]) == 1
    assert state["events"][0].payload_json["batch_summary_revision_id"] == str(
        current.batch_summary_revision_id
    )


@pytest.mark.integration
async def test_current_inconclusive_report_selected_by_this_trial_fails_closed(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            report.verdict = "inconclusive"
            report.reason_json = {
                "reasons": [
                    {
                        "code": "ENVIRONMENT_OBSERVATION_INSUFFICIENT",
                        "subject": "environment",
                        "details": {},
                    }
                ]
            }
            await session.commit()
        with pytest.raises(ResultStateConflict):
            await _summary_service(
                summary, system.database.session, _outcomes(system.database.session)
            ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    assert state["revisions"] == ()


@pytest.mark.integration
async def test_invalid_exclusion_propagates_warning_with_report_provenance(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
        best_effort_dataset=True,
    ) as (system, batch_id, trial_id, attempt_id, _workspace):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            report_id = report.validation_report_id
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    revision = state["revisions"][0]
    assert result.presentation_status == "complete_with_invalid_results"
    assert revision.excluded_reasons_json[0]["exclusion_code"] == "VALIDATION_INVALID"
    assert revision.warning_json == {
        "warnings": [
            {
                "trial_id": str(trial_id),
                "trial_ordinal": 1,
                "attempt_id": str(attempt_id),
                "validation_report_id": str(report_id),
                "code": "DATASET_BEST_EFFORT_REPRODUCIBILITY",
                "subject": "dataset",
                "details": {},
            }
        ]
    }


@pytest.mark.integration
async def test_unselected_current_inconclusive_report_is_a_settled_exclusion(
    isolated_database_url: str, tmp_path: Path
) -> None:
    summary = _summary_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        async with system.database.session() as session:
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
            assert report is not None
            selection = await session.get(TrialResultSelectionRow, trial_id)
            assert selection is not None
            await session.delete(selection)
            report.verdict = "inconclusive"
            report.reason_json = {
                "reasons": [
                    {
                        "code": "ENVIRONMENT_OBSERVATION_INSUFFICIENT",
                        "subject": "environment",
                        "details": {},
                    }
                ]
            }
            await session.commit()
        result = await _summary_service(
            summary, system.database.session, _outcomes(system.database.session)
        ).refresh_summary(batch_id)
        state = await _state(system.database, batch_id)

    exclusion = state["revisions"][0].excluded_reasons_json[0]
    assert result.presentation_status == "complete_with_invalid_results"
    assert exclusion["exclusion_code"] == "VALIDATION_INCONCLUSIVE"
    assert exclusion["validation_reason_codes"] == ["ENVIRONMENT_OBSERVATION_INSUFFICIENT"]
