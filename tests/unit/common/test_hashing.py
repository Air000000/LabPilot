import pytest

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.hashing import namespaced_sha256, namespaced_sha256_bytes


def test_namespaced_hash_is_stable() -> None:
    value = {"a": 1}
    assert namespaced_sha256("labpilot-test:v1", value) == namespaced_sha256(
        "labpilot-test:v1",
        value,
    )


def test_namespaced_byte_hash_matches_value_hash() -> None:
    assert namespaced_sha256_bytes("labpilot-test:v1", b'{"a":1}') == namespaced_sha256(
        "labpilot-test:v1",
        {"a": 1},
    )


@pytest.mark.parametrize("namespace", ["", "line\nbreak", "control\x00", "x" * 129])
def test_invalid_namespace_is_rejected(namespace: str) -> None:
    with pytest.raises(CanonicalizationError, match="namespace"):
        namespaced_sha256(namespace, {"a": 1})
