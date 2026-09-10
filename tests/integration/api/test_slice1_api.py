from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.integration.agent_runtime.test_agent_service import ScriptedProvider
from tests.integration.experiments.test_proposal_service import (
    PROJECT_ID,
    _proposal_system,
)

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import AgentApplicationService
from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.proposal_models import ExperimentVersionRow, ProposalRevisionRow
from labpilot.tools_policy.repository import SqlToolRepository
from labpilot.tools_policy.repository_tools import repository_tool_definitions
from labpilot.tools_policy.service import ToolApplicationService

pytestmark = pytest.mark.integration
BUDGET = {
    "max_steps": 4,
    "max_model_calls": 4,
    "max_wall_clock_seconds": 60,
    "max_tokens": 100,
}


class BlockingProvider(ScriptedProvider):
    def __init__(self, script: list[object]) -> None:
        super().__init__(script)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request: object):
        self.started.set()
        await self.release.wait()
        return await super().complete(request)  # type: ignore[arg-type]


def _payload(snapshot_id: UUID, identity: str, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "goal": "Reproduce baseline",
        "experiment_type": "reproduction",
        "repository_snapshot_id": str(snapshot_id),
        "repository_snapshot_identity": identity,
        "train_entrypoint": "train.py",
        "eval_entrypoint": "eval.py",
        "workdir": ".",
        "parameter_matrix": {"learning_rate": [0.1]},
        "seeds": [7],
        "dataset_reference": {
            "reference_type": "uri",
            "path_or_uri": "s3://datasets/example",
            "stable_identity": "dataset:v1",
            "reproducibility_level": "strict",
        },
        "environment_expectation": {
            "python_profile": "python-3.12",
            "framework_requirements": ["torch==2.7.0"],
            "device_requirement": "cuda",
            "cuda_requirement": "12.8",
        },
        "metrics": [{"name": "accuracy", "source_artifact": "model", "split": "test"}],
        "artifacts": [{"name": "model", "relative_path": "out/model.pt", "required": True}],
        "validation_rules": [
            {"kind": "artifact_exists", "subject": "model"},
            {"kind": "metric_finite", "subject": "accuracy"},
        ],
        "timeout_seconds": 3600,
        "max_retries": 1,
        "train_argv_template": [
            "--learning-rate",
            "{parameter:learning_rate}",
            "--seed",
            "{seed}",
            "--dataset",
            "{dataset}",
            "--output-dir",
            "{output_dir}",
        ],
    }
    value.update(changes)
    return value


def _app(system: object, source: RepositorySnapshotSourceService, provider: ScriptedProvider):
    database = system.database  # type: ignore[attr-defined]

    def tool_service_factory(repository_snapshot_id: UUID) -> ToolApplicationService:
        return ToolApplicationService(
            repository_tool_definitions(source, repository_snapshot_id),
            SqlToolRepository(database.session),
        )

    agent_runs = AgentApplicationService(
        repository=SqlAgentRepository(database.session),
        repository_source=source,
        provider=provider,
        model_name="scripted-model",
        context_compiler=ContextCompiler(),
        tool_service_factory=tool_service_factory,
        proposal_service=system.service,  # type: ignore[attr-defined]
        data_egress_policy="local_only",
    )
    return create_app(
        services=ApplicationServices(
            projects=ProjectApplicationService(database.session),
            repository_source=source,
            proposals=system.service,  # type: ignore[attr-defined]
            agent_runs=agent_runs,
        ),
        actor_provider=ServerConfiguredActorProvider(
            installation_identity="api-integration", actor_id="operator"
        ),
    )


@asynccontextmanager
async def _client(system: object, provider: ScriptedProvider) -> AsyncIterator[AsyncClient]:
    source = RepositorySnapshotSourceService(
        system.database.session,  # type: ignore[attr-defined]
        git_adapter=GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2),
        content_store=ContentAddressedStore(system.repository.parent / "content"),  # type: ignore[attr-defined]
    )
    async with AsyncClient(
        transport=ASGITransport(app=_app(system, source, provider)), base_url="http://test"
    ) as client:
        yield client


