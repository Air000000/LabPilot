from typing import Protocol
from uuid import uuid4

from starlette.requests import Request

from labpilot.common.commands import CommandActor, CommandContext


class ActorProvider(Protocol):
    async def get_command_context(
        self,
        request: Request,
    ) -> CommandContext: ...


class ServerConfiguredActorProvider:
    def __init__(
        self,
        installation_identity: str,
        actor_id: str,
    ) -> None:
        self._installation_identity = installation_identity
        self._actor_id = actor_id

    async def get_command_context(
        self,
        request: Request,
    ) -> CommandContext:
        del request
        return CommandContext(
            installation_identity=self._installation_identity,
            actor=CommandActor(
                actor_type="local_user",
                actor_id=self._actor_id,
            ),
            interaction_correlation_id=str(uuid4()),
        )
