from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

RepositoryChangeKind = Literal[
    "tracked_overlay",
    "untracked_file",
    "deletion",
    "mode_change",
]
RepositoryFileType = Literal["regular", "symlink", "submodule"]
RepositoryFileMode = Literal["100644", "100755", "120000", "160000"]


class RepositoryPathError(Exception):
    error_code = "repository_path_error"
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(str(path))

    def __str__(self) -> str:
        return f"{self.error_code}: {self.path}"


class RepositoryAllowedRootUnavailable(RepositoryPathError):
    error_code = "repository_allowed_root_unavailable"


class RepositoryAllowedRootNotDirectory(RepositoryPathError):
    error_code = "repository_allowed_root_not_directory"


class RepositoryPathMustBeAbsolute(RepositoryPathError):
    error_code = "repository_path_must_be_absolute"


class RepositoryPathOutsideAllowedRoot(RepositoryPathError):
    error_code = "repository_path_outside_allowed_root"


class RepositoryPathTraversalEscape(RepositoryPathError):
    error_code = "repository_path_traversal_escape"


class RepositorySymlinkEscape(RepositoryPathError):
    error_code = "repository_symlink_escape"
    __slots__ = ("resolved_path",)

    def __init__(self, path: Path, resolved_path: Path) -> None:
        self.resolved_path = resolved_path
        super().__init__(path)


class RepositoryUnavailable(RepositoryPathError):
    error_code = "repository_unavailable"


class RepositoryNotDirectory(RepositoryPathError):
    error_code = "repository_not_directory"


class RepositoryNotGit(RepositoryPathError):
    error_code = "repository_not_git"


class RepositoryHeadUnavailable(RepositoryPathError):
    error_code = "repository_head_unavailable"


class RepositoryGitExecutableInvalid(Exception):
    error_code = "repository_git_executable_invalid"
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(str(path))

    def __str__(self) -> str:
        return f"{self.error_code}: {self.path}"


class RepositoryGitCommandTimeout(Exception):
    error_code = "repository_git_command_timeout"
    __slots__ = ("command_name", "timeout_seconds")

    def __init__(self, command_name: str, timeout_seconds: int) -> None:
        self.command_name = command_name
        self.timeout_seconds = timeout_seconds
        super().__init__(command_name)

    def __str__(self) -> str:
        return f"{self.error_code}: {self.command_name}"


class RepositoryGitOutputLimitExceeded(Exception):
    error_code = "repository_git_output_limit_exceeded"
    __slots__ = ("command_name", "limit_bytes", "stream")

    def __init__(self, command_name: str, stream: str, limit_bytes: int) -> None:
        self.command_name = command_name
        self.stream = stream
        self.limit_bytes = limit_bytes
        super().__init__(command_name)

    def __str__(self) -> str:
        return f"{self.error_code}: {self.command_name} {self.stream}"


class RepositoryGitCommandFailed(Exception):
    error_code = "repository_git_command_failed"
    __slots__ = ("command_name", "returncode", "stderr_summary")

    def __init__(
        self,
        command_name: str,
        returncode: int,
        stderr_summary: str,
    ) -> None:
        self.command_name = command_name
        self.returncode = returncode
        self.stderr_summary = stderr_summary
        super().__init__(command_name)

    def __str__(self) -> str:
        return f"{self.error_code}: {self.command_name} exited {self.returncode}"


class RepositorySnapshotInspectionBundleUnsupportedEntry(Exception):
    error_code = "repository_snapshot_inspection_bundle_unsupported_entry"
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)

    def __str__(self) -> str:
        return f"{self.error_code}: {self.reason_code}"


class RepositoryGitObjectUnavailable(Exception):
    error_code = "repository_git_object_unavailable"
    __slots__ = ("object_oid",)

    def __init__(self, object_oid: str) -> None:
        self.object_oid = object_oid
        super().__init__(object_oid)


