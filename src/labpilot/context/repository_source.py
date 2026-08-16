from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from uuid import UUID

from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.git_adapter import GitAdapter, is_lfs_pointer_blob
from labpilot.context.models import (
    RepositoryRegistrationRow,
    RepositorySnapshotInspectionBundleEntryRow,
    RepositorySnapshotRow,
)
from labpilot.context.repository_identities import (
    PersistedTextInvalid,
    RepositoryIdentityInputInvalid,
    repository_state_digest,
    validate_repository_relative_path,
)
from labpilot.context.repository_repository import (
    RepositoryRegistrationRepository,
    RepositorySnapshotRepository,
)
from labpilot.context.repository_types import (
    RepositoryGitCommandFailed,
    RepositoryGitCommandTimeout,
    RepositoryGitOutputLimitExceeded,
    RepositoryHeadUnavailable,
    RepositoryNotGit,
    RepositorySnapshotCaptureConflict,
    RepositorySnapshotInspectionBundleUnsupportedEntry,
    RepositoryUnavailable,
)
from labpilot.db.uow import SessionContextFactory

RepositorySourceFileType = Literal["regular", "symlink", "submodule"]
ApprovalReadinessStatus = Literal[
    "ready",
    "snapshot_not_found",
    "registration_not_active",
    "snapshot_dirty",
    "repository_unavailable",
    "repository_changed",
]
_MAX_TREE_ENTRIES = 100_000
_MAX_SEARCH_RESULTS = 1_000
_MAX_TREE_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_TEXT_BYTES = 1024 * 1024
_MAX_LINE_RANGE = 500


class RepositorySourceReadError(Exception):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class RepositorySourceDescriptor:
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    repository_registration_id: UUID
    project_id: UUID
    head_commit: str
    current_branch: str | None
    is_detached_head: bool
    is_clean: bool


@dataclass(frozen=True, slots=True)
class RepositoryPathFact:
    relative_path: str
    file_type: RepositorySourceFileType
    file_mode: str


@dataclass(frozen=True, slots=True)
class RepositoryTextSlice:
    relative_path: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True, slots=True)
class RepositorySearchHit:
    relative_path: str
    line_number: int
    line_text: str


@dataclass(frozen=True, slots=True)
class RepositoryApprovalReadiness:
    status: ApprovalReadinessStatus
    repository_snapshot_id: UUID
    repository_snapshot_identity: str | None
    current_probe_identity: str | None


@dataclass(frozen=True, slots=True)
class RepositoryMaterializedEntry:
    relative_path: str
    file_type: str
    file_mode: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class RepositoryMaterialization:
    schema_version: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    entries: tuple[RepositoryMaterializedEntry, ...]


@dataclass(frozen=True, slots=True)
class _SnapshotEntry:
    relative_path: str
    file_type: RepositorySourceFileType
    file_mode: str
    object_oid: str | None
    content_ref: str | None
    content_hash: str | None


@dataclass(frozen=True, slots=True)
class _SnapshotContext:
    snapshot: RepositorySnapshotRow
    registration: RepositoryRegistrationRow
    entries: tuple[_SnapshotEntry, ...]


