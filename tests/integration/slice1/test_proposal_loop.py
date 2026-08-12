from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from tests.integration.agent_runtime.test_agent_service import (
    CrashAfterCreateProposalService,
    ScriptedProvider,
)
from tests.integration.db_helpers import upgrade_database_to_head
from tests.integration.experiments.test_proposal_service import _git

from labpilot.agent_runtime.context_compiler import (
    ContextCompiler,
    canonical_request_hash,
)
from labpilot.agent_runtime.models import AgentStepRow
from labpilot.agent_runtime.provider import ModelRequest, ModelResponse
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import AgentApplicationService
from labpilot.agent_runtime.types import AgentBudget, AgentRuntimeError
from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.common.commands import CommandActor, CommandContext
from labpilot.common.hashing import namespaced_sha256
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
from labpilot.context.repository_service import RepositoryApplicationService
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.repository_types import (
    CaptureRepositorySnapshot,
    RegisterLocalRepository,
)
from labpilot.db.session import Database
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.project_types import CreateProject
from labpilot.experiments.proposal_models import (
    DesignDecisionRow,
    ExperimentVersionRow,
    ProposalRevisionRow,
    ProposalVerificationRow,
)
from labpilot.experiments.proposal_service import (
    ProposalApplicationService,
    ProposalApprovalNotReady,
    ProposalInvariantViolation,
    ProposalVerificationConflict,
)
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
    dataset_reference_hash,
    environment_expectation_hash,
    experiment_version_identity,
    validation_protocol_hash,
)
from labpilot.experiments.proposal_verifier import ProposalVerifier
from labpilot.observability.models import RuntimeEventRow
from labpilot.tools_policy.models import ToolCallRow
from labpilot.tools_policy.repository import SqlToolRepository
from labpilot.tools_policy.repository_tools import repository_tool_definitions
from labpilot.tools_policy.service import ToolApplicationService
from labpilot.tools_policy.types import arguments_hash, bound_toolset_hash, tool_result_hash

pytestmark = pytest.mark.integration


def _context(request_id: str = "slice1-acceptance") -> CommandContext:
    return CommandContext(
        installation_identity="slice1-acceptance-installation",
        actor=CommandActor(actor_type="local_user", actor_id="operator"),
        interaction_correlation_id=request_id,
    )


def _budget() -> AgentBudget:
    return AgentBudget(
        max_steps=10,
        max_model_calls=10,
        max_wall_clock_seconds=120,
        max_tokens=1000,
    )


@dataclass(slots=True)
class SliceSystem:
    database: Database
    repository_app: RepositoryApplicationService
    source: RepositorySnapshotSourceService
    proposals: ProposalApplicationService
    project_id: UUID
    repository: Path
    registration_id: UUID
    snapshot_id: UUID
    snapshot_identity: str


