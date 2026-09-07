from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import fields
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.integration.experiments.test_proposal_service import _context
from tests.integration.test_result_processing_service import _seed_result_attempt

from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.db.uow import UnitOfWork
from labpilot.execution.batch_service import BatchApplicationService, CreateBatch
from labpilot.execution.models import BatchRow
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import RuntimeEventRow
from labpilot.observability.payloads import prepare_runtime_event_payload

pytestmark = pytest.mark.integration

_RESULT_EVENT_TYPES = (
    "result.artifact_registered",
    "result.validation_completed",
    "result.trial_selected",
    "result.summary_created",
)
_STREAM_TIMEOUT_SECONDS = 2


def _app(database: object) -> object:
    query_module = importlib.import_module("labpilot.results_validation.query_service")
    values: dict[str, object] = {
        "projects": ProjectApplicationService(database.session),  # type: ignore[attr-defined]
    }
    if "result_queries" in {field.name for field in fields(ApplicationServices)}:
        values["result_queries"] = query_module.ResultQueryService(  # type: ignore[no-untyped-call]
            database.session  # type: ignore[attr-defined]
        )
    return create_app(
        services=ApplicationServices(**values),  # type: ignore[arg-type]
        actor_provider=ServerConfiguredActorProvider(
            installation_identity="task11-sse", actor_id="operator"
        ),
    )


@asynccontextmanager
async def _client(database: object) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=_app(database)), base_url="http://test"
    ) as client:
        yield client


def _result_payload(
    event_type: str,
    *,
    attempt_id: UUID,
    trial_id: UUID,
    position: int,
) -> dict[str, object]:
    if event_type == "result.artifact_registered":
        return {
            "attempt_id": str(attempt_id),
            "artifact_id": str(uuid4()),
            "artifact_name": f"artifact-{position}",
            "content_sha256": "a" * 64,
            "byte_size": position,
            "media_type": "application/json",
        }
    if event_type == "result.validation_completed":
        return {
            "attempt_id": str(attempt_id),
            "validation_report_id": str(uuid4()),
            "verdict": "valid",
            "reason_codes": [],
            "warning_codes": [],
            "metric_names": ["score"],
        }
    if event_type == "result.trial_selected":
        return {
            "trial_id": str(trial_id),
            "attempt_id": str(attempt_id),
            "validation_report_id": str(uuid4()),
        }
    assert event_type == "result.summary_created"
    return {
        "batch_summary_revision_id": str(uuid4()),
        "revision_number": position,
        "presentation_status": "complete",
        "included_count": 1,
        "excluded_count": 0,
    }


async def _seed_result_events(
    database: object,
    batch_id: UUID,
    trial_id: UUID,
    attempt_id: UUID,
    *,
    count: int = 4,
) -> None:
    importlib.import_module("labpilot.results_validation.service")
    importlib.import_module("labpilot.results_validation.summary_service")
    async with database.session() as session:  # type: ignore[attr-defined]
        batch = await session.get(BatchRow, batch_id)
        assert batch is not None

    async with UnitOfWork(database.session) as uow:  # type: ignore[attr-defined]
        for position in range(1, count + 1):
            event_type = _RESULT_EVENT_TYPES[(position - 1) % len(_RESULT_EVENT_TYPES)]
            payload = _result_payload(
                event_type,
                attempt_id=attempt_id,
                trial_id=trial_id,
                position=position,
            )
            await append_runtime_event(
                uow,
                RuntimeEventEnvelope(
                    project_id=batch.project_id,
                    aggregate_type="batch",
                    aggregate_id=str(batch_id),
                    event_type=event_type,
                    event_schema_version=1,
                    payload=prepare_runtime_event_payload(
                        event_type=event_type,
                        event_schema_version=1,
                        payload=payload,  # type: ignore[arg-type]
                    ),
                    occurred_at=None,
                    interaction_correlation_id=None,
                    workflow_correlation_id=None,
                    causation_id=None,
                    actor_type="system",
                    actor_id=None,
                    visibility_class="internal",
                    topic="runtime-events",
                    partition_key=f"batch:{batch_id}",
                ),
                enqueue_outbox=False,
            )
        await uow.commit()


async def _batch_event_truth(
    database: object, batch_id: UUID
) -> tuple[tuple[int, str, dict[str, object]], ...]:
    async with database.session() as session:  # type: ignore[attr-defined]
        events = tuple(
            await session.scalars(
                select(RuntimeEventRow)
                .where(
                    RuntimeEventRow.aggregate_type == "batch",
                    RuntimeEventRow.aggregate_id == str(batch_id),
                )
                .order_by(RuntimeEventRow.aggregate_sequence)
            )
        )
    return tuple(
        (event.aggregate_sequence, event.event_type, deepcopy(event.payload_json))
        for event in events
    )


