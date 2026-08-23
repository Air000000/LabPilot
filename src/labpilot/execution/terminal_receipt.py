import json
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,63}")
_FIELDS = {"schema_version", "service_result", "exit_code", "exit_status"}


@dataclass(frozen=True, slots=True)
class TerminalReceiptV1:
    service_result: str | None
    exit_code: str | None
    exit_status: str | None


@dataclass(frozen=True, slots=True)
class TerminalCause:
    result: str | None
    exit_code: int | None
    termination_signal: int | None


def parse_terminal_receipt_v1(message: object) -> TerminalReceiptV1:
    if type(message) is not str:
        raise ValueError("terminal_receipt_invalid")
    try:
        value = json.loads(message)
    except json.JSONDecodeError as error:
        raise ValueError("terminal_receipt_invalid") from error
    if (
        type(value) is not dict
        or set(value) != _FIELDS
        or value.get("schema_version") != "labpilot-terminal-receipt-v1"
    ):
        raise ValueError("terminal_receipt_invalid")
    if json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) != message:
        raise ValueError("terminal_receipt_noncanonical")
    return TerminalReceiptV1(
        service_result=_optional_token(value["service_result"]),
        exit_code=_optional_token(value["exit_code"]),
        exit_status=_optional_token(value["exit_status"]),
    )


def map_terminal_receipt_v1(receipt: TerminalReceiptV1) -> TerminalCause:
    values = (receipt.service_result, receipt.exit_code, receipt.exit_status)
    if values == ("success", "exited", "0"):
        return TerminalCause("success", 0, None)
    if (
        receipt.service_result == "exit-code"
        and receipt.exit_code == "exited"
        and receipt.exit_status is not None
        and receipt.exit_status.isdigit()
        and int(receipt.exit_status) > 0
    ):
        return TerminalCause("exit-code", int(receipt.exit_status), None)
    if values == ("success", "killed", "TERM"):
        return TerminalCause("success", None, 15)
    if values == ("signal", "killed", "KILL"):
        return TerminalCause("signal", None, 9)
    if values == ("timeout", "killed", "TERM"):
        return TerminalCause("timeout", None, None)
    return TerminalCause(receipt.service_result, None, None)


def _optional_token(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("terminal_receipt_invalid")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("terminal_receipt_invalid") from error
    if len(encoded) > 64 or _TOKEN.fullmatch(value) is None:
        raise ValueError("terminal_receipt_invalid")
    return value
