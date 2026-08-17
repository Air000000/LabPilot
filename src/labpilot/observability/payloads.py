import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from labpilot.common.canonical_json import (
    CanonicalizationError,
    CanonicalValue,
    canonical_json_bytes,
)
from labpilot.common.hashing import namespaced_sha256_bytes

type EventJsonScalar = str | int | bool | None
type EventJsonValue = EventJsonScalar | list[EventJsonValue] | dict[
    str,
    EventJsonValue,
]
type EventJsonObject = dict[str, EventJsonValue]
EventPayloadValidator = Callable[[EventJsonObject], None]
_MAX_EVENT_PAYLOAD_BYTES = 16 * 1024
_MAX_EVENT_STRING_BYTES = 2 * 1024
_SECRET_FIELD_NAMES = {
    "password",
    "passwd",
    "secret",
    "token",
    "accesstoken",
    "refreshtoken",
    "apikey",
    "authorization",
    "cookie",
    "setcookie",
    "credential",
    "privatekey",
    "accesskey",
}
_SCHEMA_VALIDATORS: dict[tuple[str, int], EventPayloadValidator] = {}
_PREPARED_PAYLOAD_SEAL = object()


@dataclass(frozen=True, slots=True)
class PreparedRuntimeEventPayload:
    event_type: str
    event_schema_version: int
    canonical_bytes: bytes
    payload_hash: str
    canonical_size_bytes: int
    _seal: object = field(repr=False, compare=False)

    def assert_integrity(self) -> None:
        if self._seal is not _PREPARED_PAYLOAD_SEAL:
            raise CanonicalizationError("runtime event payload was not prepared")
        if len(self.canonical_bytes) != self.canonical_size_bytes:
            raise CanonicalizationError("runtime event payload size mismatch")
        expected = namespaced_sha256_bytes(
            "labpilot-runtime-event-payload:v1",
            self.canonical_bytes,
        )
        if expected != self.payload_hash:
            raise CanonicalizationError("runtime event payload hash mismatch")

    def persisted_json(self) -> EventJsonObject:
        self.assert_integrity()
        decoded = json.loads(self.canonical_bytes)
        if type(decoded) is not dict:
            raise CanonicalizationError("runtime event payload must decode to an object")
        return decoded


def register_runtime_event_payload_schema(
    event_type: str,
    event_schema_version: int,
    validator: EventPayloadValidator,
) -> None:
    key = (event_type, event_schema_version)
    if key in _SCHEMA_VALIDATORS:
        raise RuntimeError(f"event payload schema already registered: {key}")
    _SCHEMA_VALIDATORS[key] = validator


def prepare_runtime_event_payload(
    *,
    event_type: str,
    event_schema_version: int,
    payload: EventJsonObject,
) -> PreparedRuntimeEventPayload:
    validator = _SCHEMA_VALIDATORS.get((event_type, event_schema_version))
    if validator is None:
        raise CanonicalizationError("unregistered event payload schema")
    _scan_payload(payload)
    validator(payload)
    encoded = canonical_json_bytes(cast(CanonicalValue, payload))
    if len(encoded) > _MAX_EVENT_PAYLOAD_BYTES:
        raise CanonicalizationError("runtime event payload exceeds 16 KiB")
    return PreparedRuntimeEventPayload(
        event_type=event_type,
        event_schema_version=event_schema_version,
        canonical_bytes=bytes(encoded),
        payload_hash=namespaced_sha256_bytes(
            "labpilot-runtime-event-payload:v1",
            encoded,
        ),
        canonical_size_bytes=len(encoded),
        _seal=_PREPARED_PAYLOAD_SEAL,
    )


