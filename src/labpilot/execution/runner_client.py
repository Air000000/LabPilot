import socket

from labpilot.execution.runner_protocol import (
    RunnerRequest,
    decode_response,
    encode_request,
    pack_frame,
    recv_frame,
)


class RunnerPreSendError(ConnectionError):
    request_bytes_sent = 0


class RunnerControlUnknownError(ConnectionError):
    def __init__(self, request_bytes_sent: int) -> None:
        super().__init__("runner control outcome is unknown")
        self.request_bytes_sent = request_bytes_sent


class RunnerClient:
    def __init__(self, socket_path: str, *, timeout_seconds: float = 5.0) -> None:
        self._socket_path = socket_path
        self._timeout_seconds = timeout_seconds

    def request(self, request: RunnerRequest) -> dict[str, object]:
        try:
            frame = pack_frame(encode_request(request))
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self._timeout_seconds)
            connection.connect(self._socket_path)
        except Exception as error:
            raise RunnerPreSendError("runner request could not be sent") from error

        sent = 0
        try:
            while sent < len(frame):
                count = connection.send(memoryview(frame)[sent:])
                if count <= 0:
                    raise ConnectionError("runner socket send returned zero")
                sent += count
            return decode_response(recv_frame(connection))
        except Exception as error:
            if sent == 0:
                raise RunnerPreSendError("runner request could not be sent") from error
            raise RunnerControlUnknownError(sent) from error
        finally:
            connection.close()
