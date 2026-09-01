import errno
import importlib
import os
from hashlib import sha256
from pathlib import Path

import pytest


def _reader():
    return importlib.import_module("labpilot.results_validation.artifact_reader")


def _types():
    return importlib.import_module("labpilot.results_validation.types")


def _output_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime" / "output"
    root.mkdir(parents=True)
    return root


def test_generic_artifact_streams_identity_without_retaining_content(tmp_path: Path) -> None:
    root = _output_root(tmp_path)
    payload = b"generic artifact"
    (root / "artifact.bin").write_bytes(payload)

    identity = _reader().read_artifact_identity(root, "artifact.bin", "application/octet-stream")

    assert identity == _types().ArtifactIdentity(
        relative_path="artifact.bin",
        content_sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        media_type="application/octet-stream",
    )
    assert not hasattr(identity, "content")


def test_missing_leaf_is_distinct_from_unsafe_paths(tmp_path: Path) -> None:
    root = _output_root(tmp_path)

    result = _reader().read_artifact_identity(root, "missing.json", "application/json")

    assert result == _types().MissingArtifact(relative_path="missing.json")


@pytest.mark.parametrize(
    "relative_path", ("../outside.json", "/tmp/outside.json", "nested/../x.json")
)
def test_traversal_and_absolute_paths_are_rejected(tmp_path: Path, relative_path: str) -> None:
    root = _output_root(tmp_path)

    with pytest.raises(ValueError):
        _reader().read_artifact_identity(root, relative_path, "application/json")


def test_intermediate_and_leaf_symlinks_are_rejected(tmp_path: Path) -> None:
    root = _output_root(tmp_path)
    target = tmp_path / "target.json"
    target.write_text("{}")
    (root / "nested").symlink_to(tmp_path, target_is_directory=True)
    (root / "leaf.json").symlink_to(target)

    reader = _reader()
    with pytest.raises(ValueError):
        reader.read_artifact_identity(root, "nested/target.json", "application/json")
    with pytest.raises(ValueError):
        reader.read_artifact_identity(root, "leaf.json", "application/json")


def test_directory_leaf_is_rejected(tmp_path: Path) -> None:
    root = _output_root(tmp_path)
    (root / "directory").mkdir()

    with pytest.raises(ValueError):
        _reader().read_artifact_identity(root, "directory", "application/json")


def test_generic_artifact_over_one_mib_is_allowed_and_streamed(tmp_path: Path) -> None:
    root = _output_root(tmp_path)
    payload = b"x" * (_reader().MAX_RESULT_JSON_BYTES + 1)
    (root / "large.bin").write_bytes(payload)

    identity = _reader().read_artifact_identity(root, "large.bin", "application/octet-stream")

    assert identity.byte_size == len(payload)
    assert identity.content_sha256 == sha256(payload).hexdigest()
    assert not hasattr(identity, "content")


def test_result_json_capture_enforces_one_mib_and_accepts_exact_bound(tmp_path: Path) -> None:
    root = _output_root(tmp_path)
    reader = _reader()
    exact = b" " * reader.MAX_RESULT_JSON_BYTES
    (root / "exact.json").write_bytes(exact)
    (root / "oversize.json").write_bytes(exact + b"x")

    snapshot = reader.capture_result_json_artifact(root, "exact.json", "application/json")
    assert snapshot.content == exact
    assert snapshot.byte_size == len(exact)
    assert snapshot.content_sha256 == sha256(exact).hexdigest()

    with pytest.raises(ValueError):
        reader.capture_result_json_artifact(root, "oversize.json", "application/json")


def test_snapshot_is_bound_to_captured_descriptor_generation(tmp_path: Path) -> None:
    root = _output_root(tmp_path)
    original = b'{"schema_version":"v1","metrics":{"score":0.82}}'
    replacement = b'{"schema_version":"v1","metrics":{"score":0.10}}'
    path = root / "metrics.json"
    path.write_bytes(original)

    snapshot = _reader().capture_result_json_artifact(root, "metrics.json", "application/json")
    path.write_bytes(replacement)

    assert snapshot.content == original
    assert snapshot.content_sha256 == sha256(original).hexdigest()


def test_descriptor_stability_change_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _output_root(tmp_path)
    (root / "metrics.json").write_bytes(b"stable")
    reader = _reader()
    original_fstat = reader.os.fstat
    calls = 0

    def changed_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        result = original_fstat(descriptor)
        calls += 1
        if calls == 2:
            values = list(result)
            values[6] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(reader.os, "fstat", changed_fstat)

    with pytest.raises(ValueError):
        reader.read_artifact_identity(root, "metrics.json", "application/json")


@pytest.mark.parametrize("errno_value", (errno.EAGAIN, errno.ESTALE))
def test_only_explicit_transient_errno_is_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    errno_value: int,
) -> None:
    root = _output_root(tmp_path)
    reader = _reader()

    def unavailable(*args, **kwargs):
        raise OSError(errno_value, "transient")

    monkeypatch.setattr(reader.os, "open", unavailable)

    with pytest.raises(_types().ResultProcessingDeferred):
        reader.read_artifact_identity(root, "metrics.json", "application/json")


def test_unexpected_oserror_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _output_root(tmp_path)
    reader = _reader()

    def denied(*args, **kwargs):
        raise OSError(5, "unexpected")

    monkeypatch.setattr(reader.os, "open", denied)

    with pytest.raises(OSError) as error:
        reader.read_artifact_identity(root, "metrics.json", "application/json")
    assert not isinstance(error.value, _types().ResultProcessingDeferred)
