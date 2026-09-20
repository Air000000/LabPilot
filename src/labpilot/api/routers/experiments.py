from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from labpilot.api.dependencies import (
    get_batch_service,
    get_command_context,
    get_proposal_service,
)
from labpilot.api.errors import RequestDomainValidationError
from labpilot.api.routers.execution import batch_command_response
from labpilot.api.routers.projects import (
    IdempotencyKey,
    _validate_idempotency_key_transport,
)
from labpilot.api.schemas.execution import BatchCommandResponse
from labpilot.api.schemas.experiments import (
    ApproveProposalRevisionRequest,
    CreateProposalRevisionRequest,
    DesignDecisionResponse,
    ExperimentVersionResponse,
    ProposalRevisionResponse,
    ProposalVerificationResponse,
    RejectProposalRevisionRequest,
)
from labpilot.common.commands import CommandContext
from labpilot.execution.batch_service import BatchApplicationService, CreateBatch
from labpilot.experiments.proposal_service import ProposalApplicationService

router = APIRouter(tags=["experiments"])


@router.post(
    "/experiment-versions/{experiment_version_id}/batches",
    response_model=BatchCommandResponse,
    status_code=201,
)
async def create_batch(
    experiment_version_id: UUID,
    response: Response,
    idempotency_key: IdempotencyKey,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[BatchApplicationService, Depends(get_batch_service)],
) -> BatchCommandResponse:
    idempotency_key = _validate_idempotency_key_transport(idempotency_key)
    outcome = await service.create_batch(
        context=context,
        idempotency_key=idempotency_key,
        command=CreateBatch(experiment_version_id),
    )
    response.status_code = 200 if outcome.replayed else 201
    return batch_command_response(outcome, committed_state="created")


@router.get(
    "/proposal-revisions/{proposal_revision_id}",
    response_model=ProposalRevisionResponse,
)
async def get_proposal_revision(
    proposal_revision_id: UUID,
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> ProposalRevisionResponse:
    return ProposalRevisionResponse.from_details(
        await service.get_revision(proposal_revision_id)
    )


@router.post(
    "/experiments/{experiment_id}/proposal-revisions",
    response_model=ProposalRevisionResponse,
    status_code=201,
)
async def create_proposal_revision(
    experiment_id: UUID,
    body: CreateProposalRevisionRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> ProposalRevisionResponse:
    try:
        payload = body.payload.to_domain()
    except ValueError as exc:
        raise RequestDomainValidationError("invalid ProposalRevision request") from exc
    return ProposalRevisionResponse.from_details(
        await service.create_manual_revision(
            context=context,
            experiment_id=experiment_id,
            payload=payload,
        )
    )


@router.post(
    "/proposal-revisions/{proposal_revision_id}/verify",
    response_model=ProposalVerificationResponse,
)
async def verify_proposal_revision(
    proposal_revision_id: UUID,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> ProposalVerificationResponse:
    return ProposalVerificationResponse.from_details(
        await service.verify_revision(
            context=context,
            proposal_revision_id=proposal_revision_id,
        )
    )


@router.get(
    "/proposal-verifications/{proposal_verification_id}",
    response_model=ProposalVerificationResponse,
)
async def get_proposal_verification(
    proposal_verification_id: UUID,
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> ProposalVerificationResponse:
    details = await service.get_verification(proposal_verification_id)
    return ProposalVerificationResponse.from_details(details)


@router.post(
    "/proposal-revisions/{proposal_revision_id}/approve",
    response_model=ExperimentVersionResponse,
)
async def approve_proposal_revision(
    proposal_revision_id: UUID,
    body: ApproveProposalRevisionRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> ExperimentVersionResponse:
    return ExperimentVersionResponse.from_details(
        await service.approve_revision(
            context=context,
            proposal_revision_id=proposal_revision_id,
            verification_id=body.proposal_verification_id,
        )
    )


@router.post(
    "/proposal-revisions/{proposal_revision_id}/reject",
    response_model=DesignDecisionResponse,
)
async def reject_proposal_revision(
    proposal_revision_id: UUID,
    body: RejectProposalRevisionRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> DesignDecisionResponse:
    try:
        details = await service.reject_revision(
            context=context,
            proposal_revision_id=proposal_revision_id,
            verification_id=body.proposal_verification_id,
            reason=body.reason,
        )
    except ValueError as exc:
        raise RequestDomainValidationError("invalid ProposalRevision request") from exc
    return DesignDecisionResponse.from_details(details)


@router.get(
    "/experiment-versions/{experiment_version_id}",
    response_model=ExperimentVersionResponse,
)
async def get_experiment_version(
    experiment_version_id: UUID,
    service: Annotated[ProposalApplicationService, Depends(get_proposal_service)],
) -> ExperimentVersionResponse:
    return ExperimentVersionResponse.from_details(
        await service.get_version(experiment_version_id)
    )
