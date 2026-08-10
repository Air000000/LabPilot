import asyncio
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from labpilot.context.content_store import ContentAddressedStore
from labpilot.context.repository_identities import (
    RepositoryManifestEntryV1,
    repository_manifest_canonical_bytes,
)
from labpilot.context.repository_types import (
    CapturedBundleEntry,
    GitObject,
    GitStatusEntry,
    GitTreeEntry,
    RepositoryChangeKind,
    RepositoryFileMode,
    RepositoryFileType,
    RepositoryGitCommandFailed,
    RepositoryGitCommandTimeout,
    RepositoryGitExecutableInvalid,
    RepositoryGitObjectTooLarge,
    RepositoryGitObjectTotalLimitExceeded,
    RepositoryGitObjectUnavailable,
    RepositoryGitOutputLimitExceeded,
    RepositoryHeadUnavailable,
    RepositoryNotGit,
    RepositoryProbe,
    RepositorySnapshotCaptureConflict,
    RepositorySnapshotInspectionBundleUnsupportedEntry,
    RepositorySnapshotObservation,
    StableStatToken,
)

OBSERVATION_PROFILE_VERSION = "wp1-a-git-observation:v1"
_SAFE_GIT_ENVIRONMENT = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
_URL_PATTERN = re.compile(
    r"(?P<scheme>https?://)(?:[^\s/@]+(?::[^\s/@]*)?@)?"
    r"(?P<host>[^\s/?#]+)(?P<path>/[^\s?#]*)?(?:\?[^\s#]*)?(?:#[^\s]*)?"
)
_OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_RENAME_SCORE_PATTERN = re.compile(r"[RC][0-9]{1,3}")
_ALLOWED_MODES = {"000000", "100644", "100755", "120000", "160000"}
_ALLOWED_STATUS = {".", "M", "A", "D", "T"}
type _GitStatusMode = Literal["000000", "100644", "100755", "120000", "160000"]
_MAX_GIT_METADATA_ENTRIES = 100_000
_LFS_POINTER_MAX_BYTES = 1024
_LFS_OID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_LFS_EXTENSION_KEY_PATTERN = re.compile(r"ext-(0|[1-9][0-9]*)-([A-Za-z0-9_]+)")
_MAX_SIGNED_INT64 = 9223372036854775807


@dataclass(frozen=True, slots=True)
class _GitCommandResult:
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class _AnchoredRepositoryRoot:
    canonical_path: Path
    descriptor: int
    device: int
    inode: int

    @property
    def fd_path(self) -> Path:
        return Path(f"/proc/self/fd/{self.descriptor}")


type _RepositoryRoot = Path | _AnchoredRepositoryRoot


@dataclass(frozen=True, slots=True)
class _StreamLimitExceeded(Exception):
    stream: str
    limit_bytes: int


