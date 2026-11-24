from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.integration.agent_runtime.test_follow_up_run_binding import (
    _adapter,
    _count,
    _refresh,
    _run,
    _state,
)
from tests.integration.experiments.test_proposal_service import _context
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.result_history import HistoryContext
from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.batch_service import BatchApplicationService, CreateBatch
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.results_validation.models import BatchSummaryRevisionRow, MetricObservationRow

pytestmark = pytest.mark.integration


async def _new_batch(system, version_id: UUID, first_attempt_id: UUID, *, score="0.83") -> UUID:
    commands = BatchApplicationService(
        system.database.session,
        version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
    )
    result = await commands.create_batch(
        context=_context(), idempotency_key=str(uuid4()), command=CreateBatch(version_id)
    )
    batch_id = UUID(result.envelope.primary_result_id)
    async with system.database.session() as session:
        previous = await session.get(AttemptRow, first_attempt_id)
        trials = tuple(await session.scalars(select(TrialRow).where(TrialRow.batch_id == batch_id)))
        for trial in trials:
            attempt_id = uuid4()
            output = system.repository.parent / str(attempt_id) / "runtime" / "output"
            output.mkdir(parents=True)
            (output / "result.json").write_text(
                '{"schema_version":"v1","metrics":{"score":' + score + "}}"
            )
            session.add(
                AttemptRow(
                    attempt_id=attempt_id,
                    trial_id=trial.trial_id,
                    attempt_number=1,
                    status="terminal",
                    supervisor_execution_identity=str(attempt_id),
                    workspace_path=str(output.parent.parent),
                    terminal_kind="process_exited",
                    exit_code=0,
                    observed_environment_json=deepcopy(previous.observed_environment_json),
                    observed_environment_recorded_at=datetime.now(UTC),
                    terminal_at=datetime.now(UTC),
                )
            )
            await session.commit()
            await _process(system.database, attempt_id)
    await _refresh(system, batch_id)
    return batch_id


async def _capture(system, source_batch: UUID):
    summary, batch, version = await _state(system, source_batch)
    run = await _adapter(system).create_bound_run(_run(summary, batch, version))
    return run, HistoryContext.model_validate(run.history_context)


async def test_capture_is_frozen_latest_per_batch_and_source_cutoff(
    isolated_database_url, tmp_path
):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, older)
        old_summary, _, version = await _state(system, older)
        source = await _new_batch(system, version.experiment_version_id, attempt)
        run, history = await _capture(system, source)
        assert [b.batch_id for b in history.batches] == [older]
        assert (
            history.trial_facts.source.summary_revision_id == run.source_batch_summary_revision_id
        )
        assert history.trial_facts.source.counts.total_trials == 1
        assert history.trial_facts.included_history.total_trials == 1
        assert history.trial_facts.source_and_included_history.total_trials == 2
        assert history.batches[0].trial_evidence[0].metrics[0].numeric_value == "0.82"
        later = await _new_batch(system, version.experiment_version_id, attempt)
        async with system.database.session() as session:
            old = await session.get(BatchSummaryRevisionRow, old_summary.batch_summary_revision_id)
            session.add(
                BatchSummaryRevisionRow(
                    batch_summary_revision_id=uuid4(),
                    batch_id=older,
                    revision_number=2,
                    summary_protocol_version=old.summary_protocol_version,
                    included_result_refs_json=deepcopy(old.included_result_refs_json),
                    excluded_reasons_json=deepcopy(old.excluded_reasons_json),
                    warning_json=deepcopy(old.warning_json),
                    aggregates_json=deepcopy(old.aggregates_json),
                )
            )
            metric = await session.scalar(
                select(MetricObservationRow).order_by(MetricObservationRow.created_at)
            )
            metric.numeric_value = -12345
            await session.commit()
        _, new_history = await _capture(system, source)
        assert new_history.batches[0].summary_revision_number == 2
        assert (
            history.trial_facts.historical_batches[0].summary_revision_id
            == old_summary.batch_summary_revision_id
        )
        assert (
            new_history.trial_facts.historical_batches[0].summary_revision_id
            != old_summary.batch_summary_revision_id
        )
        assert new_history.batches[0].trial_evidence[0].metrics[0].numeric_value == "0.82"
        assert later not in [b.batch_id for b in new_history.batches]
        repository = SqlAgentRepository(system.database.session)
        restored = await repository.get_run(run.agent_run_id)
        assert restored.history_context == run.history_context
        changed = await repository.update_run_state(
            run.agent_run_id,
            expected_row_version=run.row_version,
            status="completed",
            termination_reason="test_stop",
        )
        assert changed.history_context == run.history_context
        assert (
            HistoryContext.model_validate(changed.history_context)
            .batches[0]
            .summary_revision_number
            == 1
        )
        _, anchored_later = await _capture(system, later)
        assert source in [b.batch_id for b in anchored_later.batches]


