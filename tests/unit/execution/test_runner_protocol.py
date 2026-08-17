import json
import socket
import struct

import pytest

from labpilot.execution.runner_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    InfoInspectionRequest,
    ProtocolError,
    RunnerInfoRequest,
    RunnerLaunchRequest,
    RunnerObserveRequest,
    RunnerOperation,
    RunnerStopRequest,
    decode_request,
    decode_response,
    encode_request,
    recv_frame,
)
from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_hash


def _spec() -> FrozenLaunchSpec:
    return FrozenLaunchSpec(
        schema_version="v1",
        workspace_path="/srv/labpilot/attempts/a",
        source_manifest_sha256="a" * 64,
        python_profile="python-3.12",
        runtime_path="/opt/python/bin/python",
        train_entrypoint_relative_path="train.py",
        workdir_relative_path=".",
        argv=("--output", "/srv/labpilot/attempts/a/runtime/output"),
        dataset_reference_type="uri",
        dataset_argument="s3://dataset/v1",
        dataset_reference_hash="b" * 64,
        environment=(("PATH", "/opt/python/bin:/usr/bin"),),
        timeout_seconds=300,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        launch_profile_version="candidate-b-v1",
        launch_policy_identity="c" * 64,
    )


def test_all_four_requests_round_trip_with_exact_fields() -> None:
    spec = _spec()
    requests = (
        RunnerInfoRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.INFO,
            inspection=InfoInspectionRequest("python-3.12", ("torch", "xformers")),
        ),
        RunnerLaunchRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.LAUNCH,
            execution_identity="labpilot-v1-x",
            execution_spec_hash=frozen_launch_spec_hash(spec),
            spec=spec,
            creation_authority="first_dispatch_proven",
        ),
        RunnerObserveRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.OBSERVE,
            execution_identity="labpilot-v1-x",
            execution_spec_hash="d" * 64,
        ),
        RunnerStopRequest(
            version=PROTOCOL_VERSION,
            operation=RunnerOperation.STOP,
            execution_identity="labpilot-v1-x",
            execution_spec_hash="d" * 64,
        ),
    )

    assert tuple(decode_request(encode_request(request)) for request in requests) == requests


@pytest.mark.parametrize(
    "payload",
    [
        {"version": PROTOCOL_VERSION, "operation": "INFO"},
        {
            "version": PROTOCOL_VERSION,
            "operation": "STOP",
            "execution_identity": "x",
            "execution_spec_hash": "a" * 64,
            "unit": "ssh.service",
        },
        {
            "version": PROTOCOL_VERSION,
            "operation": "LAUNCH",
            "execution_identity": "x",
            "execution_spec_hash": "a" * 64,
            "spec": {},
            "creation_authority": "first_dispatch_proven",
            "systemd_properties": {"User": "root"},
        },
    ],
)
def test_missing_or_excess_request_fields_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ProtocolError, match="request_field"):
        decode_request(json.dumps(payload).encode())


def test_wrong_protocol_version_is_rejected() -> None:
    payload = {
        "version": "future",
        "operation": "INFO",
        "inspection": None,
    }
    with pytest.raises(ProtocolError, match="protocol_version_unsupported"):
        decode_request(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "names",
    [tuple(f"dist-{index}" for index in range(33)), ("../torch",), ("x" * 129,)],
)
def test_info_inspection_is_bounded(names: tuple[str, ...]) -> None:
    request = RunnerInfoRequest(
        version=PROTOCOL_VERSION,
        operation=RunnerOperation.INFO,
        inspection=InfoInspectionRequest("python-3.12", names),
    )
    with pytest.raises(ProtocolError, match="inspection"):
        encode_request(request)


def test_launch_and_stop_have_no_raw_privileged_authority() -> None:
    spec = _spec()
    launch = json.loads(
        encode_request(
            RunnerLaunchRequest(
                PROTOCOL_VERSION,
                RunnerOperation.LAUNCH,
                "labpilot-v1-x",
                frozen_launch_spec_hash(spec),
                spec,
                "first_dispatch_proven",
            )
        )
    )
    stop = json.loads(
        encode_request(
            RunnerStopRequest(
                PROTOCOL_VERSION,
                RunnerOperation.STOP,
                "labpilot-v1-x",
                frozen_launch_spec_hash(spec),
            )
        )
    )
    forbidden = {"unit", "user", "group", "signal", "systemd_properties", "executable"}
    assert forbidden.isdisjoint(launch)
    assert forbidden.isdisjoint(stop)


def test_oversized_frame_is_rejected_before_payload_read() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
        with pytest.raises(ProtocolError, match="frame_too_large"):
            recv_frame(right)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    "result",
    [
        {"kind": "created", "detail_code": "accepted", "unit": "forbidden"},
        {"kind": "created"},
    ],
)
def test_response_result_rejects_excess_or_missing_fields(
    result: dict[str, object],
) -> None:
    payload = {
        "version": PROTOCOL_VERSION,
        "operation": "LAUNCH",
        "outcome": "ok",
        "result": result,
    }

    with pytest.raises(ProtocolError, match="result"):
        decode_response(json.dumps(payload).encode())
