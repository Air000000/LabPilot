import asyncio
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from labpilot.context.repository_types import (
    GitObject,
    GitStatusEntry,
    GitTreeEntry,
    RepositoryChangeKind,
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
    RepositorySnapshotInspectionBundleUnsupportedEntry,
)

OBSERVATION_PROFILE_VERSION = "wp1-a-git-observation:v1"
_SAFE_GIT_ENVIRONMENT = {
    "GIT_CONFIG_NOSYSTEM": "1",
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


@dataclass(frozen=True, slots=True)
class _GitCommandResult:
    stdout: bytes
    stderr: bytes


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

    async def probe(self, repository_root: Path) -> RepositoryProbe:
        canonical_root = repository_root.resolve(strict=True)
        try:
            top_level = _decode_line(
                (
                    await self._run_git(
                        canonical_root,
                        ("rev-parse", "--show-toplevel"),
                    )
                ).stdout,
                reason_code="invalid_repository_root",
            )
        except RepositoryGitCommandFailed as exc:
            raise RepositoryNotGit(canonical_root) from exc

        observed_root = Path(top_level).resolve(strict=True)
        if observed_root != canonical_root:
            raise _unsupported("repository_root_mismatch")
        try:
            head_commit = _parse_oid(
                (
                    await self._run_git(
                        canonical_root,
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
                        canonical_root,
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
                    canonical_root,
                    ("rev-parse", "HEAD^{tree}"),
                )
            ).stdout.strip()
        )
        status = (
            await self._run_git(
                canonical_root,
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
        try:
            remote_origin = _sanitize_remote_url(
                _decode_line(
                    (
                        await self._run_git(
                            canonical_root,
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
                        canonical_root,
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
                canonical_root,
                max_entries=self._max_status_entries,
            ),
            remote_origin_sanitized=remote_origin,
            default_branch=default_branch,
            observation_profile_version=OBSERVATION_PROFILE_VERSION,
        )

    async def ls_tree(
        self,
        repository_root: Path,
        commit: str,
        *,
        max_entries: int,
        max_output_bytes: int,
    ) -> tuple[GitTreeEntry, ...]:
        if max_entries < 1 or max_output_bytes < 1:
            raise ValueError("Git tree limits must be positive")
        commit_oid = _validate_object_oid(commit)
        payload = (
            await self._run_git(
                repository_root,
                ("ls-tree", "-rz", "--full-tree", commit_oid),
                max_stdout_bytes=max_output_bytes,
            )
        ).stdout
        return _parse_ls_tree(payload, max_entries=max_entries)

    async def cat_file_batch(
        self,
        repository_root: Path,
        object_oids: tuple[str, ...],
        *,
        max_object_bytes: int,
        max_total_bytes: int,
    ) -> tuple[GitObject, ...]:
        if max_object_bytes < 1 or max_total_bytes < 1:
            raise ValueError("Git object limits must be positive")
        if len(object_oids) > 50_000:
            raise _unsupported("cat_file_batch_entry_limit_exceeded")
        validated_oids = tuple(_validate_object_oid(value) for value in object_oids)
        if not validated_oids:
            return ()
        canonical_root = repository_root.resolve(strict=True)
        arguments = _safe_git_arguments(
            canonical_root,
            ("cat-file", "--batch"),
        )
        process = await asyncio.create_subprocess_exec(
            str(self._git_executable),
            *arguments,
            cwd=str(canonical_root),
            env=_SAFE_GIT_ENVIRONMENT,
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

    async def _run_git(
        self,
        repository_root: Path,
        arguments: tuple[str, ...],
        *,
        max_stdout_bytes: int | None = None,
    ) -> _GitCommandResult:
        command_name = arguments[0]
        canonical_root = repository_root.resolve(strict=True)
        safe_arguments = _safe_git_arguments(canonical_root, arguments)
        process = await asyncio.create_subprocess_exec(
            str(self._git_executable),
            *safe_arguments,
            cwd=str(canonical_root),
            env=_SAFE_GIT_ENVIRONMENT,
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
    repository_root: Path,
    *,
    max_entries: int = 10_000,
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
            entries.append(_parse_ordinary_status(fields, repository_root))
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
    repository_root: Path,
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
    elif head_oid == index_oid and len({head_mode, index_mode, worktree_mode} - {"000000"}) > 1:
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


def _parse_rename_or_copy_status(
    fields: list[bytes],
    raw_original_path: bytes,
    repository_root: Path,
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


def _parse_untracked_status(raw_path: bytes, repository_root: Path) -> GitStatusEntry:
    relative_path = _parse_relative_path(raw_path)
    try:
        metadata = os.lstat(repository_root / relative_path)
    except OSError as exc:
        raise _unsupported("untracked_path_unavailable") from exc
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
    repository_root: Path,
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
        metadata = os.lstat(repository_root / relative_path)
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


def _parse_mode(value: bytes) -> str:
    mode = _decode_ascii(value, "invalid_mode")
    if mode not in _ALLOWED_MODES:
        raise _unsupported("invalid_mode")
    return mode


def _select_file_mode(head_mode: str, index_mode: str, worktree_mode: str) -> str:
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
