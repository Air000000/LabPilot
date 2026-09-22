from __future__ import annotations

import asyncio
import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.agent_runtime.models import AgentRunRow
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.types import AgentRunDetails, AgentRuntimeError
from labpilot.api.result_aware_agent_adapter import SqlResultAwareAgentAdapter
from labpilot.context.models import RepositorySnapshotRow
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.models import BatchRow
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.experiments.proposal_models import ExperimentVersionRow
from labpilot.results_validation.models import (
    BatchSummaryRevisionRow,
    MetricObservationRow,
    ValidationReportRow,
)


def _adapter(system: object) -> SqlResultAwareAgentAdapter:
    database = system.database
    return SqlResultAwareAgentAdapter(
        database.session, agent_repository=SqlAgentRepository(database.session)
    )


async def _refresh(system: object, batch_id: UUID) -> object:
    summary = importlib.import_module("labpilot.results_validation.summary_service")
    database = system.database
    outcomes = TrialExecutionOutcomeService(
        database.session, version_semantics=VersionExecutionSemanticsAdapter(database.session)
    )
    return await summary.BatchSummaryService(
        database.session, trial_execution_outcome_service=outcomes
    ).refresh_summary(batch_id)


async def _state(
    system: object, batch_id: UUID
) -> tuple[BatchSummaryRevisionRow, BatchRow, ExperimentVersionRow]:
    async with system.database.session() as session:
        summary = await session.scalar(
            select(BatchSummaryRevisionRow)
            .where(BatchSummaryRevisionRow.batch_id == batch_id)
            .order_by(BatchSummaryRevisionRow.revision_number.desc())
        )
        batch = await session.get(BatchRow, batch_id)
        assert summary is not None and batch is not None
        version = await session.get(ExperimentVersionRow, batch.experiment_version_id)
        assert version is not None
        return summary, batch, version


def _run(
    summary: BatchSummaryRevisionRow, batch: BatchRow, version: ExperimentVersionRow
) -> AgentRunDetails:
    return AgentRunDetails(
        uuid4(),
        batch.project_id,
        version.repository_snapshot_id,
        "Improve the experiment.",
        "ready",
        None,
        "local_only",
        "test-provider",
        "test-model",
        1,
        1,
        1,
        1,
        0,
        0,
        1,
        None,
        datetime.now(UTC),
        None,
        None,
        1,
        summary.batch_summary_revision_id,
    )


async def _count(system: object) -> int:
    async with system.database.session() as session:
        result = await session.scalar(select(func.count()).select_from(AgentRunRow))
        assert result is not None
        return result


async def _change_metric(system: object) -> None:
    async with system.database.session() as session:
        metric = await session.scalar(
            select(MetricObservationRow).order_by(MetricObservationRow.created_at)
        )
        report = await session.scalar(
            select(ValidationReportRow).order_by(ValidationReportRow.created_at)
        )
        assert metric is not None and report is not None
        snapshot = deepcopy(report.input_snapshot_json)
        metrics = snapshot["metrics"]
        assert isinstance(metrics, list) and isinstance(metrics[0], dict)
        metrics[0]["numeric_value"] = "0.83"
        report.input_snapshot_json = snapshot
        metric.numeric_value = Decimal("0.83")
        await session.commit()


