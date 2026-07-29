from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from labpilot.api.dependencies import (
    get_command_context,
    get_project_service,
)
from labpilot.api.errors import (
    RequestDomainValidationError,
    UnexpectedApplicationInvariant,
)
from labpilot.api.schemas.projects import (
    ArchiveProjectRequest,
    CreateProjectRequest,
    ProjectCommandResponse,
    ProjectResponse,
)
from labpilot.common.commands import CommandContext
from labpilot.db.idempotency import IdempotentCommandOutcome
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.project_types import ArchiveProject, CreateProject

router = APIRouter(prefix="/projects", tags=["projects"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
    ),
]


def _command_response(
    outcome: IdempotentCommandOutcome,
    *,
    committed_state: str,
) -> ProjectCommandResponse:
    envelope = outcome.envelope
    if (
        envelope.primary_result_type != "project"
        or not envelope.primary_result_identity
        or envelope.committed_state != committed_state
    ):
        raise UnexpectedApplicationInvariant("invalid Project result envelope")
    try:
        project_id = UUID(envelope.primary_result_id)
    except ValueError as exc:
        raise UnexpectedApplicationInvariant(
            "invalid Project result identifier"
        ) from exc
    return ProjectCommandResponse(
        project_id=project_id,
        project_identity=envelope.primary_result_identity,
        committed_state=envelope.committed_state,
        replayed=outcome.replayed,
    )


@router.post(
    "",
    response_model=ProjectCommandResponse,
    status_code=201,
)
async def create_project(
    body: CreateProjectRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[
        ProjectApplicationService,
        Depends(get_project_service),
    ],
) -> ProjectCommandResponse:
    try:
        outcome = await service.create_project(
            context=context,
            idempotency_key=idempotency_key,
            command=CreateProject(
                name=body.name,
                description=body.description,
                artifact_root_ref=body.artifact_root_ref,
            ),
        )
    except ValueError as exc:
        raise RequestDomainValidationError("invalid Project request") from exc
    response.status_code = 200 if outcome.replayed else 201
    return _command_response(outcome, committed_state="active")


@router.post(
    "/{project_id}/archive",
    response_model=ProjectCommandResponse,
)
async def archive_project(
    project_id: UUID,
    body: ArchiveProjectRequest,
    idempotency_key: IdempotencyKey,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[
        ProjectApplicationService,
        Depends(get_project_service),
    ],
) -> ProjectCommandResponse:
    try:
        outcome = await service.archive_project(
            context=context,
            idempotency_key=idempotency_key,
            command=ArchiveProject(
                project_id=project_id,
                expected_row_version=body.expected_row_version,
            ),
        )
    except ValueError as exc:
        raise RequestDomainValidationError("invalid Project request") from exc
    return _command_response(outcome, committed_state="archived")


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
)
async def get_project(
    project_id: UUID,
    service: Annotated[
        ProjectApplicationService,
        Depends(get_project_service),
    ],
) -> ProjectResponse:
    details = await service.get_project(project_id=project_id)
    return ProjectResponse.model_validate(details)
