from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.context.content_store import ContentAddressedStore
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
    CaptureRepositorySnapshot,
    CheckRepositoryAvailability,
    RegisterLocalRepository,
    RepositoryHeadUnavailable,
    RepositoryNotGit,
    RepositoryPathMustBeAbsolute,
    RepositoryProbe,
    RepositorySnapshotInvariantViolation,
    RepositorySnapshotObservation,
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
        content_store: ContentAddressedStore | None = None,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._git_adapter = git_adapter
        self._allowed_roots = allowed_roots
        self._content_store = content_store

        self._registrations = RepositoryRegistrationRepository()
        self._projects = ProjectRepository()

    async def capture_repository_snapshot(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: CaptureRepositorySnapshot,
    ) -> IdempotentCommandOutcome:
        from labpilot.context.events import build_repository_snapshot_captured_event
        from labpilot.context.models import (
            RepositorySnapshotFreshnessRow,
            RepositorySnapshotInspectionBundleEntryRow,
            RepositorySnapshotInspectionBundleRow,
            RepositorySnapshotRow,
        )
        from labpilot.context.repository_identities import (
            BundleEntryManifestItemV1,
            EntryKind,
            FileMode,
            FileType,
            RepositoryIdentityInputInvalid,
            repository_inspection_bundle_entries_manifest_hash,
            repository_inspection_bundle_entry_identity,
            repository_inspection_bundle_identity,
        )
        from labpilot.context.repository_repository import RepositorySnapshotRepository
        from labpilot.context.repository_types import RepositorySnapshotCaptureConflict

        registration_id = command.repository_registration_id
        async with self._session_context_factory() as session:
            registration = await self._registrations.get_registration(session, registration_id)
        if registration is None:
            raise RepositoryRegistrationConflict("registration not found")
        scope_identity = prepare_project_scope(context, project_id=registration.project_id)
        identity = prepare_command_identity(
            scope_identity=scope_identity,
            command_name="CaptureRepositorySnapshot",
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request={
                "authority_scope_identity": scope_identity,
                "command_name": "CaptureRepositorySnapshot",
                "command_version": 1,
                "repository_registration_id": str(registration_id),
            },
        )
        snapshots = RepositorySnapshotRepository()

        async def prepare() -> RepositorySnapshotObservation:
            if self._content_store is None:
                raise RepositorySnapshotCaptureConflict("content_store_unconfigured")
            if registration.status != "active":
                raise RepositoryRegistrationConflict("registration is not active")
            return await self._git_adapter.capture_snapshot(
                Path(registration.canonical_root_path),
                repository_registration_id=registration_id,
                content_store=self._content_store,
            )

        async def operation(
            uow: UnitOfWork, observation: RepositorySnapshotObservation
        ) -> CommandResultEnvelope:
            current = await self._registrations.get_registration_for_update(
                uow.session, registration_id
            )
            if current is None or current.status != "active":
                raise RepositoryRegistrationConflict("registration is not active")
            if current.repository_type != "local_git":
                raise RepositoryRegistrationConflict("registration identity invalid")
            if current.canonical_root_path != observation.probe.canonical_root_path:
                raise RepositoryRegistrationConflict("registration root changed")
            if current.root_path_identity != repository_registration_identity(
                canonical_root_path=current.canonical_root_path,
                repository_type="local_git",
            ):
                raise RepositoryRegistrationConflict("registration identity invalid")
            existing = await snapshots.get_by_identity(
                uow.session, registration_id, observation.snapshot_identity
            )
            if existing is not None:
                bundle = await snapshots.get_bundle_by_snapshot(
                    uow.session,
                    existing.repository_snapshot_id,
                )
                if (
                    existing.is_clean != (not observation.bundle_entries)
                    or (bundle is None and observation.bundle_identity is not None)
                    or (
                        bundle is not None
                        and bundle.inspection_bundle_identity != observation.bundle_identity
                    )
                ):
                    raise RepositorySnapshotInvariantViolation("bundle_identity_mismatch")
                if bundle is not None:
                    from typing import Literal, cast

                    persisted_entries = await snapshots.list_bundle_entries(
                        uow.session,
                        bundle.inspection_bundle_id,
                    )
                    manifest_items: list[BundleEntryManifestItemV1] = []
                    try:
                        for entry in persisted_entries:
                            entry_kind = cast(EntryKind, entry.entry_kind)
                            file_type = cast(FileType, entry.file_type)
                            file_mode = cast(FileMode, entry.file_mode)
                            content_identity_status: Literal["verified", "not_applicable"] = (
                                "verified"
                                if entry_kind in {"tracked_overlay", "untracked_file"}
                                and file_type != "submodule"
                                else "not_applicable"
                            )
                            entry_identity = repository_inspection_bundle_entry_identity(
                                repository_path=entry.repository_path,
                                original_repository_path=entry.original_repository_path,
                                entry_kind=entry_kind,
                                file_type=file_type,
                                file_mode=file_mode,
                                head_blob_oid=entry.head_blob_oid,
                                index_blob_oid=entry.index_blob_oid,
                                content_hash=entry.content_hash,
                                size_bytes=entry.size_bytes,
                                content_identity_status=content_identity_status,
                            )
                            if entry_identity != entry.entry_identity:
                                raise RepositorySnapshotInvariantViolation(
                                    "bundle_entry_identity_mismatch"
                                )
                            manifest_items.append(
                                BundleEntryManifestItemV1(
                                    entry.repository_path,
                                    entry_kind,
                                    file_type,
                                    entry_identity,
                                )
                            )
                    except RepositoryIdentityInputInvalid as exc:
                        raise RepositorySnapshotInvariantViolation(
                            "bundle_entry_identity_mismatch"
                        ) from exc
                    try:
                        entries_hash = repository_inspection_bundle_entries_manifest_hash(
                            manifest_items
                        )
                    except RepositoryIdentityInputInvalid as exc:
                        raise RepositorySnapshotInvariantViolation(
                            "bundle_entries_manifest_hash_mismatch"
                        ) from exc
                    if entries_hash != bundle.entries_manifest_hash:
                        raise RepositorySnapshotInvariantViolation(
                            "bundle_entries_manifest_hash_mismatch"
                        )
                    try:
                        bundle_identity = repository_inspection_bundle_identity(
                            repository_snapshot_identity=existing.repository_snapshot_identity,
                            base_commit_sha=bundle.base_commit_sha,
                            entries_manifest_hash=entries_hash,
                            bundle_schema_version=bundle.bundle_schema_version,
                            capture_tool_version=bundle.capture_tool_version,
                        )
                    except RepositoryIdentityInputInvalid as exc:
                        raise RepositorySnapshotInvariantViolation(
                            "bundle_identity_mismatch"
                        ) from exc
                    if bundle_identity != bundle.inspection_bundle_identity:
                        raise RepositorySnapshotInvariantViolation("bundle_identity_mismatch")
                return CommandResultEnvelope(
                    outcome_kind="repository_snapshot_reused",
                    primary_result_type="repository_snapshot",
                    primary_result_id=str(existing.repository_snapshot_id),
                    primary_result_identity=existing.repository_snapshot_identity,
                    related_result_refs=(),
                    committed_state="current",
                )
            now = await uow.session.scalar(select(func.clock_timestamp()))
            assert now is not None
            snapshot_id = uuid4()
            sequence = await snapshots.next_sequence(uow.session, registration_id)
            snapshot = RepositorySnapshotRow(
                repository_snapshot_id=snapshot_id,
                repository_registration_id=registration_id,
                snapshot_sequence=sequence,
                captured_at=now,
                canonical_root_path=observation.probe.canonical_root_path,
                head_commit=observation.probe.head_commit,
                current_branch=observation.probe.current_branch,
                is_detached_head=observation.probe.is_detached_head,
                is_clean=not observation.bundle_entries,
                tracked_tree_identity=observation.probe.tracked_tree_identity,
                dirty_worktree_manifest_ref=observation.dirty_manifest_ref,
                dirty_worktree_manifest_hash=observation.dirty_manifest_hash,
                untracked_manifest_ref=observation.untracked_manifest_ref,
                untracked_manifest_hash=observation.untracked_manifest_hash,
                repository_state_digest=observation.state_digest,
                repository_snapshot_identity=observation.snapshot_identity,
                submodule_presence=observation.submodule_presence,
                lfs_pointer_presence=observation.lfs_pointer_presence,
                inspector_compatibility_version=1,
            )
            await snapshots.add_snapshot(uow, snapshot)
            if observation.bundle_entries:
                entries_hash = repository_inspection_bundle_entries_manifest_hash(
                    [
                        BundleEntryManifestItemV1(
                            entry.relative_path,
                            entry.entry_kind,
                            entry.file_type,
                            entry.entry_identity,
                        )
                        for entry in observation.bundle_entries
                    ]
                )
                bundle_id = uuid4()
                bundle = RepositorySnapshotInspectionBundleRow(
                    inspection_bundle_id=bundle_id,
                    repository_snapshot_id=snapshot_id,
                    base_commit_sha=observation.probe.head_commit,
                    bundle_schema_version=1,
                    capture_tool_version=observation.probe.observation_profile_version,
                    entries_manifest_hash=entries_hash,
                    inspection_bundle_identity=observation.bundle_identity,
                    created_at=now,
                )
                rows = tuple(
                    RepositorySnapshotInspectionBundleEntryRow(
                        inspection_bundle_entry_id=uuid4(),
                        inspection_bundle_id=bundle_id,
                        repository_path=entry.relative_path,
                        original_repository_path=entry.original_relative_path,
                        entry_kind=entry.entry_kind,
                        file_type=entry.file_type,
                        file_mode=entry.file_mode,
                        head_blob_oid=entry.head_blob_oid,
                        index_blob_oid=entry.index_blob_oid,
                        content_ref=entry.content_ref,
                        content_hash=entry.content_hash,
                        size_bytes=entry.size_bytes,
                        stable_ordinal=entry.stable_ordinal,
                        entry_identity=entry.entry_identity,
                    )
                    for entry in observation.bundle_entries
                )
                await snapshots.add_bundle(uow, bundle, rows)
            await snapshots.add_freshness(
                uow,
                RepositorySnapshotFreshnessRow(
                    repository_snapshot_id=snapshot_id,
                    status="current",
                    reason_code=None,
                    current_probe_identity=observation.state_digest,
                    probe_version=observation.probe.observation_profile_version,
                    checked_at=now,
                    row_version=1,
                ),
            )
            await append_runtime_event(
                uow,
                build_repository_snapshot_captured_event(
                    context=context,
                    project_id=current.project_id,
                    repository_registration_id=registration_id,
                    repository_snapshot_id=snapshot_id,
                    repository_snapshot_identity=observation.snapshot_identity,
                    repository_state_digest=observation.state_digest,
                    is_clean=snapshot.is_clean,
                    snapshot_sequence=sequence,
                ),
            )
            return CommandResultEnvelope(
                outcome_kind="repository_snapshot_created",
                primary_result_type="repository_snapshot",
                primary_result_id=str(snapshot_id),
                primary_result_identity=observation.snapshot_identity,
                related_result_refs=(f"repository_registration:{registration_id}",),
                committed_state="current",
            )

        return await execute_idempotent_command(
            session_context_factory=self._session_context_factory,
            identity=identity,
            prepare=prepare,
            operation=operation,
        )

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
