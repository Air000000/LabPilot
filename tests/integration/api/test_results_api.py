from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.integration.test_batch_summary_service import _outcomes, _summary_service
from tests.integration.test_result_processing_service import _application, _seed_result_attempt

from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.execution.models import AttemptRow, TrialRow
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import ResultProcessingDeferred

pytestmark = pytest.mark.integration


def _app(database: object, *, configure_result_queries: bool) -> object:
    values: dict[str, object] = {
        "projects": ProjectApplicationService(database.session),  # type: ignore[attr-defined]
    }
    if configure_result_queries and "result_queries" in {
        field.name for field in fields(ApplicationServices)
    }:
        module = importlib.import_module("labpilot.results_validation.query_service")
        values["result_queries"] = module.ResultQueryService(  # type: ignore[no-untyped-call]
            database.session  # type: ignore[attr-defined]
        )
    return create_app(
        services=ApplicationServices(**values),  # type: ignore[arg-type]
        actor_provider=ServerConfiguredActorProvider(
            installation_identity="task9-api", actor_id="operator"
        ),
    )


@asynccontextmanager
async def _client(
    database: object, *, configure_result_queries: bool = True
) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(
            app=_app(database, configure_result_queries=configure_result_queries)
        ),
        base_url="http://test",
    ) as client:
        yield client


async def _process_and_summarize(system: object, batch_id: UUID, attempt_id: UUID) -> None:
    service = importlib.import_module("labpilot.results_validation.service")
    await _application(service, system.database.session).process_attempt(attempt_id)  # type: ignore[attr-defined]
    summary = importlib.import_module("labpilot.results_validation.summary_service")
    await _summary_service(
        summary,
        system.database.session,  # type: ignore[attr-defined]
        _outcomes(system.database.session),  # type: ignore[attr-defined]
    ).refresh_summary(batch_id)


async def _scientific_history(
    database: object, attempt_id: UUID
) -> tuple[tuple[tuple[object, ...], ...], ...]:
    async with database.session() as session:  # type: ignore[attr-defined]
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
                select(ValidationReportRow).where(
                    ValidationReportRow.attempt_id == attempt_id
                )
            )
        )
    return (
        tuple(
            (
                row.artifact_id,
                row.attempt_id,
                row.artifact_name,
                row.relative_path,
                row.content_sha256,
                row.byte_size,
                row.media_type,
            )
            for row in artifacts
        ),
        tuple(
            (
                row.metric_observation_id,
                row.artifact_id,
                row.metric_name,
                row.split,
                row.numeric_value,
                row.extractor_version,
            )
            for row in metrics
        ),
        tuple(
            (
                row.validation_report_id,
                row.attempt_id,
                row.validation_protocol_hash,
                row.extraction_profile_version,
                row.validator_version,
                deepcopy(row.input_snapshot_json),
                row.verdict,
                deepcopy(row.reason_json),
                deepcopy(row.warning_json),
            )
            for row in reports
        ),
    )


async def _provenance_ids(
    database: object, attempt_id: UUID
) -> tuple[set[UUID], set[UUID], set[UUID]]:
    artifacts, metrics, reports = await _scientific_history(database, attempt_id)
    return (
        {row[0] for row in artifacts},
        {row[0] for row in metrics},
        {row[0] for row in reports},
    )


async def _add_second_result_attempt(
    database: object, first_attempt_id: UUID, tmp_path: Path
) -> UUID:
    workspace = tmp_path / "workspace-b"
    output_root = workspace / "runtime" / "output"
    output_root.mkdir(parents=True)
    (output_root / "result.json").write_bytes(
        b'{"schema_version":"v1","metrics":{"score":0.81}}'
    )
    async with database.session() as session:  # type: ignore[attr-defined]
        first = await session.get(AttemptRow, first_attempt_id)
        assert first is not None
        first_trial = await session.get(TrialRow, first.trial_id)
        assert first_trial is not None
        trial_id, attempt_id = uuid4(), uuid4()
        session.add(
            TrialRow(
                trial_id=trial_id,
                batch_id=first_trial.batch_id,
                trial_ordinal=first_trial.trial_ordinal + 1,
                trial_identity=uuid4().hex,
                resolved_parameters_json=deepcopy(first_trial.resolved_parameters_json),
                seed=first_trial.seed + 1,
            )
        )
        await session.flush()
        session.add(
            AttemptRow(
                attempt_id=attempt_id,
                trial_id=trial_id,
                attempt_number=1,
                status="terminal",
                supervisor_execution_identity=f"result-{attempt_id}",
                workspace_path=str(workspace),
                observed_environment_json=deepcopy(first.observed_environment_json),
                observed_environment_recorded_at=first.observed_environment_recorded_at,
                terminal_kind="process_exited",
                exit_code=0,
                terminal_at=first.terminal_at,
            )
        )
        await session.commit()
    return attempt_id


