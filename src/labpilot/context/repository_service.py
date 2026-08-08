from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.context.events import (
    build_repository_registration_availability_changed_event,
    build_repository_registration_created_event,
    build_repository_registration_revoked_event,
)
from labpilot.context.git_adapter import GitAdapter
from labpilot.context.models import RepositoryRegistrationRow
from labpilot.context.repository_identities import repository_registration_identity
from labpilot.context.repository_paths import resolve_repository_root
from labpilot.context.repository_repository import RepositoryRegistrationRepository
from labpilot.context.repository_types import (
    CheckRepositoryAvailability,
    RegisterLocalRepository,
    RepositoryHeadUnavailable,
    RepositoryNotGit,
    RepositoryPathMustBeAbsolute,
    RepositoryProbe,
    RepositoryUnavailable,
    RevokeRepositoryRegistration,
)
from labpilot.db.idempotency import (
    CommandResultEnvelope,
    IdempotentCommandOutcome,
    async_noop_prepare,
    execute_idempotent_command,
    prepare_command_identity,
    prepare_project_scope,
)
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.experiments.project_bindings import (
    clear_primary_repository_binding,
    set_primary_repository_binding,
)
from labpilot.experiments.project_repository import ProjectRepository
from labpilot.experiments.project_service import (
    ProjectNotFound,
    ProjectStateConflict,
    ProjectVersionConflict,
)
from labpilot.observability.events import append_runtime_event


class RepositoryRegistrationConflict(LabPilotError):
    pass


@dataclass(frozen=True, slots=True)
class _PreparedRegistration:
    probe: RepositoryProbe


