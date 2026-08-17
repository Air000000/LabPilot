from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.db.idempotency import CommandResultEnvelope, IdempotentCommandOutcome
from labpilot.execution.batch_service import BatchApplicationService
from labpilot.execution.query_service import (
    AttemptDetails,
    BatchDetails,
    ExecutionQueryService,
    TrialDetails,
)
from labpilot.experiments.project_service import ProjectApplicationService

VERSION_ID = UUID("10000000-0000-4000-8000-000000000001")
BATCH_ID = UUID("20000000-0000-4000-8000-000000000002")
TRIAL_ID = UUID("30000000-0000-4000-8000-000000000003")
ATTEMPT_ID = UUID("40000000-0000-4000-8000-000000000004")
PROJECT_ID = UUID("50000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 8, 17, tzinfo=UTC)


def _outcome(state: str, *, replayed: bool = False) -> IdempotentCommandOutcome:
    return IdempotentCommandOutcome(
        envelope=CommandResultEnvelope(
            outcome_kind=f"batch_{state}",
            primary_result_type="batch",
            primary_result_id=str(BATCH_ID),
            primary_result_identity=None,
            related_result_refs=(),
            committed_state=state,
        ),
        replayed=replayed,
    )


class FakeBatches:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, str]] = []

    async def create_batch(self, *, context: object, idempotency_key: str, command: object):
        self.calls.append(("create", command, idempotency_key))
        return _outcome("created")

    async def start_batch(self, *, context: object, idempotency_key: str, command: object):
        self.calls.append(("start", command, idempotency_key))
        return _outcome("started")

    async def cancel_batch(self, *, context: object, idempotency_key: str, command: object):
        self.calls.append(("cancel", command, idempotency_key))
        return _outcome("cancelled")


class FakeQueries:
    async def get_batch(self, batch_id: UUID) -> BatchDetails:
        assert batch_id == BATCH_ID
        return BatchDetails(
            BATCH_ID,
            PROJECT_ID,
            VERSION_ID,
            NOW,
            None,
            2,
            NOW,
            "running",
            1,
            (TRIAL_ID,),
        )

    async def get_trial(self, trial_id: UUID) -> TrialDetails:
        assert trial_id == TRIAL_ID
        return TrialDetails(
            TRIAL_ID,
            BATCH_ID,
            1,
            "a" * 64,
            {"lr": 0.1},
            7,
            NOW,
            "running",
            1,
            (ATTEMPT_ID,),
        )

    async def get_attempt(self, attempt_id: UUID) -> AttemptDetails:
        assert attempt_id == ATTEMPT_ID
        return AttemptDetails(
            ATTEMPT_ID,
            TRIAL_ID,
            BATCH_ID,
            1,
            "unknown",
            "execution-1",
            NOW,
            NOW,
            NOW,
            NOW,
            None,
            None,
            None,
            None,
            None,
            None,
            NOW,
            NOW,
            "PHYSICAL_STATE_UNKNOWN",
            {"python_version": "3.12.8", "probe_issue_codes": ["RUNNER_UNAVAILABLE"]},
        )


def _client(batches: FakeBatches | None = None) -> TestClient:
    services = ApplicationServices(
        projects=cast(ProjectApplicationService, object()),
        batches=cast(BatchApplicationService, batches or FakeBatches()),
        execution_queries=cast(ExecutionQueryService, FakeQueries()),
    )
    return TestClient(
        create_app(
            services=services,
            actor_provider=ServerConfiguredActorProvider(
                installation_identity="installation-1", actor_id="air"
            ),
        )
    )


def test_batch_commands_call_only_batch_service() -> None:
    batches = FakeBatches()
    client = _client(batches)
    headers = {"Idempotency-Key": "key-1"}

    created = client.post(
        f"/experiment-versions/{VERSION_ID}/batches", headers=headers
    )
    started = client.post(
        f"/batches/{BATCH_ID}/start",
        headers=headers,
        json={"expected_row_version": 1},
    )
    cancelled = client.post(
        f"/batches/{BATCH_ID}/cancel",
        headers=headers,
        json={"expected_row_version": 2},
    )

    assert created.status_code == 201
    assert started.status_code == cancelled.status_code == 200
    assert [call[0] for call in batches.calls] == ["create", "start", "cancel"]
    assert created.json() == {
        "batch_id": str(BATCH_ID),
        "committed_state": "created",
        "replayed": False,
    }


def test_execution_gets_use_bounded_query_projection() -> None:
    client = _client()

    batch = client.get(f"/batches/{BATCH_ID}")
    trial = client.get(f"/trials/{TRIAL_ID}")
    attempt = client.get(f"/attempts/{ATTEMPT_ID}")

    assert batch.status_code == trial.status_code == attempt.status_code == 200
    assert batch.json()["presentation_status"] == "running"
    assert trial.json()["attempt_ids"] == [str(ATTEMPT_ID)]
    assert attempt.json()["status"] == "unknown"
    assert set(attempt.json()["observed_environment"]) == {
        "python_version",
        "probe_issue_codes",
    }
    assert "frozen_launch_spec" not in attempt.json()

