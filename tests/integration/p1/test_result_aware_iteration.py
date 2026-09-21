from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.integration.api.test_result_aware_agent_api import (
    _BUDGET,
    ScriptedProvider,
    _app,
    _source_proposal,
    _summary_id,
)
from tests.integration.experiments.test_proposal_service import _context
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.experiments.proposal_models import (
    DesignDecisionRow,
    ExperimentRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
    ProposalVerificationRow,
)
from labpilot.results_validation.models import MetricObservationRow, ValidationReportRow

pytestmark = pytest.mark.integration

_SENTINEL = 'SHOULD_NOT_REACH_AGENT_CONTEXT_invalid_metric_0.01_stdout_checkpoint'


async def _add_excluded_trial(
    system: object, batch_id: UUID, first_attempt_id: UUID
) -> UUID:
    database = system.database  # type: ignore[attr-defined]
    async with database.session() as session:
        first_attempt = await session.get(AttemptRow, first_attempt_id)
        first_trial = await session.get(TrialRow, first_attempt.trial_id)
        assert first_attempt is not None and first_trial is not None
        excluded_trial_id = uuid4()
        session.add(
            TrialRow(
                trial_id=excluded_trial_id,
                batch_id=batch_id,
                trial_ordinal=2,
                trial_identity=uuid4().hex * 2,
                resolved_parameters_json={'learning_rate': 0.2},
                seed=first_trial.seed,
            )
        )
        await session.flush()
        session.add(
            AttemptRow(
                attempt_id=uuid4(),
                trial_id=excluded_trial_id,
                attempt_number=1,
                status='terminal',
                supervisor_execution_identity=f'excluded-{excluded_trial_id}',
                workspace_path=first_attempt.workspace_path,
                frozen_execution_spec_json=deepcopy(first_attempt.frozen_execution_spec_json),
                frozen_execution_spec_hash=first_attempt.frozen_execution_spec_hash,
                frozen_launch_spec_json=deepcopy(first_attempt.frozen_launch_spec_json),
                execution_spec_hash=first_attempt.execution_spec_hash,
                launch_intent_committed_at=first_attempt.launch_intent_committed_at,
                observed_environment_json=deepcopy(first_attempt.observed_environment_json),
                observed_environment_recorded_at=first_attempt.observed_environment_recorded_at,
                physical_started_at=first_attempt.physical_started_at,
                physical_ended_at=first_attempt.physical_ended_at,
                terminal_kind='process_exited',
                exit_code=1,
                terminal_at=first_attempt.terminal_at,
            )
        )
        await session.commit()
    return excluded_trial_id


async def _counts(system: object) -> tuple[int, int, int]:
    database = system.database  # type: ignore[attr-defined]
    async with database.session() as session:
        values = []
        for row in (ProposalRevisionRow, ExperimentVersionRow, BatchRow):
            value = await session.scalar(select(func.count()).select_from(row))
            assert value is not None
            values.append(value)
    return tuple(values)  # type: ignore[return-value]


async def _mutate_raw_metric_after_summary(system: object) -> str:
    raw_value = "0.01"
    database = system.database  # type: ignore[attr-defined]
    async with database.session() as session:
        metric = await session.scalar(
            select(MetricObservationRow).order_by(MetricObservationRow.created_at)
        )
        assert metric is not None
        metric.numeric_value = Decimal(raw_value)
        await session.commit()
    return raw_value


