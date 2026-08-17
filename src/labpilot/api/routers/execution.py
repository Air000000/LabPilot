from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from labpilot.api.dependencies import (
    get_batch_service,
    get_command_context,
    get_execution_query_service,
)
from labpilot.api.errors import (
    RequestDomainValidationError,
    UnexpectedApplicationInvariant,
)
from labpilot.api.routers.projects import (
    IdempotencyKey,
    _validate_idempotency_key_transport,
)
from labpilot.api.schemas.execution import (
    AttemptResponse,
    BatchCommandResponse,
    BatchControlRequest,
    BatchResponse,
    TrialResponse,
)
from labpilot.common.commands import CommandContext
from labpilot.db.idempotency import IdempotentCommandOutcome
from labpilot.execution.batch_service import (
    BatchApplicationService,
    CancelBatch,
    StartBatch,
)
from labpilot.execution.query_service import ExecutionQueryService

router = APIRouter(tags=["execution"])


def batch_command_response(
    outcome: IdempotentCommandOutcome, *, committed_state: str
) -> BatchCommandResponse:
    envelope = outcome.envelope
    if (
        envelope.primary_result_type != "batch"
        or envelope.committed_state != committed_state
    ):
        raise UnexpectedApplicationInvariant("invalid Batch result envelope")
    try:
        batch_id = UUID(envelope.primary_result_id)
    except ValueError as exc:
        raise UnexpectedApplicationInvariant("invalid Batch result identifier") from exc
    return BatchCommandResponse(
        batch_id=batch_id,
        committed_state=envelope.committed_state,
        replayed=outcome.replayed,
    )


@router.post("/batches/{batch_id}/start", response_model=BatchCommandResponse)
async def start_batch(
    batch_id: UUID,
    body: BatchControlRequest,
    idempotency_key: IdempotencyKey,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[BatchApplicationService, Depends(get_batch_service)],
) -> BatchCommandResponse:
    idempotency_key = _validate_idempotency_key_transport(idempotency_key)
    try:
        command = StartBatch(batch_id, body.expected_row_version)
    except ValueError as exc:
        raise RequestDomainValidationError("invalid Batch request") from exc
    return batch_command_response(
        await service.start_batch(
            context=context, idempotency_key=idempotency_key, command=command
        ),
        committed_state="started",
    )


@router.post("/batches/{batch_id}/cancel", response_model=BatchCommandResponse)
async def cancel_batch(
    batch_id: UUID,
    body: BatchControlRequest,
    idempotency_key: IdempotencyKey,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[BatchApplicationService, Depends(get_batch_service)],
) -> BatchCommandResponse:
    idempotency_key = _validate_idempotency_key_transport(idempotency_key)
    try:
        command = CancelBatch(batch_id, body.expected_row_version)
    except ValueError as exc:
        raise RequestDomainValidationError("invalid Batch request") from exc
    return batch_command_response(
        await service.cancel_batch(
            context=context, idempotency_key=idempotency_key, command=command
        ),
        committed_state="cancel_requested",
    )


@router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: UUID,
    service: Annotated[ExecutionQueryService, Depends(get_execution_query_service)],
) -> BatchResponse:
    return BatchResponse.model_validate(await service.get_batch(batch_id))


@router.get("/trials/{trial_id}", response_model=TrialResponse)
async def get_trial(
    trial_id: UUID,
    service: Annotated[ExecutionQueryService, Depends(get_execution_query_service)],
) -> TrialResponse:
    return TrialResponse.model_validate(await service.get_trial(trial_id))


@router.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(
    attempt_id: UUID,
    service: Annotated[ExecutionQueryService, Depends(get_execution_query_service)],
) -> AttemptResponse:
    return AttemptResponse.model_validate(await service.get_attempt(attempt_id))