class RepositoryGitObjectTooLarge(Exception):
    error_code = "repository_git_object_too_large"
    __slots__ = ("limit_bytes", "object_oid", "size_bytes")

    def __init__(
        self,
        object_oid: str,
        size_bytes: int,
        limit_bytes: int,
    ) -> None:
        self.object_oid = object_oid
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(object_oid)


class RepositoryGitObjectTotalLimitExceeded(Exception):
    error_code = "repository_git_object_total_limit_exceeded"
    __slots__ = ("limit_bytes", "total_bytes")

    def __init__(self, total_bytes: int, limit_bytes: int) -> None:
        self.total_bytes = total_bytes
        self.limit_bytes = limit_bytes
        super().__init__(str(total_bytes))


@dataclass(frozen=True, slots=True)
class GitTreeEntry:
    relative_path: str
    file_type: RepositoryFileType
    file_mode: str
    object_type: Literal["blob", "commit"]
    object_oid: str


@dataclass(frozen=True, slots=True)
class GitObject:
    oid: str
    object_type: Literal["blob", "tree", "commit", "tag"]
    content: bytes


@dataclass(frozen=True, slots=True)
class GitStatusEntry:
    relative_path: str
    original_relative_path: str | None
    change_kind: RepositoryChangeKind
    index_status: str
    worktree_status: str
    file_type: RepositoryFileType
    file_mode: RepositoryFileMode
    head_blob_oid: str | None
    index_blob_oid: str | None
    is_untracked: bool

    def __post_init__(self) -> None:
        if self.change_kind not in {
            "tracked_overlay",
            "untracked_file",
            "deletion",
            "mode_change",
        }:
            raise ValueError("unsupported repository change kind")
        if self.file_type not in {"regular", "symlink", "submodule"}:
            raise ValueError("unsupported repository file type")


@dataclass(frozen=True, slots=True)
class RepositoryProbe:
    canonical_root_path: str
    head_commit: str
    current_branch: str | None
    is_detached_head: bool
    tracked_tree_identity: str
    status_entries: tuple[GitStatusEntry, ...]
    remote_origin_sanitized: str | None
    default_branch: str | None
    observation_profile_version: str


@dataclass(frozen=True, slots=True)
class RegisterLocalRepository:
    project_id: UUID
    user_path: Path
    expected_project_row_version: int


@dataclass(frozen=True, slots=True)
class CheckRepositoryAvailability:
    repository_registration_id: UUID
    expected_row_version: int


@dataclass(frozen=True, slots=True)
class RevokeRepositoryRegistration:
    repository_registration_id: UUID
    expected_row_version: int
    expected_project_row_version: int


@dataclass(frozen=True, slots=True)
class CaptureRepositorySnapshot:
    repository_registration_id: UUID


class RepositorySnapshotCaptureConflict(Exception):
    error_code = "repository_snapshot_capture_conflict"


class RepositorySnapshotInvariantViolation(Exception):
    error_code = "repository_snapshot_invariant_violation"


@dataclass(frozen=True, slots=True)
class StableStatToken:
    inode: int
    file_type: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class CapturedBundleEntry:
    relative_path: str
    original_relative_path: str | None
    entry_kind: RepositoryChangeKind
    file_type: RepositoryFileType
    file_mode: RepositoryFileMode
    head_blob_oid: str | None
    index_blob_oid: str | None
    content_ref: str | None
    content_hash: str | None
    size_bytes: int
    stable_ordinal: int
    entry_identity: str
    stable_stat_token: StableStatToken | None


@dataclass(frozen=True, slots=True)
class RepositorySnapshotObservation:
    probe: RepositoryProbe
    dirty_manifest_ref: str | None
    dirty_manifest_hash: str | None
    untracked_manifest_ref: str | None
    untracked_manifest_hash: str | None
    state_digest: str
    snapshot_identity: str
    bundle_entries: tuple[CapturedBundleEntry, ...]
    bundle_identity: str | None
    submodule_presence: bool
    lfs_pointer_presence: bool