async def _append_same_id_other_aggregate(database: object, batch_id: UUID) -> None:
    async with UnitOfWork(database.session) as uow:  # type: ignore[attr-defined]
        await append_runtime_event(
            uow,
            RuntimeEventEnvelope.for_test(str(batch_id)),
            enqueue_outbox=False,
        )
        await uow.commit()


async def _create_other_batch(system: object, batch_id: UUID) -> UUID:
    async with system.database.session() as session:  # type: ignore[attr-defined]
        batch = await session.get(BatchRow, batch_id)
        assert batch is not None
        version_id = batch.experiment_version_id
    commands = BatchApplicationService(
        system.database.session,  # type: ignore[attr-defined]
        version_semantics=VersionExecutionSemanticsAdapter(
            system.database.session  # type: ignore[attr-defined]
        ),
    )
    created = await commands.create_batch(
        context=_context(),
        idempotency_key=f"task11-sse-other-{uuid4()}",
        command=CreateBatch(version_id),
    )
    return UUID(created.envelope.primary_result_id)


@asynccontextmanager
async def _event_batch(
    isolated_database_url: str, tmp_path: Any, *, result_event_count: int = 4
) -> AsyncIterator[tuple[object, UUID, UUID, UUID, tuple[tuple[int, str, dict[str, object]], ...]]]:
    async with _seed_result_attempt(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        _workspace,
    ):
        await _seed_result_events(
            system.database,
            batch_id,
            trial_id,
            attempt_id,
            count=result_event_count,
        )
        truth = await _batch_event_truth(system.database, batch_id)
        assert any(event_type == "batch.created" for _, event_type, _ in truth)
        assert any(event_type.startswith("result.") for _, event_type, _ in truth)
        yield system, batch_id, trial_id, attempt_id, truth


def _event_payload(event: object) -> dict[str, object]:
    payload = getattr(event, "payload_json", None)
    if payload is None:
        payload = event.payload
    assert isinstance(payload, dict)
    return payload


def _event_values(events: object) -> tuple[tuple[int, str, dict[str, object]], ...]:
    return tuple(
        (event.aggregate_sequence, event.event_type, _event_payload(event)) for event in events
    )


def _consume_sse_frames(buffer: bytearray) -> tuple[dict[str, object], ...]:
    frames: list[dict[str, object]] = []
    while True:
        matches = [
            (index, len(delimiter))
            for delimiter in (b"\n\n", b"\r\n\r\n")
            if (index := buffer.find(delimiter)) >= 0
        ]
        if not matches:
            return tuple(frames)
        index, delimiter_size = min(matches)
        frame = bytes(buffer[:index])
        del buffer[: index + delimiter_size]
        fields: dict[str, str] = {}
        for line in frame.decode("utf-8").splitlines():
            if line.startswith(":"):
                continue
            name, separator, value = line.partition(":")
            assert separator
            fields[name] = value.removeprefix(" ")
        if fields:
            assert set(fields) == {"id", "event", "data"}
            frames.append(
                {
                    "id": fields["id"],
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )


async def _stream_sse(
    app: Any,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    expected_event_count: int,
) -> tuple[int, dict[str, str], tuple[dict[str, object], ...]]:
    request_sent = False
    disconnected = asyncio.Event()
    response_started = asyncio.Event()
    expected_events_seen = asyncio.Event()
    body = bytearray()
    frames: list[dict[str, object]] = []
    response_status: int | None = None
    response_headers: dict[str, str] = {}

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        nonlocal response_status
        if message["type"] == "http.response.start":
            response_status = message["status"]  # type: ignore[assignment]
            response_headers.update(
                {
                    name.decode("latin-1"): value.decode("latin-1")
                    for name, value in message["headers"]  # type: ignore[union-attr]
                }
            )
            response_started.set()
            return
        if message["type"] == "http.response.body":
            body.extend(message.get("body", b""))  # type: ignore[arg-type]
            frames.extend(_consume_sse_frames(body))
            if len(frames) >= expected_event_count:
                expected_events_seen.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(response_started.wait(), _STREAM_TIMEOUT_SECONDS)
        assert response_status is not None
        if response_status == 200 and expected_event_count:
            await asyncio.wait_for(expected_events_seen.wait(), _STREAM_TIMEOUT_SECONDS)
        return response_status, response_headers, tuple(frames)
    finally:
        disconnected.set()
        try:
            await asyncio.wait_for(task, _STREAM_TIMEOUT_SECONDS)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_list_batch_events_after_zero_returns_full_batch_event_truth(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        truth,
    ):
        service = importlib.import_module("labpilot.results_validation.query_service")
        events = await service.ResultQueryService(system.database.session).list_batch_events_after(
            batch_id, after_sequence=0
        )

    assert _event_values(events) == truth
    assert any(event_type == "batch.created" for _, event_type, _ in truth)


@pytest.mark.asyncio
async def test_list_batch_events_after_uses_a_strict_persisted_sequence_cursor(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        truth,
    ):
        cursor = truth[1][0]
        service = importlib.import_module("labpilot.results_validation.query_service")
        events = await service.ResultQueryService(system.database.session).list_batch_events_after(
            batch_id, after_sequence=cursor
        )

    assert _event_values(events) == tuple(event for event in truth if event[0] > cursor)


@pytest.mark.asyncio
async def test_list_batch_events_after_excludes_another_batch(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        trial_id,
        attempt_id,
        truth,
    ):
        other_batch_id = await _create_other_batch(system, batch_id)
        await _seed_result_events(system.database, other_batch_id, trial_id, attempt_id)
        service = importlib.import_module("labpilot.results_validation.query_service")
        events = await service.ResultQueryService(system.database.session).list_batch_events_after(
            batch_id, after_sequence=0
        )

    assert _event_values(events) == truth


@pytest.mark.asyncio
async def test_list_batch_events_after_requires_batch_aggregate_type_as_well_as_id(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        truth,
    ):
        await _append_same_id_other_aggregate(system.database, batch_id)
        service = importlib.import_module("labpilot.results_validation.query_service")
        events = await service.ResultQueryService(system.database.session).list_batch_events_after(
            batch_id, after_sequence=0
        )

    assert _event_values(events) == truth


@pytest.mark.asyncio
async def test_list_batch_events_after_has_an_internal_limit_of_one_hundred(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path, result_event_count=101) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        truth,
    ):
        service = importlib.import_module("labpilot.results_validation.query_service")
        events = await service.ResultQueryService(system.database.session).list_batch_events_after(
            batch_id, after_sequence=0
        )

    assert _event_values(events) == truth[:100]


