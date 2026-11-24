from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.integration.agent_runtime.test_follow_up_run_binding import _refresh, _state
from tests.integration.agent_runtime.test_result_history_binding import _new_batch
from tests.integration.api.test_result_aware_agent_api import (
    _BUDGET,
    ScriptedProvider,
    _app,
    _counts,
    _governance_counts,
)
from tests.integration.experiments.test_proposal_service import _agent_revision, _context, _git
from tests.integration.p1.test_result_aware_iteration import _add_excluded_trial
from tests.integration.test_batch_summary_service import _process
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.agent_runtime.models import AgentRunRow
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import RepositorySnapshotRow
from labpilot.context.repository_service import RepositoryApplicationService
from labpilot.context.repository_types import CaptureRepositorySnapshot
from labpilot.experiments.proposal_models import ProposalRevisionRow
from labpilot.experiments.proposal_service import _payload_from_json
from labpilot.experiments.proposal_types import DatasetReference, MetricDeclaration, ValidationRule
from labpilot.results_validation.models import MetricObservationRow, ValidationReportRow

pytestmark = pytest.mark.integration
_SENTINEL = "SECRET_RAW_INVALID_STDOUT_CHECKPOINT_NOT_HISTORY"


async def _v2(system, v1):
    async with system.database.session() as session:
        revision = await session.get(ProposalRevisionRow, v1.proposal_revision_id)
    payload = replace(
        _payload_from_json(revision.payload_json),
        repository_snapshot_id=system.snapshot_id,
        repository_snapshot_identity=system.snapshot_identity,
        dataset_reference=DatasetReference("uri", "s3://datasets/v2", None, "best_effort"),
        metrics=(MetricDeclaration("score", "result", "validation"),),
        validation_rules=(
            ValidationRule("artifact_exists", "result"),
            ValidationRule("metric_finite", "score"),
            ValidationRule("metric_gte", "score", Decimal("0.90")),
        ),
        parameter_matrix={"learning_rate": (0.005,)},
    )
    new_revision = await system.service.create_manual_revision(
        context=_context(), experiment_id=v1.experiment_id, payload=payload
    )
    verified = await system.service.verify_revision(
        context=_context(), proposal_revision_id=new_revision.proposal_revision_id
    )
    assert verified.result.passed
    return await system.service.approve_revision(
        context=_context(),
        proposal_revision_id=new_revision.proposal_revision_id,
        verification_id=verified.proposal_verification_id,
    )