def _scan_payload(value: object) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is str:
        if len(value.encode("utf-8")) > _MAX_EVENT_STRING_BYTES:
            raise CanonicalizationError("event string exceeds 2 KiB")
        return
    if type(value) is list:
        for item in value:
            _scan_payload(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise CanonicalizationError("event object keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in _SECRET_FIELD_NAMES:
                raise CanonicalizationError(f"secret-like field is forbidden: {key}")
            _scan_payload(item)
        return
    raise CanonicalizationError(
        f"runtime event payload must use JSON-native values: {type(value).__name__}"
    )


def _validate_foundation_probe(payload: EventJsonObject) -> None:
    allowed = {"aggregate_id", "value", "nested"}
    unexpected = set(payload) - allowed
    if unexpected:
        raise CanonicalizationError(
            f"foundation probe contains unexpected fields: {sorted(unexpected)}"
        )
    if type(payload.get("aggregate_id")) is not str:
        raise CanonicalizationError("foundation probe requires aggregate_id string")
    if "value" in payload and type(payload["value"]) is not int:
        raise CanonicalizationError("foundation probe value must be an integer")


def _validate_attempt_restart_reconciliation(payload: EventJsonObject) -> None:
    required = {
        "attempt_id",
        "X",
        "H",
        "coordinator_start_id",
        "pre_reconciliation_status",
        "physical_observation_kind",
        "post_reconciliation_status",
    }
    allowed = required | {"physical_boot_id", "supervisor_invocation_id"}
    unexpected = set(payload) - allowed
    if unexpected:
        raise CanonicalizationError(
            f"attempt reconciliation contains unexpected fields: {sorted(unexpected)}"
        )
    missing = required - set(payload)
    if missing:
        raise CanonicalizationError(
            f"attempt reconciliation is missing fields: {sorted(missing)}"
        )
    if any(type(payload[name]) is not str for name in required):
        raise CanonicalizationError("attempt reconciliation fields must be strings")
    for name in ("physical_boot_id", "supervisor_invocation_id"):
        if name in payload and type(payload[name]) is not str:
            raise CanonicalizationError(f"attempt reconciliation {name} must be a string")


def _validate_exact_attempt_strings(
    payload: EventJsonObject, required: set[str]
) -> None:
    if set(payload) != required:
        raise CanonicalizationError("attempt audit payload fields are invalid")
    if any(type(payload[name]) is not str for name in required):
        raise CanonicalizationError("attempt audit payload fields must be strings")


def _validate_attempt_launch(payload: EventJsonObject) -> None:
    _validate_exact_attempt_strings(payload, {"attempt_id", "X", "H"})


def _validate_attempt_conflict(payload: EventJsonObject) -> None:
    _validate_exact_attempt_strings(payload, {"attempt_id", "X", "H", "observed_H"})


def _validate_attempt_terminal(payload: EventJsonObject) -> None:
    required = {"attempt_id", "X", "H", "terminal_kind"}
    allowed = required | {"exit_code", "termination_signal", "supervisor_result"}
    if not required <= set(payload) or set(payload) - allowed:
        raise CanonicalizationError("attempt terminal payload fields are invalid")
    if any(type(payload[name]) is not str for name in required):
        raise CanonicalizationError("attempt terminal identity fields must be strings")
    for name in ("exit_code", "termination_signal"):
        if name in payload and type(payload[name]) is not int:
            raise CanonicalizationError(f"attempt terminal {name} must be an integer")
    if "supervisor_result" in payload and type(payload["supervisor_result"]) is not str:
        raise CanonicalizationError("attempt terminal supervisor_result must be a string")


register_runtime_event_payload_schema(
    "foundation.probe.created",
    1,
    _validate_foundation_probe,
)
register_runtime_event_payload_schema(
    "attempt.reconciled_after_restart",
    1,
    _validate_attempt_restart_reconciliation,
)
register_runtime_event_payload_schema(
    "attempt.launch_intent_committed", 1, _validate_attempt_launch
)
register_runtime_event_payload_schema(
    "attempt.physical_started_observed", 1, _validate_attempt_launch
)
register_runtime_event_payload_schema(
    "attempt.identity_conflict_observed", 1, _validate_attempt_conflict
)
register_runtime_event_payload_schema(
    "attempt.terminal_observed", 1, _validate_attempt_terminal
)
