import re
from hashlib import sha256

from labpilot.common.canonical_json import (
    CanonicalizationError,
    CanonicalValue,
    canonical_json_bytes,
)

_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _namespace_prefix(namespace: str) -> bytes:
    if _NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise CanonicalizationError(
            "namespace must be 1-128 restricted ASCII characters"
        )
    return namespace.encode("ascii") + b"\n"


def namespaced_sha256_bytes(namespace: str, canonical_payload: bytes) -> str:
    return sha256(_namespace_prefix(namespace) + canonical_payload).hexdigest()


def namespaced_sha256(namespace: str, value: CanonicalValue) -> str:
    return namespaced_sha256_bytes(namespace, canonical_json_bytes(value))