@pytest.mark.asyncio
async def test_existing_attempt_without_result_facts_is_an_empty_read_model(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async with _client(system.database) as client:
            response = await client.get(f"/attempts/{attempt_id}/results")

    assert response.status_code == 200
    assert response.json() == {
        "attempt_id": str(attempt_id),
        "artifacts": [],
        "metrics": [],
        "validation_reports": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "aggregate_type"),
    (
        ("/attempts/{identifier}/results", "attempt"),
        ("/trials/{identifier}/result", "trial"),
        ("/batches/{identifier}/summary", "batch"),
    ),
)
async def test_missing_result_aggregate_has_stable_specific_not_found_error(
    isolated_database_url: str,
    tmp_path: Path,
    path: str,
    aggregate_type: str,
) -> None:
    missing_id = uuid4()
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        _attempt_id,
        _workspace,
    ):
        async with _client(system.database) as client:
            response = await client.get(path.format(identifier=missing_id))

    assert response.status_code == 404
    assert response.json()["error_code"] == f"{aggregate_type}_not_found"
    assert response.json()["aggregate_type"] == aggregate_type
    assert response.json()["aggregate_id"] == str(missing_id)
    assert response.json()["retryable"] is False


@pytest.mark.asyncio
async def test_attempt_result_api_preserves_history_and_derives_safe_availability(
    isolated_database_url: str, tmp_path: Path
) -> None:
    source = b'{"schema_version":"v1","metrics":{"score":0.8200}}'
    async with _seed_result_attempt(
        isolated_database_url, tmp_path, output=source
    ) as (system, _batch_id, _trial_id, attempt_id, workspace):
        await _process_and_summarize(system, _batch_id, attempt_id)
        historical = await _scientific_history(system.database, attempt_id)
        assert len(historical[0]) == len(historical[1]) == len(historical[2]) == 1
        async with _client(system.database) as client:
            available = await client.get(f"/attempts/{attempt_id}/results")
            assert available.status_code == 200
            available_body = available.json()
            artifact = available_body["artifacts"][0]
            assert set(artifact) == {
                "artifact_id",
                "artifact_name",
                "relative_path",
                "content_sha256",
                "byte_size",
                "media_type",
                "availability",
            }
            assert artifact["availability"] == "available"
            assert str(workspace) not in str(available_body)
            assert not {
                "workspace_path",
                "absolute_path",
                "content",
                "raw_bytes",
                "bytes",
            }.intersection(artifact)
            assert available_body["metrics"][0]["numeric_value"] == "0.82"
            assert type(available_body["metrics"][0]["numeric_value"]) is str
            assert {
                "metric_observation_id",
                "artifact_id",
                "metric_name",
                "split",
                "numeric_value",
                "extractor_version",
            }.issubset(available_body["metrics"][0])
            assert {
                "validation_report_id",
                "validation_protocol_hash",
                "extraction_profile_version",
                "validator_version",
                "verdict",
                "reasons",
                "warnings",
            }.issubset(available_body["validation_reports"][0])

            output = workspace / "runtime" / "output" / "result.json"
            output.unlink()
            missing = await client.get(f"/attempts/{attempt_id}/results")
            assert missing.status_code == 200
            assert missing.json()["artifacts"][0]["availability"] == "missing"
            assert missing.json()["metrics"] == available_body["metrics"]
            assert missing.json()["validation_reports"] == available_body["validation_reports"]

            output.write_bytes(b'{"schema_version":"v1","metrics":{"score":0.81}}')
            corrupted = await client.get(f"/attempts/{attempt_id}/results")
            assert corrupted.status_code == 200
            assert corrupted.json()["artifacts"][0]["availability"] == "corrupted"
            assert corrupted.json()["metrics"] == available_body["metrics"]
            assert corrupted.json()["validation_reports"] == available_body["validation_reports"]

            output.unlink()
            os.symlink("/tmp/not-result-json", output)
            unsafe = await client.get(f"/attempts/{attempt_id}/results")
            assert unsafe.status_code == 200
            assert unsafe.json()["artifacts"][0]["availability"] == "corrupted"

        assert await _scientific_history(system.database, attempt_id) == historical


