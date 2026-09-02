from __future__ import annotations

import asyncio
import errno
import hashlib
import importlib
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
from labpilot.execution.types import (
    ObservedEnvironment,
    observed_environment_value,
)
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ValidationRule,
    environment_expectation_hash,
)
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow
from labpilot.results_validation.models import (
    ArtifactRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import ResultProcessingDeferred
from tests.integration.experiments.test_proposal_service import (
    _agent_revision,
    _context,
    _payload,
    _proposal_system,
)


def _service_module():
    return importlib.import_module("labpilot.results_validation.service")


def _application(service_module, session_context_factory):
    return service_module.ResultApplicationService(session_context_factory)


@asynccontextmanager
async def _seed_result_attempt(
    isolated_database_url: str,
    tmp_path: Path,
    *,
    output: bytes | None = b'{"schema_version":"v1","metrics":{"score":0.82}}',
    score_threshold: Decimal = Decimal("0.80"),
    best_effort_dataset: bool = False,
) -> AsyncIterator[tuple[object, UUID, UUID, UUID, Path]]:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        dataset = DatasetReference(
            reference_type="uri",
            path_or_uri="s3://datasets/example",
            stable_identity=None if best_effort_dataset else "dataset:v1",
            reproducibility_level="best_effort" if best_effort_dataset else "strict",
        )
        expectation = EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=(),
            device_requirement=None,
            cuda_requirement=None,
        )
        payload = _payload(
            system,
            dataset_reference=dataset,
            environment_expectation=expectation,
            artifacts=(ArtifactDeclaration("result", "result.json", True),),
            metrics=(MetricDeclaration("score", "result", "test"),),
            validation_rules=(
                ValidationRule("artifact_exists", "result"),
                ValidationRule("metric_finite", "score"),
                ValidationRule("metric_gte", "score", score_threshold),
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
        commands = BatchApplicationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        batch = await commands.create_batch(
            context=_context(),
            idempotency_key=f"result-processing-{uuid4()}",
            command=CreateBatch(version.experiment_version_id),
        )
        batch_id = UUID(batch.envelope.primary_result_id)
        async with system.database.session() as session:
            trial = await session.scalar(
                select(TrialRow)
                .where(TrialRow.batch_id == batch_id)
                .order_by(TrialRow.trial_ordinal)
            )
            assert trial is not None
            attempt_id = uuid4()
            workspace = tmp_path / "workspace"
            output_root = workspace / "runtime" / "output"
            output_root.mkdir(parents=True)
            if output is not None:
                (output_root / "result.json").write_bytes(output)
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
            session.add(
                AttemptRow(
                    attempt_id=attempt_id,
                    trial_id=trial.trial_id,
                    attempt_number=1,
                    status="terminal",
                    supervisor_execution_identity=f"result-{attempt_id}",
                    workspace_path=str(workspace),
                    observed_environment_json=observed_environment_value(observed),
                    observed_environment_recorded_at=now,
                    terminal_kind="process_exited",
                    exit_code=0,
                    terminal_at=now,
                )
            )
            await session.commit()
            yield system, batch_id, trial.trial_id, attempt_id, workspace


async def _state(database, batch_id: UUID, attempt_id: UUID) -> dict[str, object]:
    async with database.session() as session:
        artifacts = tuple(
            await session.scalars(
                select(ArtifactRow).where(ArtifactRow.attempt_id == attempt_id)
            )
        )
        metrics = tuple(
            await session.scalars(
                select(MetricObservationRow)
                .join(ArtifactRow, MetricObservationRow.artifact_id == ArtifactRow.artifact_id)
                .where(ArtifactRow.attempt_id == attempt_id)
            )
        )
        reports = tuple(
            await session.scalars(
                select(ValidationReportRow).where(ValidationReportRow.attempt_id == attempt_id)
            )
        )
        selection = await session.scalar(
            select(TrialResultSelectionRow)
            .join(AttemptRow, TrialResultSelectionRow.trial_id == AttemptRow.trial_id)
            .where(AttemptRow.attempt_id == attempt_id)
        )
        events = tuple(
            await session.scalars(
                select(RuntimeEventRow)
                .where(
                    RuntimeEventRow.aggregate_type == "batch",
                    RuntimeEventRow.aggregate_id == str(batch_id),
                    RuntimeEventRow.event_type.like("result.%"),
                )
                .order_by(RuntimeEventRow.aggregate_sequence)
            )
        )
    return {
        "artifacts": artifacts,
        "metrics": metrics,
        "reports": reports,
        "selection": selection,
        "events": events,
    }


async def _set_attempt(database, attempt_id: UUID, **values: object) -> None:
    async with database.session() as session:
        attempt = await session.get(AttemptRow, attempt_id)
        assert attempt is not None
        for field, value in values.items():
            setattr(attempt, field, value)
        await session.commit()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("status", "terminal_kind", "exit_code", "termination_signal"),
    (
        ("running", None, None, None),
        ("unknown", None, None, None),
        ("identity_conflict", None, None, None),
        ("terminal", "process_exited", 1, None),
        ("terminal", "process_signaled", None, 15),
    ),
)
async def test_ineligible_execution_creates_no_result_state(
    isolated_database_url: str,
    tmp_path: Path,
    status: str,
    terminal_kind: str | None,
    exit_code: int | None,
    termination_signal: int | None,
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        await _set_attempt(
            system.database,
            attempt_id,
            status=status,
            terminal_kind=terminal_kind,
            exit_code=exit_code,
            termination_signal=termination_signal,
            terminal_at=datetime.now(UTC) if terminal_kind else None,
        )
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    assert result is None
    assert all(not state[name] for name in state)


@pytest.mark.integration
async def test_valid_result_persists_snapshot_metrics_selection_and_events(
    isolated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_module = _service_module()
    source = b'{"schema_version":"v1","metrics":{"score":0.8200}}'
    captured_events: list[tuple[object, bool, UUID]] = []
    original_append_runtime_event = service_module.append_runtime_event

    async def append_runtime_event_spy(
        uow: object, envelope: object, *, enqueue_outbox: bool = True
    ) -> object:
        event = await original_append_runtime_event(
            uow, envelope, enqueue_outbox=enqueue_outbox
        )
        captured_events.append((envelope, enqueue_outbox, event.event_id))
        return event

    monkeypatch.setattr(service_module, "append_runtime_event", append_runtime_event_spy)
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=source) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)
        async with system.database.session() as session:
            outboxes = tuple(
                await session.scalars(
                    select(OutboxRecordRow).where(
                        OutboxRecordRow.event_id.in_([
                            event_id for _, _, event_id in captured_events
                        ])
                    )
                )
            )

    artifact = state["artifacts"][0]
    metric = state["metrics"][0]
    report = state["reports"][0]
    selection = state["selection"]
    assert result is not None and result.selected is True
    assert artifact.media_type == "application/json"
    assert artifact.content_sha256 == hashlib.sha256(source).hexdigest()
    assert artifact.byte_size == len(source)
    assert metric.numeric_value == Decimal("0.82")
    assert metric.extractor_version == "result-json-v1"
    assert report.verdict == "valid"
    assert report.extraction_profile_version == "result-json-v1"
    assert report.validator_version == "slice3-validator-v1"
    assert selection is not None and selection.validation_report_id == result.validation_report_id
    assert [event.event_type for event in state["events"]] == [
        "result.artifact_registered",
        "result.validation_completed",
        "result.trial_selected",
    ]
    assert all(event.aggregate_type == "batch" for event in state["events"])
    assert all(event.aggregate_id == str(batch_id) for event in state["events"])
    assert all(event.actor_type == "system" for event in state["events"])
    assert all(event.actor_id is None for event in state["events"])
    assert all(event.visibility_class == "internal" for event in state["events"])
    assert all(event.interaction_correlation_id is None for event in state["events"])
    assert all("content" not in event.payload_json for event in state["events"])
    assert [envelope.topic for envelope, _, _ in captured_events] == ["runtime-events"] * 3
    assert [envelope.partition_key for envelope, _, _ in captured_events] == [
        f"batch:{batch_id}"
    ] * 3
    assert [enqueue_outbox for _, enqueue_outbox, _ in captured_events] == [False] * 3
    assert outboxes == ()
    snapshot = report.input_snapshot_json
    assert snapshot["schema_version"] == "v1"
    assert snapshot["metrics"][0]["numeric_value"] == "0.82"
    assert snapshot["processing_errors"] == []
    rules = snapshot["experiment_version"]["validation_rules"]
    assert "threshold" not in rules[0] and "threshold" not in rules[1]
    assert rules[2]["threshold"] == "0.8"


@pytest.mark.integration
async def test_threshold_failure_is_invalid_and_is_not_selected(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        output=b'{"schema_version":"v1","metrics":{"score":0.61}}',
    ) as (system, batch_id, _trial_id, attempt_id, _workspace):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    report = state["reports"][0]
    assert result is not None and result.selected is False
    assert len(state["artifacts"]) == len(state["metrics"]) == 1
    assert report.verdict == "invalid"
    assert report.reason_json == {
        "reasons": [
            {
                "code": "METRIC_BELOW_THRESHOLD",
                "subject": "score",
                "details": {"actual": "0.61", "threshold": "0.8"},
            }
        ]
    }
    assert state["selection"] is None
    assert [event.event_type for event in state["events"]] == [
        "result.artifact_registered",
        "result.validation_completed",
    ]


@pytest.mark.integration
async def test_missing_declared_artifact_is_invalid_with_explicit_snapshot_marker(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=None) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    report = state["reports"][0]
    assert result is not None and result.selected is False
    assert state["artifacts"] == () and state["metrics"] == ()
    assert report.verdict == "invalid"
    assert report.reason_json["reasons"] == [
        {"code": "METRIC_MISSING", "subject": "score", "details": {}},
        {"code": "METRIC_MISSING", "subject": "score", "details": {}},
        {"code": "REQUIRED_ARTIFACT_MISSING", "subject": "result", "details": {}},
    ]
    assert report.input_snapshot_json["artifacts"] == [
        {"artifact_name": "result", "relative_path": "result.json", "state": "missing"}
    ]
    assert state["selection"] is None


@pytest.mark.integration
async def test_best_effort_dataset_warning_is_persisted_without_blocking_selection(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(
        isolated_database_url, tmp_path, best_effort_dataset=True
    ) as (system, batch_id, _trial_id, attempt_id, _workspace):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    assert result is not None and result.selected is True
    assert state["reports"][0].warning_json["warnings"][0]["code"] == (
        "DATASET_BEST_EFFORT_REPRODUCIBILITY"
    )
    assert state["reports"][0].warning_json["warnings"][0]["details"] == {}


@pytest.mark.integration
async def test_exact_rerun_reuses_rows_events_and_selected_semantics(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        service = _application(service_module, system.database.session)
        first = await service.process_attempt(attempt_id)
        second = await service.process_attempt(attempt_id)
        state = await _state(system.database, batch_id, attempt_id)

    assert first is not None and second is not None
    assert first.validation_report_id == second.validation_report_id
    assert first.selected is second.selected is True
    assert len(state["artifacts"]) == len(state["metrics"]) == len(state["reports"]) == 1
    assert state["selection"] is not None
    assert len(state["events"]) == 3


@pytest.mark.integration
async def test_malformed_result_json_is_error_not_missing_metric(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        output=b'{"schema_version":"v1","metrics":{"score":0.82,"score":0.61}}',
    ) as (system, batch_id, _trial_id, attempt_id, _workspace):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    report = state["reports"][0]
    assert result is not None and result.selected is False
    assert len(state["artifacts"]) == 1 and state["metrics"] == ()
    assert report.verdict == "error"
    assert report.reason_json["reasons"][0]["code"] == "VALIDATION_INPUT_INVALID"
    assert "METRIC_MISSING" not in str(report.reason_json)
    assert report.input_snapshot_json["processing_errors"] == [
        {
            "code": "VALIDATION_INPUT_INVALID",
            "subject": "result",
            "details": {"failure": "result_json_invalid"},
        }
    ]


@pytest.mark.integration
async def test_oversize_result_json_is_deterministic_error(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    oversized = b"{" + b"x" * (1024 * 1024) + b"}"
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=oversized) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    assert result is not None and result.selected is False
    assert state["artifacts"] == () and state["metrics"] == ()
    assert state["reports"][0].verdict == "error"
    assert state["reports"][0].reason_json["reasons"][0]["code"] == "VALIDATION_INPUT_INVALID"
    assert state["reports"][0].input_snapshot_json["processing_errors"] == [
        {
            "code": "VALIDATION_INPUT_INVALID",
            "subject": "result",
            "details": {"failure": "result_json_oversize"},
        }
    ]


@pytest.mark.integration
async def test_best_effort_warning_is_preserved_on_processing_error(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(
        isolated_database_url,
        tmp_path,
        best_effort_dataset=True,
        output=b'{"schema_version":"v1","metrics":{"score":0.82,"score":0.61}}',
    ) as (system, batch_id, _trial_id, attempt_id, _workspace):
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    report = state["reports"][0]
    assert result is not None and result.selected is False
    assert report.verdict == "error"
    assert report.reason_json["reasons"][0]["code"] == "VALIDATION_INPUT_INVALID"
    assert report.warning_json == {
        "warnings": [
            {"code": "DATASET_BEST_EFFORT_REPRODUCIBILITY", "subject": "dataset", "details": {}}
        ]
    }

async def test_transient_reader_error_rolls_back_and_retry_succeeds(
    isolated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async def deferred(*args, **kwargs):
            raise ResultProcessingDeferred("retry")

        monkeypatch.setattr(service_module, "capture_result_json_artifact", deferred)
        service = _application(service_module, system.database.session)
        with pytest.raises(ResultProcessingDeferred):
            await service.process_attempt(attempt_id)
        state = await _state(system.database, batch_id, attempt_id)
        monkeypatch.undo()
        result = await service.process_attempt(attempt_id)

    assert all(not state[name] for name in state)
    assert result is not None and result.selected is True


@pytest.mark.integration
async def test_unknown_oserror_propagates_without_result_state(
    isolated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        def failed(*args, **kwargs):
            raise OSError(errno.EIO, "disk failure")

        monkeypatch.setattr(service_module, "capture_result_json_artifact", failed)
        with pytest.raises(OSError) as raised:
            await _application(service_module, system.database.session).process_attempt(
                attempt_id
            )
        state = await _state(system.database, batch_id, attempt_id)

    assert raised.value.errno == errno.EIO
    assert all(not state[name] for name in state)


@pytest.mark.integration
async def test_artifact_slot_mismatch_creates_provenance_error_without_mutation(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async with system.database.session() as session:
            row = ArtifactRow(
                artifact_id=uuid4(),
                attempt_id=attempt_id,
                artifact_name="result",
                relative_path="result.json",
                content_sha256="0" * 64,
                byte_size=1,
                media_type="application/json",
            )
            session.add(row)
            await session.commit()
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    assert result is not None and result.selected is False
    assert len(state["artifacts"]) == 1
    assert state["artifacts"][0].content_sha256 == "0" * 64
    assert state["metrics"] == ()
    assert state["reports"][0].reason_json["reasons"][0]["code"] == (
        "RESULT_PROVENANCE_CONFLICT"
    )


@pytest.mark.integration
async def test_metric_slot_mismatch_creates_provenance_error_without_mutation(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    source = b'{"schema_version":"v1","metrics":{"score":0.82}}'
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=source) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        artifact_id = uuid4()
        async with system.database.session() as session:
            session.add(
                ArtifactRow(
                    artifact_id=artifact_id,
                    attempt_id=attempt_id,
                    artifact_name="result",
                    relative_path="result.json",
                    content_sha256=hashlib.sha256(source).hexdigest(),
                    byte_size=len(source),
                    media_type="application/json",
                )
            )
            session.add(
                MetricObservationRow(
                    metric_observation_id=uuid4(),
                    artifact_id=artifact_id,
                    metric_name="score",
                    split="test",
                    numeric_value=Decimal("0.61"),
                    extractor_version="result-json-v1",
                )
            )
            await session.commit()
        result = await _application(service_module, system.database.session).process_attempt(
            attempt_id
        )
        state = await _state(system.database, batch_id, attempt_id)

    assert result is not None and result.selected is False
    assert state["metrics"][0].numeric_value == Decimal("0.61")
    assert state["reports"][0].reason_json["reasons"][0]["code"] == (
        "RESULT_PROVENANCE_CONFLICT"
    )


@pytest.mark.integration
async def test_artifact_provenance_error_is_idempotent_for_same_observation(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    source = b'{"schema_version":"v1","metrics":{"score":0.82}}'
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=source) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        workspace,
    ):
        artifact_id = uuid4()
        async with system.database.session() as session:
            session.add(
                ArtifactRow(
                    artifact_id=artifact_id,
                    attempt_id=attempt_id,
                    artifact_name="result",
                    relative_path="result.json",
                    content_sha256="0" * 64,
                    byte_size=1,
                    media_type="application/json",
                )
            )
            await session.commit()
        service = _application(service_module, system.database.session)
        first = await service.process_attempt(attempt_id)
        second = await service.process_attempt(attempt_id)
        (workspace / "runtime" / "output" / "result.json").write_bytes(
            b'{"schema_version":"v1","metrics":{"score":0.83}}'
        )
        with pytest.raises(service_module.ResultStateConflict):
            await service.process_attempt(attempt_id)
        state = await _state(system.database, batch_id, attempt_id)

    report = state["reports"][0]
    details = report.input_snapshot_json["processing_errors"][0]["details"]
    assert first is not None and second is not None
    assert first.verdict.value == second.verdict.value == "error"
    assert first.validation_report_id == second.validation_report_id
    assert len(state["reports"]) == 1
    assert len(state["artifacts"]) == 1
    assert state["artifacts"][0].artifact_id == artifact_id
    assert state["artifacts"][0].content_sha256 == "0" * 64
    assert state["selection"] is None
    assert len(state["events"]) == 1
    assert details["persisted_artifact_id"] == str(artifact_id)
    assert details["expected_content_sha256"] == "0" * 64
    assert details["observed_content_sha256"] == hashlib.sha256(source).hexdigest()
    assert details["expected_byte_size"] == "1"
    assert details["observed_byte_size"] == str(len(source))


@pytest.mark.integration
async def test_metric_provenance_error_is_idempotent_for_same_observation(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    source = b'{"schema_version":"v1","metrics":{"score":0.82}}'
    async with _seed_result_attempt(isolated_database_url, tmp_path, output=source) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        artifact_id = uuid4()
        metric_observation_id = uuid4()
        async with system.database.session() as session:
            session.add(
                ArtifactRow(
                    artifact_id=artifact_id,
                    attempt_id=attempt_id,
                    artifact_name="result",
                    relative_path="result.json",
                    content_sha256=hashlib.sha256(source).hexdigest(),
                    byte_size=len(source),
                    media_type="application/json",
                )
            )
            session.add(
                MetricObservationRow(
                    metric_observation_id=metric_observation_id,
                    artifact_id=artifact_id,
                    metric_name="score",
                    split="test",
                    numeric_value=Decimal("0.61"),
                    extractor_version="result-json-v1",
                )
            )
            await session.commit()
        service = _application(service_module, system.database.session)
        first = await service.process_attempt(attempt_id)
        second = await service.process_attempt(attempt_id)
        state = await _state(system.database, batch_id, attempt_id)

    report = state["reports"][0]
    details = report.input_snapshot_json["processing_errors"][0]["details"]
    assert first is not None and second is not None
    assert first.verdict.value == second.verdict.value == "error"
    assert first.validation_report_id == second.validation_report_id
    assert len(state["reports"]) == 1
    assert len(state["metrics"]) == 1
    assert state["metrics"][0].metric_observation_id == metric_observation_id
    assert state["metrics"][0].numeric_value == Decimal("0.61")
    assert state["selection"] is None
    assert len(state["events"]) == 1
    assert details["persisted_metric_observation_id"] == str(metric_observation_id)
    assert details["expected_numeric_value"] == "0.61"
    assert details["observed_numeric_value"] == "0.82"


async def test_changed_input_after_report_fails_closed(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        workspace,
    ):
        service = _application(service_module, system.database.session)
        first = await service.process_attempt(attempt_id)
        (workspace / "runtime" / "output" / "result.json").write_bytes(
            b'{"schema_version":"v1","metrics":{"score":0.61}}'
        )
        with pytest.raises(service_module.ResultStateConflict):
            await service.process_attempt(attempt_id)
        state = await _state(system.database, batch_id, attempt_id)

    assert first is not None and first.selected is True
    assert len(state["artifacts"]) == len(state["metrics"]) == len(state["reports"]) == 1
    selection = state["selection"]
    assert selection is not None
    assert selection.validation_report_id == first.validation_report_id
    assert len(state["events"]) == 3


@pytest.mark.integration
async def test_persisted_report_output_corruption_fails_closed(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        service = _application(service_module, system.database.session)
        first = await service.process_attempt(attempt_id)
        async with system.database.session() as session:
            report = await session.get(ValidationReportRow, first.validation_report_id)
            assert report is not None
            report.verdict = "invalid"
            await session.commit()
        with pytest.raises(service_module.ResultStateConflict):
            await service.process_attempt(attempt_id)
        state = await _state(system.database, batch_id, attempt_id)

    assert len(state["reports"]) == 1 and state["reports"][0].verdict == "invalid"
    assert len(state["events"]) == 3


@pytest.mark.integration
async def test_existing_trial_selection_is_never_replaced_by_later_valid_attempt(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        first_attempt_id,
        workspace,
    ):
        service = _application(service_module, system.database.session)
        first = await service.process_attempt(first_attempt_id)
        second_attempt_id = uuid4()
        async with system.database.session() as session:
            first_attempt = await session.get(AttemptRow, first_attempt_id)
            assert first_attempt is not None
            now = datetime.now(UTC)
            session.add(
                AttemptRow(
                    attempt_id=second_attempt_id,
                    trial_id=trial_id,
                    attempt_number=2,
                    status="terminal",
                    supervisor_execution_identity=f"result-{second_attempt_id}",
                    workspace_path=str(workspace),
                    terminal_kind="process_exited",
                    exit_code=0,
                    observed_environment_json=first_attempt.observed_environment_json,
                    observed_environment_recorded_at=first_attempt.observed_environment_recorded_at,
                    terminal_at=now,
                )
            )
            await session.commit()
        second = await service.process_attempt(second_attempt_id)
        state = await _state(system.database, batch_id, first_attempt_id)

    assert first is not None and first.selected is True
    assert second is not None and second.selected is False
    assert state["selection"] is not None and state["selection"].attempt_id == first_attempt_id


@pytest.mark.integration
async def test_concurrent_same_attempt_creates_one_immutable_result_graph(
    isolated_database_url: str, tmp_path: Path
) -> None:
    service_module = _service_module()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        first, second = await asyncio.gather(
            _application(service_module, system.database.session).process_attempt(attempt_id),
            _application(service_module, system.database.session).process_attempt(attempt_id),
        )
        state = await _state(system.database, batch_id, attempt_id)

    assert first is not None and second is not None
    assert first.validation_report_id == second.validation_report_id
    assert len(state["artifacts"]) == len(state["metrics"]) == len(state["reports"]) == 1
    assert state["selection"] is not None and len(state["events"]) == 3