async def _create(client, system, summary, batch):
    response = await client.post(
        f"/projects/{batch.project_id}/agent-runs/follow-up",
        json={
            "source_batch_summary_revision_id": str(summary.batch_summary_revision_id),
            "repository_snapshot_id": str(system.snapshot_id),
            "goal": "Compare frozen history and design the next experiment.",
            "budget": _BUDGET,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_history_tool_then_v3_proposal_keeps_authority_and_source_binding(
    isolated_database_url, tmp_path
):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        b1,
        _,
        attempt,
        workspace,
    ):
        (workspace / "runtime" / "output" / "raw-debug.txt").write_text(_SENTINEL)
        await _process(system.database, attempt)
        await _add_excluded_trial(system, b1, attempt)
        await _refresh(system, b1)
        s1, _, v1 = await _state(system, b1)
        # An unrelated Experiment in the same Project must never enter history.
        unrelated_revision = await _agent_revision(
            system, payload=_payload_from_json((await _source_proposal_row(system)).payload_json)
        )
        verified = await system.service.verify_revision(
            context=_context(), proposal_revision_id=unrelated_revision.proposal_revision_id
        )
        unrelated_version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=unrelated_revision.proposal_revision_id,
            verification_id=verified.proposal_verification_id,
        )
        unrelated = await _new_batch(system, unrelated_version.experiment_version_id, attempt)
        async with system.database.session() as session:
            snapshot = await session.get(RepositorySnapshotRow, system.snapshot_id)
            registration_id = UUID(str(snapshot.repository_registration_id))
        (system.repository / "train.py").write_text("print('train v2')\n")
        await _git(system.repository, "add", "train.py")
        await _git(
            system.repository,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "v2 source",
        )
        captured = await RepositoryApplicationService(
            system.database.session,
            git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
            allowed_roots=(tmp_path,),
            content_store=ContentAddressedStore(tmp_path / "content"),
        ).capture_repository_snapshot(
            context=_context(),
            idempotency_key="v2-snapshot",
            command=CaptureRepositorySnapshot(registration_id),
        )
        system.snapshot_id = UUID(captured.envelope.primary_result_id)
        async with system.database.session() as session:
            system.snapshot_identity = (
                await session.get(RepositorySnapshotRow, system.snapshot_id)
            ).repository_snapshot_identity
        v2 = await _v2(system, v1)
        b2 = await _new_batch(system, v2.experiment_version_id, attempt, score="0.95")
        s2, source_batch, _ = await _state(system, b2)
        async with system.database.session() as session:
            observation = await session.scalar(
                select(MetricObservationRow).order_by(MetricObservationRow.created_at)
            )
            observation.numeric_value = -12345
            report = await session.scalar(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt)
            )
            report.input_snapshot_json = {"raw_logs": _SENTINEL}
            report.validator_version = "historical-validator-v1"
            await session.commit()
        async with system.database.session() as session:
            payload = deepcopy(
                (await session.get(ProposalRevisionRow, v2.proposal_revision_id)).payload_json
            )
        payload["parameter_matrix"] = {"learning_rate": [0.02]}
        provider = ScriptedProvider(
            [
                {"kind": "tool_call", "tool_name": "experiment.read_history", "arguments": {}},
                {"kind": "propose", "proposal": payload},
            ]
        )
        before, governed_before = await _counts(system), await _governance_counts(system)
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, provider)), base_url="http://test"
        ) as client:
            run = await _create(client, system, s2, source_batch)
            trace_before = (await client.get(f"/agent-runs/{run['agent_run_id']}/trace")).json()
            assert trace_before["history_context"]["batches"][0]["batch_id"] == str(b1)
            advanced = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            assert advanced.status_code == 200, advanced.text
            assert advanced.json()["termination_reason"] == "proposal_created"
            trace_after = (await client.get(f"/agent-runs/{run['agent_run_id']}/trace")).json()
        first, second = [json.loads(call.messages[1]["content"]) for call in provider.calls]
        assert first["prior_tool_results"] == [] and "history_context" not in first
        assert first["result_context"] == second["result_context"]
        assert second["result_context"]["source_batch_summary_revision_id"] == str(
            s2.batch_summary_revision_id
        )
        history = second["prior_tool_results"][0]["result"]
        assert [b["batch_id"] for b in history["batches"]] == [str(b1)]
        assert str(unrelated) not in json.dumps(history)
        old = history["batches"][0]
        assert old["summary_revision_id"] == str(s1.batch_summary_revision_id)
        assert old["design"]["repository_snapshot_id"] == str(v1.repository_snapshot_id)
        assert old["design"]["repository_snapshot_id"] != str(v2.repository_snapshot_id)
        assert old["design"]["repository_snapshot_identity"] != v2.repository_snapshot_identity
        assert old["design"]["dataset_reference"]["stable_identity"] == "dataset:v1"
        assert old["design"]["dataset_reference_hash"] != v2.dataset_reference_hash
        assert old["design"]["validation_protocol_hash"] != v2.validation_protocol_hash
        historical_threshold = next(
            rule["threshold"]
            for rule in old["design"]["validation_rules"]
            if rule["kind"] == "metric_gte"
        )
        assert Decimal(historical_threshold) == Decimal("0.80")
        current_threshold = next(
            rule["threshold"]
            for rule in second["result_context"]["source_proposal"]["validation_rules"]
            if rule["kind"] == "metric_gte"
        )
        assert Decimal(current_threshold) == Decimal("0.90")
        assert old["trial_evidence"][0]["metrics"][0]["numeric_value"] == "0.82"
        assert old["trial_evidence"][0]["metrics"][0]["split"] == "test"
        assert (
            old["trial_evidence"][0]["validation"]["validator_version"] == "historical-validator-v1"
        )
        assert old["trial_evidence"][1]["outcome"] == "excluded"
        assert "metrics" not in old["trial_evidence"][1] and "aggregates" not in old
        assert trace_after["history_context"] == trace_before["history_context"] == history
        for value in (history, trace_after, [call.messages for call in provider.calls]):
            assert _SENTINEL not in json.dumps(value) and "-12345" not in json.dumps(value)
        after = await _counts(system)
        assert after == (before[0] + 1, *before[1:])
        assert await _governance_counts(system) == governed_before
        async with system.database.session() as session:
            proposal = await session.get(
                ProposalRevisionRow, UUID(advanced.json()["produced_proposal_revision_id"])
            )
            assert proposal.experiment_id == v2.experiment_id and proposal.revision_number == 3