async def test_empty_capture_and_legacy_null_remain_distinct(isolated_database_url, tmp_path):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        source,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, source)
        run, history = await _capture(system, source)
        assert history.batches == () and history.omissions == () and not history.coverage_limited
        repository = SqlAgentRepository(system.database.session)
        legacy = await repository.add_run(replace(run, agent_run_id=uuid4(), history_context=None))
        assert (await repository.get_run(legacy.agent_run_id)).history_context is None


@pytest.mark.parametrize("limit", ["trials", "input", "envelope", "malformed", "report"])
async def test_candidate_omission_or_integrity_failure_is_atomic(
    isolated_database_url, tmp_path, limit
):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, older)
        summary, _, version = await _state(system, older)
        source = await _new_batch(system, version.experiment_version_id, attempt)
        async with system.database.session() as session:
            row = await session.get(BatchSummaryRevisionRow, summary.batch_summary_revision_id)
            if limit == "trials":
                for ordinal in range(2, 22):
                    session.add(
                        TrialRow(
                            trial_id=uuid4(),
                            batch_id=older,
                            trial_ordinal=ordinal,
                            trial_identity=uuid4().hex * 2,
                            resolved_parameters_json={},
                            seed=ordinal,
                        )
                    )
            elif limit in {"input", "envelope"}:
                ref = row.included_result_refs_json[0]
                row.warning_json = {
                    "warnings": [
                        {
                            "trial_id": ref["trial_id"],
                            "trial_ordinal": ref["trial_ordinal"],
                            "attempt_id": ref["selected_attempt_id"],
                            "validation_report_id": ref["validation_report_id"],
                            "code": "FIXTURE",
                            "subject": "fixture",
                            "details": {"text": "x" * (140000 if limit == "input" else 34000)},
                        }
                    ]
                }
            elif limit == "malformed":
                row.included_result_refs_json = []
            else:
                other_summary, _, _ = await _state(system, source)
                refs = deepcopy(row.included_result_refs_json)
                refs[0]["validation_report_id"] = other_summary.included_result_refs_json[0][
                    "validation_report_id"
                ]
                row.included_result_refs_json = refs
            await session.commit()
        before = await _count(system)
        if limit in {"malformed", "report"}:
            with pytest.raises(AgentRuntimeError):
                await _capture(system, source)
            assert await _count(system) == before
        else:
            _, history = await _capture(system, source)
            assert history.batches == () and history.coverage_limited
            assert history.trial_facts.included_history.total_trials == 0
            assert (
                history.trial_facts.source_and_included_history == history.trial_facts.source.counts
            )
            assert (
                history.omissions[0].reason
                == {
                    "trials": "trial_limit",
                    "input": "input_bytes_limit",
                    "envelope": "history_bytes_limit",
                }[limit]
            )


async def test_order_lookahead_and_no_unbounded_refill(isolated_database_url, tmp_path):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, older)
        _, _, version = await _state(system, older)
        eligible = [older]
        for _ in range(3):
            eligible.append(await _new_batch(system, version.experiment_version_id, attempt))
        source = await _new_batch(system, version.experiment_version_id, attempt)
        cutoff = datetime.now(UTC) - timedelta(days=1)
        async with system.database.session() as session:
            for batch_id in eligible:
                (await session.get(BatchRow, batch_id)).created_at = cutoff
            await session.commit()
        _, history = await _capture(system, source)
        assert [b.batch_id for b in history.batches] == sorted(eligible, reverse=True)[:3]
        assert history.has_more_candidates and history.coverage_limited


async def test_later_version_is_excluded_even_when_batch_precedes_source(
    isolated_database_url, tmp_path
):
    from tests.integration.p1.test_historical_comparison import _v2

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, older)
        _, _, v1 = await _state(system, older)
        v2 = await _v2(system, v1)
        future_version_batch = await _new_batch(
            system, v2.experiment_version_id, attempt, score="0.95"
        )
        source = await _new_batch(system, v1.experiment_version_id, attempt)
        _, history = await _capture(system, source)
        assert [batch.batch_id for batch in history.batches] == [older]
        assert future_version_batch not in [batch.batch_id for batch in history.batches]


