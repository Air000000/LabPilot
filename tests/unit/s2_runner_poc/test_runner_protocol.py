import json
import socket
import struct
from uuid import UUID

import pytest
from tests.poc.s2_runner.contract import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    FrozenLaunchSpec,
    execution_spec_hash,
    supervisor_execution_identity,
)
from tests.poc.s2_runner.runner_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    RunnerLaunchRequest,
    RunnerOperation,
    RunnerStopRequest,
    decode_request,
    encode_request,
    recv_frame,
)

X = supervisor_execution_identity(
    "native-installation", UUID("12345678-1234-5678-1234-567812345678")
)


def _spec() -> FrozenLaunchSpec:
    return FrozenLaunchSpec(
        schema_version=EXECUTION_SPEC_SCHEMA_VERSION,
        runtime_path="/usr/bin/python3",
        argv=("-m", "tests.poc.s2_runner.probe"),
        working_directory="/var/lib/labpilot-s2-poc/workspace/run-1",
        environment=(("LABPILOT_POC_ENV", "visible"),),
        credential_slots=(),
        launch_profile_version="s2-poc-b1",
        start_marker_path="/var/lib/labpilot-s2-poc/markers/run-1.jsonl",
        sleep_seconds=2.0,
        exit_code=0,
        child_sleep_seconds=0.0,
        print_env_keys=("LABPILOT_POC_ENV",),
    )


def test_launch_round_trip_preserves_x_h_and_spec() -> None:
    spec = _spec()
    request = RunnerLaunchRequest(
        version=PROTOCOL_VERSION,
        operation=RunnerOperation.LAUNCH,
        execution_identity=X,
        execution_spec_hash=execution_spec_hash(spec),
        spec=spec,
        fault_point="none",
    )

    assert decode_request(encode_request(request)) == request


def test_unknown_field_is_rejected() -> None:
    payload = json.dumps(
        {"version": PROTOCOL_VERSION, "operation": "INFO", "raw_unit_name": "ssh.service"}
    ).encode()

    with pytest.raises(ProtocolError, match="unknown_request_field"):
        decode_request(payload)


def test_nested_unknown_spec_field_is_rejected() -> None:
    spec = _spec()
    payload = {
        "version": PROTOCOL_VERSION,
        "operation": "LAUNCH",
        "execution_identity": X,
        "execution_spec_hash": execution_spec_hash(spec),
        "spec": {
            "schema_version": spec.schema_version,
            "runtime_path": spec.runtime_path,
            "argv": list(spec.argv),
            "working_directory": spec.working_directory,
            "environment": [list(item) for item in spec.environment],
            "credential_slots": list(spec.credential_slots),
            "launch_profile_version": spec.launch_profile_version,
            "start_marker_path": spec.start_marker_path,
            "sleep_seconds": spec.sleep_seconds,
            "exit_code": spec.exit_code,
            "child_sleep_seconds": spec.child_sleep_seconds,
            "print_env_keys": list(spec.print_env_keys),
            "user": "root",
        },
        "fault_point": "none",
    }

    with pytest.raises(ProtocolError, match="unknown_spec_field"):
        decode_request(json.dumps(payload).encode())


def test_raw_unit_name_is_not_wire_expressible() -> None:
    payload = json.dumps(
        {
            "version": PROTOCOL_VERSION,
            "operation": "STOP",
            "execution_identity": X,
            "execution_spec_hash": "a" * 64,
            "force": False,
            "unit": "ssh.service",
        }
    ).encode()

    with pytest.raises(ProtocolError, match="unknown_request_field"):
        decode_request(payload)


def test_user_group_or_raw_systemd_properties_are_not_wire_expressible() -> None:
    spec = _spec()
    base = {
        "version": PROTOCOL_VERSION,
        "operation": "LAUNCH",
        "execution_identity": X,
        "execution_spec_hash": execution_spec_hash(spec),
        "spec": json.loads(encode_request(
            RunnerLaunchRequest(
                version=PROTOCOL_VERSION,
                operation=RunnerOperation.LAUNCH,
                execution_identity=X,
                execution_spec_hash=execution_spec_hash(spec),
                spec=spec,
                fault_point="none",
            )
        ))["spec"],
        "fault_point": "none",
    }
    for forbidden in ("user", "group", "systemd_properties"):
        payload = dict(base)
        payload[forbidden] = "root" if forbidden != "systemd_properties" else {"User": "root"}
        with pytest.raises(ProtocolError, match="unknown_request_field"):
            decode_request(json.dumps(payload).encode())


def test_oversized_frame_is_rejected_before_payload_read() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
        with pytest.raises(ProtocolError, match="frame_too_large"):
            recv_frame(right)
    finally:
        left.close()
        right.close()


def test_stop_request_has_no_arbitrary_service_or_signal_surface() -> None:
    request = RunnerStopRequest(
        version=PROTOCOL_VERSION,
        operation=RunnerOperation.STOP,
        execution_identity=X,
        execution_spec_hash="b" * 64,
        force=True,
    )

    decoded = json.loads(encode_request(request))
    assert set(decoded) == {
        "version",
        "operation",
        "execution_identity",
        "execution_spec_hash",
        "force",
    }
