import json
import socket
import struct
from dataclasses import asdict, dataclass, fields
from enum import StrEnum

from tests.poc.s2_runner.contract import FrozenLaunchSpec

PROTOCOL_VERSION = "s2-runner-poc-v1"
MAX_FRAME_BYTES = 65536
_HEADER = struct.Struct("!I")


class ProtocolError(ValueError):
    pass


class RunnerOperation(StrEnum):
    INFO = "INFO"
    LAUNCH = "LAUNCH"
    OBSERVE = "OBSERVE"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class RunnerInfoRequest:
    version: str
    operation: RunnerOperation


@dataclass(frozen=True, slots=True)
class RunnerLaunchRequest:
    version: str
    operation: RunnerOperation
    execution_identity: str
    execution_spec_hash: str
    spec: FrozenLaunchSpec
    fault_point: str


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
    force: bool


type RunnerRequest = (
    RunnerInfoRequest | RunnerLaunchRequest | RunnerObserveRequest | RunnerStopRequest
)


def encode_request(request: RunnerRequest) -> bytes:
    value = asdict(request)
    value["operation"] = request.operation.value
    if isinstance(request, RunnerLaunchRequest):
        value["spec"] = _spec_to_wire(request.spec)
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
        _require_exact_keys(value, {"version", "operation"}, "unknown_request_field")
        return RunnerInfoRequest(version=version, operation=operation)
    if operation is RunnerOperation.LAUNCH:
        _require_exact_keys(
            value,
            {
                "version",
                "operation",
                "execution_identity",
                "execution_spec_hash",
                "spec",
                "fault_point",
            },
            "unknown_request_field",
        )
        return RunnerLaunchRequest(
            version=version,
            operation=operation,
            execution_identity=_require_str(value, "execution_identity"),
            execution_spec_hash=_require_str(value, "execution_spec_hash"),
            spec=_spec_from_wire(value.get("spec")),
            fault_point=_require_str(value, "fault_point"),
        )
    if operation is RunnerOperation.OBSERVE:
        _require_exact_keys(
            value,
            {"version", "operation", "execution_identity", "execution_spec_hash"},
            "unknown_request_field",
        )
        return RunnerObserveRequest(
            version=version,
            operation=operation,
            execution_identity=_require_str(value, "execution_identity"),
            execution_spec_hash=_require_str(value, "execution_spec_hash"),
        )

    _require_exact_keys(
        value,
        {"version", "operation", "execution_identity", "execution_spec_hash", "force"},
        "unknown_request_field",
    )
    force = value.get("force")
    if not isinstance(force, bool):
        raise ProtocolError("invalid_force")
    return RunnerStopRequest(
        version=version,
        operation=operation,
        execution_identity=_require_str(value, "execution_identity"),
        execution_spec_hash=_require_str(value, "execution_spec_hash"),
        force=force,
    )


def encode_response(response: dict[str, object]) -> bytes:
    return _encode_json_object(response)


def decode_response(payload: bytes) -> dict[str, object]:
    return _decode_json_object(payload)


def send_frame(sock: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    sock.sendall(_HEADER.pack(len(payload)) + payload)


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


def _spec_to_wire(spec: FrozenLaunchSpec) -> dict[str, object]:
    value = asdict(spec)
    value["argv"] = list(spec.argv)
    value["environment"] = [list(item) for item in spec.environment]
    value["credential_slots"] = list(spec.credential_slots)
    value["print_env_keys"] = list(spec.print_env_keys)
    return value


def _spec_from_wire(value: object) -> FrozenLaunchSpec:
    if not isinstance(value, dict):
        raise ProtocolError("invalid_spec")
    expected = {field.name for field in fields(FrozenLaunchSpec)}
    _require_exact_keys(value, expected, "unknown_spec_field")
    environment = value.get("environment")
    if not isinstance(environment, list):
        raise ProtocolError("invalid_spec_environment")
    env_items: list[tuple[str, str]] = []
    for item in environment:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
        ):
            raise ProtocolError("invalid_spec_environment")
        env_items.append((item[0], item[1]))

    return FrozenLaunchSpec(
        schema_version=_require_str(value, "schema_version"),
        runtime_path=_require_str(value, "runtime_path"),
        argv=_require_str_list(value.get("argv"), "invalid_spec_argv"),
        working_directory=_require_str(value, "working_directory"),
        environment=tuple(env_items),
        credential_slots=_require_str_list(
            value.get("credential_slots"), "invalid_spec_credential_slots"
        ),
        launch_profile_version=_require_str(value, "launch_profile_version"),
        start_marker_path=_require_str(value, "start_marker_path"),
        sleep_seconds=_require_number(value.get("sleep_seconds"), "invalid_spec_sleep_seconds"),
        exit_code=_require_int(value.get("exit_code"), "invalid_spec_exit_code"),
        child_sleep_seconds=_require_number(
            value.get("child_sleep_seconds"), "invalid_spec_child_sleep_seconds"
        ),
        print_env_keys=_require_str_list(
            value.get("print_env_keys"), "invalid_spec_print_env_keys"
        ),
    )


def _require_exact_keys(
    value: dict[str, object], expected: set[str], error_code: str
) -> None:
    if set(value) != expected:
        raise ProtocolError(error_code)


def _require_str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ProtocolError(f"invalid_{key}")
    return item


def _require_str_list(value: object, error_code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProtocolError(error_code)
    return tuple(value)


def _require_number(value: object, error_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProtocolError(error_code)
    return float(value)


def _require_int(value: object, error_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(error_code)
    return value


def _encode_json_object(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _decode_json_object(payload: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid_json") from error
    if not isinstance(decoded, dict):
        raise ProtocolError("invalid_json_object")
    return decoded
