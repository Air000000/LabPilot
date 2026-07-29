from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from labpilot.api.actor import ActorProvider
from labpilot.api.errors import ServiceUnavailableError
from labpilot.common.commands import CommandContext
from labpilot.experiments.project_service import ProjectApplicationService


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    projects: ProjectApplicationService


def get_application_services(request: Request) -> ApplicationServices:
    services = getattr(request.app.state, "application_services", None)
    if not isinstance(services, ApplicationServices):
        raise ServiceUnavailableError("application services are not configured")
    return services


def get_project_service(
    services: Annotated[ApplicationServices, Depends(get_application_services)],
) -> ProjectApplicationService:
    return services.projects


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