@pytest.mark.integration
async def test_missing_and_stale_summary_write_no_follow_up_run(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        await _refresh(system, batch_id)
        summary, batch, version = await _state(system, batch_id)
        run, adapter = _run(summary, batch, version), _adapter(system)
        with pytest.raises(AgentRuntimeError, match="source_summary_not_found"):
            await adapter.create_bound_run(replace(run, source_batch_summary_revision_id=uuid4()))
        await _change_metric(system)
        await _refresh(system, batch_id)
        with pytest.raises(AgentRuntimeError, match="source_summary_not_latest"):
            await adapter.create_bound_run(run)
        assert await _count(system) == 0


@pytest.mark.integration
async def test_batch_lock_linearizes_refresh_before_follow_up(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        await _refresh(system, batch_id)
        summary, batch, version = await _state(system, batch_id)
        await _change_metric(system)
        summary_service = importlib.import_module("labpilot.results_validation.summary_service")
        original_build_state = summary_service._build_state
        refresh_has_batch_lock = asyncio.Event()
        allow_refresh_to_continue = asyncio.Event()

        async def pause_after_batch_lock(*args: object, **kwargs: object) -> object:
            refresh_has_batch_lock.set()
            await allow_refresh_to_continue.wait()
            return await original_build_state(*args, **kwargs)

        monkeypatch.setattr(summary_service, "_build_state", pause_after_batch_lock)
        refresh_task = asyncio.create_task(_refresh(system, batch_id))
        await asyncio.wait_for(refresh_has_batch_lock.wait(), timeout=1)
        follow_up_task = asyncio.create_task(
            _adapter(system).create_bound_run(_run(summary, batch, version))
        )
        await asyncio.sleep(0)
        assert not follow_up_task.done()
        allow_refresh_to_continue.set()
        assert (await refresh_task).created is True
        with pytest.raises(AgentRuntimeError, match="source_summary_not_latest"):
            await follow_up_task
        assert await _count(system) == 0


@pytest.mark.integration
async def test_bound_run_pins_exact_summary_not_later_raw_result_rows(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        await _refresh(system, batch_id)
        summary, batch, version = await _state(system, batch_id)
        created = await _adapter(system).create_bound_run(_run(summary, batch, version))
        await _change_metric(system)
        await _refresh(system, batch_id)
        context = await _adapter(system).load(summary.batch_summary_revision_id)
    assert created.source_batch_summary_revision_id == summary.batch_summary_revision_id
    assert context.source_batch_summary_revision_id == summary.batch_summary_revision_id
    assert context.source_summary_revision_number == 1
    assert context.presentation_status == "complete"
    assert context.aggregates == {"score": {"count": 1, "mean": "0.82"}}
    assert "0.83" not in str(context.included_results)


@pytest.mark.integration
@pytest.mark.parametrize("mutation", ("project", "dirty", "snapshot_id", "snapshot_identity"))
async def test_bound_run_rejects_project_and_snapshot_mismatches(
    isolated_database_url: str, tmp_path: Path, mutation: str
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        await _refresh(system, batch_id)
        summary, batch, version = await _state(system, batch_id)
        run = _run(summary, batch, version)
        expected = {
            "project": "source_project_mismatch",
            "dirty": "source_snapshot_not_clean",
        }.get(mutation, "source_snapshot_mismatch")
        if mutation == "project":
            run = replace(run, project_id=uuid4())
        else:
            async with system.database.session() as session:
                current = await session.get(ExperimentVersionRow, version.experiment_version_id)
                snapshot = await session.get(RepositorySnapshotRow, version.repository_snapshot_id)
                assert current is not None and snapshot is not None
                if mutation == "dirty":
                    snapshot.is_clean = False
                elif mutation == "snapshot_id":
                    current.repository_snapshot_id = uuid4()
                else:
                    current.repository_snapshot_identity = "0" * 64
                await session.commit()
        with pytest.raises(AgentRuntimeError, match=expected):
            await _adapter(system).create_bound_run(run)
        assert await _count(system) == 0


@pytest.mark.integration
async def test_context_derives_invalid_presentation_from_persisted_summary(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
    ) as (system, batch_id, _trial_id, attempt_id, _workspace):
        await _process(system.database, attempt_id)
        await _refresh(system, batch_id)
        summary, _batch, _version = await _state(system, batch_id)
        context = await _adapter(system).load(summary.batch_summary_revision_id)

    assert context.presentation_status == "complete_with_invalid_results"
    assert context.included_results == ()
    assert context.excluded_results == tuple(summary.excluded_reasons_json)
    assert context.aggregates == summary.aggregates_json


@pytest.mark.integration
async def test_context_uses_persisted_summary_warnings_and_aggregates(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path, best_effort_dataset=True) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _process(system.database, attempt_id)
        await _refresh(system, batch_id)
        summary, _batch, _version = await _state(system, batch_id)
        context = await _adapter(system).load(summary.batch_summary_revision_id)

    assert context.presentation_status == "complete"
    assert context.warnings == tuple(summary.warning_json["warnings"])
    assert context.aggregates == summary.aggregates_json
