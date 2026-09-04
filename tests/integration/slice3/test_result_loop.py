from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.batch_service import BatchApplicationService, CreateBatch
from labpilot.execution.models import AttemptRow, TrialRow
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.execution.types import ObservedEnvironment, observed_environment_value
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ValidationRule,
    environment_expectation_hash,
)
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.service import ResultApplicationService
from labpilot.results_validation.summary_service import BatchSummaryService
from labpilot.results_validation.types import ValidationVerdict
from tests.integration.experiments.test_proposal_service import (
    _agent_revision,
    _context,
    _payload,
    _proposal_system,
)

_TRAINING_FIXTURE = (
    Path(__file__).resolve().parents[2] / "acceptance" / "slice2" / "training_fixture" / "train.py"
)


def _run_training_fixture(workspace: Path, *, seed: int, score: str) -> Path:
    output = workspace / "runtime" / "output"
    output.mkdir(parents=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(_TRAINING_FIXTURE),
            "--seed",
            str(seed),
            "--value",
            f"value-{seed}",
            "--dataset",
            "demo-dataset-v1",
            "--output-dir",
            str(output),
            "--duration",
            "0",
            "--exit-code",
            "0",
            "--score",
            score,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = output / "result.json"
    assert result.is_file()
    return result


@asynccontextmanager
async def _seed_two_trial_loop(
    isolated_database_url: str, tmp_path: Path
) -> AsyncIterator[tuple[object, UUID, tuple[UUID, UUID], tuple[UUID, UUID], tuple[Path, Path]]]:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        dataset = DatasetReference(
            reference_type="uri",
            path_or_uri="s3://datasets/demo",
            stable_identity="dataset:demo:v1",
            reproducibility_level="strict",
        )
        expectation = EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=(),
            device_requirement=None,
            cuda_requirement=None,
        )
        payload = _payload(
            system,
            seeds=(7, 8),
            dataset_reference=dataset,
            environment_expectation=expectation,
            artifacts=(ArtifactDeclaration("result", "result.json", True),),
            metrics=(MetricDeclaration("score", "result", "test"),),
            validation_rules=(
                ValidationRule("artifact_exists", "result"),
                ValidationRule("metric_finite", "score"),
                ValidationRule("metric_gte", "score", Decimal("0.80")),
            ),
        )
        revision = await _agent_revision(system, payload=payload)
        verification = await system.service.verify_revision(
            context=_context(), proposal_revision_id=revision.proposal_revision_id
        )
        version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        batch = await BatchApplicationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        ).create_batch(
            context=_context(),
            idempotency_key=f"task10-result-loop-{uuid4()}",
            command=CreateBatch(version.experiment_version_id),
        )
        batch_id = UUID(batch.envelope.primary_result_id)
        async with system.database.session() as session:
            trials = tuple(
                (
                    await session.scalars(
                        select(TrialRow)
                        .where(TrialRow.batch_id == batch_id)
                        .order_by(TrialRow.trial_ordinal)
                    )
                ).all()
            )
            assert len(trials) == 2
            workspaces = (tmp_path / "workspace-a", tmp_path / "workspace-b")
            result_paths = (
                _run_training_fixture(workspaces[0], seed=7, score="0.82"),
                _run_training_fixture(workspaces[1], seed=8, score="0.61"),
            )
            observed = ObservedEnvironment(
                schema_version="v1",
                environment_expectation_hash=environment_expectation_hash(expectation),
                python_profile="python-3.12",
                runtime_path="/runtime/python",
                python_version="3.12.0",
                framework_versions=(),
                cuda_runtime_version=None,
                cuda_device_count=None,
                probe_issue_codes=(),
            )
            now = datetime.now(UTC)
            attempt_ids = (uuid4(), uuid4())
            session.add_all(
                (
                    AttemptRow(
                        attempt_id=attempt_id,
                        trial_id=trial.trial_id,
                        attempt_number=1,
                        status="terminal",
                        supervisor_execution_identity=f"task10-{attempt_id}",
                        workspace_path=str(workspace),
                        observed_environment_json=observed_environment_value(observed),
                        observed_environment_recorded_at=now,
                        terminal_kind="process_exited",
                        exit_code=0,
                        terminal_at=now,
                    )
                    for attempt_id, trial, workspace in zip(
                        attempt_ids, trials, workspaces, strict=True
                    )
                )
            )
            await session.commit()
        yield (
            system,
            batch_id,
            (trials[0].trial_id, trials[1].trial_id),
            attempt_ids,
            result_paths,
        )


