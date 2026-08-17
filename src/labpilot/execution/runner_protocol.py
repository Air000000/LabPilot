import json
import re
import socket
import struct
from dataclasses import dataclass, fields
from enum import StrEnum

from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_value

PROTOCOL_VERSION = "labpilot-runner:v1"
MAX_FRAME_BYTES = 64 * 1024
_HEADER = struct.Struct("!I")
_DISTRIBUTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProtocolError(ValueError):
    pass


class RunnerOperation(StrEnum):
    INFO = "INFO"
    LAUNCH = "LAUNCH"
    OBSERVE = "OBSERVE"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class InfoInspectionRequest:
    python_profile: str
    framework_distribution_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunnerInfoRequest:
    version: str
    operation: RunnerOperation
    inspection: InfoInspectionRequest | None


@dataclass(frozen=True, slots=True)
class RunnerLaunchRequest:
    version: str
    operation: RunnerOperation
    execution_identity: str
    execution_spec_hash: str
    spec: FrozenLaunchSpec
    creation_authority: str


@dataclass(frozen=True, slots=True)
class RunnerObserveRequest:
    version: str
    operation: RunnerOperation
    execution_identity: str
    execution_spec_hash: str


@dataclass(frozen=True, slots=True)
class RunnerStopRequest:
    version: str
    operation: RunnerOperation
    execution_identity: str
    execution_spec_hash: str


type RunnerRequest = (
    RunnerInfoRequest | RunnerLaunchRequest | RunnerObserveRequest | RunnerStopRequest
)


def encode_request(request: RunnerRequest) -> bytes:
    if request.version != PROTOCOL_VERSION:
        raise ProtocolError("protocol_version_unsupported")
    value: dict[str, object] = {
        "version": request.version,
        "operation": request.operation.value,
    }
    if isinstance(request, RunnerInfoRequest):
        value["inspection"] = (
            None if request.inspection is None else _inspection_to_wire(request.inspection)
        )
    elif isinstance(request, RunnerLaunchRequest):
        if request.creation_authority != "first_dispatch_proven":
            raise ProtocolError("invalid_creation_authority")
        value.update(
            execution_identity=request.execution_identity,
            execution_spec_hash=request.execution_spec_hash,
            spec=frozen_launch_spec_value(request.spec),
            creation_authority=request.creation_authority,
        )
    else:
        value.update(
            execution_identity=request.execution_identity,
            execution_spec_hash=request.execution_spec_hash,
        )
    return _encode_json_object(value)


