from __future__ import annotations

import time
from collections.abc import Callable
from typing import NoReturn, TypeVar

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class ConnectionPrevented(ConnectionError):
    request_bytes_sent = 0


class ResponseLostAfterDelegate(ConnectionError):
    def __init__(self, response: object) -> None:
        super().__init__("response lost after delegated request completed")
        self.delegate_completed = True
        self.response = response


class CrashAfterDelegate(RuntimeError):
    def __init__(self, result: object) -> None:
        super().__init__("controlled crash after delegated operation completed")
        self.delegate_completed = True
        self.result = result


class ObservationTemporarilyUnavailable(ConnectionError):
    pass


def prevent_connection() -> NoReturn:
    raise ConnectionPrevented("connection prevented before first byte")


def delay_then_delegate(
    delegate: Callable[[RequestT], ResponseT],
    request: RequestT,
    *,
    delay_seconds: float,
    wait: Callable[[float], None] = time.sleep,
) -> ResponseT:
    if not 0 <= delay_seconds <= 300:
        raise ValueError("delay_seconds must be between 0 and 300")
    wait(delay_seconds)
    return delegate(request)


def delegate_then_drop(
    delegate: Callable[[RequestT], ResponseT], request: RequestT
) -> NoReturn:
    response = delegate(request)
    raise ResponseLostAfterDelegate(response)


def crash_after_delegate(delegate: Callable[[], object]) -> NoReturn:
    result = delegate()
    raise CrashAfterDelegate(result)


def temporarily_unavailable() -> NoReturn:
    raise ObservationTemporarilyUnavailable("controlled observation outage")