@pytest.mark.asyncio
async def test_batch_events_sse_has_event_stream_content_type(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        _truth,
    ):
        status, headers, _frames = await _stream_sse(
            _app(system.database),
            f"/batches/{batch_id}/events",
            expected_event_count=0,
        )

    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_batch_events_sse_emits_persisted_sequence_type_and_payload(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        truth,
    ):
        status, _headers, frames = await _stream_sse(
            _app(system.database),
            f"/batches/{batch_id}/events",
            expected_event_count=len(truth),
        )

    assert status == 200
    assert frames == tuple(
        {"id": str(sequence), "event": event_type, "data": payload}
        for sequence, event_type, payload in truth
    )


@pytest.mark.asyncio
async def test_batch_events_sse_resumes_strictly_after_persisted_last_event_id(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        truth,
    ):
        cursor = truth[1][0]
        suffix = tuple(event for event in truth if event[0] > cursor)
        status, _headers, frames = await _stream_sse(
            _app(system.database),
            f"/batches/{batch_id}/events",
            headers={"Last-Event-ID": str(cursor)},
            expected_event_count=len(suffix),
        )

    assert status == 200
    assert frames == tuple(
        {"id": str(sequence), "event": event_type, "data": payload}
        for sequence, event_type, payload in suffix
    )


@pytest.mark.asyncio
async def test_batch_events_sse_rejects_non_integer_last_event_id(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        _truth,
    ):
        async with _client(system.database) as client:
            response = await client.get(
                f"/batches/{batch_id}/events", headers={"Last-Event-ID": "abc"}
            )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_batch_events_sse_rejects_negative_last_event_id(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        batch_id,
        _trial_id,
        _attempt_id,
        _truth,
    ):
        async with _client(system.database) as client:
            response = await client.get(
                f"/batches/{batch_id}/events", headers={"Last-Event-ID": "-1"}
            )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_batch_events_sse_rejects_missing_batch_before_starting_stream(
    isolated_database_url: str, tmp_path: Any
) -> None:
    async with _event_batch(isolated_database_url, tmp_path) as (
        system,
        _batch_id,
        _trial_id,
        _attempt_id,
        _truth,
    ):
        status, headers, frames = await _stream_sse(
            _app(system.database),
            f"/batches/{uuid4()}/events",
            expected_event_count=0,
        )

    assert status == 404
    assert not headers["content-type"].startswith("text/event-stream")
    assert frames == ()