class RepositoryApplicationService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        git_adapter: GitAdapter,
        allowed_roots: tuple[Path, ...],
    ) -> None:
        self._session_context_factory = session_context_factory
        self._git_adapter = git_adapter
        self._allowed_roots = allowed_roots

        self._registrations = RepositoryRegistrationRepository()
        self._projects = ProjectRepository()

    async def register_local_repository(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: RegisterLocalRepository,
    ) -> IdempotentCommandOutcome:
        scope_identity = prepare_project_scope(context, project_id=command.project_id)
        identity = prepare_command_identity(
            scope_identity=scope_identity,
            command_name="RegisterLocalRepository",
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope_identity,
                "command_name": "RegisterLocalRepository",
                "command_version": 1,
                "expected_project_row_version": command.expected_project_row_version,
                "project_id": str(command.project_id),
                "user_path": str(command.user_path),
            },
        )

        async def prepare() -> _PreparedRegistration:
            if not command.user_path.is_absolute():
                raise RepositoryPathMustBeAbsolute(command.user_path)
            repository_root = resolve_repository_root(
                command.user_path,
                allowed_roots=self._allowed_roots,
            )
            return _PreparedRegistration(await self._git_adapter.probe(repository_root))

        async def operation(
            uow: UnitOfWork,
            prepared: _PreparedRegistration,
        ) -> CommandResultEnvelope:
            project = await self._projects.get_for_update(uow.session, command.project_id)
            if project is None:
                raise ProjectNotFound(command.project_id)
            if project.row_version != command.expected_project_row_version:
                raise ProjectVersionConflict(
                    command.project_id,
                    expected_row_version=command.expected_project_row_version,
                    current_row_version=project.row_version,
                )
            if project.status != "active":
                raise ProjectStateConflict(
                    command.project_id,
                    current_state=project.status,
                    expected_state="active",
                )

            root_identity = repository_registration_identity(
                canonical_root_path=prepared.probe.canonical_root_path,
                repository_type="local_git",
            )
            registrations = await self._registrations.list_project_registrations(
                uow.session,
                command.project_id,
            )
            matching = next(
                (
                    row
                    for row in registrations
                    if row.root_path_identity == root_identity and row.status != "revoked"
                ),
                None,
            )
            if matching is not None:
                if (
                    project.primary_repository_registration_id
                    != matching.repository_registration_id
                ):
                    raise RepositoryRegistrationConflict("live root conflicts with primary")
                return CommandResultEnvelope(
                    outcome_kind="repository_registration_reused",
                    primary_result_type="repository_registration",
                    primary_result_id=str(matching.repository_registration_id),
                    primary_result_identity=matching.root_path_identity,
                    related_result_refs=(),
                    committed_state=matching.status,
                )
            if project.primary_repository_registration_id is not None:
                raise RepositoryRegistrationConflict(
                    "project already has a different primary repository"
                )

            now = await uow.session.scalar(select(func.clock_timestamp()))
            assert now is not None
            registration_id = uuid4()
            row = RepositoryRegistrationRow(
                repository_registration_id=registration_id,
                project_id=command.project_id,
                repository_type="local_git",
                canonical_root_path=prepared.probe.canonical_root_path,
                root_path_identity=root_identity,
                remote_origin_sanitized=prepared.probe.remote_origin_sanitized,
                default_branch=prepared.probe.default_branch,
                status="active",
                last_access_check_at=now,
                row_version=1,
            )
            await self._registrations.add_registration(uow, row)
            project = await set_primary_repository_binding(
                uow,
                project_id=command.project_id,
                repository_registration_id=registration_id,
                expected_project_row_version=command.expected_project_row_version,
            )
            await append_runtime_event(
                uow,
                build_repository_registration_created_event(
                    context=context,
                    project_id=command.project_id,
                    repository_registration_id=registration_id,
                    root_path_identity=root_identity,
                    row_version=row.row_version,
                ),
            )
            return CommandResultEnvelope(
                outcome_kind="repository_registration_created",
                primary_result_type="repository_registration",
                primary_result_id=str(registration_id),
                primary_result_identity=root_identity,
                related_result_refs=(f"project:{project.project_id}",),
                committed_state="active",
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=prepare,
            operation=operation,
        )

    async def check_repository_availability(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: CheckRepositoryAvailability,
    ) -> IdempotentCommandOutcome:
        scope_identity = prepare_project_scope(
            context,
            project_id=command.repository_registration_id,
        )
        identity = prepare_command_identity(
            scope_identity=scope_identity,
            command_name="CheckRepositoryAvailability",
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope_identity,
                "command_name": "CheckRepositoryAvailability",
                "command_version": 1,
                "expected_row_version": command.expected_row_version,
                "repository_registration_id": str(command.repository_registration_id),
            },
        )

        async def prepare() -> tuple[str, str]:
            async with self._session_context_factory() as session:
                registration = await self._registrations.get_registration(
                    session,
                    command.repository_registration_id,
                )
            if registration is None:
                raise RepositoryRegistrationConflict("registration not found")
            try:
                await self._git_adapter.probe(Path(registration.canonical_root_path))
            except RepositoryUnavailable:
                return "unavailable", "repository_unavailable"
            except (RepositoryNotGit, RepositoryHeadUnavailable):
                return "invalid_git_repository", "invalid_git_repository"
            return "active", "repository_available"

        async def operation(
            uow: UnitOfWork,
            prepared: tuple[str, str],
        ) -> CommandResultEnvelope:
            registration = await self._registrations.get_registration_for_update(
                uow.session,
                command.repository_registration_id,
            )
            if registration is None:
                raise RepositoryRegistrationConflict("registration not found")
            if registration.row_version != command.expected_row_version:
                raise RepositoryRegistrationConflict("registration row version conflict")
            if registration.status == "revoked":
                raise RepositoryRegistrationConflict("registration is revoked")
            old_status = registration.status
            new_status, reason_code = prepared
            now = await uow.session.scalar(select(func.clock_timestamp()))
            assert now is not None
            if old_status == new_status:
                return CommandResultEnvelope(
                    outcome_kind="repository_availability_unchanged",
                    primary_result_type="repository_registration",
                    primary_result_id=str(registration.repository_registration_id),
                    primary_result_identity=registration.root_path_identity,
                    related_result_refs=(),
                    committed_state=registration.status,
                )
            registration.status = new_status
            registration.last_access_check_at = now
            registration.updated_at = now
            registration.row_version += 1
            await uow.session.flush()
            await append_runtime_event(
                uow,
                build_repository_registration_availability_changed_event(
                    context=context,
                    project_id=registration.project_id,
                    repository_registration_id=registration.repository_registration_id,
                    old_status=old_status,
                    new_status=new_status,
                    reason_code=reason_code,
                    row_version=registration.row_version,
                ),
            )
            return CommandResultEnvelope(
                outcome_kind="repository_availability_changed",
                primary_result_type="repository_registration",
                primary_result_id=str(registration.repository_registration_id),
                primary_result_identity=registration.root_path_identity,
                related_result_refs=(),
                committed_state=registration.status,
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=prepare,
            operation=operation,
        )

    async def revoke_repository_registration(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: RevokeRepositoryRegistration,
    ) -> IdempotentCommandOutcome:
        scope_identity = prepare_project_scope(
            context,
            project_id=command.repository_registration_id,
        )
        identity = prepare_command_identity(
            scope_identity=scope_identity,
            command_name="RevokeRepositoryRegistration",
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope_identity,
                "command_name": "RevokeRepositoryRegistration",
                "command_version": 1,
                "expected_project_row_version": command.expected_project_row_version,
                "expected_row_version": command.expected_row_version,
                "repository_registration_id": str(command.repository_registration_id),
            },
        )

        async def operation(
            uow: UnitOfWork,
            prepared_none: None,
        ) -> CommandResultEnvelope:
            del prepared_none
            registration = await self._registrations.get_registration_for_update(
                uow.session,
                command.repository_registration_id,
            )
            if registration is None:
                raise RepositoryRegistrationConflict("registration not found")
            if registration.status == "revoked":
                raise RepositoryRegistrationConflict("registration is revoked")
            if registration.row_version != command.expected_row_version:
                raise RepositoryRegistrationConflict("registration row version conflict")
            project = await self._projects.get_for_update(uow.session, registration.project_id)
            if project is None:
                raise ProjectNotFound(registration.project_id)
            if project.row_version != command.expected_project_row_version:
                raise ProjectVersionConflict(
                    registration.project_id,
                    expected_row_version=command.expected_project_row_version,
                    current_row_version=project.row_version,
                )
            old_status = registration.status
            now = await uow.session.scalar(select(func.clock_timestamp()))
            assert now is not None
            registration.status = "revoked"
            registration.updated_at = now
            registration.row_version += 1
            await uow.session.flush()
            if (
                project.primary_repository_registration_id
                == registration.repository_registration_id
            ):
                project = await clear_primary_repository_binding(
                    uow,
                    project_id=registration.project_id,
                    expected_registration_id=registration.repository_registration_id,
                    expected_project_row_version=command.expected_project_row_version,
                )
            await append_runtime_event(
                uow,
                build_repository_registration_revoked_event(
                    context=context,
                    project_id=registration.project_id,
                    repository_registration_id=registration.repository_registration_id,
                    previous_status=old_status,
                    root_path_identity=registration.root_path_identity,
                    row_version=registration.row_version,
                ),
            )
            return CommandResultEnvelope(
                outcome_kind="repository_registration_revoked",
                primary_result_type="repository_registration",
                primary_result_id=str(registration.repository_registration_id),
                primary_result_identity=registration.root_path_identity,
                related_result_refs=(f"project:{project.project_id}",),
                committed_state="revoked",
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=async_noop_prepare,
            operation=operation,
        )