async def _state(
    database: object, batch_id: UUID, attempt_ids: tuple[UUID, UUID]
) -> dict[str, tuple[object, ...]]:
    async with database.session() as session:
        artifacts = tuple(
            (
                await session.scalars(
                    select(ArtifactRow)
                    .where(ArtifactRow.attempt_id.in_(attempt_ids))
                    .order_by(ArtifactRow.attempt_id)
                )
            ).all()
        )
        metrics = tuple(
            (
                await session.scalars(
                    select(MetricObservationRow)
                    .join(ArtifactRow, MetricObservationRow.artifact_id == ArtifactRow.artifact_id)
                    .where(ArtifactRow.attempt_id.in_(attempt_ids))
                    .order_by(MetricObservationRow.metric_observation_id)
                )
            ).all()
        )
        reports = tuple(
            (
                await session.scalars(
                    select(ValidationReportRow)
                    .where(ValidationReportRow.attempt_id.in_(attempt_ids))
                    .order_by(ValidationReportRow.attempt_id)
                )
            ).all()
        )
        selections = tuple(
            (
                await session.scalars(
                    select(TrialResultSelectionRow)
                    .where(TrialResultSelectionRow.attempt_id.in_(attempt_ids))
                    .order_by(TrialResultSelectionRow.trial_id)
                )
            ).all()
        )
        summaries = tuple(
            (
                await session.scalars(
                    select(BatchSummaryRevisionRow)
                    .where(BatchSummaryRevisionRow.batch_id == batch_id)
                    .order_by(BatchSummaryRevisionRow.revision_number)
                )
            ).all()
        )
    return {
        "artifacts": artifacts,
        "metrics": metrics,
        "reports": reports,
        "selections": selections,
        "summaries": summaries,
    }


def _identities(state: dict[str, tuple[object, ...]]) -> dict[str, frozenset[object]]:
    return {
        "artifacts": frozenset(item.artifact_id for item in state["artifacts"]),
        "metrics": frozenset(item.metric_observation_id for item in state["metrics"]),
        "reports": frozenset(item.validation_report_id for item in state["reports"]),
        "selections": frozenset(
            (item.trial_id, item.attempt_id, item.validation_report_id)
            for item in state["selections"]
        ),
        "summaries": frozenset(
            (item.batch_summary_revision_id, item.revision_number) for item in state["summaries"]
        ),
    }


