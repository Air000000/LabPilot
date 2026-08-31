import json
import struct

import pytest

from labpilot.execution.runner_client import (
    RunnerClient,
    RunnerControlUnknownError,
    RunnerPreSendError,
)
from labpilot.execution.runner_protocol import (
    PROTOCOL_VERSION,
    RunnerObserveRequest,
    RunnerOperation,
)

X = f"labpilot-v1-{'a' * 64}-{'b' * 32}"


def _request() -> RunnerObserveRequest:
    return RunnerObserveRequest(
        PROTOCOL_VERSION,
        RunnerOperation.OBSERVE,
        X,
        "a" * 64,
    )


def _response_frame() -> bytes:
    payload = json.dumps(
        {
            "version": PROTOCOL_VERSION,
            "operation": "OBSERVE",
            "outcome": "ok",
            "result": {
                "kind": "not_found",
                "observed_spec_hash": None,
                "pid": None,
                "invocation_id": None,
                "boot_id": "boot-1",
                "result": None,
                "exit_code": None,
                "termination_signal": None,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return struct.pack("!I", len(payload)) + payload


class _Socket:
    family = None
    calls = 0

    def __init__(self, *args: object) -> None:
        type(self).calls += 1
        type(self).family = args[0]
        self.response = bytearray(_response_frame())
        self.closed = False
        self.sent = 0

    def settimeout(self, timeout: float) -> None:
        assert timeout == 2.0

    def connect(self, path: str) -> None:
        assert path == "/run/labpilot-runner/control.sock"

    def send(self, data: memoryview) -> int:
        self.sent += len(data)
        return len(data)

    def recv(self, length: int) -> bytes:
        chunk = bytes(self.response[:length])
        del self.response[:length]
        return chunk

    def close(self) -> None:
        self.closed = True


def test_client_uses_one_af_unix_connection_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _Socket.calls = 0
    monkeypatch.setattr("labpilot.execution.runner_client.socket.socket", _Socket)

    response = RunnerClient(
        "/run/labpilot-runner/control.sock", timeout_seconds=2.0
    ).request(_request())

    assert _Socket.calls == 1
    assert response["result"]["kind"] == "not_found"  # type: ignore[index]


def test_connect_failure_proves_zero_request_bytes_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConnectFailure(_Socket):
        instance: "ConnectFailure | None" = None

        def __init__(self, *args: object) -> None:
            super().__init__(*args)
            type(self).instance = self

        def connect(self, path: str) -> None:
            del path
            raise FileNotFoundError("socket absent")

    monkeypatch.setattr("labpilot.execution.runner_client.socket.socket", ConnectFailure)

    with pytest.raises(RunnerPreSendError) as captured:
        RunnerClient(
            "/run/labpilot-runner/control.sock", timeout_seconds=2.0
        ).request(_request())
    assert captured.value.request_bytes_sent == 0
    assert ConnectFailure.instance is not None
    assert ConnectFailure.instance.closed


def test_partial_send_then_timeout_is_control_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PartialSend(_Socket):
        def send(self, data: memoryview) -> int:
            if self.sent:
                raise TimeoutError("send timeout")
            sent = min(7, len(data))
            self.sent += sent
            return sent

    monkeypatch.setattr("labpilot.execution.runner_client.socket.socket", PartialSend)

    with pytest.raises(RunnerControlUnknownError) as captured:
        RunnerClient(
            "/run/labpilot-runner/control.sock", timeout_seconds=2.0
        ).request(_request())
    assert captured.value.request_bytes_sent == 7


def test_full_send_then_eof_is_control_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    class LostResponse(_Socket):
        def recv(self, length: int) -> bytes:
            del length
            return b""

    monkeypatch.setattr("labpilot.execution.runner_client.socket.socket", LostResponse)

    with pytest.raises(RunnerControlUnknownError) as captured:
        RunnerClient(
            "/run/labpilot-runner/control.sock", timeout_seconds=2.0
        ).request(_request())
    assert captured.value.request_bytes_sent > 0