async def _create(client: AsyncClient, snapshot_id: UUID) -> dict[str, object]:
    response = await client.post(
        f"/projects/{PROJECT_ID}/agent-runs",
        json={"repository_snapshot_id": str(snapshot_id), "goal": "Draft", "budget": BUDGET},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_slice1_api_proposal_loop(isolated_database_url: str, tmp_path: Path) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        invalid = _payload(
            system.snapshot_id, system.snapshot_identity, train_entrypoint="missing.py"
        )
        provider = ScriptedProvider(
            [
                {"kind": "request_human_input", "question": "Seed?"},
                {"kind": "propose", "proposal": invalid},
            ]
        )
        async with _client(system, provider) as client:
            run = await _create(client, system.snapshot_id)
            assert provider.calls == []
            assert (
                await client.get("/agent-runs/cccccccc-cccc-4ccc-8ccc-cccccccccccc")
            ).status_code == 404
            assert (
                await client.post(f"/projects/{PROJECT_ID}/agent-runs", json={"unexpected": True})
            ).status_code == 422
            unknown_snapshot = await client.post(
                f"/projects/{PROJECT_ID}/agent-runs",
                json={
                    "repository_snapshot_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "goal": "Draft",
                    "budget": BUDGET,
                },
            )
            assert unknown_snapshot.status_code == 404
            assert unknown_snapshot.json() == {
                "error_code": "resource_not_found",
                "human_message": "requested resource was not found",
                "aggregate_type": None,
                "aggregate_id": None,
                "current_state": None,
                "expected_state": None,
                "retryable": False,
                "details": {},
            }
            awaiting = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            assert awaiting.json()["status"] == "awaiting_human"
            assert (
                await client.post(
                    f"/agent-runs/{run['agent_run_id']}/human-input",
                    json={"expected_row_version": run["row_version"], "text": "7"},
                )
            ).status_code == 409
            resumed = await client.post(
                f"/agent-runs/{run['agent_run_id']}/human-input",
                json={"expected_row_version": awaiting.json()["row_version"], "text": "Use 7"},
            )
            proposed = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": resumed.json()["row_version"]},
            )
            proposal_id = proposed.json()["produced_proposal_revision_id"]
            fetched = await client.get(f"/proposal-revisions/{proposal_id}")
            invalid_verification = await client.post(f"/proposal-revisions/{proposal_id}/verify")
            assert invalid_verification.json()["result"]["passed"] is False
            corrected = await client.post(
                f"/experiments/{fetched.json()['experiment_id']}/proposal-revisions",
                json={"payload": _payload(system.snapshot_id, system.snapshot_identity)},
            )
            assert corrected.status_code == 201
            assert corrected.json()["revision_number"] == 2
            verification = await client.post(
                f"/proposal-revisions/{corrected.json()['proposal_revision_id']}/verify"
            )
            assert verification.json()["result"]["passed"] is True
            body = {"proposal_verification_id": verification.json()["proposal_verification_id"]}
            approve = f"/proposal-revisions/{corrected.json()['proposal_revision_id']}/approve"
            approved, repeated = (
                await client.post(approve, json=body),
                await client.post(approve, json=body),
            )
            assert (
                approved.json()["experiment_version_id"] == repeated.json()["experiment_version_id"]
            )
            version = await client.get(
                f"/experiment-versions/{approved.json()['experiment_version_id']}"
            )
            assert version.status_code == 200
            assert version.json() == approved.json()
            assert (
                await client.get("/experiment-versions/cccccccc-cccc-4ccc-8ccc-cccccccccccc")
            ).status_code == 404
            changed = await client.post(
                f"/experiments/{fetched.json()['experiment_id']}/proposal-revisions",
                json={
                    "payload": _payload(
                        system.snapshot_id, system.snapshot_identity, goal="Changed"
                    )
                },
            )
            changed_verification = await client.post(
                f"/proposal-revisions/{changed.json()['proposal_revision_id']}/verify"
            )
            (system.repository / "README.md").write_text("changed\n", encoding="utf-8")
            assert (
                await client.post(
                    f"/proposal-revisions/{changed.json()['proposal_revision_id']}/approve",
                    json={
                        "proposal_verification_id": changed_verification.json()[
                            "proposal_verification_id"
                        ]
                    },
                )
            ).status_code == 409
            assert (
                await client.post(
                    f"/proposal-revisions/{changed.json()['proposal_revision_id']}/reject",
                    json={
                        "reason": "Changed source",
                        "proposal_verification_id": changed_verification.json()[
                            "proposal_verification_id"
                        ],
                    },
                )
            ).status_code == 200
            async with system.database.session() as session:
                assert (
                    await session.scalar(select(func.count()).select_from(ExperimentVersionRow))
                    == 1
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_slice1_api_rejects_non_finite_proposal_values_without_side_effects(
    isolated_database_url: str, tmp_path: Path, value: float
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        provider = ScriptedProvider(
            [
                {
                    "kind": "propose",
                    "proposal": _payload(system.snapshot_id, system.snapshot_identity),
                }
            ]
        )
        async with _client(system, provider) as client:
            run = await _create(client, system.snapshot_id)
            proposed = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            proposal = await client.get(
                f"/proposal-revisions/{proposed.json()['produced_proposal_revision_id']}"
            )
            experiment_id = proposal.json()["experiment_id"]
            async with system.database.session() as session:
                before = await session.scalar(select(func.count()).select_from(ProposalRevisionRow))
            payload = _payload(system.snapshot_id, system.snapshot_identity)
            payload["parameter_matrix"] = {"learning_rate": [value]}
            response = await client.post(
                f"/experiments/{experiment_id}/proposal-revisions",
                content=json.dumps({"payload": payload}, allow_nan=True),
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 422
            async with system.database.session() as session:
                after = await session.scalar(select(func.count()).select_from(ProposalRevisionRow))
            assert after == before
            payload["parameter_matrix"] = {"learning_rate": [0.1]}
            finite = await client.post(
                f"/experiments/{experiment_id}/proposal-revisions",
                content=json.dumps({"payload": payload}, allow_nan=True),
                headers={"content-type": "application/json"},
            )
            assert finite.status_code == 201


@pytest.mark.asyncio
async def test_slice1_api_concurrent_advance_and_remote_policy(
    isolated_database_url: str, tmp_path: Path
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        provider = BlockingProvider([{"kind": "stop", "reason_code": "done"}])
        async with _client(system, provider) as client:
            run = await _create(client, system.snapshot_id)
            winner = asyncio.create_task(
                client.post(
                    f"/agent-runs/{run['agent_run_id']}/advance",
                    json={"expected_row_version": run["row_version"]},
                )
            )
            await provider.started.wait()
            stale = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            provider.release.set()
            assert (await winner).status_code == 200
            assert stale.status_code == 409
            assert len(provider.calls) == 1
        remote = ScriptedProvider([{"kind": "stop", "reason_code": "done"}])
        remote.egress = "remote"
        async with _client(system, remote) as client:
            run = await _create(client, system.snapshot_id)
            blocked = await client.post(
                f"/agent-runs/{run['agent_run_id']}/advance",
                json={"expected_row_version": run["row_version"]},
            )
            assert blocked.status_code == 403
            assert remote.calls == []



def test_threshold_rule_request_reaches_domain() -> None:
    from labpilot.api.schemas.experiments import ProposalPayloadRequest

    payload = _payload(UUID("10000000-0000-4000-8000-000000000001"), "a" * 64)
    payload["validation_rules"] = [
        {"kind": "artifact_exists", "subject": "model"},
        {"kind": "metric_finite", "subject": "accuracy"},
        {"kind": "metric_gte", "subject": "accuracy", "threshold": "0.80"},
        {"kind": "metric_lte", "subject": "accuracy", "threshold": "1.0"},
    ]

    domain = ProposalPayloadRequest.model_validate(payload).to_domain()

    assert domain.validation_rules[-2].threshold == Decimal("0.80")
    assert domain.validation_rules[-1].threshold == Decimal("1.0")