class GitAdapter:
    def __init__(
        self,
        *,
        git_executable: Path,
        timeout_seconds: int,
        max_stdout_bytes: int = 8 * 1024 * 1024,
        max_stderr_bytes: int = 16 * 1024,
        max_status_entries: int = 10_000,
    ) -> None:
        if (
            not git_executable.is_absolute()
            or not git_executable.is_file()
            or not os.access(git_executable, os.X_OK)
        ):
            raise RepositoryGitExecutableInvalid(git_executable)
        if (
            timeout_seconds < 1
            or max_stdout_bytes < 1
            or max_stderr_bytes < 1
            or max_status_entries < 1
        ):
            raise ValueError("Git runner limits must be positive")
        self._git_executable = git_executable
        self._timeout_seconds = timeout_seconds
        self._max_stdout_bytes = max_stdout_bytes
        self._max_stderr_bytes = max_stderr_bytes
        self._max_status_entries = max_status_entries

    async def probe(self, repository_root: _RepositoryRoot) -> RepositoryProbe:
        if isinstance(repository_root, Path):
            root = await asyncio.to_thread(_anchor_repository_root, repository_root)
            try:
                return await self.probe(root)
            finally:
                os.close(root.descriptor)
        root = repository_root
        await self._validate_repository_layout(root)
        canonical_root = root.canonical_path
        try:
            inside_worktree = _decode_line(
                (
                    await self._run_git(
                        root,
                        ("rev-parse", "--is-inside-work-tree"),
                    )
                ).stdout,
                reason_code="invalid_repository_root",
            )
        except RepositoryGitCommandFailed as exc:
            raise RepositoryNotGit(canonical_root) from exc

        if inside_worktree != "true":
            raise _unsupported("repository_root_mismatch")
        try:
            head_commit = _parse_oid(
                (
                    await self._run_git(
                        root,
                        ("rev-parse", "--verify", "HEAD"),
                    )
                ).stdout.strip()
            )
        except RepositoryGitCommandFailed as exc:
            raise RepositoryHeadUnavailable(canonical_root) from exc

        try:
            branch = _decode_line(
                (
                    await self._run_git(
                        root,
                        ("symbolic-ref", "--quiet", "--short", "HEAD"),
                    )
                ).stdout,
                reason_code="invalid_branch",
            )
        except RepositoryGitCommandFailed as exc:
            if exc.returncode != 1:
                raise
            branch = None

        tracked_tree = _parse_oid(
            (
                await self._run_git(
                    root,
                    ("rev-parse", "HEAD^{tree}"),
                )
            ).stdout.strip()
        )
        status = (
            await self._run_git(
                root,
                (
                    "--no-optional-locks",
                    "-c",
                    "status.renames=copies",
                    "-c",
                    "status.renameLimit=1000",
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--untracked-files=all",
                ),
            )
        ).stdout
        worktree_content_changes = _parse_numstat_content_changes(
            (
                await self._run_git(
                    root,
                    (
                        "--no-optional-locks",
                        "diff",
                        "--no-ext-diff",
                        "--no-renames",
                        "--numstat",
                        "-z",
                        "--",
                    ),
                )
            ).stdout,
            max_entries=self._max_status_entries,
        )
        try:
            remote_origin = _sanitize_remote_url(
                _decode_line(
                    (
                        await self._run_git(
                            root,
                            (
                                "config",
                                "--local",
                                "--no-includes",
                                "--get",
                                "remote.origin.url",
                            ),
                        )
                    ).stdout,
                    reason_code="invalid_remote_origin",
                )
            )
        except RepositoryGitCommandFailed as exc:
            if exc.returncode != 1:
                raise
            remote_origin = None

        try:
            origin_head = _decode_line(
                (
                    await self._run_git(
                        root,
                        ("symbolic-ref", "refs/remotes/origin/HEAD"),
                    )
                ).stdout,
                reason_code="invalid_default_branch",
            )
        except RepositoryGitCommandFailed as exc:
            if exc.returncode not in {1, 128}:
                raise
            default_branch = None
        else:
            prefix = "refs/remotes/origin/"
            if not origin_head.startswith(prefix) or origin_head == prefix:
                raise _unsupported("invalid_default_branch")
            default_branch = origin_head.removeprefix(prefix)

        return RepositoryProbe(
            canonical_root_path=str(canonical_root),
            head_commit=head_commit,
            current_branch=branch,
            is_detached_head=branch is None,
            tracked_tree_identity=tracked_tree,
            status_entries=_parse_porcelain_v2(
                status,
                root.descriptor,
                max_entries=self._max_status_entries,
                worktree_content_changes=worktree_content_changes,
            ),
            remote_origin_sanitized=remote_origin,
            default_branch=default_branch,
            observation_profile_version=OBSERVATION_PROFILE_VERSION,
        )

    async def ls_tree(
        self,
        repository_root: _RepositoryRoot,
        commit: str,
        *,
        max_entries: int,
        max_output_bytes: int,
    ) -> tuple[GitTreeEntry, ...]:
        if max_entries < 1 or max_output_bytes < 1:
            raise ValueError("Git tree limits must be positive")
        if isinstance(repository_root, Path):
            root = await asyncio.to_thread(_anchor_repository_root, repository_root)
            try:
                return await self.ls_tree(
                    root,
                    commit,
                    max_entries=max_entries,
                    max_output_bytes=max_output_bytes,
                )
            finally:
                os.close(root.descriptor)
        root = repository_root
        await self._validate_repository_layout(root)
        commit_oid = _validate_object_oid(commit)
        payload = (
            await self._run_git(
                root,
                ("ls-tree", "-rz", "--full-tree", commit_oid),
                max_stdout_bytes=max_output_bytes,
            )
        ).stdout
        return _parse_ls_tree(payload, max_entries=max_entries)

    async def cat_file_batch(
        self,
        repository_root: _RepositoryRoot,
        object_oids: tuple[str, ...],
        *,
        max_object_bytes: int,
        max_total_bytes: int,
    ) -> tuple[GitObject, ...]:
        if max_object_bytes < 1 or max_total_bytes < 1:
            raise ValueError("Git object limits must be positive")
        if isinstance(repository_root, Path):
            root = await asyncio.to_thread(_anchor_repository_root, repository_root)
            try:
                return await self.cat_file_batch(
                    root,
                    object_oids,
                    max_object_bytes=max_object_bytes,
                    max_total_bytes=max_total_bytes,
                )
            finally:
                os.close(root.descriptor)
        root = repository_root
        await self._validate_repository_layout(root)
        if len(object_oids) > 50_000:
            raise _unsupported("cat_file_batch_entry_limit_exceeded")
        validated_oids = tuple(_validate_object_oid(value) for value in object_oids)
        if not validated_oids:
            return ()
        arguments = _safe_git_arguments(
            root.fd_path,
            ("cat-file", "--batch"),
        )
        process = await asyncio.create_subprocess_exec(
            str(self._git_executable),
            *arguments,
            cwd=str(root.fd_path),
            env=_SAFE_GIT_ENVIRONMENT,
            pass_fds=(root.descriptor,),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        object_task = asyncio.create_task(
            _read_cat_file_objects(
                process.stdout,
                validated_oids,
                max_object_bytes=max_object_bytes,
                max_total_bytes=max_total_bytes,
            )
        )
        stderr_task = asyncio.create_task(
            self._read_bounded(
                process.stderr,
                stream="stderr",
                limit_bytes=self._max_stderr_bytes,
            )
        )
        input_task = asyncio.create_task(
            _write_cat_file_input(
                process.stdin,
                b"".join(f"{value}\n".encode("ascii") for value in validated_oids),
            )
        )
        tasks: tuple[asyncio.Task[Any], ...] = (
            object_task,
            stderr_task,
            input_task,
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                objects, stderr, _ = await asyncio.gather(
                    object_task,
                    stderr_task,
                    input_task,
                )
                returncode = await process.wait()
        except _StreamLimitExceeded as exc:
            await self._stop_process(process)
            await self._cancel_tasks(tasks)
            raise RepositoryGitOutputLimitExceeded(
                "cat-file",
                exc.stream,
                exc.limit_bytes,
            ) from exc
        except TimeoutError as exc:
            await self._stop_process(process)
            await self._cancel_tasks(tasks)
            raise RepositoryGitCommandTimeout(
                "cat-file",
                self._timeout_seconds,
            ) from exc
        except BaseException:
            await self._stop_process(process)
            await self._cancel_tasks(tasks)
            raise

        if returncode != 0:
            raise RepositoryGitCommandFailed(
                "cat-file",
                returncode,
                _sanitize_stderr(stderr),
            )
        return objects

    async def _validate_repository_layout(self, root: _AnchoredRepositoryRoot) -> None:
        try:
            git_descriptor = os.open(
                ".git",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=root.descriptor,
            )
        except OSError as exc:
            raise _unsupported("repository_git_dir_escape") from exc
        try:
            _validate_git_metadata_tree(git_descriptor)
            unsupported_paths = (
                (("commondir",), "repository_git_commondir_unsupported"),
                (("objects", "info", "alternates"), "repository_git_alternates_unsupported"),
                (
                    ("objects", "info", "http-alternates"),
                    "repository_git_alternates_unsupported",
                ),
            )
            for components, reason_code in unsupported_paths:
                if _git_metadata_path_exists(git_descriptor, components, reason_code):
                    raise _unsupported(reason_code)
        finally:
            os.close(git_descriptor)

        try:
            config_names = (
                await self._run_git(
                    root,
                    (
                        "config",
                        "--local",
                        "--no-includes",
                        "--name-only",
                        "--get-regexp",
                        ".*",
                    ),
                )
            ).stdout
        except RepositoryGitCommandFailed as exc:
            if exc.returncode != 1:
                raise
        else:
            try:
                names = (line.decode("utf-8") for line in config_names.splitlines())
                for name in names:
                    folded_name = name.casefold()
                    if folded_name == "include.path" or (
                        folded_name.startswith("includeif.") and folded_name.endswith(".path")
                    ):
                        raise _unsupported("repository_git_config_include_unsupported")
            except UnicodeDecodeError as exc:
                raise _unsupported("repository_git_config_include_unsupported") from exc

        metadata_paths = (
            (
                ("rev-parse", "--path-format=relative", "--git-dir"),
                "repository_git_dir_escape",
                ".git",
            ),
            (
                ("rev-parse", "--path-format=relative", "--git-common-dir"),
                "repository_git_common_dir_escape",
                ".git",
            ),
            (
                ("rev-parse", "--path-format=relative", "--git-path", "objects"),
                "repository_git_object_dir_escape",
                ".git/objects",
            ),
        )
        for arguments, reason_code, expected in metadata_paths:
            raw_path = (await self._run_git(root, arguments)).stdout
            if _decode_line(raw_path, reason_code=reason_code) != expected:
                raise _unsupported(reason_code)

    async def _run_git(
        self,
        repository_root: _RepositoryRoot,
        arguments: tuple[str, ...],
        *,
        max_stdout_bytes: int | None = None,
    ) -> _GitCommandResult:
        if isinstance(repository_root, Path):
            root = await asyncio.to_thread(_anchor_repository_root, repository_root)
            try:
                return await self._run_git(
                    root,
                    arguments,
                    max_stdout_bytes=max_stdout_bytes,
                )
            finally:
                os.close(root.descriptor)
        root = repository_root
        command_name = arguments[0]
        safe_arguments = _safe_git_arguments(root.fd_path, arguments)
        process = await asyncio.create_subprocess_exec(
            str(self._git_executable),
            *safe_arguments,
            cwd=str(root.fd_path),
            env=_SAFE_GIT_ENVIRONMENT,
            pass_fds=(root.descriptor,),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(
            self._read_bounded(
                process.stdout,
                stream="stdout",
                limit_bytes=max_stdout_bytes or self._max_stdout_bytes,
            )
        )
        stderr_task = asyncio.create_task(
            self._read_bounded(
                process.stderr,
                stream="stderr",
                limit_bytes=self._max_stderr_bytes,
            )
        )
        reader_tasks = (stdout_task, stderr_task)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                stdout, stderr = await asyncio.gather(*reader_tasks)
                returncode = await process.wait()
        except _StreamLimitExceeded as exc:
            await self._stop_process(process)
            await self._cancel_tasks(reader_tasks)
            raise RepositoryGitOutputLimitExceeded(
                command_name,
                exc.stream,
                exc.limit_bytes,
            ) from exc
        except TimeoutError as exc:
            await self._stop_process(process)
            await self._cancel_tasks(reader_tasks)
            raise RepositoryGitCommandTimeout(
                command_name,
                self._timeout_seconds,
            ) from exc
        except BaseException:
            await self._stop_process(process)
            await self._cancel_tasks(reader_tasks)
            raise

        if returncode != 0:
            raise RepositoryGitCommandFailed(
                command_name,
                returncode,
                _sanitize_stderr(stderr),
            )
        return _GitCommandResult(stdout=stdout, stderr=stderr)

    @staticmethod
    async def _read_bounded(
        stream_reader: asyncio.StreamReader,
        *,
        stream: str,
        limit_bytes: int,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while chunk := await stream_reader.read(min(64 * 1024, limit_bytes - total + 1)):
            total += len(chunk)
            if total > limit_bytes:
                raise _StreamLimitExceeded(stream, limit_bytes)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            await process.wait()
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    @staticmethod
    async def _cancel_tasks(
        tasks: tuple[asyncio.Task[Any], ...],
    ) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def capture_snapshot(
        self,
        repository_root: _RepositoryRoot,
        *,
        repository_registration_id: UUID,
        content_store: ContentAddressedStore,
        max_entries: int = 2048,
        max_file_bytes: int = 1024 * 1024,
        max_total_bytes: int = 64 * 1024 * 1024,
    ) -> RepositorySnapshotObservation:
        """Capture all mutable inputs before the caller starts a database UoW."""
        from hashlib import sha256

        from labpilot.context.repository_identities import (
            BundleEntryManifestItemV1,
            dirty_worktree_manifest_hash,
            repository_inspection_bundle_entries_manifest_hash,
            repository_inspection_bundle_entry_identity,
            repository_inspection_bundle_identity,
            repository_snapshot_identity,
            repository_state_digest,
            untracked_manifest_hash,
        )
        from labpilot.context.repository_types import (
            RepositorySnapshotCaptureConflict,
        )

        if max_entries < 1 or max_file_bytes < 1 or max_total_bytes < 1:
            raise ValueError("snapshot capture limits must be positive")
        if isinstance(repository_root, Path):
            anchored_root = await asyncio.to_thread(_anchor_repository_root, repository_root)
            try:
                return await self.capture_snapshot(
                    anchored_root,
                    repository_registration_id=repository_registration_id,
                    content_store=content_store,
                    max_entries=max_entries,
                    max_file_bytes=max_file_bytes,
                    max_total_bytes=max_total_bytes,
                )
            finally:
                os.close(anchored_root.descriptor)
        anchored_root = repository_root
        before = await self.probe(anchored_root)
        if len(before.status_entries) > max_entries:
            raise _unsupported("snapshot_entry_limit_exceeded")
        root_descriptor = os.dup(anchored_root.descriptor)
        try:
            entries: list[CapturedBundleEntry] = []
            manifest_entries: list[RepositoryManifestEntryV1] = []
            captured_paths: list[tuple[str, StableStatToken]] = []
            total_bytes = 0
            lfs_pointer_presence = False
            for ordinal, status_entry in enumerate(before.status_entries):
                content_ref: str | None = None
                content_hash: str | None = None
                size_bytes = 0
                stable_token: StableStatToken | None = None
                identity_status: Literal["verified", "not_applicable"] = "not_applicable"
                if status_entry.file_type == "submodule":
                    pass
                elif status_entry.change_kind == "mode_change":
                    content, stable_token = await asyncio.to_thread(
                        _read_snapshot_path,
                        root_descriptor,
                        status_entry.relative_path,
                        status_entry.file_type,
                        max_file_bytes,
                    )
                    if total_bytes + len(content) > max_total_bytes:
                        raise _unsupported("snapshot_content_limit_exceeded")
                    total_bytes += len(content)
                    if not _matches_git_blob_oid(content, status_entry.index_blob_oid):
                        raise RepositorySnapshotCaptureConflict("mode_change_content_changed")
                    assert stable_token is not None
                    captured_paths.append((status_entry.relative_path, stable_token))
                elif status_entry.change_kind in {"tracked_overlay", "untracked_file"}:
                    content, stable_token = await asyncio.to_thread(
                        _read_snapshot_path,
                        root_descriptor,
                        status_entry.relative_path,
                        status_entry.file_type,
                        max_file_bytes,
                    )
                    if (
                        len(content) > max_file_bytes
                        or total_bytes + len(content) > max_total_bytes
                    ):
                        raise _unsupported("snapshot_content_limit_exceeded")
                    total_bytes += len(content)
                    content_ref = await content_store.put_bytes(content)
                    content_hash = sha256(content).hexdigest()
                    await content_store.verify(content_ref, content_hash)
                    size_bytes = len(content)
                    identity_status = "verified"
                    assert stable_token is not None
                    captured_paths.append((status_entry.relative_path, stable_token))
                    lfs_pointer_presence = lfs_pointer_presence or (
                        status_entry.file_type == "regular" and _is_lfs_pointer(content)
                    )
                manifest = RepositoryManifestEntryV1(
                    status_entry.relative_path,
                    status_entry.original_relative_path,
                    status_entry.change_kind,
                    status_entry.file_type,
                    status_entry.file_mode,
                    status_entry.head_blob_oid,
                    status_entry.index_blob_oid,
                    content_hash,
                    size_bytes,
                    identity_status,
                )
                manifest_entries.append(manifest)
                entry_identity = repository_inspection_bundle_entry_identity(
                    repository_path=status_entry.relative_path,
                    original_repository_path=status_entry.original_relative_path,
                    entry_kind=status_entry.change_kind,
                    file_type=status_entry.file_type,
                    file_mode=status_entry.file_mode,
                    head_blob_oid=status_entry.head_blob_oid,
                    index_blob_oid=status_entry.index_blob_oid,
                    content_hash=content_hash,
                    size_bytes=size_bytes,
                    content_identity_status=identity_status,
                )
                entries.append(
                    CapturedBundleEntry(
                        status_entry.relative_path,
                        status_entry.original_relative_path,
                        status_entry.change_kind,
                        status_entry.file_type,
                        status_entry.file_mode,
                        status_entry.head_blob_oid,
                        status_entry.index_blob_oid,
                        content_ref,
                        content_hash,
                        size_bytes,
                        ordinal,
                        entry_identity,
                        stable_token,
                    )
                )
            dirty = [entry for entry in manifest_entries if entry.entry_kind != "untracked_file"]
            untracked = [
                entry for entry in manifest_entries if entry.entry_kind == "untracked_file"
            ]
            dirty_hash = dirty_worktree_manifest_hash(dirty) if dirty else None
            untracked_hash = untracked_manifest_hash(untracked) if untracked else None
            dirty_ref = await _put_manifest(content_store, dirty, "dirty") if dirty else None
            untracked_ref = (
                await _put_manifest(content_store, untracked, "untracked") if untracked else None
            )
            after = await self.probe(anchored_root)
            if (
                before.canonical_root_path,
                before.head_commit,
                before.tracked_tree_identity,
                before.status_entries,
            ) != (
                after.canonical_root_path,
                after.head_commit,
                after.tracked_tree_identity,
                after.status_entries,
            ):
                raise RepositorySnapshotCaptureConflict("probe_changed")
            for relative_path, token in captured_paths:
                if (
                    await asyncio.to_thread(_snapshot_path_token, root_descriptor, relative_path)
                    != token
                ):
                    raise RepositorySnapshotCaptureConflict("captured_path_changed")
        finally:
            os.close(root_descriptor)
        is_clean = not manifest_entries
        state_digest = repository_state_digest(
            head_commit=before.head_commit,
            tracked_tree_identity=before.tracked_tree_identity,
            is_clean=is_clean,
            dirty_worktree_manifest_hash=dirty_hash,
            untracked_manifest_hash=untracked_hash,
        )
        snapshot_identity = repository_snapshot_identity(
            repository_registration_id=repository_registration_id,
            repository_state_digest=state_digest,
            current_branch=before.current_branch,
            is_detached_head=before.is_detached_head,
        )
        bundle_identity = None
        if entries:
            entries_hash = repository_inspection_bundle_entries_manifest_hash(
                [
                    BundleEntryManifestItemV1(
                        entry.relative_path, entry.entry_kind, entry.file_type, entry.entry_identity
                    )
                    for entry in entries
                ]
            )
            bundle_identity = repository_inspection_bundle_identity(
                repository_snapshot_identity=snapshot_identity,
                base_commit_sha=before.head_commit,
                entries_manifest_hash=entries_hash,
                bundle_schema_version=1,
                capture_tool_version=OBSERVATION_PROFILE_VERSION,
            )
        tree = await self.ls_tree(
            anchored_root,
            before.head_commit,
            max_entries=max_entries,
            max_output_bytes=self._max_stdout_bytes,
        )
        replaced_or_deleted_paths = {
            entry.relative_path
            for entry in before.status_entries
            if entry.change_kind in {"tracked_overlay", "deletion"}
        }
        submodule_presence = any(
            entry.change_kind == "tracked_overlay" and entry.file_type == "submodule"
            for entry in before.status_entries
        ) or any(
            entry.file_type == "submodule" and entry.relative_path not in replaced_or_deleted_paths
            for entry in tree
        )
        if not lfs_pointer_presence:
            from labpilot.context.repository_types import RepositoryGitObjectTooLarge

            for entry in tree:
                if entry.file_type != "regular" or entry.relative_path in replaced_or_deleted_paths:
                    continue
                try:
                    (blob,) = await self.cat_file_batch(
                        anchored_root,
                        (entry.object_oid,),
                        max_object_bytes=1024,
                        max_total_bytes=1024,
                    )
                except RepositoryGitObjectTooLarge:
                    continue
                if _is_lfs_pointer(blob.content):
                    lfs_pointer_presence = True
                    break
        await asyncio.to_thread(_validate_repository_root_binding, anchored_root)
        return RepositorySnapshotObservation(
            before,
            dirty_ref,
            dirty_hash,
            untracked_ref,
            untracked_hash,
            state_digest,
            snapshot_identity,
            tuple(entries),
            bundle_identity,
            submodule_presence,
            lfs_pointer_presence,
        )


def _validate_git_metadata_tree(git_descriptor: int) -> None:
    entry_count = 0

    def walk(descriptor: int) -> None:
        nonlocal entry_count
        with os.scandir(descriptor) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_GIT_METADATA_ENTRIES:
                    raise _unsupported("repository_git_metadata_entry_limit_exceeded")
                entry_mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(entry_mode):
                    raise _unsupported("repository_git_metadata_symlink_unsupported")
                if stat.S_ISDIR(entry_mode):
                    child = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    try:
                        walk(child)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(entry_mode):
                    raise _unsupported("repository_git_metadata_special_file_unsupported")

    try:
        walk(git_descriptor)
    except OSError as exc:
        raise _unsupported("repository_git_metadata_special_file_unsupported") from exc


def _git_metadata_path_exists(
    git_descriptor: int,
    components: tuple[str, ...],
    reason_code: str,
) -> bool:
    descriptor = os.dup(git_descriptor)
    try:
        for component in components[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        try:
            os.stat(components[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True
    except OSError as exc:
        raise _unsupported(reason_code) from exc
    finally:
        os.close(descriptor)


def _safe_git_arguments(
    repository_root: Path,
    arguments: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"core.worktree={repository_root}",
        "-c",
        "core.excludesFile=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.hooksPath=/dev/null",
        *arguments,
    )


async def _write_cat_file_input(
    writer: asyncio.StreamWriter,
    payload: bytes,
) -> None:
    try:
        writer.write(payload)
        await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def _read_cat_file_objects(
    reader: asyncio.StreamReader,
    expected_oids: tuple[str, ...],
    *,
    max_object_bytes: int,
    max_total_bytes: int,
) -> tuple[GitObject, ...]:
    objects: list[GitObject] = []
    total_bytes = 0
    for expected_oid in expected_oids:
        try:
            header = await reader.readline()
        except ValueError as exc:
            raise _unsupported("invalid_cat_file_header") from exc
        if not header.endswith(b"\n") or len(header) > 200:
            raise _unsupported("invalid_cat_file_header")
        fields = header[:-1].split(b" ")
        if len(fields) == 2 and fields[1] == b"missing":
            returned_oid = _parse_oid(fields[0])
            raise RepositoryGitObjectUnavailable(returned_oid)
        if len(fields) != 3:
            raise _unsupported("invalid_cat_file_header")
        returned_oid = _parse_oid(fields[0])
        if returned_oid != expected_oid:
            raise _unsupported("cat_file_oid_mismatch")
        object_type = _decode_ascii(fields[1], "invalid_cat_file_type")
        if object_type not in {"blob", "tree", "commit", "tag"}:
            raise _unsupported("invalid_cat_file_type")
        size_text = _decode_ascii(fields[2], "invalid_cat_file_size")
        if not size_text.isdigit():
            raise _unsupported("invalid_cat_file_size")
        size_bytes = int(size_text)
        if size_bytes > max_object_bytes:
            raise RepositoryGitObjectTooLarge(
                returned_oid,
                size_bytes,
                max_object_bytes,
            )
        total_bytes += size_bytes
        if total_bytes > max_total_bytes:
            raise RepositoryGitObjectTotalLimitExceeded(
                total_bytes,
                max_total_bytes,
            )
        try:
            framed_content = await reader.readexactly(size_bytes + 1)
        except asyncio.IncompleteReadError as exc:
            raise _unsupported("truncated_cat_file_object") from exc
        if not framed_content.endswith(b"\n"):
            raise _unsupported("invalid_cat_file_framing")
        objects.append(
            GitObject(
                oid=returned_oid,
                object_type=cast(Any, object_type),
                content=framed_content[:-1],
            )
        )
    if await reader.read(1):
        raise _unsupported("unexpected_cat_file_output")
    return tuple(objects)


def _parse_ls_tree(
    payload: bytes,
    *,
    max_entries: int,
) -> tuple[GitTreeEntry, ...]:
    if payload and not payload.endswith(b"\0"):
        raise _unsupported("truncated_ls_tree_record")
    records = payload.split(b"\0")[:-1] if payload else []
    if len(records) > max_entries:
        raise _unsupported("ls_tree_entry_limit_exceeded")
    entries: list[GitTreeEntry] = []
    seen_paths: set[str] = set()
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
        except ValueError as exc:
            raise _unsupported("invalid_ls_tree_record") from exc
        fields = metadata.split(b" ")
        if len(fields) != 3:
            raise _unsupported("invalid_ls_tree_record")
        mode = _parse_mode(fields[0])
        object_type = _decode_ascii(fields[1], "invalid_ls_tree_type")
        object_oid = _parse_oid(fields[2])
        relative_path = _parse_relative_path(raw_path)
        if relative_path in seen_paths:
            raise _unsupported("duplicate_ls_tree_path")
        seen_paths.add(relative_path)
        if mode == "160000" and object_type == "commit":
            file_type = "submodule"
        elif mode == "120000" and object_type == "blob":
            file_type = "symlink"
        elif mode in {"100644", "100755"} and object_type == "blob":
            file_type = "regular"
        else:
            raise _unsupported("invalid_ls_tree_mode_type")
        entries.append(
            GitTreeEntry(
                relative_path=relative_path,
                file_type=cast(Any, file_type),
                file_mode=mode,
                object_type=cast(Any, object_type),
                object_oid=object_oid,
            )
        )
    return tuple(entries)


def _validate_object_oid(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _unsupported("invalid_oid") from exc
    return _parse_oid(encoded)


def _parse_porcelain_v2(
    payload: bytes,
    repository_root: int,
    *,
    max_entries: int = 10_000,
    worktree_content_changes: frozenset[str] = frozenset(),
) -> tuple[GitStatusEntry, ...]:
    if payload and not payload.endswith(b"\0"):
        raise _unsupported("truncated_status_record")
    records = payload.split(b"\0")[:-1] if payload else []
    entries: list[GitStatusEntry] = []
    record_index = 0
    while record_index < len(records):
        record = records[record_index]
        if not record:
            raise _unsupported("empty_status_record")
        if record.startswith(b"1 "):
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                raise _unsupported("malformed_ordinary_status")
            entries.append(
                _parse_ordinary_status(
                    fields,
                    repository_root,
                    worktree_content_changes=worktree_content_changes,
                )
            )
        elif record.startswith(b"2 "):
            fields = record.split(b" ", 9)
            if len(fields) != 10:
                raise _unsupported("malformed_rename_status")
            record_index += 1
            if record_index >= len(records):
                raise _unsupported("truncated_rename_status")
            entries.extend(
                _parse_rename_or_copy_status(
                    fields,
                    records[record_index],
                    repository_root,
                )
            )
        elif record.startswith(b"? "):
            entries.append(_parse_untracked_status(record[2:], repository_root))
        elif record.startswith(b"u "):
            raise _unsupported("unmerged_status")
        else:
            raise _unsupported("unknown_status_record")
        if len(entries) > max_entries:
            raise _unsupported("status_entry_limit_exceeded")
        record_index += 1
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                entry.relative_path.encode("utf-8"),
                entry.change_kind,
            ),
        )
    )


def _parse_ordinary_status(
    fields: list[bytes],
    repository_root: int,
    *,
    worktree_content_changes: frozenset[str],
) -> GitStatusEntry:
    (
        _,
        raw_xy,
        raw_submodule,
        raw_head_mode,
        raw_index_mode,
        raw_worktree_mode,
        raw_head_oid,
        raw_index_oid,
        raw_path,
    ) = fields
    xy = _decode_ascii(raw_xy, "invalid_status")
    if len(xy) != 2 or any(value not in _ALLOWED_STATUS for value in xy):
        raise _unsupported("invalid_status")
    _validate_submodule(raw_submodule)
    head_mode = _parse_mode(raw_head_mode)
    index_mode = _parse_mode(raw_index_mode)
    worktree_mode = _parse_mode(raw_worktree_mode)
    head_oid = _parse_optional_oid(raw_head_oid)
    index_oid = _parse_optional_oid(raw_index_oid)
    relative_path = _parse_relative_path(raw_path)
    deleted = "D" in xy
    file_mode = _select_file_mode(head_mode, index_mode, worktree_mode)
    file_type = _tracked_file_type(
        repository_root,
        relative_path,
        file_mode=file_mode,
        deleted=deleted,
    )
    change_kind: RepositoryChangeKind
    if deleted:
        change_kind = "deletion"
    elif (
        head_oid == index_oid
        and len({head_mode, index_mode, worktree_mode} - {"000000"}) > 1
        and relative_path not in worktree_content_changes
    ):
        change_kind = "mode_change"
    else:
        change_kind = "tracked_overlay"
    return GitStatusEntry(
        relative_path=relative_path,
        original_relative_path=None,
        change_kind=change_kind,
        index_status=xy[0],
        worktree_status=xy[1],
        file_type=file_type,
        file_mode=file_mode,
        head_blob_oid=head_oid,
        index_blob_oid=index_oid,
        is_untracked=False,
    )


def _parse_numstat_content_changes(
    payload: bytes,
    *,
    max_entries: int,
) -> frozenset[str]:
    if payload and not payload.endswith(b"\0"):
        raise _unsupported("truncated_numstat_record")
    records = payload.split(b"\0")[:-1] if payload else []
    if len(records) > max_entries:
        raise _unsupported("status_entry_limit_exceeded")
    changed_paths: set[str] = set()
    for record in records:
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            raise _unsupported("malformed_numstat_record")
        additions, deletions, raw_path = fields
        if not raw_path or not all(
            value == b"-" or (value.isdigit() and value) for value in (additions, deletions)
        ):
            raise _unsupported("malformed_numstat_record")
        if additions != b"0" or deletions != b"0":
            changed_paths.add(_parse_relative_path(raw_path))
    return frozenset(changed_paths)


def _parse_rename_or_copy_status(
    fields: list[bytes],
    raw_original_path: bytes,
    repository_root: int,
) -> tuple[GitStatusEntry, ...]:
    (
        _,
        raw_xy,
        raw_submodule,
        raw_head_mode,
        raw_index_mode,
        raw_worktree_mode,
        raw_head_oid,
        raw_index_oid,
        raw_score,
        raw_path,
    ) = fields
    xy = _decode_ascii(raw_xy, "invalid_status")
    if len(xy) != 2 or xy[0] not in {"R", "C"} or xy[1] not in _ALLOWED_STATUS:
        raise _unsupported("invalid_rename_status")
    _validate_submodule(raw_submodule)
    head_mode = _parse_mode(raw_head_mode)
    index_mode = _parse_mode(raw_index_mode)
    worktree_mode = _parse_mode(raw_worktree_mode)
    head_oid = _parse_optional_oid(raw_head_oid)
    index_oid = _parse_optional_oid(raw_index_oid)
    score = _decode_ascii(raw_score, "invalid_rename_score")
    if not _RENAME_SCORE_PATTERN.fullmatch(score) or score[0] != xy[0] or int(score[1:]) > 100:
        raise _unsupported("invalid_rename_score")
    relative_path = _parse_relative_path(raw_path)
    original_relative_path = _parse_relative_path(raw_original_path)
    file_mode = _select_file_mode(head_mode, index_mode, worktree_mode)
    file_type = _tracked_file_type(
        repository_root,
        relative_path,
        file_mode=file_mode,
        deleted=False,
    )
    destination = GitStatusEntry(
        relative_path=relative_path,
        original_relative_path=original_relative_path,
        change_kind="tracked_overlay",
        index_status=xy[0],
        worktree_status=xy[1],
        file_type=file_type,
        file_mode=file_mode,
        head_blob_oid=head_oid,
        index_blob_oid=index_oid,
        is_untracked=False,
    )
    if xy[0] == "C":
        return (destination,)

    original_mode = _select_file_mode(head_mode, "000000", "000000")
    deletion = GitStatusEntry(
        relative_path=original_relative_path,
        original_relative_path=None,
        change_kind="deletion",
        index_status="D",
        worktree_status=".",
        file_type=_tracked_file_type(
            repository_root,
            original_relative_path,
            file_mode=original_mode,
            deleted=True,
        ),
        file_mode=original_mode,
        head_blob_oid=head_oid,
        index_blob_oid=None,
        is_untracked=False,
    )
    return deletion, destination


def _parse_untracked_status(raw_path: bytes, repository_root: int) -> GitStatusEntry:
    relative_path = _parse_relative_path(raw_path)
    try:
        metadata = _snapshot_path_metadata(repository_root, relative_path)
    except OSError as exc:
        raise _unsupported("untracked_path_unavailable") from exc
    file_mode: RepositoryFileMode
    file_type: RepositoryFileType
    if stat.S_ISLNK(metadata.st_mode):
        file_type = "symlink"
        file_mode = "120000"
    elif stat.S_ISREG(metadata.st_mode):
        file_type = "regular"
        file_mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
    else:
        raise _unsupported("unsupported_untracked_file_type")
    return GitStatusEntry(
        relative_path=relative_path,
        original_relative_path=None,
        change_kind="untracked_file",
        index_status="?",
        worktree_status="?",
        file_type=file_type,
        file_mode=file_mode,
        head_blob_oid=None,
        index_blob_oid=None,
        is_untracked=True,
    )


def _tracked_file_type(
    repository_root: int,
    relative_path: str,
    *,
    file_mode: str,
    deleted: bool,
) -> RepositoryFileType:
    if file_mode == "160000":
        return "submodule"
    expected: RepositoryFileType = "symlink" if file_mode == "120000" else "regular"
    if deleted:
        return expected
    try:
        metadata = _snapshot_path_metadata(repository_root, relative_path)
    except OSError as exc:
        raise _unsupported("tracked_path_unavailable") from exc
    observed: RepositoryFileType = "symlink" if stat.S_ISLNK(metadata.st_mode) else "regular"
    if observed != expected or not (
        stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
    ):
        raise _unsupported("tracked_file_type_mismatch")
    return observed


def _parse_relative_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unsupported("non_utf8_path") from exc
    components = path.split("/")
    if (
        not path
        or path.startswith("/")
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise _unsupported("unsafe_relative_path")
    return path


def _parse_mode(value: bytes) -> _GitStatusMode:
    mode = _decode_ascii(value, "invalid_mode")
    if mode not in _ALLOWED_MODES:
        raise _unsupported("invalid_mode")
    return cast(_GitStatusMode, mode)


def _select_file_mode(
    head_mode: _GitStatusMode,
    index_mode: _GitStatusMode,
    worktree_mode: _GitStatusMode,
) -> RepositoryFileMode:
    for mode in (worktree_mode, index_mode, head_mode):
        if mode != "000000":
            return mode
    raise _unsupported("missing_file_mode")


def _validate_submodule(value: bytes) -> None:
    submodule = _decode_ascii(value, "invalid_submodule_field")
    if submodule != "N..." and not (
        len(submodule) == 4
        and submodule[0] == "S"
        and submodule[1] in ".C"
        and submodule[2] in ".M"
        and submodule[3] in ".U"
    ):
        raise _unsupported("invalid_submodule_field")


def _parse_oid(value: bytes) -> str:
    oid = _decode_ascii(value, "invalid_oid")
    if not _OID_PATTERN.fullmatch(oid):
        raise _unsupported("invalid_oid")
    return oid


def _parse_optional_oid(value: bytes) -> str | None:
    oid = _decode_ascii(value, "invalid_oid")
    if oid and set(oid) == {"0"} and len(oid) in {40, 64}:
        return None
    if not _OID_PATTERN.fullmatch(oid):
        raise _unsupported("invalid_oid")
    return oid


def _decode_line(value: bytes, *, reason_code: str) -> str:
    try:
        return value.rstrip(b"\n").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unsupported(reason_code) from exc


def _decode_ascii(value: bytes, reason_code: str) -> str:
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _unsupported(reason_code) from exc


def _unsupported(reason_code: str) -> RepositorySnapshotInspectionBundleUnsupportedEntry:
    return RepositorySnapshotInspectionBundleUnsupportedEntry(reason_code)


def _sanitize_remote_url(value: str) -> str:
    if any(unicodedata.category(character)[0] == "C" for character in value):
        raise _unsupported("invalid_remote_origin")
    if "://" in value:
        parsed = urlsplit(value)
        if not parsed.scheme:
            raise _unsupported("invalid_remote_origin")
        hostname = parsed.hostname
        if hostname is None:
            netloc = ""
        else:
            netloc = f"[{hostname}]" if ":" in hostname else hostname
            try:
                port = parsed.port
            except ValueError as exc:
                raise _unsupported("invalid_remote_origin") from exc
            if port is not None:
                netloc = f"{netloc}:{port}"
        sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    else:
        without_fragment = value.split("#", 1)[0].split("?", 1)[0]
        if ":" in without_fragment:
            host, path = without_fragment.split(":", 1)
            sanitized = f"{host.rsplit('@', 1)[-1]}:{path}"
        else:
            sanitized = without_fragment
    if not sanitized or len(sanitized.encode("utf-8")) > 2048:
        raise _unsupported("invalid_remote_origin")
    return sanitized


def _sanitize_stderr(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace")
    text = _URL_PATTERN.sub(
        lambda match: (f"{match.group('scheme')}{match.group('host')}{match.group('path') or ''}"),
        text,
    )
    text = "".join(character for character in text if unicodedata.category(character)[0] != "C")
    text = " ".join(text.split())
    return text.encode("utf-8")[:512].decode("utf-8", errors="ignore")


def _stat_token_from_metadata(metadata: os.stat_result) -> StableStatToken:
    if stat.S_ISREG(metadata.st_mode):
        file_type = "regular"
    elif stat.S_ISLNK(metadata.st_mode):
        file_type = "symlink"
    else:
        raise _unsupported("unsupported_snapshot_file_type")
    return StableStatToken(
        metadata.st_ino,
        file_type,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_components(relative_path: str) -> tuple[str, ...]:
    components = tuple(relative_path.split("/"))
    if not components or any(component in {"", ".", ".."} for component in components):
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable")
    return components


def _open_canonical_snapshot_root(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    if root.anchor != "/":
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable")
    descriptor = os.open("/", flags)
    try:
        for component in root.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError as exc:
        os.close(descriptor)
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable") from exc
    return descriptor


def _anchor_repository_root(root: Path) -> _AnchoredRepositoryRoot:
    canonical_path = Path(os.path.abspath(root))
    descriptor = _open_canonical_snapshot_root(canonical_path)
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable")
    return _AnchoredRepositoryRoot(
        canonical_path,
        descriptor,
        metadata.st_dev,
        metadata.st_ino,
    )


def _validate_repository_root_binding(root: _AnchoredRepositoryRoot) -> None:
    descriptor = _open_canonical_snapshot_root(root.canonical_path)
    try:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != (root.device, root.inode):
            raise RepositorySnapshotCaptureConflict("repository_root_changed")
    finally:
        os.close(descriptor)


def _open_snapshot_parent(root_descriptor: int, components: tuple[str, ...]) -> tuple[int, str]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError:
        os.close(descriptor)
        raise
    return descriptor, components[-1]


def _snapshot_path_metadata(root_descriptor: int, relative_path: str) -> os.stat_result:
    parent, name = _open_snapshot_parent(
        root_descriptor,
        _snapshot_components(relative_path),
    )
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def _snapshot_path_token(root_descriptor: int, relative_path: str) -> StableStatToken:
    try:
        parent, name = _open_snapshot_parent(root_descriptor, _snapshot_components(relative_path))
        try:
            return _stat_token_from_metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
        finally:
            os.close(parent)
    except OSError as exc:
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable") from exc


def _read_snapshot_path(
    root_descriptor: int,
    relative_path: str,
    expected_file_type: str,
    max_file_bytes: int,
) -> tuple[bytes, StableStatToken]:
    try:
        parent, name = _open_snapshot_parent(root_descriptor, _snapshot_components(relative_path))
    except OSError as exc:
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable") from exc
    try:
        before = _stat_token_from_metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
        if before.file_type != expected_file_type:
            raise RepositorySnapshotCaptureConflict("snapshot_file_type_changed")
        if before.file_type == "symlink":
            target = os.readlink(name, dir_fd=parent)
            after = _stat_token_from_metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
            if before != after:
                raise RepositorySnapshotCaptureConflict("symlink_changed")
            return unicodedata.normalize("NFC", target).encode("utf-8", "strict"), before
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        try:
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(descriptor, min(64 * 1024, max_file_bytes - total + 1)):
                total += len(chunk)
                if total > max_file_bytes:
                    raise _unsupported("snapshot_single_file_limit_exceeded")
                chunks.append(chunk)
            after = _stat_token_from_metadata(os.fstat(descriptor))
        finally:
            os.close(descriptor)
        if before != after:
            raise RepositorySnapshotCaptureConflict("regular_file_changed")
        return b"".join(chunks), before
    except OSError as exc:
        raise RepositorySnapshotCaptureConflict("snapshot_path_unavailable") from exc
    finally:
        os.close(parent)


def _matches_git_blob_oid(content: bytes, expected_oid: str | None) -> bool:
    from hashlib import sha1, sha256

    if expected_oid is None:
        raise RepositorySnapshotCaptureConflict("mode_change_content_changed")
    payload = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    if len(expected_oid) == 40:
        actual_oid = sha1(payload, usedforsecurity=False).hexdigest()
    elif len(expected_oid) == 64:
        actual_oid = sha256(payload).hexdigest()
    else:
        raise RepositorySnapshotCaptureConflict("mode_change_content_changed")
    return actual_oid == expected_oid


async def _put_manifest(
    content_store: ContentAddressedStore,
    entries: list[RepositoryManifestEntryV1],
    kind: Literal["dirty", "untracked"],
) -> str:
    return await content_store.put_bytes(repository_manifest_canonical_bytes(entries, kind=kind))


def _is_lfs_pointer(value: bytes) -> bool:
    if len(value) >= _LFS_POINTER_MAX_BYTES:
        return False
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not value.endswith(b"\n"):
        return False
    lines = text[:-1].split("\n")
    if len(lines) < 3:
        return False

    def split_line(line: str) -> tuple[str, str] | None:
        if line.count(" ") != 1:
            return None
        key, separator, field_value = line.partition(" ")
        if not separator or not key or not field_value:
            return None
        return key, field_value

    first = split_line(lines[0])
    if first != ("version", "https://git-lfs.github.com/spec/v1"):
        return False

    index = 1
    priorities: set[int] = set()
    previous_priority = -1
    while index < len(lines):
        field = split_line(lines[index])
        if field is None:
            return False
        key, field_value = field
        extension = _LFS_EXTENSION_KEY_PATTERN.fullmatch(key)
        if extension is None:
            break
        priority = int(extension.group(1))
        if (
            priority > _MAX_SIGNED_INT64
            or priority in priorities
            or priority <= previous_priority
            or _LFS_OID_PATTERN.fullmatch(field_value) is None
        ):
            return False
        priorities.add(priority)
        previous_priority = priority
        index += 1

    if len(lines) != index + 2:
        return False
    oid = split_line(lines[index])
    size = split_line(lines[index + 1])
    if oid is None or size is None or oid[0] != "oid" or size[0] != "size":
        return False
    if _LFS_OID_PATTERN.fullmatch(oid[1]) is None:
        return False
    size_value = size[1]
    return (
        size_value == "0"
        or (size_value[:1] in "123456789" and size_value.isascii() and size_value.isdecimal())
    ) and int(size_value) <= _MAX_SIGNED_INT64
