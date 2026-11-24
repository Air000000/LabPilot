"""Human-only source adoption: read source evidence, atomically record a fact."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from labpilot.common.canonical_json import CanonicalizationError, CanonicalObject
from labpilot.common.commands import CommandContext
from labpilot.common.errors import LabPilotError
from labpilot.common.hashing import namespaced_sha256
from labpilot.context.models import (
    RepositoryRegistrationRow,
    RepositorySnapshotRow,
    SourceAdoptionRow,
)
from labpilot.context.project_source_candidates import (
    LIMITS,
    ProjectSourceCandidateService,
    ProjectSourceRequest,
)
from labpilot.context.source_candidates import (
    Digest,
    Ref,
    SourceCandidateService,
    SourceChangeRequest,
)
from labpilot.db.idempotency import (
    CommandResultEnvelope,
    IdempotentCommandOutcome,
    execute_idempotent_command,
    prepare_command_identity,
    prepare_project_scope,
)
from labpilot.db.uow import SessionContextFactory, UnitOfWork
from labpilot.experiments.models import ProjectRow
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.payloads import (
    EventJsonObject,
    prepare_runtime_event_payload,
    register_runtime_event_payload_schema,
)


class AdoptionError(LabPilotError):
    """Fixed reason codes, with no paths, source text or secret-bearing errors."""


class AdoptSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    package_ref: Ref
    target_snapshot_id: UUID
    target_snapshot_identity: Digest


def _validate_event(payload: EventJsonObject) -> None:
    if set(payload) != {"source_adoption_id", "adoption_identity", "target_snapshot_id"}:
        raise CanonicalizationError("source adoption event fields")
    try:
        UUID(str(payload["source_adoption_id"]))
        UUID(str(payload["target_snapshot_id"]))
        if re.fullmatch(r"[0-9a-f]{64}", str(payload["adoption_identity"])) is None:
            raise ValueError("identity")
    except ValueError as exc:
        raise CanonicalizationError("source adoption event identity") from exc


register_runtime_event_payload_schema("source.adopted", 1, _validate_event)


class SourceAdoptionService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        candidates: SourceCandidateService | ProjectSourceCandidateService,
    ) -> None:
        self._sessions = session_context_factory
        self._candidates = candidates

    async def _bindings(
        self,
        session: AsyncSession,
        command: AdoptSource,
        request: SourceChangeRequest | ProjectSourceRequest,
        *,
        lock: bool,
    ) -> tuple[RepositorySnapshotRow, RepositorySnapshotRow]:
        query = select(ProjectRow).where(ProjectRow.project_id == command.project_id)
        project = await session.scalar(query.with_for_update() if lock else query)
        if (
            project is None
            or project.status != "active"
            or request.project_id != command.project_id
        ):
            raise AdoptionError("adoption_project_mismatch")
        query_reg = select(RepositoryRegistrationRow).where(
            RepositoryRegistrationRow.repository_registration_id
            == project.primary_repository_registration_id
        )
        registration = await session.scalar(query_reg.with_for_update() if lock else query_reg)
        if (
            registration is None
            or registration.status != "active"
            or registration.project_id != command.project_id
        ):
            raise AdoptionError("adoption_registration_mismatch")
        base = await session.get(RepositorySnapshotRow, request.snapshot_id)
        target = await session.get(RepositorySnapshotRow, command.target_snapshot_id)
        if (
            base is None
            or target is None
            or base.repository_snapshot_id == target.repository_snapshot_id
            or base.repository_registration_id != registration.repository_registration_id
            or target.repository_registration_id != registration.repository_registration_id
            or base.repository_snapshot_identity != request.snapshot_identity
            or target.repository_snapshot_identity != command.target_snapshot_identity
            or not base.is_clean
            or not target.is_clean
        ):
            raise AdoptionError("adoption_snapshot_mismatch")
        return base, target

    async def adopt(
        self,
        *,
        context: CommandContext,
        idempotency_key: str,
        command: AdoptSource,
    ) -> IdempotentCommandOutcome:
        if context.actor.actor_type != "user" or context.actor.actor_id is None:
            raise AdoptionError("adoption_user_required")
        identity = prepare_command_identity(
            scope_identity=prepare_project_scope(context, project_id=command.project_id),
            command_name="AdoptSource",
            command_version=1,
            idempotency_key=idempotency_key,
            normalized_request=cast(CanonicalObject, command.model_dump(mode="json")),
        )

        async def prepare() -> tuple[SourceChangeRequest | ProjectSourceRequest, str]:
            request, manifest = await self._candidates.adoption_evidence(command.package_ref)
            async with self._sessions() as session:
                base, target = await self._bindings(session, command, request, lock=False)
            source = self._candidates.source
            if await source.commit_parents(command.target_snapshot_id) != (base.head_commit,):
                raise AdoptionError("adoption_parent_mismatch")
            limits = (
                LIMITS
                if isinstance(request, ProjectSourceRequest)
                else (128, 8 * 1024**2, 8 * 1024**2)
            )
            with TemporaryDirectory(prefix="adopt-", dir=self._candidates.root) as directory:
                materialized = await source.materialize_exact_source(
                    repository_snapshot_id=command.target_snapshot_id,
                    expected_repository_snapshot_identity=command.target_snapshot_identity,
                    destination=Path(directory) / "source",
                    max_entries=limits[0],
                    max_file_bytes=limits[1],
                    max_total_bytes=limits[2],
                )
                expected = sorted(
                    (e.path, e.file_type, e.git_mode, e.content_ref[7:]) for e in manifest.entries
                )
                actual = sorted(
                    (e.relative_path, e.file_type, e.file_mode, e.content_sha256)
                    for e in materialized.entries
                )
                if actual != expected:
                    raise AdoptionError("adoption_content_mismatch")
            readiness = await source.check_approval_readiness(command.target_snapshot_id)
            if (
                readiness.status != "ready"
                or readiness.repository_snapshot_identity != command.target_snapshot_identity
            ):
                raise AdoptionError("adoption_source_not_ready")
            fact_identity = namespaced_sha256(
                "labpilot.source-adoption.v1",
                {
                    **cast(CanonicalObject, command.model_dump(mode="json")),
                    "base_snapshot_id": str(request.snapshot_id),
                    "base_snapshot_identity": request.snapshot_identity,
                },
            )
            return request, fact_identity

        async def operation(
            uow: UnitOfWork, prepared: tuple[SourceChangeRequest | ProjectSourceRequest, str]
        ) -> CommandResultEnvelope:
            request, fact_identity = prepared
            await self._bindings(uow.session, command, request, lock=True)
            row = await uow.session.scalar(
                select(SourceAdoptionRow).where(
                    SourceAdoptionRow.target_snapshot_id == command.target_snapshot_id
                )
            )
            if row is not None and row.adoption_identity != fact_identity:
                raise AdoptionError("adoption_target_conflict")
            if row is None:
                row = SourceAdoptionRow(
                    source_adoption_id=uuid4(),
                    project_id=command.project_id,
                    base_snapshot_id=request.snapshot_id,
                    target_snapshot_id=command.target_snapshot_id,
                    package_ref=command.package_ref,
                    adoption_identity=fact_identity,
                    actor_id=context.actor.actor_id,
                )
                uow.session.add(row)
                await uow.session.flush()
                adoption_id = str(row.source_adoption_id)
                await append_runtime_event(
                    uow,
                    RuntimeEventEnvelope(
                        project_id=command.project_id,
                        aggregate_type="source_adoption",
                        aggregate_id=adoption_id,
                        event_type="source.adopted",
                        event_schema_version=1,
                        payload=prepare_runtime_event_payload(
                            event_type="source.adopted",
                            event_schema_version=1,
                            payload={
                                "source_adoption_id": adoption_id,
                                "adoption_identity": fact_identity,
                                "target_snapshot_id": str(command.target_snapshot_id),
                            },
                        ),
                        occurred_at=None,
                        interaction_correlation_id=context.interaction_correlation_id,
                        workflow_correlation_id=None,
                        causation_id=None,
                        actor_type=context.actor.actor_type,
                        actor_id=context.actor.actor_id,
                        visibility_class="internal",
                        topic="runtime-events",
                        partition_key=f"source_adoption:{adoption_id}",
                    ),
                )
            return CommandResultEnvelope(
                outcome_kind="source_adopted",
                primary_result_type="source_adoption",
                primary_result_id=str(row.source_adoption_id),
                primary_result_identity=fact_identity,
                related_result_refs=(
                    f"repository_snapshot:{request.snapshot_id}",
                    f"repository_snapshot:{command.target_snapshot_id}",
                    command.package_ref,
                ),
                committed_state="recorded",
            )

        return await execute_idempotent_command(
            session_context_factory=self._sessions,
            identity=identity,
            prepare=prepare,
            operation=operation,
        )
