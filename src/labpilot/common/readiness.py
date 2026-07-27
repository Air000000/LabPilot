from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    database: str
    migration: str
    detail: str | None


class ReadinessProbe(Protocol):
    async def check(self) -> ReadinessResult: ...


class UnconfiguredReadinessProbe:
    async def check(self) -> ReadinessResult:
        return ReadinessResult(
            ready=False,
            database="unknown",
            migration="unknown",
            detail="database readiness probe is not configured",
        )
