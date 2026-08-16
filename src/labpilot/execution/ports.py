from pathlib import Path
from typing import Protocol
from uuid import UUID

from labpilot.execution.types import SourceManifest, VersionExecutionSemantics


class ExecutionInputError(Exception):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class VersionExecutionSemanticsPort(Protocol):
    async def load(self, experiment_version_id: UUID) -> VersionExecutionSemantics:
        raise NotImplementedError


class SnapshotMaterializationPort(Protocol):
    async def materialize_exact_source(
        self,
        *,
        repository_snapshot_id: UUID,
        expected_repository_snapshot_identity: str,
        destination: Path,
    ) -> SourceManifest:
        raise NotImplementedError
