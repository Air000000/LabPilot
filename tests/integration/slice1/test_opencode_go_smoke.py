from __future__ import annotations

import json
import logging
import os

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from tests.integration.slice1.test_proposal_loop import (
    _assert_secret_absent,
    _context,
    persisted_secret_surfaces,
    proposal_payload,
    slice_system,
    tool_service,
)

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.opencode_go import OpenCodeGoChatProvider
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import AgentApplicationService
from labpilot.agent_runtime.types import AgentBudget
from labpilot.experiments.proposal_models import DesignDecisionRow, ExperimentVersionRow
from labpilot.tools_policy.models import ToolCallRow

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return (
        os.getenv("LABPILOT_RUN_EXTERNAL_PROVIDER_SMOKE") == "1"
        and bool(os.getenv("LABPILOT_AGENT_PROVIDER_MODEL"))
        and bool(os.getenv("LABPILOT_AGENT_PROVIDER_API_KEY"))
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _enabled(), reason="external OpenCode Go smoke is opt-in")
async def test_real_opencode_go_proposal_loop(
    isolated_database_url: str,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
    request: pytest.FixtureRequest,
) -> None:
    model = os.environ["LABPILOT_AGENT_PROVIDER_MODEL"]
    key = os.environ["LABPILOT_AGENT_PROVIDER_API_KEY"]
    async with slice_system(isolated_database_url, tmp_path, name="opencode-go") as system:
        for logger_name in (
            "labpilot.agent_runtime.service",
            "labpilot.agent_runtime.opencode_go",
            "labpilot.tools_policy.service",
        ):
            logger = logging.getLogger(logger_name)
            logger.disabled = False
            logger.addHandler(caplog.handler)
            request.addfinalizer(lambda logger=logger: logger.removeHandler(caplog.handler))
        caplog.set_level(logging.INFO)

        payload = proposal_payload(system)
        proposal_json = {
            "goal": payload.goal,
            "experiment_type": payload.experiment_type,
            "repository_snapshot_id": str(payload.repository_snapshot_id),
            "repository_snapshot_identity": payload.repository_snapshot_identity,
            "train_entrypoint": payload.train_entrypoint,
            "eval_entrypoint": payload.eval_entrypoint,
            "workdir": payload.workdir,
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
            "metrics": [
                {"name": "accuracy", "source_artifact": "model", "split": "test"}
            ],
            "artifacts": [
                {"name": "model", "relative_path": "out/model.pt", "required": True}
            ],
            "validation_rules": [
                {"kind": "artifact_exists", "subject": "model"},
                {"kind": "metric_finite", "subject": "accuracy"},
            ],
            "timeout_seconds": 3600,
            "max_retries": 1,
        }
        provider = OpenCodeGoChatProvider(model=model, api_key=SecretStr(key))
        service = AgentApplicationService(
            repository=SqlAgentRepository(system.database.session),
            repository_source=system.source,
            provider=provider,
            model_name=model,
            context_compiler=ContextCompiler(),
            tool_service_factory=lambda snapshot_id: tool_service(system, snapshot_id),
            proposal_service=system.proposals,
            data_egress_policy="remote_provider_allowed",
        )
        run = await service.create_run(
            context=_context("opencode-go-smoke"),
            project_id=system.project_id,
            repository_snapshot_id=system.snapshot_id,
            goal=(
                "First return a repository.list_tree tool_call with empty arguments. "
                "After its observation, return a propose decision using exactly this proposal: "
                + json.dumps(proposal_json, separators=(",", ":"), sort_keys=True)
            ),
            budget=AgentBudget(
                max_steps=4,
                max_model_calls=4,
                max_wall_clock_seconds=180,
                max_tokens=20_000,
            ),
        )
        completed = await service.advance_run(
            context=_context("opencode-go-smoke"),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
        assert completed.status == "completed"
        assert completed.produced_proposal_revision_id is not None
        steps = await SqlAgentRepository(system.database.session).list_steps(run.agent_run_id)
        assert all(step.outcome == "succeeded" for step in steps)
        async with system.database.session() as session:
            calls = (await session.execute(select(ToolCallRow))).scalars().all()
            assert await session.scalar(
                select(func.count()).select_from(DesignDecisionRow)
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(ExperimentVersionRow)
            ) == 0
        assert any(
            call.tool_name == "repository.list_tree"
            and call.status == "executed"
            and call.result_json is not None
            for call in calls
        )

        revision = await system.proposals.get_revision(
            completed.produced_proposal_revision_id
        )
        verification = await system.proposals.verify_revision(
            context=_context("opencode-go-smoke"),
            proposal_revision_id=revision.proposal_revision_id,
        )
        assert verification.result.passed is True
        version = await system.proposals.approve_revision(
            context=_context("opencode-go-smoke"),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        assert version.proposal_revision_id == revision.proposal_revision_id
        assert await system.proposals.get_version(version.experiment_version_id) == version

        _assert_secret_absent(
            key,
            await persisted_secret_surfaces(system.database)
            + [
                json.dumps(record.__dict__, default=str, sort_keys=True)
                for record in caplog.records
            ],
        )
