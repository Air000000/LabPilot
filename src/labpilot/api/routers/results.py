import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from labpilot.api.dependencies import get_result_query_service
from labpilot.api.schemas.results import (
    AttemptResultsResponse,
    BatchSummaryResponse,
    TrialResultResponse,
)
from labpilot.results_validation.query_service import ResultQueryService

router = APIRouter(tags=["results"])


def _last_event_id_after_sequence(
    last_event_id: Annotated[str | None, Header()] = None,
) -> int:
    if last_event_id is None:
        return 0
    if not last_event_id.isascii() or not last_event_id.isdecimal():
        raise HTTPException(status_code=400)
    return int(last_event_id)


async def _get_existing_batch_event_service(
    batch_id: UUID,
    service: Annotated[ResultQueryService, Depends(get_result_query_service)],
) -> ResultQueryService:
    await service.ensure_batch_exists(batch_id)
    return service


@router.get("/attempts/{attempt_id}/results", response_model=AttemptResultsResponse)
async def get_attempt_results(
    attempt_id: UUID,
    service: Annotated[ResultQueryService, Depends(get_result_query_service)],
) -> AttemptResultsResponse:
    return AttemptResultsResponse.model_validate(
        await service.get_attempt_results(attempt_id)
    )


@router.get("/trials/{trial_id}/result", response_model=TrialResultResponse)
async def get_trial_result(
    trial_id: UUID,
    service: Annotated[ResultQueryService, Depends(get_result_query_service)],
) -> TrialResultResponse:
    return TrialResultResponse.model_validate(await service.get_trial_result(trial_id))


@router.get("/batches/{batch_id}/summary", response_model=BatchSummaryResponse)
async def get_batch_summary(
    batch_id: UUID,
    service: Annotated[ResultQueryService, Depends(get_result_query_service)],
) -> BatchSummaryResponse:
    return BatchSummaryResponse.model_validate(await service.get_batch_summary(batch_id))


@router.get("/batches/{batch_id}/events", response_class=EventSourceResponse)
async def stream_batch_events(
    batch_id: UUID,
    request: Request,
    service: Annotated[ResultQueryService, Depends(_get_existing_batch_event_service)],
    after_sequence: Annotated[int, Depends(_last_event_id_after_sequence)],
) -> AsyncIterator[ServerSentEvent]:
    cursor = after_sequence
    while True:
        if await request.is_disconnected():
            return
        events = await service.list_batch_events_after(batch_id, after_sequence=cursor)
        if events:
            for event in events:
                yield ServerSentEvent(
                    id=str(event.aggregate_sequence),
                    event=event.event_type,
                    data=event.payload_json,
                )
                cursor = event.aggregate_sequence
            continue
        await asyncio.sleep(0.5)