@asynccontextmanager
async def slice_system(
    isolated_database_url: str,
    tmp_path: Path,
    *,
    name: str = "primary",
    dirty: bool = False,
) -> AsyncIterator[SliceSystem]:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    repository = tmp_path / name
    repository.mkdir(parents=True)
    await _git(repository, "init", "-q", "-b", "main")
    (repository / "train.py").write_text("print('train')\n", encoding="utf-8")
    (repository / "eval.py").write_text("print('eval')\n", encoding="utf-8")
    (repository / "config.json").write_text('{"seed":7}\n', encoding="utf-8")
    (repository / "README.md").write_text("LabPilot acceptance fixture\n", encoding="utf-8")
    await _git(repository, "add", "train.py", "eval.py", "config.json", "README.md")
    await _git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    git = GitAdapter(git_executable=Path("/usr/bin/git"), timeout_seconds=2)
    content_store = ContentAddressedStore(tmp_path / f"{name}-content")
    repository_app = RepositoryApplicationService(
        database.session,
        git_adapter=git,
        allowed_roots=(tmp_path.resolve(),),
        content_store=content_store,
    )
    try:
        project = await ProjectApplicationService(database.session).create_project(
            context=_context(),
            idempotency_key=f"create-{name}",
            command=CreateProject(
                name=f"Slice 1 {name}",
                description="Vertical slice acceptance",
                artifact_root_ref=f"artifact-root:{name}",
            ),
        )
        project_id = UUID(project.envelope.primary_result_id)
        registered = await repository_app.register_local_repository(
            context=_context(),
            idempotency_key=f"register-{name}",
            command=RegisterLocalRepository(project_id, repository.resolve(), 1),
        )
        registration_id = UUID(registered.envelope.primary_result_id)
        if dirty:
            (repository / "train.py").write_text("print('dirty')\n", encoding="utf-8")
        captured = await repository_app.capture_repository_snapshot(
            context=_context(),
            idempotency_key=f"capture-{name}",
            command=CaptureRepositorySnapshot(registration_id),
        )
        snapshot_id = UUID(captured.envelope.primary_result_id)
        source = RepositorySnapshotSourceService(
            database.session,
            git_adapter=git,
            content_store=content_store,
        )
        descriptor = await source.describe(snapshot_id)
        facts = ProposalRepositoryFactAdapter(source)
        yield SliceSystem(
            database=database,
            repository_app=repository_app,
            source=source,
            proposals=ProposalApplicationService(
                database.session,
                repository_facts=facts,
                verifier=ProposalVerifier(facts),
            ),
            project_id=project_id,
            repository=repository,
            registration_id=registration_id,
            snapshot_id=snapshot_id,
            snapshot_identity=descriptor.repository_snapshot_identity,
        )
    finally:
        await database.dispose()


def proposal_payload(system: SliceSystem, **changes: object) -> ProposalPayload:
    payload = ProposalPayload(
        goal="Reproduce the baseline",
        experiment_type="reproduction",
        repository_snapshot_id=system.snapshot_id,
        repository_snapshot_identity=system.snapshot_identity,
        train_entrypoint="train.py",
        eval_entrypoint="eval.py",
        workdir=".",
        parameter_matrix={"learning_rate": (0.1,)},
        seeds=(7,),
        dataset_reference=DatasetReference(
            reference_type="uri",
            path_or_uri="s3://datasets/example",
            stable_identity="dataset:v1",
            reproducibility_level="strict",
        ),
        environment_expectation=EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=("torch==2.7.0",),
            device_requirement="cuda",
            cuda_requirement="12.8",
        ),
        metrics=(MetricDeclaration("accuracy", "model", "test"),),
        artifacts=(ArtifactDeclaration("model", "out/model.pt", True),),
        validation_rules=(
            ValidationRule("artifact_exists", "model"),
            ValidationRule("metric_finite", "accuracy"),
        ),
        timeout_seconds=3600,
        max_retries=1,
    )
    return replace(payload, **changes)


def tool_service(system: SliceSystem, snapshot_id: UUID | None = None) -> ToolApplicationService:
    return ToolApplicationService(
        repository_tool_definitions(system.source, snapshot_id or system.snapshot_id),
        SqlToolRepository(system.database.session),
    )


def agent_service(
    system: SliceSystem,
    provider: object,
    *,
    policy: str = "local_only",
    proposals: object | None = None,
) -> AgentApplicationService:
    return AgentApplicationService(
        repository=SqlAgentRepository(system.database.session),
        repository_source=system.source,
        provider=provider,  # type: ignore[arg-type]
        model_name="acceptance-model",
        context_compiler=ContextCompiler(),
        tool_service_factory=lambda snapshot_id: tool_service(system, snapshot_id),
        proposal_service=proposals or system.proposals,  # type: ignore[arg-type]
        data_egress_policy=policy,  # type: ignore[arg-type]
    )


async def row_count(database: Database, row_type: type[object]) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(row_type)))


def _assert_secret_absent(secret: str, surfaces: list[str]) -> None:
    if any(secret in surface for surface in surfaces):
        raise AssertionError("secret sentinel found in an audited surface")