def decode_request(payload: bytes) -> RunnerRequest:
    value = _decode_json_object(payload)
    version = _require_str(value, "version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("protocol_version_unsupported")
    try:
        operation = RunnerOperation(_require_str(value, "operation"))
    except ValueError as error:
        raise ProtocolError("operation_unsupported") from error
    if operation is RunnerOperation.INFO:
        _require_exact_keys(value, {"version", "operation", "inspection"})
        return RunnerInfoRequest(version, operation, _inspection_from_wire(value["inspection"]))
    if operation is RunnerOperation.LAUNCH:
        _require_exact_keys(
            value,
            {
                "version",
                "operation",
                "execution_identity",
                "execution_spec_hash",
                "spec",
                "creation_authority",
            },
        )
        authority = _require_str(value, "creation_authority")
        if authority != "first_dispatch_proven":
            raise ProtocolError("invalid_creation_authority")
        return RunnerLaunchRequest(
            version,
            operation,
            _require_str(value, "execution_identity"),
            _require_str(value, "execution_spec_hash"),
            _spec_from_wire(value["spec"]),
            authority,
        )
    _require_exact_keys(
        value,
        {"version", "operation", "execution_identity", "execution_spec_hash"},
    )
    request_type = (
        RunnerObserveRequest if operation is RunnerOperation.OBSERVE else RunnerStopRequest
    )
    return request_type(
        version,
        operation,
        _require_str(value, "execution_identity"),
        _require_str(value, "execution_spec_hash"),
    )


def decode_response(payload: bytes) -> dict[str, object]:
    value = _decode_json_object(payload)
    _require_exact_keys(value, {"version", "operation", "outcome", "result"})
    if _require_str(value, "version") != PROTOCOL_VERSION:
        raise ProtocolError("protocol_version_unsupported")
    try:
        RunnerOperation(_require_str(value, "operation"))
    except ValueError as error:
        raise ProtocolError("operation_unsupported") from error
    _require_str(value, "outcome")
    if type(value["result"]) is not dict:
        raise ProtocolError("invalid_result")
    return value


def encode_response(response: dict[str, object]) -> bytes:
    encoded = _encode_json_object(response)
    decode_response(encoded)
    return encoded


def pack_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return _HEADER.pack(len(payload)) + payload


def send_frame(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(pack_frame(payload))


def recv_frame(sock: socket.socket) -> bytes:
    header = _recv_exact(sock, _HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return _recv_exact(sock, length)


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ProtocolError("truncated_frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _inspection_to_wire(inspection: InfoInspectionRequest) -> dict[str, object]:
    _validate_inspection(inspection)
    return {
        "python_profile": inspection.python_profile,
        "framework_distribution_names": list(inspection.framework_distribution_names),
    }


def _inspection_from_wire(value: object) -> InfoInspectionRequest | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ProtocolError("invalid_inspection")
    _require_exact_keys(value, {"python_profile", "framework_distribution_names"})
    inspection = InfoInspectionRequest(
        _require_str(value, "python_profile"),
        _require_str_list(value["framework_distribution_names"], "invalid_inspection"),
    )
    _validate_inspection(inspection)
    return inspection


def _validate_inspection(inspection: InfoInspectionRequest) -> None:
    names = inspection.framework_distribution_names
    if (
        not inspection.python_profile
        or len(inspection.python_profile.encode("utf-8")) > 128
        or len(names) > 32
        or any(_DISTRIBUTION.fullmatch(name) is None for name in names)
        or names != tuple(sorted(set(names)))
    ):
        raise ProtocolError("invalid_inspection")


def _spec_from_wire(value: object) -> FrozenLaunchSpec:
    if type(value) is not dict:
        raise ProtocolError("invalid_spec")
    expected = {field.name for field in fields(FrozenLaunchSpec)}
    if set(value) != expected:
        raise ProtocolError("unknown_spec_field")
    environment = value["environment"]
    if type(environment) is not dict or any(
        type(key) is not str or type(item) is not str
        for key, item in environment.items()
    ):
        raise ProtocolError("invalid_spec_environment")
    timeout = value["timeout_seconds"]
    if type(timeout) is not int:
        raise ProtocolError("invalid_timeout_seconds")
    return FrozenLaunchSpec(
        schema_version=_require_str(value, "schema_version"),
        workspace_path=_require_str(value, "workspace_path"),
        source_manifest_sha256=_require_str(value, "source_manifest_sha256"),
        python_profile=_require_str(value, "python_profile"),
        runtime_path=_require_str(value, "runtime_path"),
        train_entrypoint_relative_path=_require_str(
            value, "train_entrypoint_relative_path"
        ),
        workdir_relative_path=_require_str(value, "workdir_relative_path"),
        argv=_require_str_list(value["argv"], "invalid_spec_argv"),
        dataset_reference_type=_require_str(value, "dataset_reference_type"),
        dataset_argument=_require_str(value, "dataset_argument"),
        dataset_reference_hash=_require_str(value, "dataset_reference_hash"),
        environment=tuple(sorted(environment.items())),
        timeout_seconds=timeout,
        execution_user=_require_str(value, "execution_user"),
        execution_group=_require_str(value, "execution_group"),
        launch_profile_version=_require_str(value, "launch_profile_version"),
        launch_policy_identity=_require_str(value, "launch_policy_identity"),
    )


def _require_exact_keys(value: dict[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ProtocolError("invalid_request_fields")


def _require_str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str:
        raise ProtocolError(f"invalid_{key}")
    return item


def _require_str_list(value: object, error_code: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ProtocolError(error_code)
    return tuple(value)


def _encode_json_object(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as error:
        raise ProtocolError("invalid_json") from error
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return encoded


def _decode_json_object(payload: bytes) -> dict[str, object]:
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid_json") from error
    if type(value) is not dict:
        raise ProtocolError("invalid_json_object")
    return value
