import asyncio
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from labpilot.common.signals import install_shutdown_handlers
from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.coordinator.execution_adapters import (
    RepositorySnapshotMaterializationAdapter,
    VersionExecutionSemanticsAdapter,
)
from labpilot.coordinator.service import CoordinatorService
from labpilot.db.session import Database
from labpilot.execution.batch_service import AttemptCreationService
from labpilot.execution.execution_service import AttemptExecutionService
from labpilot.execution.preparation_service import AttemptPreparationService
from labpilot.execution.runner_client import RunnerClient
from labpilot.execution.trial_outcome import TrialExecutionOutcomeService
from labpilot.results_validation.service import ResultApplicationService
from labpilot.results_validation.summary_service import BatchSummaryService
from labpilot.settings import Settings, get_settings


def build_coordinator(settings: Settings, database: Database) -> CoordinatorService:
    workspace_root = settings.attempt_workspace_root
    socket_path = settings.runner_socket_path
    controller_uid = settings.controller_uid
    controller_gid = settings.controller_gid
    train_gid = settings.train_gid
    if (
        workspace_root is None
        or socket_path is None
        or controller_uid is None
        or controller_gid is None
        or train_gid is None
    ):
        raise RuntimeError("Coordinator execution configuration is incomplete")
    repository_source = RepositorySnapshotSourceService(
        database.session,
        git_adapter=GitAdapter(
            git_executable=Path("/usr/bin/git"),
            timeout_seconds=settings.repository_git_timeout_seconds,
        ),
        content_store=ContentAddressedStore(settings.repository_content_store_root),
    )
    semantics = VersionExecutionSemanticsAdapter(database.session)
    outcomes = TrialExecutionOutcomeService(
        database.session,
        version_semantics=semantics,
    )
    result_processing = ResultApplicationService(database.session)
    summary = BatchSummaryService(
        database.session,
        trial_execution_outcome_service=outcomes,
    )
    return CoordinatorService(
        database.session,
        attempt_creation=AttemptCreationService(
            database.session,
            version_semantics=semantics,
            installation_identity=settings.installation_identity,
            attempt_workspace_root=workspace_root,
        ),
        preparation=AttemptPreparationService(
            database.session,
            version_semantics=semantics,
            snapshot_materialization=RepositorySnapshotMaterializationAdapter(
                repository_source
            ),
            execution_gid=train_gid,
        ),
        execution=AttemptExecutionService(
            database.session,
            RunnerClient(
                str(socket_path),
                timeout_seconds=settings.runner_request_timeout_seconds,
            ),
            workspace_root=workspace_root,
            unknown_attention_grace_seconds=(
                settings.unknown_attention_grace_seconds
            ),
        ),
        result_processing=result_processing,
        summary=summary,
    )


async def run(
    stop_event: asyncio.Event | None = None,
    database: Database | None = None,
    coordinator: CoordinatorService | None = None,
    probe_interval_seconds: float = 5.0,
) -> None:
    event = stop_event or asyncio.Event()
    if stop_event is None:
        install_shutdown_handlers(event)
    if database is None:
        settings = get_settings()
        active_database = Database(settings.database_url)
    else:
        settings = None
        active_database = database
    active_coordinator = coordinator
    coordinator_start_id = uuid4()
    startup_complete = False
    ready_emitted = False
    try:
        if active_coordinator is None and database is None:
            assert settings is not None
            active_coordinator = build_coordinator(settings, active_database)
        while not event.is_set():
            async with active_database.session() as session:
                await session.execute(text("SELECT 1"))
            if active_coordinator is not None and not startup_complete:
                await active_coordinator.run_startup_barrier(
                    coordinator_start_id=coordinator_start_id
                )
                startup_complete = True
            if active_coordinator is not None:
                await active_coordinator.run_scan()
            if not ready_emitted:
                print(
                    json.dumps(
                        {"event": "process_shell.ready", "process": "coordinator"}
                    ),
                    flush=True,
                )
                ready_emitted = True
            try:
                await asyncio.wait_for(event.wait(), timeout=probe_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await active_database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
