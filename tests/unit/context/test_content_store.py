import asyncio
import os
import stat
from hashlib import sha256
from pathlib import Path

import pytest

import labpilot.context.content_store as content_store_module
from labpilot.context.content_store import (
    ContentAddressedStore,
    ContentBlobMissing,
    ContentHashInvalid,
    ContentReferenceInvalid,
    ContentStoreCorruption,
)


@pytest.mark.asyncio
async def test_put_read_verify_and_layout(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    value = b"hello"
    digest = sha256(value).hexdigest()

    ref = await store.put_bytes(value)

    assert ref == f"sha256:{digest}"
    path = tmp_path / "content" / "sha256" / digest[:2] / digest[2:]
    assert path.read_bytes() == value
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert await store.read_bytes(ref) == value
    await store.verify(ref, digest)


@pytest.mark.asyncio
async def test_duplicate_and_concurrent_puts_return_one_identity(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    value = b"same bytes"

    refs = await asyncio.gather(*(store.put_bytes(value) for _ in range(8)))

    assert len(set(refs)) == 1
    digest = sha256(value).hexdigest()
    parent = tmp_path / "content" / "sha256" / digest[:2]
    assert [path.name for path in parent.iterdir()] == [digest[2:]]


@pytest.mark.asyncio
async def test_existing_verified_blob_is_reused_without_rewrite(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    ref = await store.put_bytes(b"stable")
    digest = ref.removeprefix("sha256:")
    path = tmp_path / "content" / "sha256" / digest[:2] / digest[2:]
    before = path.stat().st_mtime_ns

    assert await store.put_bytes(b"stable") == ref

    assert path.stat().st_mtime_ns == before


@pytest.mark.asyncio
async def test_damaged_existing_blob_is_not_overwritten(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    value = b"intended"
    digest = sha256(value).hexdigest()
    path = tmp_path / "content" / "sha256" / digest[:2] / digest[2:]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"damaged")

    with pytest.raises(ContentStoreCorruption) as captured:
        await store.put_bytes(value)

    assert captured.value.reason_code == "blob_digest_mismatch"
    assert path.read_bytes() == b"damaged"


@pytest.mark.asyncio
async def test_read_revalidates_bytes_and_missing_blob_is_stable(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    value = b"original"
    ref = await store.put_bytes(value)
    digest = ref.removeprefix("sha256:")
    path = tmp_path / "content" / "sha256" / digest[:2] / digest[2:]
    path.write_bytes(b"corrupt")

    with pytest.raises(ContentStoreCorruption) as corrupted:
        await store.read_bytes(ref)
    assert corrupted.value.reason_code == "blob_digest_mismatch"

    missing_ref = f"sha256:{'f' * 64}"
    with pytest.raises(ContentBlobMissing) as missing:
        await store.read_bytes(missing_ref)
    assert missing.value.reason_code == "blob_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref",
    [
        "",
        "sha256:",
        f"sha256:{'A' * 64}",
        f"sha256:{'a' * 63}",
        f"sha256:{'a' * 65}",
        f"sha512:{'a' * 64}",
        "sha256:../../escape",
        f" sha256:{'a' * 64}",
        f"sha256:{'a' * 64}\n",
        f"sha256:{'a' * 32}\0{'a' * 32}",
    ],
)
async def test_ref_grammar_is_exact(tmp_path: Path, ref: str) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    with pytest.raises(ContentReferenceInvalid) as captured:
        await store.read_bytes(ref)
    assert captured.value.reason_code == "invalid_ref"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expected_hash",
    ["", "a" * 63, "a" * 65, "A" * 64, f"sha256:{'a' * 64}", "g" * 64, "a" * 63 + "\0"],
)
async def test_expected_hash_grammar_is_exact(tmp_path: Path, expected_hash: str) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    with pytest.raises(ContentHashInvalid) as captured:
        await store.verify(f"sha256:{'a' * 64}", expected_hash)
    assert captured.value.reason_code == "invalid_expected_hash"


@pytest.mark.asyncio
async def test_verify_requires_reference_expected_and_actual_equality(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    ref = await store.put_bytes(b"value")
    actual_hash = ref.removeprefix("sha256:")

    with pytest.raises(ContentStoreCorruption) as mismatch:
        await store.verify(ref, "f" * 64)
    assert mismatch.value.reason_code == "reference_expected_hash_mismatch"

    path = tmp_path / "content" / "sha256" / actual_hash[:2] / actual_hash[2:]
    path.write_bytes(b"wrong")
    with pytest.raises(ContentStoreCorruption) as corrupted:
        await store.verify(ref, actual_hash)
    assert corrupted.value.reason_code == "blob_digest_mismatch"


@pytest.mark.asyncio
async def test_parent_directory_fsync_occurs_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_fchmod = os.fchmod
    original_replace = os.replace
    original_fsync = os.fsync

    def fchmod(fd: int, mode: int) -> None:
        assert mode == 0o640
        events.append("fchmod")
        original_fchmod(fd, mode)

    def replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        events.append("replace")
        original_replace(source, destination)

    def fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        events.append("fsync-dir" if stat.S_ISDIR(mode) else "fsync-file")
        original_fsync(fd)

    monkeypatch.setattr(content_store_module.os, "fchmod", fchmod)
    monkeypatch.setattr(content_store_module.os, "replace", replace)
    monkeypatch.setattr(content_store_module.os, "fsync", fsync)

    await ContentAddressedStore(tmp_path / "content").put_bytes(b"durable")

    assert (
        events.index("fchmod")
        < events.index("fsync-file")
        < events.index("replace")
        < events.index("fsync-dir")
    )


@pytest.mark.asyncio
async def test_replace_failure_never_publishes_partial_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(*args: object) -> None:
        del args
        raise OSError("simulated replace failure")

    monkeypatch.setattr(content_store_module.os, "replace", fail_replace)
    value = b"partial"
    digest = sha256(value).hexdigest()
    final_path = tmp_path / "content" / "sha256" / digest[:2] / digest[2:]

    with pytest.raises(OSError, match="simulated"):
        await ContentAddressedStore(tmp_path / "content").put_bytes(value)

    assert not final_path.exists()
    assert list(final_path.parent.glob(".*.tmp")) == []


@pytest.mark.asyncio
async def test_put_requires_exact_bytes(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "content")
    with pytest.raises(TypeError):
        await store.put_bytes(bytearray(b"not exact bytes"))  # type: ignore[arg-type]
