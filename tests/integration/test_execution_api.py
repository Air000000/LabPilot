from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.db.idempotency_models import CommandReceiptRow
from labpilot.execution.batch_service import (
    AttemptCreationService,
    BatchApplicationService,
)
from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.execution.query_service import ExecutionQueryService
from labpilot.execution.types import AttemptStatus
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.observability.models import RuntimeEventRow
from tests.integration.test_batch_commands import _approved_version


@pytest.mark.integration
async def test_batch_api_round_trip_and_attempt_projection_is_bounded(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _approved_version(isolated_database_url, tmp_path) as (system, version):
        semantics = VersionExecutionSemanticsAdapter(system.database.session)
        batches = BatchApplicationService(
            system.database.session, version_semantics=semantics
        )
        app = create_app(
            services=ApplicationServices(
                projects=ProjectApplicationService(system.database.session),
                batches=batches,
                execution_queries=ExecutionQueryService(system.database.session),
            ),
            actor_provider=ServerConfiguredActorProvider(
                installation_identity="installation-1", actor_id="air"
            ),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                f"/experiment-versions/{version.experiment_version_id}/batches",
                headers={"Idempotency-Key": "create-api"},
            )
            assert created.status_code == 201
            batch_id = UUID(created.json()["batch_id"])
            batch = await client.get(f"/batches/{batch_id}")
            assert batch.status_code == 200
            assert batch.json()["trial_count"] == 8

            started = await client.post(
                f"/batches/{batch_id}/start",
                headers={"Idempotency-Key": "start-api"},
                json={"expected_row_version": 1},
            )
            assert started.status_code == 200
            trial_id = UUID(batch.json()["trial_ids"][0])
            attempts = AttemptCreationService(
                system.database.session,
                version_semantics=semantics,
                installation_identity="installation-1",
                attempt_workspace_root=tmp_path / "attempts",
            )
            attempt = await attempts.create_attempt_for_trial(trial_id)
            assert attempt is not None
            async with system.database.session() as session:
                async with session.begin():
                    row = await session.get(AttemptRow, attempt.attempt_id)
                    assert row is not None
                    row.status = AttemptStatus.UNKNOWN.value
                    row.unknown_since = datetime.now(UTC)
                    row.observed_environment_json = {
                        "python_version": "3.12.8",
                        "probe_issue_codes": ["RUNNER_UNAVAILABLE"],
                        "secret": "must-not-leak",
                    }
                    row.observed_environment_recorded_at = datetime.now(UTC)

            projected = await client.get(f"/attempts/{attempt.attempt_id}")
            assert projected.status_code == 200
            assert projected.json()["status"] == "unknown"
            assert projected.json()["observed_environment"] == {
                "python_version": "3.12.8",
                "probe_issue_codes": ["RUNNER_UNAVAILABLE"],
            }
            assert "must-not-leak" not in projected.text

            missing = await client.get(f"/attempts/{UUID(int=0)}")
            assert missing.status_code == 404
            assert missing.json()["error_code"] == "attempt_not_found"


@pytest.mark.integration
async def test_real_cancel_api_returns_committed_state_and_replays_without_side_effect(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _approved_version(isolated_database_url, tmp_path) as (system, version):
        semantics = VersionExecutionSemanticsAdapter(system.database.session)
        app = create_app(
            services=ApplicationServices(
                projects=ProjectApplicationService(system.database.session),
                batches=BatchApplicationService(
                    system.database.session, version_semantics=semantics
                ),
                execution_queries=ExecutionQueryService(system.database.session),
            ),
            actor_provider=ServerConfiguredActorProvider(
                installation_identity="installation-1", actor_id="air"
            ),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                f"/experiment-versions/{version.experiment_version_id}/batches",
                headers={"Idempotency-Key": "create-cancel-api"},
            )
            batch_id = UUID(created.json()["batch_id"])
            await client.post(
                f"/batches/{batch_id}/start",
                headers={"Idempotency-Key": "start-cancel-api"},
                json={"expected_row_version": 1},
            )
            cancel = await client.post(
                f"/batches/{batch_id}/cancel",
                headers={"Idempotency-Key": "cancel-api"},
                json={"expected_row_version": 2},
            )
            replay = await client.post(
                f"/batches/{batch_id}/cancel",
                headers={"Idempotency-Key": "cancel-api"},
                json={"expected_row_version": 2},
            )

        async with system.database.session() as session:
            batch = await session.get(BatchRow, batch_id)
            attempt_count = await session.scalar(select(func.count(AttemptRow.attempt_id)))

        assert cancel.status_code == replay.status_code == 200
        assert cancel.json() == {
            "batch_id": str(batch_id),
            "committed_state": "cancel_requested",
            "replayed": False,
        }
        assert replay.json() == {**cancel.json(), "replayed": True}
        assert batch is not None and batch.cancel_requested_at is not None
        assert batch.row_version == 3
        assert attempt_count == 0


@pytest.mark.integration
async def test_missing_experiment_version_batch_create_is_not_found_and_writes_nothing(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _approved_version(isolated_database_url, tmp_path) as (system, _):
        semantics = VersionExecutionSemanticsAdapter(system.database.session)
        app = create_app(
            services=ApplicationServices(
                projects=ProjectApplicationService(system.database.session),
                batches=BatchApplicationService(
                    system.database.session, version_semantics=semantics
                ),
                execution_queries=ExecutionQueryService(system.database.session),
            ),
            actor_provider=ServerConfiguredActorProvider(
                installation_identity="installation-1", actor_id="air"
            ),
        )
        async with system.database.session() as session:
            before = (
                await session.scalar(select(func.count(BatchRow.batch_id))),
                await session.scalar(select(func.count(TrialRow.trial_id))),
                await session.scalar(select(func.count(RuntimeEventRow.event_id))),
                await session.scalar(
                    select(func.count(CommandReceiptRow.command_receipt_id))
                ),
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/experiment-versions/{UUID(int=0)}/batches",
                headers={"Idempotency-Key": "missing-version"},
            )

        async with system.database.session() as session:
            after = (
                await session.scalar(select(func.count(BatchRow.batch_id))),
                await session.scalar(select(func.count(TrialRow.trial_id))),
                await session.scalar(select(func.count(RuntimeEventRow.event_id))),
                await session.scalar(
                    select(func.count(CommandReceiptRow.command_receipt_id))
                ),
            )

        assert response.status_code == 404
        assert response.json()["error_code"] == "resource_not_found"
        assert after == before