@pytest.mark.integration
async def test_real_training_fixture_drives_two_trial_postgresql_result_loop(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_two_trial_loop(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        (trial_a_id, trial_b_id),
        (attempt_a_id, attempt_b_id),
        (result_a_path, result_b_path),
    ):
        application = ResultApplicationService(system.database.session)
        result_a = await application.process_attempt(attempt_a_id)
        result_b = await application.process_attempt(attempt_b_id)

        assert result_a is not None
        assert result_a.verdict is ValidationVerdict.VALID
        assert result_a.selected is True
        assert result_b is not None
        assert result_b.verdict is ValidationVerdict.INVALID
        assert result_b.selected is False

        first = await _state(system.database, batch_id, (attempt_a_id, attempt_b_id))
        artifacts = {item.attempt_id: item for item in first["artifacts"]}
        assert len(first["artifacts"]) == 2
        assert {
            (item.attempt_id, item.artifact_name, item.relative_path) for item in first["artifacts"]
        } == {
            (attempt_a_id, "result", "result.json"),
            (attempt_b_id, "result", "result.json"),
        }
        assert all(item.relative_path != "workload-test-oracle.json" for item in first["artifacts"])
        assert set(artifacts) == {attempt_a_id, attempt_b_id}
        for attempt_id, result_path in (
            (attempt_a_id, result_a_path),
            (attempt_b_id, result_b_path),
        ):
            artifact = artifacts[attempt_id]
            assert (
                artifact.artifact_name,
                artifact.relative_path,
                artifact.media_type,
                artifact.content_sha256,
            ) == (
                "result",
                "result.json",
                "application/json",
                hashlib.sha256(result_path.read_bytes()).hexdigest(),
            )

        artifacts_by_id = {item.artifact_id: item for item in first["artifacts"]}
        metrics = {artifacts_by_id[item.artifact_id].attempt_id: item for item in first["metrics"]}
        assert len(first["metrics"]) == 2
        assert {
            (
                artifacts_by_id[item.artifact_id].attempt_id,
                item.metric_name,
                item.split,
                item.extractor_version,
            )
            for item in first["metrics"]
        } == {
            (attempt_a_id, "score", "test", "result-json-v1"),
            (attempt_b_id, "score", "test", "result-json-v1"),
        }
        assert set(metrics) == {attempt_a_id, attempt_b_id}
        assert (
            metrics[attempt_a_id].metric_name,
            metrics[attempt_a_id].numeric_value,
            metrics[attempt_a_id].extractor_version,
        ) == ("score", Decimal("0.82"), "result-json-v1")
        assert (
            metrics[attempt_b_id].metric_name,
            metrics[attempt_b_id].numeric_value,
            metrics[attempt_b_id].extractor_version,
        ) == ("score", Decimal("0.61"), "result-json-v1")

        reports = {item.attempt_id: item for item in first["reports"]}
        assert len(first["reports"]) == 2
        assert reports[attempt_a_id].verdict == ValidationVerdict.VALID.value
        assert reports[attempt_b_id].verdict == ValidationVerdict.INVALID.value
        reason_codes = {item["code"] for item in reports[attempt_b_id].reason_json["reasons"]}
        assert reason_codes == {"METRIC_BELOW_THRESHOLD"}
        assert len(first["selections"]) == 1
        selection = first["selections"][0]
        assert (
            selection.trial_id,
            selection.attempt_id,
            selection.validation_report_id,
        ) == (trial_a_id, attempt_a_id, reports[attempt_a_id].validation_report_id)
        assert trial_b_id not in {item.trial_id for item in first["selections"]}

        outcomes = TrialExecutionOutcomeService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        summary_result = await BatchSummaryService(
            system.database.session,
            trial_execution_outcome_service=outcomes,
        ).refresh_summary(batch_id)
        assert summary_result.created is True
        assert summary_result.presentation_status == "complete_with_invalid_results"

        first = await _state(system.database, batch_id, (attempt_a_id, attempt_b_id))
        assert len(first["summaries"]) == 1
        summary = first["summaries"][0]
        included = summary.included_result_refs_json
        excluded = summary.excluded_reasons_json
        assert len(included) == 1
        assert included[0]["trial_id"] == str(trial_a_id)
        assert included[0]["selected_attempt_id"] == str(attempt_a_id)
        assert included[0]["validation_report_id"] == str(
            reports[attempt_a_id].validation_report_id
        )
        assert included[0]["metrics"] == [
            {
                "metric_observation_id": str(metrics[attempt_a_id].metric_observation_id),
                "artifact_id": str(metrics[attempt_a_id].artifact_id),
                "metric_name": "score",
                "split": "test",
                "numeric_value": "0.82",
            }
        ]
        assert len(excluded) == 1
        assert excluded[0]["trial_id"] == str(trial_b_id)
        assert excluded[0]["source"] == "validation"
        assert excluded[0]["exclusion_code"] == "VALIDATION_INVALID"
        assert excluded[0]["validation_report_id"] == str(
            reports[attempt_b_id].validation_report_id
        )
        assert excluded[0]["validation_reason_codes"] == ["METRIC_BELOW_THRESHOLD"]
        assert summary.aggregates_json["score"] == {"count": 1, "mean": "0.82"}

        before = _identities(first)
        second_application = ResultApplicationService(system.database.session)
        second_outcomes = TrialExecutionOutcomeService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        second_summary = BatchSummaryService(
            system.database.session,
            trial_execution_outcome_service=second_outcomes,
        )
        await second_application.process_attempt(attempt_a_id)
        await second_application.process_attempt(attempt_b_id)
        second_summary_result = await second_summary.refresh_summary(batch_id)
        after = await _state(system.database, batch_id, (attempt_a_id, attempt_b_id))

    assert second_summary_result.created is False
    assert _identities(after) == before
    assert len(after["summaries"]) == 1
