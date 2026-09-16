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


def test_competition_shell_is_html_and_hidden_from_openapi() -> None:
    app = create_app(
        services=_SERVICES,
        actor_provider=_ACTOR_PROVIDER,
    )
    client = TestClient(app)

    response = client.get("/competition")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "LabPilot / Experiment Console" in response.text
    assert "Unable to load persisted Golden Path evidence" in response.text
    for marker in (
        "Bounded agent planning. Verified experiment execution.",
        "Run status",
        "Scientific outcome",
        "Validated &amp; Included",
        "AI Agent",
        "Controlled handoff",
        "LabPilot runtime",
        "Agent Decision Trace",
        "Validation &amp; Summary",
    ):
        assert marker in response.text
    for endpoint in (
        "/agent-runs/9702da20-968a-469f-8fd4-e33b0f2dbfa4/trace",
        "/proposal-verifications/64daf1fc-a1cf-4736-8009-b932ee07da57",
        "/experiment-versions/63755870-9582-46dd-bf3f-3d7054643ad2",
    ):
        assert endpoint in response.text
    for stale_navigation_id in (
        "c6d80472-31f7-4c6c-9e56-6f00d3cde1ed",
        "b4871e14-fd3e-4a94-aa9d-3d81aecece31",
        "6e19ac98-8f6c-4c8b-b698-8c35b7a9cd2d",
        "42c60d3c-ca98-47ac-8a9f-273fad87f44f",
    ):
        assert stale_navigation_id not in response.text
    for forbidden in ("0.941", "0.650", "0.895", "0.92"):
        assert forbidden not in response.text
    assert "4fef6810-a1b9-4463-9f00-edac8e22f40a" in response.text
    for endpoint in ("/batches/", "/trials/", "/attempts/", "/results", "/result", "/summary"):
        assert endpoint in response.text
    for forbidden in (
        "0.941",
        "0.650",
        "0.895",
        "bad7c1c7",
        "a7372865",
        "f0d502f2",
        "cbd5b16a",
        "0f9c3af2",
        "93e94e28",
        "349bd8a8",
        "386fbada",
        "duplicate launches",
        "exactly once",
        "Best result evidence",
        "best",
        "winner",
        "autonomous scientist",
        "autonomous research loop",
        "self-improving",
        "innerHTML",
        "insertAdjacentHTML",
        "document.write",
    ):
        assert forbidden not in response.text
    assert "POST" not in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text
    assert "/competition" not in app.openapi()["paths"]


def test_operator_shell_is_html_and_uses_safe_local_browser_code() -> None:
    app = create_app(services=_SERVICES, actor_provider=_ACTOR_PROVIDER)
    response = TestClient(app).get("/operator")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for marker in (
        "LabPilot / AI 实验操作台",
        "研究目标",
        "启动 Agent",
        "验证实验方案",
        "批准实验",
        "实验已准备执行",
        "正在启动 Batch…",
        "/experiment-versions/",
        "/batches/",
        "Idempotency-Key",
        "EventSource",
        "/events",
        "attempt.terminal_observed",
        "result.validation_completed",
        "result.summary_created",
        "/trials/",
        "/attempts/",
        "/results",
        "/result",
        "/summary",
        "实时连接\uff1a已连接",
        "实时连接\uff1a正在重新连接…",
        "重新读取状态",
        "正在生成实验汇总。",
        "Summary · 持久化验证结果",
        "实验已完成",
        "等待执行",
        "已验证并纳入 Summary",
        "聚合指标",
        "均值",
        "样本数",
        "parameter-row",
        "executionStatusText",
        "达到目标",
        "无可纳入结果",
        "正在查看已完成的 Agent 运行记录。",
        'projectId: "22209cf9-1b5d-456a-a227-d6584f733d9d"',
        "/agent-runs",
        "/advance",
        "/trace",
        "/verify",
        "/approve",
        "expected_row_version",
    ):
        assert marker in response.text
    for stale_product_copy in (
        "Research goal",
        "Run progress",
        "Start Agent",
        "Verify Proposal",
        "Approve Experiment",
        "Experiment ready to execute",
        "Waiting",
        "Current",
        "Complete",
    ):
        assert stale_product_copy not in response.text
    for forbidden in (
        "innerhtml",
        "insertadjacenthtml",
        "document.write",
        "http://",
        "https://",
        "cdn",
    ):
        assert forbidden not in response.text.lower()
    assert "/operator" not in app.openapi()["paths"]
