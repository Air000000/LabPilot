from collections import UserDict
from decimal import Decimal

import pytest

import labpilot.common.canonical_json as canonical_json
from labpilot.common.canonical_json import (
    CanonicalizationError,
    CanonicalizationLimitsV1,
    canonical_json_bytes,
)


def test_object_keys_use_utf8_byte_order() -> None:
    assert canonical_json_bytes({"é": 1, "z": 2}) == b'{"z":2,"\xc3\xa9":1}'


def test_control_characters_are_json_escaped() -> None:
    assert canonical_json_bytes({"value": "a\nb\t"}) == b'{"value":"a\\nb\\t"}'


def test_lone_surrogate_is_rejected_with_stable_error() -> None:
    with pytest.raises(CanonicalizationError, match="surrogate"):
        canonical_json_bytes({"value": "\ud800"})


def test_semantically_equal_decimals_have_identical_bytes() -> None:
    assert canonical_json_bytes(Decimal("1.2300")) == b"1.23"
    assert canonical_json_bytes(Decimal("1.23")) == b"1.23"
    assert canonical_json_bytes(Decimal("-0")) == b"0"


@pytest.mark.parametrize("value", [1.5, (1, 2), {1, 2}, b"bytes", UserDict({"a": 1})])
def test_unsupported_types_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalizationError, match="unsupported type"):
        canonical_json_bytes(value)


def test_excessive_decimal_exponent_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="exponent"):
        canonical_json_bytes(Decimal("1E+10001"))


def test_excessive_depth_is_rejected() -> None:
    value: object = None
    for _ in range(66):
        value = [value]
    with pytest.raises(CanonicalizationError, match="depth"):
        canonical_json_bytes(value)


def test_output_budget_is_enforced_while_tokens_are_written(monkeypatch) -> None:
    monkeypatch.setattr(
        canonical_json,
        "_LIMITS",
        CanonicalizationLimitsV1(
            max_string_bytes=16,
            max_output_bytes=12,
        ),
    )
    with pytest.raises(CanonicalizationError, match="max_output_bytes"):
        canonical_json_bytes(["aaaa", "bbbb"])


def test_exact_output_budget_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        canonical_json,
        "_LIMITS",
        CanonicalizationLimitsV1(
            max_string_bytes=16,
            max_output_bytes=8,
        ),
    )
    assert canonical_json_bytes(["aaaa"]) == b'["aaaa"]'


def test_extreme_integer_uses_stable_canonicalization_error() -> None:
    with pytest.raises(CanonicalizationError, match="integer"):
        canonical_json_bytes(1 << 20_000)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, b"null"),
        (True, b"true"),
        (123, b"123"),
        (Decimal("100.000"), b"100"),
        ({"b": 1, "a": 2}, b'{"a":2,"b":1}'),
        (["x", Decimal("0.10")], b'["x",0.1]'),
    ],
)
def test_canonical_json_v1_conformance_vectors(value: object, expected: bytes) -> None:
    assert canonical_json_bytes(value) == expected