async def test_wrong_project_candidate_does_not_enter_capture(isolated_database_url, tmp_path):
    from labpilot.experiments.models import ProjectRow

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, older)
        _, _, version = await _state(system, older)
        source = await _new_batch(system, version.experiment_version_id, attempt)
        # A cross-Project Batch reference is ignored by discovery before JSON is loaded.
        async with system.database.session() as session:
            project_id = uuid4()
            session.add(
                ProjectRow(
                    project_id=project_id,
                    project_identity=uuid4().hex * 2,
                    name="other",
                    status="active",
                    artifact_root_ref="fixture",
                )
            )
            await session.flush()
            (await session.get(BatchRow, older)).project_id = project_id
            await session.commit()
        _, history = await _capture(system, source)
        assert history.batches == () and not history.coverage_limited


async def test_validation_invalid_history_exposes_reasons_without_numeric_values(
    isolated_database_url, tmp_path
):
    from decimal import Decimal

    async with _seed_result_attempt(
        isolated_database_url, tmp_path, score_threshold=Decimal("0.90"), best_effort_dataset=True
    ) as (system, older, trial_id, attempt, _):
        await _process(system.database, attempt)
        await _refresh(system, older)
        _, _, version = await _state(system, older)
        source = await _new_batch(system, version.experiment_version_id, attempt, score="0.95")
        _, history = await _capture(system, source)
        excluded = history.batches[0].trial_evidence[0]
        assert excluded.trial_id == trial_id and excluded.outcome == "excluded"
        assert excluded.exclusion_code == "VALIDATION_INVALID"
        assert excluded.validation.validation_protocol_hash == version.validation_protocol_hash
        assert excluded.validation_reason_codes
        assert history.batches[0].warnings[0].code == "DATASET_BEST_EFFORT_REPRODUCIBILITY"
        assert "numeric_value" not in history.model_dump_json()


async def test_cancelled_execution_history_preserves_actual_exclusion_code(
    isolated_database_url, tmp_path
):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        async with system.database.session() as session:
            (await session.get(AttemptRow, attempt)).exit_code = 1
            (await session.get(BatchRow, older)).cancel_requested_at = datetime.now(UTC)
            await session.commit()
        await _refresh(system, older)
        _, _, version = await _state(system, older)
        source = await _new_batch(system, version.experiment_version_id, attempt)
        _, history = await _capture(system, source)
        trial = history.batches[0].trial_evidence[0]
        assert trial.exclusion_code == "EXECUTION_CANCELLED" and trial.validation is None


async def test_source_fact_capture_failure_does_not_persist_run(isolated_database_url, tmp_path):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        source,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, source)
        summary, _, _ = await _state(system, source)
        async with system.database.session() as session:
            row = await session.get(BatchSummaryRevisionRow, summary.batch_summary_revision_id)
            refs = deepcopy(row.included_result_refs_json)
            refs[0]["trial_ordinal"] = 99
            row.included_result_refs_json = refs
            await session.commit()
        before = await _count(system)
        with pytest.raises(AgentRuntimeError):
            await _capture(system, source)
        assert await _count(system) == before


async def test_fact_metadata_participates_in_history_byte_limit(isolated_database_url, tmp_path):
    from labpilot.agent_runtime.result_history import MAX_HISTORY_BYTES
    from labpilot.common.canonical_json import canonical_json_bytes
    from labpilot.tools_policy.types import _canonical_value

    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        older,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, older)
        summary, _, version = await _state(system, older)
        source = await _new_batch(system, version.experiment_version_id, attempt)
        async with system.database.session() as session:
            row = await session.get(BatchSummaryRevisionRow, summary.batch_summary_revision_id)
            ref = row.included_result_refs_json[0]
            row.warning_json = {
                "warnings": [
                    {
                        "trial_id": ref["trial_id"],
                        "trial_ordinal": ref["trial_ordinal"],
                        "attempt_id": ref["selected_attempt_id"],
                        "validation_report_id": ref["validation_report_id"],
                        "code": "FIXTURE",
                        "subject": "fixture",
                        "details": {"text": ""},
                    }
                ]
            }
            await session.commit()
        _, captured = await _capture(system, source)
        legacy = captured.model_dump(mode="json")
        legacy.pop("trial_facts")
        gap = MAX_HISTORY_BYTES - len(canonical_json_bytes(_canonical_value(legacy))) - 1
        assert gap > 0
        async with system.database.session() as session:
            row = await session.get(BatchSummaryRevisionRow, summary.batch_summary_revision_id)
            warnings = deepcopy(row.warning_json)
            warnings["warnings"][0]["details"]["text"] = "x" * gap
            row.warning_json = warnings
            await session.commit()
        _, limited = await _capture(system, source)
        assert limited.batches == () and limited.coverage_limited
        assert limited.omissions[0].reason == "history_bytes_limit"
        assert limited.trial_facts.included_history.total_trials == 0
        assert limited.trial_facts.source_and_included_history.total_trials == 1
