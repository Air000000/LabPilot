from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from labpilot.agent_runtime.service import AgentApplicationService
from labpilot.api.actor import ActorProvider
from labpilot.api.errors import ServiceUnavailableError
from labpilot.common.commands import CommandContext
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.execution.batch_service import BatchApplicationService
from labpilot.execution.query_service import ExecutionQueryService
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.proposal_service import ProposalApplicationService
from labpilot.results_validation.query_service import ResultQueryService


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    projects: ProjectApplicationService
    repository_source: RepositorySnapshotSourceService | None = None
    proposals: ProposalApplicationService | None = None
    agent_runs: AgentApplicationService | None = None
    batches: BatchApplicationService | None = None
    execution_queries: ExecutionQueryService | None = None
    result_queries: ResultQueryService | None = None


def get_application_services(request: Request) -> ApplicationServices:
    services = getattr(request.app.state, "application_services", None)
    if not isinstance(services, ApplicationServices):
        raise ServiceUnavailableError("application services are not configured")
    return services


def get_project_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> ProjectApplicationService:
    return services.projects


def get_proposal_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> ProposalApplicationService:
    if services.proposals is None:
        raise ServiceUnavailableError("proposal service is not configured")
    return services.proposals


def get_agent_run_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> AgentApplicationService:
    if services.agent_runs is None:
        raise ServiceUnavailableError("agent run service is not configured")
    return services.agent_runs


def get_batch_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> BatchApplicationService:
    if services.batches is None:
        raise ServiceUnavailableError("batch service is not configured")
    return services.batches


def get_execution_query_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> ExecutionQueryService:
    if services.execution_queries is None:
        raise ServiceUnavailableError("execution query service is not configured")
    return services.execution_queries


def get_result_query_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> ResultQueryService:
    if services.result_queries is None:
        raise ServiceUnavailableError("result query service is not configured")
    return services.result_queries


def get_actor_provider(request: Request) -> ActorProvider:
    provider = getattr(request.app.state, "actor_provider", None)
    if provider is None or not hasattr(provider, "get_command_context"):
        raise ServiceUnavailableError("actor provider is not configured")
    return cast(ActorProvider, provider)


async def get_command_context(
    request: Request,
    actor_provider: Annotated[ActorProvider, Depends(get_actor_provider)],
) -> CommandContext:
    return await actor_provider.get_command_context(request)
