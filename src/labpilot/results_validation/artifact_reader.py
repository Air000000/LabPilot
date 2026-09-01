import errno
import os
import stat
from hashlib import sha256
from pathlib import Path

from labpilot.results_validation.types import (
    ArtifactIdentity,
    ArtifactSnapshot,
    MissingArtifact,
    ResultProcessingDeferred,
)

MAX_RESULT_JSON_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_TRANSIENT_ERRNOS = frozenset({errno.EAGAIN, errno.ESTALE})
_DETERMINISTIC_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EISDIR})
type ArtifactReadResult = ArtifactIdentity | ArtifactSnapshot | MissingArtifact


def read_artifact_identity(
    output_root: Path,
    relative_path: str,
    media_type: str,
) -> ArtifactIdentity | MissingArtifact:
    result = _read_artifact(output_root, relative_path, media_type, capture=False)
    assert isinstance(result, ArtifactIdentity | MissingArtifact)
    return result


def capture_result_json_artifact(
    output_root: Path,
    relative_path: str,
    media_type: str,
) -> ArtifactSnapshot | MissingArtifact:
    result = _read_artifact(output_root, relative_path, media_type, capture=True)
    assert isinstance(result, ArtifactSnapshot | MissingArtifact)
    return result


def _read_artifact(
    output_root: Path,
    relative_path: str,
    media_type: str,
    *,
    capture: bool,
) -> ArtifactReadResult:
    components = _relative_components(relative_path)
    root_descriptor = _open_root(output_root)
    descriptor = root_descriptor
    try:
        for component in components[:-1]:
            child = _open_child(
                component,
                descriptor,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                leaf=False,
            )
            assert child is not None
            os.close(descriptor)
            descriptor = child

        leaf = _open_child(
            components[-1],
            descriptor,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            leaf=True,
        )
        if leaf is None:
            return MissingArtifact(relative_path=relative_path)
        try:
            return _read_open_leaf(leaf, relative_path, media_type, capture=capture)
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def _relative_components(relative_path: str) -> tuple[str, ...]:
    if not relative_path or relative_path.startswith("/"):
        raise ValueError("artifact_path_unsafe")
    components = tuple(relative_path.split("/"))
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("artifact_path_unsafe")
    return components


def _open_root(output_root: Path) -> int:
    try:
        return os.open(
            output_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as error:
        _raise_for_oserror(error)
        raise AssertionError("unreachable") from error


def _open_child(
    component: str,
    parent_descriptor: int,
    flags: int,
    *,
    leaf: bool,
) -> int | None:
    try:
        return os.open(component, flags, dir_fd=parent_descriptor)
    except OSError as error:
        if leaf and error.errno == errno.ENOENT:
            return None
        _raise_for_oserror(error)
        raise AssertionError("unreachable") from error


def _read_open_leaf(
    descriptor: int,
    relative_path: str,
    media_type: str,
    *,
    capture: bool,
) -> ArtifactIdentity | ArtifactSnapshot:
    before = _fstat_regular_file(descriptor)
    if capture and before.st_size > MAX_RESULT_JSON_BYTES:
        raise ValueError("result_json_oversize")

    digest = sha256()
    chunks: list[bytes] | None = [] if capture else None
    total = 0
    while True:
        try:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
        except OSError as error:
            _raise_for_oserror(error)
            raise AssertionError("unreachable") from error
        if not chunk:
            break
        total += len(chunk)
        if capture and total > MAX_RESULT_JSON_BYTES:
            raise ValueError("result_json_oversize")
        digest.update(chunk)
        if chunks is not None:
            chunks.append(chunk)

    after = _fstat_regular_file(descriptor)
    if _stability_token(before) != _stability_token(after) or total != before.st_size:
        raise ValueError("artifact_changed_during_read")

    if chunks is None:
        return ArtifactIdentity(
            relative_path=relative_path,
            content_sha256=digest.hexdigest(),
            byte_size=total,
            media_type=media_type,
        )
    content = b"".join(chunks)
    return ArtifactSnapshot(
        relative_path=relative_path,
        content=content,
        content_sha256=digest.hexdigest(),
        byte_size=total,
        media_type=media_type,
    )


def _fstat_regular_file(descriptor: int) -> os.stat_result:
    try:
        result = os.fstat(descriptor)
    except OSError as error:
        _raise_for_oserror(error)
        raise AssertionError("unreachable") from error
    if not stat.S_ISREG(result.st_mode):
        raise ValueError("artifact_not_regular_file")
    return result


def _stability_token(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _raise_for_oserror(error: OSError) -> None:
    if error.errno in _TRANSIENT_ERRNOS:
        raise ResultProcessingDeferred(str(error)) from error
    if error.errno in _DETERMINISTIC_ERRNOS:
        raise ValueError("artifact_path_unsafe") from error
    raise error
