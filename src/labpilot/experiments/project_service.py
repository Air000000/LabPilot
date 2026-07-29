from uuid import UUID, uuid4

from sqlalchemy import func, select

from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.db.idempotency import (
    CommandResultEnvelope,
    IdempotentCommandOutcome,
    async_noop_prepare,
    execute_idempotent_command,
    prepare_command_identity,
    prepare_create_scope,
    prepare_project_scope,
)
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.experiments.events import (
    build_project_archived_event,
    build_project_created_event,
)
from labpilot.experiments.models import ProjectRow
from labpilot.experiments.project_repository import ProjectRepository
from labpilot.experiments.project_types import (
    ArchiveProject,
    CreateProject,
    ProjectDetails,
    project_identity,
)
from labpilot.observability.events import append_runtime_event

_CREATE_PROJECT_COMMAND = "CreateProject"
_ARCHIVE_PROJECT_COMMAND = "ArchiveProject"
_COMMAND_VERSION = 1


class ProjectNotFound(LabPilotError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project not found: {project_id}")


class ProjectVersionConflict(LabPilotError):
    def __init__(
        self,
        project_id: UUID,
        *,
        expected_row_version: int,
        current_row_version: int,
    ) -> None:
        self.project_id = project_id
        self.expected_row_version = expected_row_version
        self.current_row_version = current_row_version
        super().__init__(
            f"project {project_id} row version is {current_row_version}, "
            f"expected {expected_row_version}"
        )


class ProjectStateConflict(LabPilotError):
    def __init__(
        self,
        project_id: UUID,
        *,
        current_state: str,
        expected_state: str,
    ) -> None:
        self.project_id = project_id
        self.current_state = current_state
        self.expected_state = expected_state
        super().__init__(
            f"project {project_id} state is {current_state}, expected {expected_state}"
        )


class ProjectApplicationService:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory
        self._repository = ProjectRepository()

    async def get_project(
        self,
        *,
        project_id: UUID,
    ) -> ProjectDetails:
        async with self._session_context_factory() as session:
            row = await self._repository.get(session, project_id)
            if row is None:
                raise ProjectNotFound(project_id)
            return ProjectDetails(
                project_id=row.project_id,
                project_identity=row.project_identity,
                name=row.name,
                description=row.description,
                status=row.status,
                primary_repository_registration_id=(
                    row.primary_repository_registration_id
                ),
                artifact_root_ref=row.artifact_root_ref,
                created_at=row.created_at,
                updated_at=row.updated_at,
                row_version=row.row_version,
            )

    async def create_project(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: CreateProject,
    ) -> IdempotentCommandOutcome:
        scope_identity = prepare_create_scope(context)
        identity = prepare_command_identity(
            scope_identity=scope_identity,
            command_name=_CREATE_PROJECT_COMMAND,
            command_version=_COMMAND_VERSION,
            idempotency_key=idempotency_key,
            normalized_request={
                "artifact_root_ref": command.artifact_root_ref,
                "authority_scope_identity": scope_identity,
                "command_name": _CREATE_PROJECT_COMMAND,
                "command_version": _COMMAND_VERSION,
                "description": command.description,
                "name": command.name,
            },
        )

        async def operation(
            uow: UnitOfWork,
            prepared_none: None,
        ) -> CommandResultEnvelope:
            del prepared_none
            project_id = uuid4()
            identity_value = project_identity(project_id)
            row = ProjectRow(
                project_id=project_id,
                project_identity=identity_value,
                name=command.name,
                description=command.description,
                status="active",
                primary_repository_registration_id=None,
                artifact_root_ref=command.artifact_root_ref,
                row_version=1,
            )
            await self._repository.add(uow, row)
            await append_runtime_event(
                uow,
                build_project_created_event(
                    context=context,
                    project_id=project_id,
                    project_identity=identity_value,
                ),
            )
            return CommandResultEnvelope(
                outcome_kind="project_created",
                primary_result_type="project",
                primary_result_id=str(project_id),
                primary_result_identity=identity_value,
                related_result_refs=(),
                committed_state="active",
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=async_noop_prepare,
            operation=operation,
        )

    async def archive_project(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: ArchiveProject,
    ) -> IdempotentCommandOutcome:
        scope_identity = prepare_project_scope(
            context,
            project_id=command.project_id,
        )
        identity = prepare_command_identity(
            scope_identity=scope_identity,
            command_name=_ARCHIVE_PROJECT_COMMAND,
            command_version=_COMMAND_VERSION,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope_identity,
                "command_name": _ARCHIVE_PROJECT_COMMAND,
                "command_version": _COMMAND_VERSION,
                "expected_row_version": command.expected_row_version,
                "project_id": str(command.project_id),
            },
        )

        async def operation(
            uow: UnitOfWork,
            prepared_none: None,
        ) -> CommandResultEnvelope:
            del prepared_none
            row = await self._repository.get_for_update(
                uow.session,
                command.project_id,
            )
            if row is None:
                raise ProjectNotFound(command.project_id)
            if row.row_version != command.expected_row_version:
                raise ProjectVersionConflict(
                    command.project_id,
                    expected_row_version=command.expected_row_version,
                    current_row_version=row.row_version,
                )
            if row.status != "active":
                raise ProjectStateConflict(
                    command.project_id,
                    current_state=row.status,
                    expected_state="active",
                )

            db_now = await uow.session.scalar(select(func.clock_timestamp()))
            assert db_now is not None
            row.status = "archived"
            row.row_version += 1
            row.updated_at = db_now
            await uow.session.flush()
            await append_runtime_event(
                uow,
                build_project_archived_event(
                    context=context,
                    project_id=row.project_id,
                    project_identity=row.project_identity,
                    row_version=row.row_version,
                ),
            )
            return CommandResultEnvelope(
                outcome_kind="project_archived",
                primary_result_type="project",
                primary_result_id=str(row.project_id),
                primary_result_identity=row.project_identity,
                related_result_refs=(),
                committed_state="archived",
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=async_noop_prepare,
            operation=operation,
        )
