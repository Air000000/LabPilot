from dataclasses import dataclass
from uuid import UUID

from labpilot.common.hashing import namespaced_sha256

_PROJECT_IDENTITY_NAMESPACE = "labpilot-project:v1"


def _utf8_size(value: str, *, field: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc


def _require_text(value: str, *, field: str, max_bytes: int) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    if _utf8_size(value, field=field) > max_bytes:
        raise ValueError(f"{field} exceeds {max_bytes} UTF-8 bytes")


@dataclass(frozen=True, slots=True)
class CreateProject:
    name: str
    description: str | None
    artifact_root_ref: str

    def __post_init__(self) -> None:
        _require_text(self.name, field="name", max_bytes=200)
        if self.description is not None and _utf8_size(
            self.description,
            field="description",
        ) > 8 * 1024:
            raise ValueError("description exceeds 8192 UTF-8 bytes")
        _require_text(
            self.artifact_root_ref,
            field="artifact_root_ref",
            max_bytes=1000,
        )


@dataclass(frozen=True, slots=True)
class ArchiveProject:
    project_id: UUID
    expected_row_version: int

    def __post_init__(self) -> None:
        if self.expected_row_version <= 0:
            raise ValueError("expected_row_version must be positive")


def project_identity(project_id: UUID) -> str:
    return namespaced_sha256(
        _PROJECT_IDENTITY_NAMESPACE,
        {
            "project_id": str(project_id),
            "identity_schema_version": 1,
        },
    )