async def persisted_secret_surfaces(database: Database) -> list[str]:
    async with database.session() as session:
        steps = (await session.execute(select(AgentStepRow))).scalars().all()
        calls = (await session.execute(select(ToolCallRow))).scalars().all()
        events = (await session.execute(select(RuntimeEventRow))).scalars().all()
    values: list[object] = []
    for step in steps:
        values.extend(
            (
                step.semantic_parameters_json,
                step.token_usage_json,
                step.decision_json,
                step.human_input_text,
            )
        )
    for call in calls:
        values.extend((call.arguments_json, call.result_json, call.error_code))
    for event in events:
        values.extend((event.payload_json, event.actor_id))
    return [json.dumps(value, default=str, sort_keys=True) for value in values]


@pytest.mark.asyncio
async def test_positive_repository_agent_proposal_approval_version_path(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with slice_system(isolated_database_url, tmp_path) as system:
        payload = proposal_payload(system)
        list_arguments: dict[str, object] = {}
        read_arguments = {"relative_path": "train.py", "start_line": 1, "end_line": 20}
        provider = ScriptedProvider(
            [
                {
                    "kind": "tool_call",
                    "tool_name": "repository.list_tree",
                    "arguments": list_arguments,
                },
                {
                    "kind": "tool_call",
                    "tool_name": "repository.read_text",
                    "arguments": read_arguments,
                },
                {"kind": "propose", "proposal": payload},
            ]
        )
        service = agent_service(system, provider)
        run = await service.create_run(
            context=_context(),
            project_id=system.project_id,
            repository_snapshot_id=system.snapshot_id,
            goal="Inspect the bound source and propose an experiment",
            budget=_budget(),
        )
        assert provider.calls == []
        assert await row_count(system.database, ToolCallRow) == 0

        (system.repository / "train.py").write_text("print('mutable worktree')\n", encoding="utf-8")
        completed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
        assert completed.status == "completed"
        assert completed.produced_proposal_revision_id is not None
        assert await row_count(system.database, DesignDecisionRow) == 0
        assert await row_count(system.database, ExperimentVersionRow) == 0

        definitions = repository_tool_definitions(system.source, system.snapshot_id)
        expected_definitions = {definition.name: definition for definition in definitions}
        async with system.database.session() as session:
            calls = (
                await session.execute(select(ToolCallRow).order_by(ToolCallRow.requested_at))
            ).scalars().all()
        assert [call.tool_name for call in calls] == [
            "repository.list_tree",
            "repository.read_text",
        ]
        for call, expected_arguments in zip(calls, (list_arguments, read_arguments), strict=True):
            definition = expected_definitions[call.tool_name]
            assert call.tool_version == definition.version == "v1"
            assert call.arguments_json == expected_arguments
            assert call.arguments_hash == arguments_hash(expected_arguments)
            assert call.status == "executed"
            assert call.result_json is not None
            assert call.result_hash == tool_result_hash(call.result_json)
        assert len(calls[0].result_json["entries"]) == 4
        assert calls[1].result_json == {
            "relative_path": "train.py",
            "start_line": 1,
            "end_line": 20,
            "text": "print('train')\n",
        }

        agent_repository = SqlAgentRepository(system.database.session)
        steps = await agent_repository.list_steps(run.agent_run_id)
        model_steps = [step for step in steps if step.step_kind == "model_call"]
        assert len(model_steps) == len(provider.calls) == 3
        expected_toolset_hash = bound_toolset_hash(definitions)
        for step, request in zip(model_steps, provider.calls, strict=True):
            assert step.output_schema_hash is not None
            assert step.bound_toolset_hash == request.bound_toolset_hash == expected_toolset_hash
            assert step.semantic_parameters == dict(request.semantic_parameters) == {}
            assert step.output_schema_hash == namespaced_sha256(
                "labpilot-agent-output-schema:v1", request.output_schema
            )
            assert step.canonical_request_hash == canonical_request_hash(
                provider_name=provider.provider_name,
                request=request,
                output_schema_hash=step.output_schema_hash,
            )
        assert len({step.canonical_request_hash for step in model_steps}) == 3

        revision = await system.proposals.get_revision(completed.produced_proposal_revision_id)
        assert revision.payload.repository_snapshot_id == system.snapshot_id
        assert revision.payload.repository_snapshot_identity == system.snapshot_identity
        async with system.database.session() as session:
            stored_revision = await session.get(
                ProposalRevisionRow, revision.proposal_revision_id
            )
        assert stored_revision is not None
        assert stored_revision.origin_agent_step_id == model_steps[-1].agent_step_id
        assert stored_revision.repository_snapshot_id == system.snapshot_id
        assert stored_revision.repository_snapshot_identity == system.snapshot_identity

        await _git(system.repository, "checkout", "--", "train.py")
        verification = await system.proposals.verify_revision(
            context=_context(), proposal_revision_id=revision.proposal_revision_id
        )
        expected_verification = await ProposalVerifier(
            ProposalRepositoryFactAdapter(system.source)
        ).verify(project_id=system.project_id, payload=payload)
        assert verification.result == expected_verification
        assert verification.result.passed is True
        async with system.database.session() as session:
            stored_verification = await session.get(
                ProposalVerificationRow, verification.proposal_verification_id
            )
        assert stored_verification is not None
        assert stored_verification.result_hash == verification.result.result_hash
        assert stored_verification.result_json == {
            "profile": "slice1-v1",
            "passed": True,
            "findings": [],
            "result_hash": verification.result.result_hash,
        }

        version = await system.proposals.approve_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        repeated = await system.proposals.approve_revision(
            context=_context(),
            proposal_revision_id=revision.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        assert repeated.experiment_version_id == version.experiment_version_id
        assert await row_count(system.database, DesignDecisionRow) == 1
        assert await row_count(system.database, ExperimentVersionRow) == 1
        fetched = await system.proposals.get_version(version.experiment_version_id)
        assert fetched == version
        dataset_hash = dataset_reference_hash(payload.dataset_reference)
        environment_hash = environment_expectation_hash(payload.environment_expectation)
        validation_hash = validation_protocol_hash(payload.validation_rules)
        expected_version_identity = experiment_version_identity(
            experiment_id=revision.experiment_id,
            proposal_payload_hash_value=revision.payload_hash,
            repository_snapshot_identity=system.snapshot_identity,
            dataset_reference_hash_value=dataset_hash,
            environment_expectation_hash_value=environment_hash,
            validation_protocol_hash_value=validation_hash,
        )
        async with system.database.session() as session:
            decision = await session.get(DesignDecisionRow, version.design_decision_id)
            stored_version = await session.get(
                ExperimentVersionRow, version.experiment_version_id
            )
            slice2_tables = await session.execute(
                text(
                    "SELECT to_regclass('public.batches'), to_regclass('public.trials'), "
                    "to_regclass('public.attempts'), to_regclass('public.projections')"
                )
            )
        assert decision is not None and stored_version is not None
        assert decision.proposal_revision_id == revision.proposal_revision_id
        assert decision.proposal_verification_id == verification.proposal_verification_id
        assert decision.repository_snapshot_id == system.snapshot_id
        assert decision.repository_snapshot_identity == system.snapshot_identity
        assert decision.dataset_reference_hash == dataset_hash
        assert decision.environment_expectation_hash == environment_hash
        assert decision.actor_type == "local_user"
        assert decision.actor_id == "operator"
        assert decision.decided_at is not None
        assert stored_version.experiment_version_identity == expected_version_identity
        assert stored_version.proposal_revision_id == revision.proposal_revision_id
        assert stored_version.proposal_payload_hash == revision.payload_hash
        assert stored_version.repository_snapshot_id == system.snapshot_id
        assert stored_version.repository_snapshot_identity == system.snapshot_identity
        assert stored_version.dataset_reference_hash == dataset_hash
        assert stored_version.environment_expectation_hash == environment_hash
        assert stored_version.validation_protocol_hash == validation_hash
        assert stored_version.dataset_reference_json == {
            "reference_type": "uri",
            "path_or_uri": "s3://datasets/example",
            "stable_identity": "dataset:v1",
            "reproducibility_level": "strict",
        }
        assert stored_version.environment_expectation_json == {
            "python_profile": "python-3.12",
            "framework_requirements": ["torch==2.7.0"],
            "device_requirement": "cuda",
            "cuda_requirement": "12.8",
        }
        assert stored_version.validation_protocol_json == [
            {"kind": "artifact_exists", "subject": "model"},
            {"kind": "metric_finite", "subject": "accuracy"},
        ]
        assert slice2_tables.one() == (None, None, None, None)


@pytest.mark.asyncio
async def test_cross_project_dirty_snapshot_and_missing_entrypoint_are_contained(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with slice_system(isolated_database_url, tmp_path, name="owner") as owner:
        async with slice_system(isolated_database_url, tmp_path, name="other") as other:
            with pytest.raises(AgentRuntimeError, match="snapshot_project_mismatch"):
                await agent_service(owner, ScriptedProvider([])).create_run(
                    context=_context(),
                    project_id=other.project_id,
                    repository_snapshot_id=owner.snapshot_id,
                    goal="cross project",
                    budget=_budget(),
                )
            with pytest.raises(ProposalInvariantViolation):
                await other.proposals.create_agent_revision(
                    context=_context(),
                    project_id=other.project_id,
                    origin_agent_step_id=UUID("11111111-1111-4111-8111-111111111111"),
                    payload=proposal_payload(owner),
                )

        missing = await owner.proposals.create_agent_revision(
            context=_context(),
            project_id=owner.project_id,
            origin_agent_step_id=UUID("22222222-2222-4222-8222-222222222222"),
            payload=proposal_payload(owner, train_entrypoint="missing.py"),
        )
        missing_verification = await owner.proposals.verify_revision(
            context=_context(), proposal_revision_id=missing.proposal_revision_id
        )
        assert missing_verification.result.passed is False
        assert {finding.code for finding in missing_verification.result.findings} == {
            "train_entrypoint_invalid"
        }

    dirty_path = tmp_path / "dirty-case"
    async with slice_system(
        isolated_database_url, dirty_path, name="dirty", dirty=True
    ) as dirty:
        payload = proposal_payload(dirty)
        provider = ScriptedProvider(
            [
                {
                    "kind": "tool_call",
                    "tool_name": "repository.read_text",
                    "arguments": {
                        "relative_path": "train.py",
                        "start_line": 1,
                        "end_line": 20,
                    },
                },
                {"kind": "propose", "proposal": payload},
            ]
        )
        service = agent_service(dirty, provider)
        run = await service.create_run(
            context=_context(),
            project_id=dirty.project_id,
            repository_snapshot_id=dirty.snapshot_id,
            goal="inspect dirty snapshot and propose",
            budget=_budget(),
        )
        completed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
        assert completed.produced_proposal_revision_id is not None
        verification = await dirty.proposals.verify_revision(
            context=_context(),
            proposal_revision_id=completed.produced_proposal_revision_id,
        )
        assert verification.result.passed is False
        assert "snapshot_dirty" in {
            finding.code for finding in verification.result.findings
        }
        with pytest.raises(ProposalApprovalNotReady, match="snapshot_dirty"):
            await dirty.proposals.approve_revision(
                context=_context(),
                proposal_revision_id=completed.produced_proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
        assert await row_count(dirty.database, ExperimentVersionRow) == 0


async def _mutate_source(repository: Path, mutation: str) -> None:
    if mutation == "tracked":
        (repository / "train.py").write_text("changed\n", encoding="utf-8")
    elif mutation == "untracked":
        (repository / "untracked.txt").write_text("new\n", encoding="utf-8")
    else:
        await _git(repository, "checkout", "-q", "-b", "other")


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["tracked", "untracked", "branch"])
async def test_current_source_change_blocks_approval_without_rewriting_verification(
    isolated_database_url: str,
    tmp_path: Path,
    mutation: str,
) -> None:
    async with slice_system(isolated_database_url, tmp_path, name=mutation) as system:
        revision = await system.proposals.create_agent_revision(
            context=_context(),
            project_id=system.project_id,
            origin_agent_step_id=UUID("33333333-3333-4333-8333-333333333333"),
            payload=proposal_payload(system),
        )
        verification = await system.proposals.verify_revision(
            context=_context(), proposal_revision_id=revision.proposal_revision_id
        )
        before = (
            verification.proposal_verification_id,
            verification.result.result_hash,
            await row_count(system.database, ProposalVerificationRow),
        )
        await _mutate_source(system.repository, mutation)
        with pytest.raises(ProposalApprovalNotReady):
            await system.proposals.approve_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
        reused = await system.proposals.verify_revision(
            context=_context(), proposal_revision_id=revision.proposal_revision_id
        )
        assert (
            reused.proposal_verification_id,
            reused.result.result_hash,
            await row_count(system.database, ProposalVerificationRow),
        ) == before
        assert await row_count(system.database, ExperimentVersionRow) == 0

        if mutation == "tracked":
            await _git(system.repository, "checkout", "--", "train.py")
            approved = await system.proposals.approve_revision(
                context=_context(),
                proposal_revision_id=revision.proposal_revision_id,
                verification_id=verification.proposal_verification_id,
            )
            assert approved.proposal_revision_id == revision.proposal_revision_id


@pytest.mark.asyncio
async def test_human_input_manual_revision_and_verification_authority(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with slice_system(isolated_database_url, tmp_path) as system:
        invalid = proposal_payload(system, train_entrypoint="missing.py")
        provider = ScriptedProvider(
            [
                {"kind": "request_human_input", "question": "Which seed?"},
                {"kind": "propose", "proposal": invalid},
            ]
        )
        service = agent_service(system, provider)
        run = await service.create_run(
            context=_context(),
            project_id=system.project_id,
            repository_snapshot_id=system.snapshot_id,
            goal="ask then propose",
            budget=_budget(),
        )
        awaiting = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
        assert awaiting.status == "awaiting_human"
        resumed = await service.submit_human_input(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=awaiting.row_version,
            text="Use seed 7",
        )
        completed = await service.advance_run(
            context=_context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=resumed.row_version,
        )
        steps = await SqlAgentRepository(system.database.session).list_steps(run.agent_run_id)
        assert [step.human_input_text for step in steps if step.step_kind == "human_input"] == [
            "Use seed 7"
        ]
        assert completed.produced_proposal_revision_id is not None
        first = await system.proposals.get_revision(completed.produced_proposal_revision_id)
        failed = await system.proposals.verify_revision(
            context=_context(), proposal_revision_id=first.proposal_revision_id
        )
        assert failed.result.passed is False

        corrected = await system.proposals.create_manual_revision(
            context=_context(),
            experiment_id=first.experiment_id,
            payload=proposal_payload(system),
        )
        passed = await system.proposals.verify_revision(
            context=_context(), proposal_revision_id=corrected.proposal_revision_id
        )
        assert corrected.revision_number == first.revision_number + 1 == 2
        assert passed.result.passed is True
        with pytest.raises(ProposalVerificationConflict):
            await system.proposals.approve_revision(
                context=_context(),
                proposal_revision_id=corrected.proposal_revision_id,
                verification_id=failed.proposal_verification_id,
            )
        version = await system.proposals.approve_revision(
            context=_context(),
            proposal_revision_id=corrected.proposal_revision_id,
            verification_id=passed.proposal_verification_id,
        )
        async with system.database.session() as session:
            stored_first = await session.get(ProposalRevisionRow, first.proposal_revision_id)
            stored_failed = await session.get(
                ProposalVerificationRow, failed.proposal_verification_id
            )
            decision = await session.get(DesignDecisionRow, version.design_decision_id)
            stored_version = await session.get(
                ExperimentVersionRow, version.experiment_version_id
            )
        assert stored_first is not None and stored_failed is not None
        assert stored_first.revision_number == 1
        assert stored_first.payload_json["train_entrypoint"] == "missing.py"
        assert stored_failed.passed is False
        assert stored_failed.proposal_revision_id == first.proposal_revision_id
        assert decision is not None and stored_version is not None
        assert decision.proposal_revision_id == corrected.proposal_revision_id
        assert decision.proposal_verification_id == passed.proposal_verification_id
        assert stored_version.proposal_revision_id == corrected.proposal_revision_id


class SentinelRemoteProvider:
    provider_name = "sentinel-remote"
    egress = "remote"

    def __init__(self, secret: str) -> None:
        self.secret = SecretStr(secret)
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        raise AssertionError("remote provider must not be invoked")


def _api(system: SliceSystem, agent_runs: AgentApplicationService):
    return create_app(
        services=ApplicationServices(
            projects=ProjectApplicationService(system.database.session),
            repository_source=system.source,
            proposals=system.proposals,
            agent_runs=agent_runs,
        ),
        actor_provider=ServerConfiguredActorProvider(
            installation_identity="slice1-api", actor_id="operator"
        ),
    )


@pytest.mark.asyncio
async def test_local_only_blocks_remote_before_source_or_secret_egress(
    isolated_database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    request: pytest.FixtureRequest,
) -> None:
    sentinel = secrets.token_urlsafe(32)
    async with slice_system(isolated_database_url, tmp_path) as system:
        for logger_name in (
            "labpilot.agent_runtime.service",
            "labpilot.tools_policy.service",
        ):
            logger = logging.getLogger(logger_name)
            logger.disabled = False
            logger.addHandler(caplog.handler)
            request.addfinalizer(lambda logger=logger: logger.removeHandler(caplog.handler))
        caplog.set_level(logging.INFO, logger="labpilot.agent_runtime.service")
        caplog.set_level(logging.INFO, logger="labpilot.tools_policy.service")
        local_tools = tool_service(system)
        call = await local_tools.request_call(
            context=_context("tool-correlation"),
            project_id=system.project_id,
            definition_name="repository.list_tree",
            arguments={},
        )
        await local_tools.execute_call(
            context=_context("tool-correlation"), tool_call_id=call.tool_call_id
        )
        remote = SentinelRemoteProvider(sentinel)
        service = agent_service(system, remote)
        async with AsyncClient(
            transport=ASGITransport(app=_api(system, service)),
            base_url="http://test",
        ) as client:
            created = await client.post(
                f"/projects/{system.project_id}/agent-runs",
                json={
                    "repository_snapshot_id": str(system.snapshot_id),
                    "goal": "Do not leak source",
                    "budget": {
                        "max_steps": 2,
                        "max_model_calls": 2,
                        "max_wall_clock_seconds": 60,
                        "max_tokens": 100,
                    },
                },
            )
            blocked = await client.post(
                f"/agent-runs/{created.json()['agent_run_id']}/advance",
                json={"expected_row_version": created.json()["row_version"]},
            )
        assert created.status_code == 201
        assert blocked.status_code == 403
        assert remote.calls == []

        serialized_responses = [
            json.dumps(created.json(), sort_keys=True),
            json.dumps(blocked.json(), sort_keys=True),
        ]
        serialized_logs = [
            json.dumps(record.__dict__, default=str, sort_keys=True)
            for record in caplog.records
        ]
        _assert_secret_absent(
            sentinel,
            await persisted_secret_surfaces(system.database)
            + serialized_responses
            + serialized_logs,
        )
        agent_logs = [
            record
            for record in caplog.records
            if record.name == "labpilot.agent_runtime.service"
        ]
        tool_logs = [
            record
            for record in caplog.records
            if record.name == "labpilot.tools_policy.service"
        ]
        assert agent_logs and tool_logs
        for record in agent_logs:
            assert record.request_id
            assert record.project_id == str(system.project_id)
            assert record.agent_run_id
        for record in tool_logs:
            assert record.request_id == "tool-correlation"
            assert record.project_id == str(system.project_id)
            assert record.tool_call_id == str(call.tool_call_id)


class BlockingProvider(ScriptedProvider):
    def __init__(self, script: list[object]) -> None:
        super().__init__(script)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        await self.release.wait()
        return await super().complete(request)


@pytest.mark.asyncio
async def test_advance_crash_replay_and_approval_concurrency_boundaries(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with slice_system(isolated_database_url, tmp_path) as system:
        blocking = BlockingProvider([{"kind": "stop", "reason_code": "done"}])
        service = agent_service(system, blocking)
        run = await service.create_run(
            context=_context(),
            project_id=system.project_id,
            repository_snapshot_id=system.snapshot_id,
            goal="one winner",
            budget=_budget(),
        )
        winner = asyncio.create_task(
            service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        )
        await blocking.started.wait()
        with pytest.raises(AgentRuntimeError, match="row_version_conflict"):
            await service.advance_run(
                context=_context(),
                agent_run_id=run.agent_run_id,
                expected_row_version=run.row_version,
            )
        blocking.release.set()
        assert (await winner).status == "completed"
        assert len(blocking.calls) == 1

        interrupted = BlockingProvider([{"kind": "stop", "reason_code": "unreachable"}])
        interrupted_service = agent_service(system, interrupted)
        interrupted_run = await interrupted_service.create_run(
            context=_context(),
            project_id=system.project_id,
            repository_snapshot_id=system.snapshot_id,
            goal="unknown outcome",
            budget=_budget(),
        )
        task = asyncio.create_task(
            interrupted_service.advance_run(
                context=_context(),
                agent_run_id=interrupted_run.agent_run_id,
                expected_row_version=interrupted_run.row_version,
            )
        )
        await interrupted.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reserved = await interrupted_service.get_run(interrupted_run.agent_run_id)
        failed_closed = await interrupted_service.advance_run(
            context=_context(),
            agent_run_id=interrupted_run.agent_run_id,
            expected_row_version=reserved.row_version,
        )
        assert failed_closed.status == "failed"
        assert failed_closed.termination_reason == "model_call_outcome_unknown"
        assert interrupted.calls == []

        crash_provider = ScriptedProvider(
            [{"kind": "propose", "proposal": proposal_payload(system)}]
        )
        crashing = CrashAfterCreateProposalService(system.proposals)
        crash_service = agent_service(system, crash_provider, proposals=crashing)
        crash_run = await crash_service.create_run(
            context=_context(),
            project_id=system.project_id,
            repository_snapshot_id=system.snapshot_id,
            goal="proposal crash replay",
            budget=_budget(),
        )
        with pytest.raises(RuntimeError, match="crash after committed"):
            await crash_service.advance_run(
                context=_context(),
                agent_run_id=crash_run.agent_run_id,
                expected_row_version=crash_run.row_version,
            )
        after_crash = await crash_service.get_run(crash_run.agent_run_id)
        replayed = await crash_service.advance_run(
            context=_context(),
            agent_run_id=crash_run.agent_run_id,
            expected_row_version=after_crash.row_version,
        )
        assert replayed.produced_proposal_revision_id is not None
        async with system.database.session() as session:
            proposal_count = await session.scalar(
                select(func.count()).select_from(ProposalRevisionRow).where(
                    ProposalRevisionRow.origin_agent_step_id.is_not(None)
                )
            )
        assert proposal_count == 1
        assert len(crash_provider.calls) == 1

        first = await system.proposals.get_revision(replayed.produced_proposal_revision_id)
        second = await system.proposals.create_manual_revision(
            context=_context(),
            experiment_id=first.experiment_id,
            payload=proposal_payload(system, goal="Second valid semantics"),
        )
        first_verification, second_verification = await asyncio.gather(
            system.proposals.verify_revision(
                context=_context(), proposal_revision_id=first.proposal_revision_id
            ),
            system.proposals.verify_revision(
                context=_context(), proposal_revision_id=second.proposal_revision_id
            ),
        )
        versions = await asyncio.gather(
            system.proposals.approve_revision(
                context=_context(),
                proposal_revision_id=first.proposal_revision_id,
                verification_id=first_verification.proposal_verification_id,
            ),
            system.proposals.approve_revision(
                context=_context(),
                proposal_revision_id=second.proposal_revision_id,
                verification_id=second_verification.proposal_verification_id,
            ),
        )
        assert sorted(version.version_number for version in versions) == [1, 2]
        assert len({version.experiment_version_id for version in versions}) == 2
        assert await row_count(system.database, DesignDecisionRow) == 2
        assert await row_count(system.database, ExperimentVersionRow) == 2
