from dataclasses import dataclass
from enum import StrEnum


class ValidationVerdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    relative_path: str
    content_sha256: str
    byte_size: int
    media_type: str


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    relative_path: str
    content: bytes
    content_sha256: str
    byte_size: int
    media_type: str


@dataclass(frozen=True, slots=True)
class MissingArtifact:
    relative_path: str


class ResultProcessingDeferred(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    subject: str
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationEvaluation:
    verdict: ValidationVerdict
    reasons: tuple[ValidationFinding, ...]
    warnings: tuple[ValidationFinding, ...]
