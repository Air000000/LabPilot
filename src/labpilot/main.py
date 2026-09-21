from pathlib import Path
from typing import cast
from uuid import UUID

from labpilot.agent_runtime.context_compiler import ContextCompiler
from labpilot.agent_runtime.deepseek import DeepSeekChatProvider
from labpilot.agent_runtime.opencode_go import OpenCodeGoChatProvider
from labpilot.agent_runtime.provider import ModelProvider, UnconfiguredModelProvider
from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.service import (
    AgentApplicationService,
    AgentProposalService,
)
from labpilot.api.actor import ServerConfiguredActorProvider
from labpilot.api.app import create_app
from labpilot.api.dependencies import ApplicationServices
from labpilot.api.result_aware_agent_adapter import SqlResultAwareAgentAdapter
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.proposal_repository_adapter import ProposalRepositoryFactAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.db.readiness import DatabaseReadinessProbe
from labpilot.db.session import Database
from labpilot.execution.batch_service import BatchApplicationService
from labpilot.execution.query_service import ExecutionQueryService
from labpilot.experiments.project_service import ProjectApplicationService
from labpilot.experiments.proposal_service import ProposalApplicationService
from labpilot.experiments.proposal_verifier import ProposalVerifier
from labpilot.results_validation.query_service import ResultQueryService
from labpilot.settings import get_settings
from labpilot.tools_policy.repository import SqlToolRepository
from labpilot.tools_policy.repository_tools import repository_tool_definitions
from labpilot.tools_policy.service import ToolApplicationService

settings = get_settings()
database = Database(settings.database_url)
repository_source = RepositorySnapshotSourceService(
    database.session,
    git_adapter=GitAdapter(
        git_executable=Path("/usr/bin/git"),
        timeout_seconds=settings.repository_git_timeout_seconds,
    ),
    content_store=ContentAddressedStore(settings.repository_content_store_root),
)
proposal_facts = ProposalRepositoryFactAdapter(repository_source)
proposals = ProposalApplicationService(
    database.session,
    repository_facts=proposal_facts,
    verifier=ProposalVerifier(proposal_facts),
)
tool_repository = SqlToolRepository(database.session)


def tool_service_for_snapshot(repository_snapshot_id: UUID) -> ToolApplicationService:
    return ToolApplicationService(
        repository_tool_definitions(repository_source, repository_snapshot_id),
        tool_repository,
    )


provider: ModelProvider
if settings.agent_provider_model and settings.agent_provider_api_key:
    if settings.agent_provider_kind == "deepseek":
        provider = DeepSeekChatProvider(
            model=settings.agent_provider_model,
            api_key=settings.agent_provider_api_key,
            timeout_seconds=settings.agent_provider_timeout_seconds,
            max_response_bytes=settings.agent_provider_max_response_bytes,
        )
    else:
        provider = OpenCodeGoChatProvider(
            model=settings.agent_provider_model,
            api_key=settings.agent_provider_api_key,
            timeout_seconds=settings.agent_provider_timeout_seconds,
            max_response_bytes=settings.agent_provider_max_response_bytes,
        )
    model_name = settings.agent_provider_model
else:
    provider = UnconfiguredModelProvider()
    model_name = "unconfigured"

agent_repository = SqlAgentRepository(database.session)
result_aware_agent = SqlResultAwareAgentAdapter(
    database.session,
    agent_repository=agent_repository,
)
agent_runs = AgentApplicationService(
    repository=agent_repository,
    repository_source=repository_source,
    provider=provider,
    model_name=model_name,
    context_compiler=ContextCompiler(),
    tool_service_factory=tool_service_for_snapshot,
    proposal_service=cast(AgentProposalService, proposals),
    data_egress_policy=settings.data_egress_policy,
    follow_up_run_store=result_aware_agent,
    result_context_source=result_aware_agent,
)
services = ApplicationServices(
    projects=ProjectApplicationService(database.session),
    repository_source=repository_source,
    proposals=proposals,
    agent_runs=agent_runs,
    batches=BatchApplicationService(
        database.session,
        version_semantics=VersionExecutionSemanticsAdapter(database.session),
    ),
    execution_queries=ExecutionQueryService(database.session),
    result_queries=ResultQueryService(database.session),
)
actor_provider = ServerConfiguredActorProvider(
    installation_identity=settings.installation_identity,
    actor_id=settings.local_actor_id,
)
app = create_app(
    readiness_probe=DatabaseReadinessProbe(database.engine, "alembic.ini"),
    shutdown_callbacks=(database.dispose,),
    services=services,
    actor_provider=actor_provider,
)
