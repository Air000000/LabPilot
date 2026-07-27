import json
from decimal import Decimal

import pytest

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.observability.payloads import prepare_runtime_event_payload


def test_foundation_payload_is_copied_canonicalized_and_hashed() -> None:
    source = {"aggregate_id": "probe-1", "value": 1}
    prepared = prepare_runtime_event_payload(
        event_type="foundation.probe.created",
        event_schema_version=1,
        payload=source,
    )
    source["password"] = "mutated after validation"
    assert json.loads(prepared.canonical_bytes) == {
        "aggregate_id": "probe-1",
        "value": 1,
    }
    assert prepared.event_type == "foundation.probe.created"
    assert prepared.event_schema_version == 1
    assert len(prepared.payload_hash) == 64
    assert prepared.canonical_size_bytes == len(prepared.canonical_bytes)


@pytest.mark.parametrize(
    "secret_key",
    ["password", "apiKey", "access-token", "Authorization", "private_key"],
)
def test_secret_like_keys_are_rejected_recursively(secret_key: str) -> None:
    with pytest.raises(CanonicalizationError, match="secret-like field"):
        prepare_runtime_event_payload(
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload={"aggregate_id": "probe-1", "nested": {secret_key: "value"}},
        )


def test_unknown_event_schema_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="unregistered event payload schema"):
        prepare_runtime_event_payload(
            event_type="unknown.event",
            event_schema_version=1,
            payload={},
        )


def test_large_runtime_event_string_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="event string exceeds"):
        prepare_runtime_event_payload(
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload={"aggregate_id": "x" * 2049},
        )


def test_decimal_is_rejected_at_event_json_boundary() -> None:
    with pytest.raises(CanonicalizationError, match="JSON-native"):
        prepare_runtime_event_payload(
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload={"aggregate_id": "probe-1", "value": Decimal("1.2")},
        )
