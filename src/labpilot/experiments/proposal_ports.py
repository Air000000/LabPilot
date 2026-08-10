from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProposalSnapshotState:
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    project_id: UUID
    is_clean: bool


@dataclass(frozen=True, slots=True)
class ApprovalSourceState:
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    project_id: UUID
    ready: bool
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class ProposalPathFact:
    relative_path: str
    file_type: str
    file_mode: str


class ProposalRepositoryFactPort(Protocol):
    async def snapshot_state(self, repository_snapshot_id: UUID) -> ProposalSnapshotState: ...

    async def approval_source_state(
        self, repository_snapshot_id: UUID
    ) -> ApprovalSourceState: ...

    async def path_fact(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> ProposalPathFact | None: ...

    async def directory_exists(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> bool: ...
