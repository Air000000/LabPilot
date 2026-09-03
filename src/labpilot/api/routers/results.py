from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from labpilot.api.dependencies import get_result_query_service
from labpilot.api.schemas.results import (
    AttemptResultsResponse,
    BatchSummaryResponse,
    TrialResultResponse,
)
from labpilot.results_validation.query_service import ResultQueryService

router = APIRouter(tags=["results"])


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
