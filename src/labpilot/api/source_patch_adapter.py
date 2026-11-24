"""SQL qualification and read-only draft export; never prepares a candidate."""

import asyncio
import json
from uuid import UUID

from labpilot.agent_runtime.repository import SqlAgentRepository
from labpilot.agent_runtime.source_patch import (
    MAX_PATCH_BYTES,
    MAX_PATCH_SOURCE_LINES,
    SourcePatchContext,
    SourcePatchInput,
)
from labpilot.agent_runtime.types import AgentRuntimeError, source_patch_bytes
from labpilot.context.models import RepositoryRegistrationRow, RepositorySnapshotRow
from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.context.source_candidate_check import protected_parts
from labpilot.db.uow import SessionContextFactory
from labpilot.experiments.models import ProjectRow


async def load_source_patch(
    sessions: SessionContextFactory,
    source: RepositorySnapshotSourceService,
    project_id: UUID,
    snapshot_id: UUID,
    pinned: SourcePatchContext | None,
) -> SourcePatchInput:
    async with sessions() as session:
        snapshot = await session.get(RepositorySnapshotRow, snapshot_id)
        project = await session.get(ProjectRow, project_id)
        registration = (
            await session.get(RepositoryRegistrationRow, snapshot.repository_registration_id)
            if snapshot is not None
            else None
        )
        if (
            snapshot is None
            or project is None
            or registration is None
            or registration.project_id != project_id
            or not snapshot.is_clean
        ):
            raise AgentRuntimeError("source_patch_source_invalid")
        if (
            project.status != "active"
            or registration.status != "active"
            or project.primary_repository_registration_id != registration.repository_registration_id
        ):
            raise AgentRuntimeError("source_patch_source_not_active")
        identity = snapshot.repository_snapshot_identity
    try:
        readiness = await source.check_approval_readiness(snapshot_id)
        if readiness.status != "ready" or readiness.repository_snapshot_identity != identity:
            raise AgentRuntimeError("source_patch_source_changed")
        fact = await source.path_fact(snapshot_id, "train.py")
        if (
            fact is None
            or fact.file_type != "regular"
            or fact.file_mode not in {"100644", "100755"}
        ):
            raise AgentRuntimeError("source_patch_source_invalid")
        text = await source.read_text(
            snapshot_id,
            "train.py",
            start_line=1,
            end_line=MAX_PATCH_SOURCE_LINES,
            max_bytes=MAX_PATCH_BYTES,
        )
        tail = await source.read_text(
            snapshot_id,
            "train.py",
            start_line=MAX_PATCH_SOURCE_LINES + 1,
            end_line=MAX_PATCH_SOURCE_LINES + 1,
            max_bytes=MAX_PATCH_BYTES,
        )
        if tail.text:
            raise AgentRuntimeError("source_patch_source_invalid")
        data = text.text.encode("utf-8", "strict")
        # Existing bounded layout/compile check; never imports or executes source.
        protected_parts(data)
        from hashlib import sha256

        context = SourcePatchContext(
            base_snapshot_identity=identity, train_py_sha256=sha256(data).hexdigest()
        )
        if pinned is not None and context != pinned:
            raise AgentRuntimeError("source_patch_source_changed")
        return SourcePatchInput(context=context, train_py=text.text)
    except AgentRuntimeError:
        raise
    except Exception:
        # Repository/Git/parser errors are data-boundary failures, never raw diagnostics.
        raise AgentRuntimeError("source_patch_source_invalid") from None


async def export_agent_patch(sessions: SessionContextFactory, run_id: UUID) -> bytes:
    repository = SqlAgentRepository(sessions)
    run = await repository.get_run(run_id)
    if run is None:
        raise AgentRuntimeError("source_patch_not_ready")
    data = source_patch_bytes(run, await repository.list_steps(run_id))
    context = SourcePatchContext.model_validate(dict(run.source_patch_context or {}))
    async with sessions() as session:
        snapshot = await session.get(RepositorySnapshotRow, run.repository_snapshot_id)
        registration = (
            await session.get(RepositoryRegistrationRow, snapshot.repository_registration_id)
            if snapshot is not None
            else None
        )
        if (
            snapshot is None
            or registration is None
            or registration.project_id != run.project_id
            or snapshot.repository_snapshot_identity != context.base_snapshot_identity
        ):
            raise AgentRuntimeError("source_patch_context_invalid")
    return data


async def normalize_agent_patch(
    sessions: SessionContextFactory,
    source: RepositorySnapshotSourceService,
    run_id: UUID,
) -> tuple[bytes, dict[str, str]]:
    from labpilot.context.source_candidates import CandidateError
    from labpilot.context.source_patch_normalization import normalize_patch

    data = await export_agent_patch(sessions, run_id)
    repository = SqlAgentRepository(sessions)
    run = await repository.get_run(run_id)
    if run is None:
        raise AgentRuntimeError("source_patch_not_ready")
    steps = await repository.list_steps(run_id)
    if source_patch_bytes(run, steps) != data:
        raise AgentRuntimeError("source_patch_context_invalid")
    pinned = SourcePatchContext.model_validate(dict(run.source_patch_context or {}))
    baseline = await load_source_patch(
        sessions, source, run.project_id, run.repository_snapshot_id, pinned
    )
    canonical, receipt = await asyncio.to_thread(
        normalize_patch, baseline.train_py.encode("utf-8"), data
    )
    receipt.update(
        status="derived_patch_not_submitted",
        agent_run_id=str(run.agent_run_id),
        agent_step_id=str(steps[-1].agent_step_id),
        repository_snapshot_id=str(run.repository_snapshot_id),
        repository_snapshot_identity=pinned.base_snapshot_identity,
    )
    if len(json.dumps(receipt).encode("utf-8")) > 4096:
        raise CandidateError("patch_normalization_failed")
    return canonical, receipt
