from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from labpilot.api.actor import ActorProvider
from labpilot.api.dependencies import ApplicationServices
from labpilot.api.errors import register_error_handlers
from labpilot.api.routers.health import router as health_router
from labpilot.api.routers.projects import router as projects_router
from labpilot.common.readiness import ReadinessProbe, UnconfiguredReadinessProbe

ShutdownCallback = Callable[[], Awaitable[None]]


def create_app(
    *,
    readiness_probe: ReadinessProbe | None = None,
    shutdown_callbacks: tuple[ShutdownCallback, ...] = (),
    services: ApplicationServices,
    actor_provider: ActorProvider,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        body_error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            body_error = exc

        cleanup_errors: list[BaseException] = []
        for callback in reversed(shutdown_callbacks):
            try:
                await callback()
            except BaseException as exc:
                cleanup_errors.append(exc)

        if body_error is not None and cleanup_errors:
            raise BaseExceptionGroup(
                "application lifespan and cleanup failed",
                [body_error, *cleanup_errors],
            )
        if body_error is not None:
            raise body_error
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise BaseExceptionGroup("application cleanup failed", cleanup_errors)

    app = FastAPI(title="LabPilot", version="0.1.0", lifespan=lifespan)
    app.state.readiness_probe = readiness_probe or UnconfiguredReadinessProbe()
    app.state.application_services = services
    app.state.actor_provider = actor_provider
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(projects_router)
    return app
