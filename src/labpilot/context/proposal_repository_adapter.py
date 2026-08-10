from uuid import UUID

from labpilot.context.repository_source import RepositorySnapshotSourceService
from labpilot.experiments.proposal_ports import (
    ApprovalSourceState,
    ProposalPathFact,
    ProposalSnapshotState,
)


class ProposalRepositoryFactAdapter:
    def __init__(self, source: RepositorySnapshotSourceService) -> None:
        self._source = source

    async def snapshot_state(self, repository_snapshot_id: UUID) -> ProposalSnapshotState:
        descriptor = await self._source.describe(repository_snapshot_id)
        return ProposalSnapshotState(
            repository_snapshot_id=descriptor.repository_snapshot_id,
            repository_snapshot_identity=descriptor.repository_snapshot_identity,
            project_id=descriptor.project_id,
            is_clean=descriptor.is_clean,
        )

    async def approval_source_state(self, repository_snapshot_id: UUID) -> ApprovalSourceState:
        snapshot_state = await self.snapshot_state(repository_snapshot_id)
        readiness = await self._source.check_approval_readiness(repository_snapshot_id)
        return ApprovalSourceState(
            repository_snapshot_id=readiness.repository_snapshot_id,
            repository_snapshot_identity=snapshot_state.repository_snapshot_identity,
            project_id=snapshot_state.project_id,
            ready=readiness.status == "ready",
            reason_code=None if readiness.status == "ready" else readiness.status,
        )

    async def path_fact(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> ProposalPathFact | None:
        fact = await self._source.path_fact(repository_snapshot_id, relative_path)
        if fact is None:
            return None
        return ProposalPathFact(fact.relative_path, fact.file_type, fact.file_mode)

    async def directory_exists(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> bool:
        return await self._source.directory_exists(repository_snapshot_id, relative_path)
