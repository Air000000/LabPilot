import importlib
import json

import pytest


def _module():
    return importlib.import_module("labpilot.execution.terminal_receipt")


def _message(
    service_result: str | None,
    exit_code: str | None,
    exit_status: str | None,
) -> str:
    return json.dumps(
        {
            "schema_version": "labpilot-terminal-receipt-v1",
            "service_result": service_result,
            "exit_code": exit_code,
            "exit_status": exit_status,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_parse_terminal_receipt_v1_accepts_exact_canonical_message() -> None:
    receipt = _module().parse_terminal_receipt_v1(
        _message("exit-code", "exited", "7")
    )

    assert receipt.service_result == "exit-code"
    assert receipt.exit_code == "exited"
    assert receipt.exit_status == "7"


@pytest.mark.parametrize(
    "message",
    [
        object(),
        "not-json",
        '{"schema_version":"labpilot-terminal-receipt-v1"}',
        _message("success", "exited", "0") + "\n",
        _message("success", "exited", "0").replace(",", ", ", 1),
        _message("success", "exited", "0").replace(
            '"service_result":"success"', '"service_result":"success","extra":null'
        ),
        _message("success", "exited", "0").replace(
            "labpilot-terminal-receipt-v1", "labpilot-terminal-receipt-v2"
        ),
        _message("bad value", "exited", "0"),
        _message("A" * 65, "exited", "0"),
        _message("é", "exited", "0"),
    ],
)
def test_parse_terminal_receipt_v1_rejects_noncanonical_or_invalid_message(
    message: object,
) -> None:
    with pytest.raises(ValueError, match="terminal_receipt"):
        _module().parse_terminal_receipt_v1(message)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("success", "exited", "0"), ("success", 0, None)),
        (("exit-code", "exited", "7"), ("exit-code", 7, None)),
        (("success", "killed", "TERM"), ("success", None, 15)),
        (("signal", "killed", "KILL"), ("signal", None, 9)),
        (("timeout", "killed", "TERM"), ("timeout", None, None)),
        (("watchdog", "killed", "ABRT"), ("watchdog", None, None)),
        (("exit-code", "exited", "0"), ("exit-code", None, None)),
        ((None, None, None), (None, None, None)),
    ],
)
def test_map_terminal_receipt_v1_admits_only_measured_combinations(
    values: tuple[str | None, str | None, str | None],
    expected: tuple[str | None, int | None, int | None],
) -> None:
    receipt = _module().parse_terminal_receipt_v1(_message(*values))

    cause = _module().map_terminal_receipt_v1(receipt)

    assert (cause.result, cause.exit_code, cause.termination_signal) == expected
