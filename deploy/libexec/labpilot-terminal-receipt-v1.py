import json
import os
import re
import sys
from collections.abc import Mapping

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,63}")
_SOURCE_NAMES = ("SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS")


def _optional_token(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(name)
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(name) from error
    if len(encoded) > 64 or _TOKEN.fullmatch(value) is None:
        raise ValueError(name)
    return value


def main(environ: Mapping[str, str] | None = None) -> int:
    source = os.environ if environ is None else environ
    try:
        payload = {
            "schema_version": "labpilot-terminal-receipt-v1",
            "service_result": _optional_token(source, _SOURCE_NAMES[0]),
            "exit_code": _optional_token(source, _SOURCE_NAMES[1]),
            "exit_status": _optional_token(source, _SOURCE_NAMES[2]),
        }
    except (TypeError, ValueError):
        return 1
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii") + b"\n"
    os.write(1, encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
