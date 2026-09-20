import asyncio
import os
import re
import tempfile
import threading
from hashlib import sha256
from pathlib import Path

_REF = re.compile(r"sha256:([0-9a-f]{64})")
_HASH = re.compile(r"[0-9a-f]{64}")
_PUBLISH_LOCK = threading.Lock()


class _ContentStoreError(Exception):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ContentReferenceInvalid(_ContentStoreError):
    pass


class ContentHashInvalid(_ContentStoreError):
    pass


class ContentBlobMissing(_ContentStoreError):
    pass


class ContentStoreCorruption(_ContentStoreError):
    pass


class ContentAddressedStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def put_bytes(self, value: bytes) -> str:
        if type(value) is not bytes:
            raise TypeError("value must be exact bytes")
        digest = sha256(value).hexdigest()
        await asyncio.to_thread(self._put_bytes, value, digest)
        return f"sha256:{digest}"

    async def read_bytes(self, ref: str) -> bytes:
        digest = _ref_digest(ref)
        return await asyncio.to_thread(self._read_verified, digest)

    async def verify(self, ref: str, expected_hash: str) -> None:
        digest = _ref_digest(ref)
        expected = _expected_digest(expected_hash)
        if digest != expected:
            raise ContentStoreCorruption("reference_expected_hash_mismatch")
        await asyncio.to_thread(self._read_verified, digest)

    def _blob_path(self, digest: str) -> Path:
        return self._root / "sha256" / digest[:2] / digest[2:]

    def _read_verified(self, digest: str) -> bytes:
        path = self._blob_path(digest)
        try:
            value = path.read_bytes()
        except FileNotFoundError as exc:
            raise ContentBlobMissing("blob_missing") from exc
        if sha256(value).hexdigest() != digest:
            raise ContentStoreCorruption("blob_digest_mismatch")
        return value

    def _put_bytes(self, value: bytes, digest: str) -> None:
        # ponytail: one process-wide lock is enough; use per-digest locks if writes contend.
        with _PUBLISH_LOCK:
            final_path = self._blob_path(digest)
            if final_path.exists():
                self._read_verified(digest)
                return
            final_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{digest}.",
                suffix=".tmp",
                dir=final_path.parent,
            )
            temp_path = Path(raw_temp_path)
            published = False
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o640)
                    os.fsync(stream.fileno())
                os.replace(temp_path, final_path)
                published = True
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                parent_descriptor = os.open(final_path.parent, directory_flags)
                try:
                    os.fsync(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
                self._read_verified(digest)
            finally:
                if not published:
                    temp_path.unlink(missing_ok=True)


def _ref_digest(ref: str) -> str:
    if type(ref) is not str:
        raise ContentReferenceInvalid("invalid_ref")
    matched = _REF.fullmatch(ref)
    if matched is None:
        raise ContentReferenceInvalid("invalid_ref")
    return matched.group(1)


def _expected_digest(expected_hash: str) -> str:
    if type(expected_hash) is not str or _HASH.fullmatch(expected_hash) is None:
        raise ContentHashInvalid("invalid_expected_hash")
    return expected_hash
