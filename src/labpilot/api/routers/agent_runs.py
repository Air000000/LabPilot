from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from labpilot.agent_runtime.service import AgentApplicationService
from labpilot.api.dependencies import get_agent_run_service, get_command_context
from labpilot.api.errors import RequestDomainValidationError
from labpilot.api.schemas.agent_runs import (
    AdvanceAgentRunRequest,
    AgentRunResponse,
    AgentRunTraceResponse,
    CreateAgentRunRequest,
    CreateFollowUpAgentRunRequest,
    HumanInputRequest,
)
from labpilot.common.commands import CommandContext

router = APIRouter(tags=["agent-runs"])


@router.post(
    "/projects/{project_id}/agent-runs",
    response_model=AgentRunResponse,
    status_code=201,
)
async def create_agent_run(
    project_id: UUID,
    body: CreateAgentRunRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[AgentApplicationService, Depends(get_agent_run_service)],
) -> AgentRunResponse:
    try:
        budget = body.budget.to_domain()
    except ValueError as exc:
        raise RequestDomainValidationError("invalid AgentRun request") from exc
    details = await service.create_run(
        context=context,
        project_id=project_id,
        repository_snapshot_id=body.repository_snapshot_id,
        goal=body.goal,
        budget=budget,
    )
    return AgentRunResponse.from_details(details)


@router.post(
    "/projects/{project_id}/agent-runs/follow-up",
    response_model=AgentRunResponse,
    status_code=201,
)
async def create_follow_up_agent_run(
    project_id: UUID,
    body: CreateFollowUpAgentRunRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[AgentApplicationService, Depends(get_agent_run_service)],
) -> AgentRunResponse:
    try:
        budget = body.budget.to_domain()
    except ValueError as exc:
        raise RequestDomainValidationError("invalid AgentRun request") from exc
    details = await service.create_follow_up_run(
        context=context,
        project_id=project_id,
        source_batch_summary_revision_id=body.source_batch_summary_revision_id,
        repository_snapshot_id=body.repository_snapshot_id,
        goal=body.goal,
        budget=budget,
    )
    return AgentRunResponse.from_details(details)


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    agent_run_id: UUID,
    service: Annotated[AgentApplicationService, Depends(get_agent_run_service)],
) -> AgentRunResponse:
    return AgentRunResponse.from_details(await service.get_run(agent_run_id))


@router.get(
    "/agent-runs/{agent_run_id}/trace",
    response_model=AgentRunTraceResponse,
)
async def get_agent_trace(
    agent_run_id: UUID,
    service: Annotated[AgentApplicationService, Depends(get_agent_run_service)],
) -> AgentRunTraceResponse:
    details = await service.get_trace(agent_run_id)
    return AgentRunTraceResponse.from_details(details)


@router.post("/agent-runs/{agent_run_id}/advance", response_model=AgentRunResponse)
async def advance_agent_run(
    agent_run_id: UUID,
    body: AdvanceAgentRunRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[AgentApplicationService, Depends(get_agent_run_service)],
) -> AgentRunResponse:
    return AgentRunResponse.from_details(
        await service.advance_run(
            context=context,
            agent_run_id=agent_run_id,
            expected_row_version=body.expected_row_version,
        )
    )


@router.post("/agent-runs/{agent_run_id}/human-input", response_model=AgentRunResponse)
async def submit_human_input(
    agent_run_id: UUID,
    body: HumanInputRequest,
    context: Annotated[CommandContext, Depends(get_command_context)],
    service: Annotated[AgentApplicationService, Depends(get_agent_run_service)],
) -> AgentRunResponse:
    return AgentRunResponse.from_details(
        await service.submit_human_input(
            context=context,
            agent_run_id=agent_run_id,
            expected_row_version=body.expected_row_version,
            text=body.text,
        )
    )
