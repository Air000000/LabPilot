from dataclasses import dataclass
from typing import cast

import pytest
from fastapi.testclient import TestClient

from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.common.readiness import ReadinessResult
from labpilot.experiments.project_service import ProjectApplicationService

_SERVICES = ApplicationServices(
    projects=cast(ProjectApplicationService, object()),
)
_ACTOR_PROVIDER = ServerConfiguredActorProvider(
    installation_identity="test-installation",
    actor_id="test-actor",
)


@dataclass
class FakeProbe:
    result: ReadinessResult

    async def check(self) -> ReadinessResult:
        return self.result


def test_liveness_does_not_depend_on_database_readiness() -> None:
    app = create_app(
        readiness_probe=FakeProbe(
            ReadinessResult(
                ready=False,
                database="unavailable",
                migration="unknown",
                detail="database is down",
            )
        ),
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_returns_200_when_dependencies_are_ready() -> None:
    app = create_app(
        readiness_probe=FakeProbe(
            ReadinessResult(
                ready=True,
                database="ok",
                migration="head",
                detail=None,
            )
        ),
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "migration": "head",
        "detail": None,
    }


def test_readiness_returns_503_when_dependencies_are_not_ready() -> None:
    app = create_app(
        readiness_probe=FakeProbe(
            ReadinessResult(
                ready=False,
                database="unavailable",
                migration="unknown",
                detail="database is down",
            )
        ),
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_app_lifespan_runs_shutdown_callbacks_once() -> None:
    calls = 0

    async def shutdown() -> None:
        nonlocal calls
        calls += 1

    app = create_app(
        shutdown_callbacks=(shutdown,),
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_lifespan_body_failure_still_runs_shutdown() -> None:
    calls = 0

    async def shutdown() -> None:
        nonlocal calls
        calls += 1

    app = create_app(
        shutdown_callbacks=(shutdown,),
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    with pytest.raises(ValueError, match="body failed"):
        async with app.router.lifespan_context(app):
            raise ValueError("body failed")
    assert calls == 1


@pytest.mark.asyncio
async def test_lifespan_preserves_body_and_cleanup_failures() -> None:
    async def shutdown() -> None:
        raise RuntimeError("cleanup failed")

    app = create_app(
        shutdown_callbacks=(shutdown,),
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    with pytest.raises(BaseExceptionGroup) as captured:
        async with app.router.lifespan_context(app):
            raise ValueError("body failed")
    messages = {str(error) for error in captured.value.exceptions}
    assert messages == {"body failed", "cleanup failed"}