@pytest.mark.asyncio
async def test_result_aware_two_iteration_closure(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        included_trial_id,
        attempt_id,
        workspace,
    ):
        (workspace / 'runtime' / 'output' / 'raw-debug.txt').write_text(
            _SENTINEL, encoding='utf-8'
        )
        await _process(system.database, attempt_id)
        excluded_trial_id = await _add_excluded_trial(system, batch_id, attempt_id)
        summary_id, project_id = await _summary_id(system, batch_id)
        raw_metric_after_summary = await _mutate_raw_metric_after_summary(system)
        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
            assert batch is not None
            v1 = await session.get(ExperimentVersionRow, batch.experiment_version_id)
            report = await session.scalar(select(ValidationReportRow))
            assert v1 is not None and report is not None
            r1 = await session.get(ProposalRevisionRow, v1.proposal_revision_id)
            assert r1 is not None

        r2_payload = deepcopy(await _source_proposal(system))
        r2_payload['parameter_matrix'] = {'learning_rate': [0.2]}
        provider = ScriptedProvider([{'kind': 'propose', 'proposal': r2_payload}])
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, provider)),
            base_url='http://test',
        ) as client:
            created = await client.post(
                f'/projects/{project_id}/agent-runs/follow-up',
                json={
                    'source_batch_summary_revision_id': str(summary_id),
                    'repository_snapshot_id': str(system.snapshot_id),
                    'goal': 'Improve this experiment from the exact results.',
                    'budget': _BUDGET,
                },
            )
            assert created.status_code == 201
            run = created.json()
            assert run['source_batch_summary_revision_id'] == str(summary_id)

            # Phase A: the Agent may materialize R2, but nothing governed after it.
            advanced = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={'expected_row_version': run['row_version']},
            )

        assert advanced.status_code == 200
        advanced_body = advanced.json()
        assert advanced_body['status'] == 'completed'
        assert advanced_body['termination_reason'] == 'proposal_created'
        r2_id = UUID(advanced_body['produced_proposal_revision_id'])

        request = json.loads(provider.calls[0].messages[1]['content'])
        result_context = request['result_context']
        assert result_context['source_batch_summary_revision_id'] == str(summary_id)
        assert result_context['source_experiment_version_id'] == str(v1.experiment_version_id)
        assert result_context['experiment_id'] == str(r1.experiment_id)
        assert result_context['source_batch_id'] == str(batch_id)
        assert any(
            item['trial_id'] == str(included_trial_id)
            and item['metrics'][0]['numeric_value'] == '0.82'
            and item['validation_report_id'] == str(report.validation_report_id)
            for item in result_context['included_results']
        )
        assert any(
            item['trial_id'] == str(excluded_trial_id)
            and item['exclusion_code'] == 'EXECUTION_EXHAUSTED'
            for item in result_context['excluded_results']
        )
        assert any(
            item['trial_id'] == str(excluded_trial_id)
            and item['trial_ordinal'] == 2
            and item['resolved_parameters'] == {'learning_rate': 0.2}
            and item['seed'] == 7
            for item in result_context['trials']
        )
        assert _SENTINEL not in provider.calls[0].messages[1]['content']
        assert raw_metric_after_summary not in provider.calls[0].messages[1]['content']

        async with system.database.session() as session:
            r2 = await session.get(ProposalRevisionRow, r2_id)
            verification_count = await session.scalar(
                select(func.count())
                .select_from(ProposalVerificationRow)
                .where(ProposalVerificationRow.proposal_revision_id == r2_id)
            )
            decision_count = await session.scalar(
                select(func.count())
                .select_from(DesignDecisionRow)
                .where(DesignDecisionRow.proposal_revision_id == r2_id)
            )
            v2_before_human = await session.scalar(
                select(ExperimentVersionRow).where(
                    ExperimentVersionRow.proposal_revision_id == r2_id
                )
            )
            experiment_count = await session.scalar(select(func.count()).select_from(ExperimentRow))
        assert r2 is not None
        assert r2.experiment_id == r1.experiment_id
        assert r2.revision_number == r1.revision_number + 1
        assert verification_count == decision_count == 0
        assert v2_before_human is None
        assert experiment_count == 1

        # Phase B: deterministic verification is explicit and outside Agent runtime.
        verification = await system.service.verify_revision(
            context=_context(), proposal_revision_id=r2_id
        )
        assert verification.result.passed is True

        # Phase C: approval is an explicit human command, never an Agent side effect.
        v2 = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=r2_id,
            verification_id=verification.proposal_verification_id,
        )
        assert v2.experiment_id == v1.experiment_id
        assert v2.version_number == v1.version_number + 1
        assert v2.proposal_revision_id == r2_id
        async with system.database.session() as session:
            v2_batches = await session.scalar(
                select(func.count())
                .select_from(BatchRow)
                .where(BatchRow.experiment_version_id == v2.experiment_version_id)
            )
        assert v2_batches == 0

        # A second exact-source run that stops creates no R3, V3, or Batch.
        counts_before_stop = await _counts(system)
        stop_provider = ScriptedProvider(
            [{'kind': 'stop', 'reason_code': 'goal_satisfied', 'summary': 'done'}]
        )
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, stop_provider)),
            base_url='http://test',
        ) as client:
            stopped_created = await client.post(
                f'/projects/{project_id}/agent-runs/follow-up',
                json={
                    'source_batch_summary_revision_id': str(summary_id),
                    'repository_snapshot_id': str(system.snapshot_id),
                    'goal': 'Confirm the exact batch goal is satisfied.',
                    'budget': _BUDGET,
                },
            )
            assert stopped_created.status_code == 201
            stopped = stopped_created.json()
            stopped_advanced = await client.post(
                f"/agent-runs/{stopped['agent_run_id']}/advance",
                json={'expected_row_version': stopped['row_version']},
            )
        assert stopped_advanced.status_code == 200
        assert stopped_advanced.json()['status'] == 'completed'
        assert stopped_advanced.json()['termination_reason'] == 'goal_satisfied'
        assert str(summary_id) in stop_provider.calls[0].messages[1]['content']
        assert await _counts(system) == counts_before_stop
