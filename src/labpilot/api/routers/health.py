from fastapi import APIRouter, Request, Response, status

from labpilot.common.readiness import ReadinessProbe

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, str | None]:
    probe: ReadinessProbe = request.app.state.readiness_probe
    result = await probe.check()
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if result.ready else "not_ready",
        "database": result.database,
        "migration": result.migration,
        "detail": result.detail,
    }