async def _source_proposal_row(system):
    async with system.database.session() as session:
        return await session.scalar(
            select(ProposalRevisionRow).order_by(ProposalRevisionRow.created_at)
        )


@pytest.mark.parametrize("ending", ["stop", "human"])
async def test_history_stop_or_human_resume_remains_frozen(isolated_database_url, tmp_path, ending):
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
        summary, batch, _ = await _state(system, source)
        script = [{"kind": "tool_call", "tool_name": "experiment.read_history", "arguments": {}}]
        if ending == "human":
            script.append({"kind": "request_human_input", "question": "Which next condition?"})
        script.append({"kind": "stop", "reason_code": "goal_satisfied"})
        provider = ScriptedProvider(script)
        before = await _counts(system)
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, provider)), base_url="http://test"
        ) as client:
            run = await _create(client, system, summary, batch)
            advanced = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            assert advanced.status_code == 200, advanced.text
            if ending == "human":
                assert advanced.json()["status"] == "awaiting_human"
                await _new_batch(system, version.experiment_version_id, attempt)
                resumed = await client.post(
                    f"/agent-runs/{run['agent_run_id']}/human-input",
                    json={
                        "expected_row_version": advanced.json()["row_version"],
                        "text": "Stop here.",
                    },
                )
                assert resumed.status_code == 200, resumed.text
                resumed = await client.post(
                    f"/agent-runs/{run['agent_run_id']}/advance",
                    json={"expected_row_version": resumed.json()["row_version"]},
                )
                assert resumed.status_code == 200, resumed.text
                assert resumed.json()["termination_reason"] == "goal_satisfied"
        second = json.loads(provider.calls[1].messages[1]["content"])
        if ending == "human":
            third = json.loads(provider.calls[2].messages[1]["content"])
            assert third["prior_tool_results"] == second["prior_tool_results"]
        else:
            assert advanced.json()["termination_reason"] == "goal_satisfied"
        after = await _counts(system)
        assert after[0:2] == before[0:2] and after[3] == before[3]


async def test_malformed_stored_history_fails_before_provider(isolated_database_url, tmp_path):
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _,
        attempt,
        _,
    ):
        await _process(system.database, attempt)
        await _refresh(system, batch_id)
        summary, batch, _ = await _state(system, batch_id)
        provider = ScriptedProvider([])
        async with AsyncClient(
            transport=ASGITransport(app=_app(system, provider)), base_url="http://test"
        ) as client:
            run = await _create(client, system, summary, batch)
            async with system.database.session() as session:
                row = await session.get(AgentRunRow, UUID(run["agent_run_id"]))
                row.history_context_json = {}
                await session.commit()
            response = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            assert response.status_code == 409
            assert "history_context_invalid" in response.text
        assert provider.calls == []