class RepositorySnapshotSourceService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        git_adapter: GitAdapter,
        content_store: ContentAddressedStore,
    ) -> None:
        self._session_context_factory = session_context_factory
        self._git_adapter = git_adapter
        self._content_store = content_store
        self._snapshots = RepositorySnapshotRepository()
        self._registrations = RepositoryRegistrationRepository()

    async def describe(self, repository_snapshot_id: UUID) -> RepositorySourceDescriptor:
        snapshot, registration = await self._snapshot_and_registration(repository_snapshot_id)
        return RepositorySourceDescriptor(
            repository_snapshot_id=snapshot.repository_snapshot_id,
            repository_snapshot_identity=snapshot.repository_snapshot_identity,
            repository_registration_id=snapshot.repository_registration_id,
            project_id=registration.project_id,
            head_commit=snapshot.head_commit,
            current_branch=snapshot.current_branch,
            is_detached_head=snapshot.is_detached_head,
            is_clean=snapshot.is_clean,
        )

    async def list_tree(
        self,
        repository_snapshot_id: UUID,
        *,
        prefix: str | None,
        max_entries: int,
    ) -> tuple[RepositoryPathFact, ...]:
        if type(max_entries) is not int or max_entries < 1 or max_entries > _MAX_TREE_ENTRIES:
            raise RepositorySourceReadError("max_entries")
        prefix = _validate_prefix(prefix)
        context = await self._snapshot_context(repository_snapshot_id)
        return tuple(
            RepositoryPathFact(entry.relative_path, entry.file_type, entry.file_mode)
            for entry in context.entries
            if _matches_prefix(entry.relative_path, prefix)
        )[:max_entries]

    async def path_fact(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> RepositoryPathFact | None:
        relative_path = _validate_path(relative_path)
        context = await self._snapshot_context(repository_snapshot_id)
        for entry in context.entries:
            if entry.relative_path == relative_path:
                return RepositoryPathFact(entry.relative_path, entry.file_type, entry.file_mode)
        return None

    async def directory_exists(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> bool:
        if relative_path == "":
            await self._snapshot_context(repository_snapshot_id)
            return True
        relative_path = _validate_path(relative_path)
        context = await self._snapshot_context(repository_snapshot_id)
        return any(entry.relative_path.startswith(f"{relative_path}/") for entry in context.entries)

    async def read_text(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
        *,
        start_line: int,
        end_line: int,
        max_bytes: int,
    ) -> RepositoryTextSlice:
        relative_path = _validate_path(relative_path)
        _validate_read_limits(start_line, end_line, max_bytes)
        context = await self._snapshot_context(repository_snapshot_id)
        entry = _entry_for_path(context.entries, relative_path)
        if entry is None:
            raise RepositorySourceReadError("path_not_found")
        if entry.file_type == "submodule":
            raise RepositorySourceReadError("submodule")
        data = await self._entry_bytes(context, entry, max_bytes=max_bytes)
        try:
            text = data.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise RepositorySourceReadError("non_utf8") from exc
        return RepositoryTextSlice(
            relative_path=relative_path,
            start_line=start_line,
            end_line=end_line,
            text="".join(text.splitlines(keepends=True)[start_line - 1 : end_line]),
        )

    async def search_text(
        self,
        repository_snapshot_id: UUID,
        query: str,
        *,
        prefix: str | None,
        max_results: int,
        max_file_bytes: int,
    ) -> tuple[RepositorySearchHit, ...]:
        if type(query) is not str or not query:
            raise RepositorySourceReadError("query")
        try:
            query_bytes = query.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise RepositorySourceReadError("query") from exc
        if type(max_results) is not int or max_results < 1 or max_results > _MAX_SEARCH_RESULTS:
            raise RepositorySourceReadError("max_results")
        if (
            type(max_file_bytes) is not int
            or max_file_bytes < 1
            or max_file_bytes > _MAX_TEXT_BYTES
        ):
            raise RepositorySourceReadError("max_file_bytes")
        prefix = _validate_prefix(prefix)
        context = await self._snapshot_context(repository_snapshot_id)
        hits: list[RepositorySearchHit] = []
        for entry in context.entries:
            if entry.file_type == "submodule" or not _matches_prefix(entry.relative_path, prefix):
                continue
            data = await self._entry_bytes(context, entry, max_bytes=max_file_bytes)
            try:
                text = data.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise RepositorySourceReadError("non_utf8") from exc
            if query_bytes not in data:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    hits.append(RepositorySearchHit(entry.relative_path, number, line))
                    if len(hits) == max_results:
                        return tuple(hits)
        return tuple(hits)

    async def materialize_exact_source(
        self,
        *,
        repository_snapshot_id: UUID,
        expected_repository_snapshot_identity: str,
        destination: Path,
    ) -> RepositoryMaterialization:
        context = await self._snapshot_context(repository_snapshot_id)
        if (
            context.snapshot.repository_snapshot_identity
            != expected_repository_snapshot_identity
        ):
            raise RepositorySourceReadError("REPOSITORY_SNAPSHOT_BINDING_MISMATCH")

        verify_only = destination.exists()
        if verify_only and (destination.is_symlink() or not destination.is_dir()):
            raise RepositorySourceReadError("SOURCE_TAMPERED")
        if not verify_only:
            destination.mkdir(parents=True)

        materialized: list[RepositoryMaterializedEntry] = []
        expected_paths: set[str] = set()
        expected_directories: set[str] = set()
        for entry in context.entries:
            if entry.file_type == "submodule" or entry.file_mode == "160000":
                raise RepositorySourceReadError("SOURCE_SUBMODULE_UNSUPPORTED")
            if (entry.file_type, entry.file_mode) not in {
                ("regular", "100644"),
                ("regular", "100755"),
                ("symlink", "120000"),
            }:
                raise RepositorySourceReadError("SOURCE_MODE_UNSUPPORTED")

            relative_path = _validate_path(entry.relative_path)
            data = await self._entry_bytes(context, entry, max_bytes=(1 << 63) - 1)
            if is_lfs_pointer_blob(data):
                raise RepositorySourceReadError("SOURCE_LFS_UNSUPPORTED")
            target = destination.joinpath(*PurePosixPath(relative_path).parts)
            expected_paths.add(relative_path)
            expected_directories.update(_parent_paths(relative_path))

            if entry.file_type == "symlink":
                link_target = _safe_symlink_target(relative_path, data)
                if verify_only:
                    _verify_symlink(target, link_target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(link_target)
            elif verify_only:
                _verify_regular(target, data, executable=entry.file_mode == "100755")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o755 if entry.file_mode == "100755" else 0o644)

            materialized.append(
                RepositoryMaterializedEntry(
                    relative_path,
                    entry.file_type,
                    entry.file_mode,
                    sha256(data).hexdigest(),
                )
            )

        if verify_only:
            actual_paths, actual_directories = _filesystem_tree(destination)
            if actual_paths != expected_paths or actual_directories != expected_directories:
                raise RepositorySourceReadError("SOURCE_TAMPERED")

        return RepositoryMaterialization(
            "v1",
            repository_snapshot_id,
            expected_repository_snapshot_identity,
            tuple(materialized),
        )

    async def check_approval_readiness(
        self,
        repository_snapshot_id: UUID,
    ) -> RepositoryApprovalReadiness:
        async with self._session_context_factory() as session:
            snapshot = await self._snapshots.get_snapshot(session, repository_snapshot_id)
            if snapshot is None:
                return RepositoryApprovalReadiness(
                    "snapshot_not_found", repository_snapshot_id, None, None
                )
            registration = await self._registrations.get_registration(
                session, snapshot.repository_registration_id
            )
        if registration is None or registration.status != "active":
            return _readiness("registration_not_active", snapshot)
        if not snapshot.is_clean:
            return _readiness("snapshot_dirty", snapshot)
        try:
            probe = await self._git_adapter.probe(Path(registration.canonical_root_path))
        except (
            OSError,
            RepositoryUnavailable,
            RepositoryNotGit,
            RepositoryHeadUnavailable,
            RepositorySnapshotCaptureConflict,
            RepositorySnapshotInspectionBundleUnsupportedEntry,
            RepositoryGitCommandFailed,
            RepositoryGitCommandTimeout,
            RepositoryGitOutputLimitExceeded,
        ):
            return _readiness("repository_unavailable", snapshot)
        current_identity = None
        if not probe.status_entries:
            current_identity = repository_state_digest(
                head_commit=probe.head_commit,
                tracked_tree_identity=probe.tracked_tree_identity,
                is_clean=True,
                dirty_worktree_manifest_hash=None,
                untracked_manifest_hash=None,
            )
        if (
            probe.canonical_root_path == snapshot.canonical_root_path
            and probe.head_commit == snapshot.head_commit
            and probe.tracked_tree_identity == snapshot.tracked_tree_identity
            and probe.current_branch == snapshot.current_branch
            and probe.is_detached_head == snapshot.is_detached_head
            and probe.status_entries == ()
        ):
            return RepositoryApprovalReadiness(
                "ready",
                snapshot.repository_snapshot_id,
                snapshot.repository_snapshot_identity,
                current_identity,
            )
        return RepositoryApprovalReadiness(
            "repository_changed",
            snapshot.repository_snapshot_id,
            snapshot.repository_snapshot_identity,
            current_identity,
        )

    async def _snapshot_and_registration(
        self, repository_snapshot_id: UUID
    ) -> tuple[RepositorySnapshotRow, RepositoryRegistrationRow]:
        async with self._session_context_factory() as session:
            snapshot = await self._snapshots.get_snapshot(session, repository_snapshot_id)
            if snapshot is None:
                raise RepositorySourceReadError("snapshot_not_found")
            registration = await self._registrations.get_registration(
                session, snapshot.repository_registration_id
            )
            if registration is None:
                raise RepositorySourceReadError("registration_not_found")
        return snapshot, registration

    async def _snapshot_context(self, repository_snapshot_id: UUID) -> _SnapshotContext:
        snapshot, registration = await self._snapshot_and_registration(repository_snapshot_id)
        async with self._session_context_factory() as session:
            bundle = await self._snapshots.get_bundle_by_snapshot(session, repository_snapshot_id)
            bundle_entries = (
                await self._snapshots.list_bundle_entries(session, bundle.inspection_bundle_id)
                if bundle is not None
                else []
            )
        base_tree = await self._git_adapter.ls_tree(
            Path(snapshot.canonical_root_path),
            snapshot.head_commit,
            max_entries=_MAX_TREE_ENTRIES,
            max_output_bytes=_MAX_TREE_OUTPUT_BYTES,
        )
        entries = {
            item.relative_path: _SnapshotEntry(
                item.relative_path,
                item.file_type,
                item.file_mode,
                item.object_oid,
                None,
                None,
            )
            for item in base_tree
        }
        for bundle_entry in bundle_entries:
            _apply_bundle_entry(entries, bundle_entry)
        return _SnapshotContext(
            snapshot,
            registration,
            tuple(sorted(entries.values(), key=lambda item: item.relative_path.encode("utf-8"))),
        )

    async def _entry_bytes(
        self, context: _SnapshotContext, entry: _SnapshotEntry, *, max_bytes: int
    ) -> bytes:
        if entry.content_ref is not None:
            if entry.content_hash is None:
                raise RepositorySourceReadError("content_hash")
            await self._content_store.verify(entry.content_ref, entry.content_hash)
            data = await self._content_store.read_bytes(entry.content_ref)
        elif entry.object_oid is not None:
            objects = await self._git_adapter.cat_file_batch(
                Path(context.snapshot.canonical_root_path),
                (entry.object_oid,),
                max_object_bytes=max_bytes,
                max_total_bytes=max_bytes,
            )
            data = objects[0].content
        else:
            raise RepositorySourceReadError("content_unavailable")
        if len(data) > max_bytes:
            raise RepositorySourceReadError("max_bytes")
        return data


def _apply_bundle_entry(
    entries: dict[str, _SnapshotEntry],
    bundle_entry: RepositorySnapshotInspectionBundleEntryRow,
) -> None:
    path = bundle_entry.repository_path
    kind = bundle_entry.entry_kind
    if kind == "deletion":
        entries.pop(path, None)
        return
    if kind == "mode_change":
        previous = entries.get(path)
        if previous is None:
            raise RepositorySourceReadError("mode_change_missing_base")
        entries[path] = _SnapshotEntry(
            path,
            previous.file_type,
            bundle_entry.file_mode,
            previous.object_oid,
            previous.content_ref,
            previous.content_hash,
        )
        return
    if kind not in {"tracked_overlay", "untracked_file"}:
        raise RepositorySourceReadError("bundle_entry_kind")
    entries[path] = _SnapshotEntry(
        path,
        cast(RepositorySourceFileType, bundle_entry.file_type),
        bundle_entry.file_mode,
        None,
        bundle_entry.content_ref,
        bundle_entry.content_hash,
    )


def _entry_for_path(entries: tuple[_SnapshotEntry, ...], path: str) -> _SnapshotEntry | None:
    return next((entry for entry in entries if entry.relative_path == path), None)


def _matches_prefix(path: str, prefix: str | None) -> bool:
    return prefix is None or path == prefix or path.startswith(f"{prefix}/")


def _readiness(
    status: ApprovalReadinessStatus, snapshot: RepositorySnapshotRow
) -> RepositoryApprovalReadiness:
    return RepositoryApprovalReadiness(
        status,
        snapshot.repository_snapshot_id,
        snapshot.repository_snapshot_identity,
        None,
    )


def _validate_path(value: str) -> str:
    try:
        return validate_repository_relative_path(value)
    except (PersistedTextInvalid, RepositoryIdentityInputInvalid) as exc:
        raise RepositorySourceReadError("unsafe_path") from exc


def _validate_prefix(value: str | None) -> str | None:
    return None if value is None else _validate_path(value)


def _validate_read_limits(start_line: int, end_line: int, max_bytes: int) -> None:
    if (
        type(start_line) is not int
        or type(end_line) is not int
        or start_line < 1
        or end_line < start_line
        or end_line - start_line + 1 > _MAX_LINE_RANGE
    ):
        raise RepositorySourceReadError("line_range")
    if type(max_bytes) is not int or max_bytes < 1 or max_bytes > _MAX_TEXT_BYTES:
        raise RepositorySourceReadError("max_bytes")


def _parent_paths(relative_path: str) -> set[str]:
    parent = PurePosixPath(relative_path).parent
    return set() if parent == PurePosixPath(".") else {
        PurePosixPath(*parent.parts[:index]).as_posix()
        for index in range(1, len(parent.parts) + 1)
    }


def _safe_symlink_target(relative_path: str, data: bytes) -> str:
    try:
        target = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise RepositorySourceReadError("SOURCE_SYMLINK_ESCAPE") from exc
    parsed = PurePosixPath(target)
    if not target or parsed.is_absolute():
        raise RepositorySourceReadError("SOURCE_SYMLINK_ESCAPE")
    depth = len(PurePosixPath(relative_path).parent.parts)
    for part in parsed.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                raise RepositorySourceReadError("SOURCE_SYMLINK_ESCAPE")
        else:
            depth += 1
    return target


def _verify_regular(path: Path, expected: bytes, *, executable: bool) -> None:
    try:
        mode = path.lstat().st_mode
        actual = path.read_bytes()
    except OSError as exc:
        raise RepositorySourceReadError("SOURCE_TAMPERED") from exc
    if not stat.S_ISREG(mode) or actual != expected or bool(mode & 0o111) != executable:
        raise RepositorySourceReadError("SOURCE_TAMPERED")


def _verify_symlink(path: Path, expected_target: str) -> None:
    try:
        if not path.is_symlink() or os.readlink(path) != expected_target:
            raise RepositorySourceReadError("SOURCE_TAMPERED")
    except OSError as exc:
        raise RepositorySourceReadError("SOURCE_TAMPERED") from exc


def _filesystem_tree(root: Path) -> tuple[set[str], set[str]]:
    paths: set[str] = set()
    directories: set[str] = set()

    def visit(directory: Path, relative: PurePosixPath) -> None:
        try:
            children = tuple(os.scandir(directory))
        except OSError as exc:
            raise RepositorySourceReadError("SOURCE_TAMPERED") from exc
        for child in children:
            child_relative = relative / child.name
            rendered = child_relative.as_posix()
            if child.is_symlink() or child.is_file(follow_symlinks=False):
                paths.add(rendered)
            elif child.is_dir(follow_symlinks=False):
                directories.add(rendered)
                visit(Path(child.path), child_relative)
            else:
                raise RepositorySourceReadError("SOURCE_TAMPERED")

    visit(root, PurePosixPath())
    return paths, directories