@pytest.mark.asyncio
async def test_attempt_result_api_returns_historical_reports_without_revalidation(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        service = importlib.import_module("labpilot.results_validation.service")
        await _application(service, system.database.session).process_attempt(attempt_id)
        historical_id = uuid4()
        async with system.database.session() as session:
            current = await session.scalar(
                select(ValidationReportRow).where(
                    ValidationReportRow.attempt_id == attempt_id
                )
            )
            assert current is not None
            session.add(
                ValidationReportRow(
                    validation_report_id=historical_id,
                    attempt_id=attempt_id,
                    validation_protocol_hash=current.validation_protocol_hash,
                    extraction_profile_version=(
                        f"{current.extraction_profile_version}-historical"
                    ),
                    validator_version=f"{current.validator_version}-historical",
                    input_snapshot_json=deepcopy(current.input_snapshot_json),
                    verdict="invalid",
                    reason_json={"reasons": [{"code": "METRIC_BELOW_THRESHOLD"}]},
                    warning_json={
                        "warnings": [
                            {"code": "DATASET_BEST_EFFORT_REPRODUCIBILITY"}
                        ]
                    },
                )
            )
            await session.commit()
            expected_report_ids = [
                str(report.validation_report_id)
                for report in tuple(
                    await session.scalars(
                        select(ValidationReportRow)
                        .where(ValidationReportRow.attempt_id == attempt_id)
                        .order_by(
                            ValidationReportRow.created_at.asc(),
                            ValidationReportRow.validation_report_id.asc(),
                        )
                    )
                )
            ]
        async with _client(system.database) as client:
            response = await client.get(f"/attempts/{attempt_id}/results")

    assert response.status_code == 200
    reports = response.json()["validation_reports"]
    assert [report["validation_report_id"] for report in reports] == expected_report_ids
    historical = next(
        report for report in reports if report["validation_report_id"] == str(historical_id)
    )
    assert historical["reasons"] == [{"code": "METRIC_BELOW_THRESHOLD"}]
    assert historical["warnings"] == [
        {"code": "DATASET_BEST_EFFORT_REPRODUCIBILITY"}
    ]


@pytest.mark.asyncio
async def test_attempt_result_api_scopes_artifacts_metrics_and_reports_per_attempt(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_a_id,
        attempt_a_id,
        _workspace,
    ):
        attempt_b_id = await _add_second_result_attempt(
            system.database, attempt_a_id, tmp_path
        )
        service = importlib.import_module("labpilot.results_validation.service")
        await _application(service, system.database.session).process_attempt(attempt_a_id)
        await _application(service, system.database.session).process_attempt(attempt_b_id)
        expected_a = await _provenance_ids(system.database, attempt_a_id)
        expected_b = await _provenance_ids(system.database, attempt_b_id)
        assert all(expected_a) and all(expected_b)
        async with _client(system.database) as client:
            response_a = await client.get(f"/attempts/{attempt_a_id}/results")
            response_b = await client.get(f"/attempts/{attempt_b_id}/results")

    assert response_a.status_code == response_b.status_code == 200
    for response, expected, other in (
        (response_a, expected_a, expected_b),
        (response_b, expected_b, expected_a),
    ):
        body = response.json()
        returned = (
            {UUID(item["artifact_id"]) for item in body["artifacts"]},
            {UUID(item["metric_observation_id"]) for item in body["metrics"]},
            {UUID(item["validation_report_id"]) for item in body["validation_reports"]},
        )
        assert returned == expected
        assert all(not values.intersection(other[index]) for index, values in enumerate(returned))


@pytest.mark.asyncio
async def test_trial_result_api_distinguishes_no_selection_and_persisted_selection(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        async with _client(system.database) as client:
            empty = await client.get(f"/trials/{trial_id}/result")
            assert empty.status_code == 200
            assert empty.json() == {"trial_id": str(trial_id), "selection": None}

        service = importlib.import_module("labpilot.results_validation.service")
        processed = await _application(service, system.database.session).process_attempt(
            attempt_id
        )
        assert processed is not None
        historical_id = uuid4()
        async with system.database.session() as session:
            current = await session.get(
                ValidationReportRow, processed.validation_report_id
            )
            assert current is not None
            session.add(
                ValidationReportRow(
                    validation_report_id=historical_id,
                    attempt_id=attempt_id,
                    validation_protocol_hash=current.validation_protocol_hash,
                    extraction_profile_version=(
                        f"{current.extraction_profile_version}-historical"
                    ),
                    validator_version=f"{current.validator_version}-historical",
                    input_snapshot_json=deepcopy(current.input_snapshot_json),
                    verdict="invalid",
                    reason_json={"reasons": [{"code": "METRIC_BELOW_THRESHOLD"}]},
                    warning_json={"warnings": []},
                )
            )
            await session.commit()

        async with _client(system.database) as client:
            selected = await client.get(f"/trials/{trial_id}/result")

    assert selected.status_code == 200
    assert selected.json()["selection"] == {
        "attempt_id": str(attempt_id),
        "validation_report_id": str(processed.validation_report_id),
    }


@pytest.mark.asyncio
async def test_batch_summary_api_distinguishes_no_revision_and_latest_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async with _client(system.database) as client:
            empty = await client.get(f"/batches/{batch_id}/summary")
            assert empty.status_code == 200
            assert empty.json() == {"batch_id": str(batch_id), "latest_summary": None}

        await _process_and_summarize(system, batch_id, attempt_id)
        async with system.database.session() as session:
            first = await session.scalar(
                select(BatchSummaryRevisionRow).where(
                    BatchSummaryRevisionRow.batch_id == batch_id
                )
            )
            assert first is not None
            second_id = uuid4()
            session.add(
                BatchSummaryRevisionRow(
                    batch_summary_revision_id=second_id,
                    batch_id=batch_id,
                    revision_number=2,
                    summary_protocol_version=first.summary_protocol_version,
                    included_result_refs_json=deepcopy(first.included_result_refs_json),
                    excluded_reasons_json=deepcopy(first.excluded_reasons_json),
                    warning_json=deepcopy(first.warning_json),
                    aggregates_json=deepcopy(first.aggregates_json),
                )
            )
            await session.commit()
        async with _client(system.database) as client:
            summary = await client.get(f"/batches/{batch_id}/summary")

    assert summary.status_code == 200
    latest = summary.json()["latest_summary"]
    assert set(latest) == {
        "revision_id",
        "revision_number",
        "presentation_status",
        "summary_protocol_version",
        "included",
        "excluded",
        "warnings",
        "aggregates",
    }
    assert latest["revision_id"] == str(second_id)
    assert latest["revision_number"] == 2
    assert latest["presentation_status"] == "complete"
    assert latest["aggregates"] == {"score": {"count": 1, "mean": "0.82"}}


@pytest.mark.asyncio
async def test_result_routes_require_a_configured_query_service(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        async with _client(system.database, configure_result_queries=False) as client:
            response = await client.get(f"/attempts/{attempt_id}/results")

    assert response.status_code == 503
    assert response.json()["error_code"] == "service_unavailable"
    assert response.json()["retryable"] is True


@pytest.mark.asyncio
async def test_transient_safe_reader_failure_is_service_unavailable(
    isolated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        attempt_id,
        _workspace,
    ):
        service = importlib.import_module("labpilot.results_validation.service")
        await _application(service, system.database.session).process_attempt(attempt_id)
        historical = await _scientific_history(system.database, attempt_id)
        assert len(historical[0]) == len(historical[1]) == len(historical[2]) == 1
        if importlib.util.find_spec("labpilot.results_validation.query_service"):
            module = importlib.import_module("labpilot.results_validation.query_service")

            def deferred_reader(
                output_root: Path, relative_path: str, media_type: str
            ) -> object:
                assert output_root.name == "output"
                assert relative_path == "result.json"
                assert media_type == "application/json"
                raise ResultProcessingDeferred("EAGAIN")

            monkeypatch.setattr(module, "read_artifact_identity", deferred_reader)
        async with _client(system.database) as client:
            response = await client.get(f"/attempts/{attempt_id}/results")

    assert response.status_code == 503
    assert response.json()["error_code"] == "service_unavailable"
    assert response.json()["retryable"] is True
    assert "EAGAIN" not in response.text
    assert await _scientific_history(system.database, attempt_id) == historical


@pytest.mark.asyncio
async def test_result_routes_have_no_public_mutation_commands_and_only_allow_get(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        async with _client(system.database) as client:
            for command_path in ("/process-results", "/validate", "/generate-summary"):
                assert (await client.post(command_path)).status_code == 404
            for resource_path in (
                f"/attempts/{attempt_id}/results",
                f"/trials/{trial_id}/result",
                f"/batches/{batch_id}/summary",
            ):
                assert (await client.post(resource_path)).status_code == 405
