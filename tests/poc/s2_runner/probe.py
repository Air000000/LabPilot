import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-identity", required=True)
    parser.add_argument("--execution-spec-hash", required=True)
    parser.add_argument("--start-marker", required=True, type=Path)
    parser.add_argument("--sleep-seconds", required=True, type=float)
    parser.add_argument("--exit-code", required=True, type=int)
    parser.add_argument("--child-sleep-seconds", required=True, type=float)
    parser.add_argument("--print-env", action="append", default=[])
    arguments = parser.parse_args(argv)

    started = {
        "event": "probe_started",
        "execution_identity": arguments.execution_identity,
        "execution_spec_hash": arguments.execution_spec_hash,
        "pid": os.getpid(),
        "timestamp": _timestamp(),
    }
    _append_marker(arguments.start_marker, started)
    _print_record(started)
    print(
        json.dumps(
            {
                "event": "probe_stderr",
                "execution_identity": arguments.execution_identity,
                "execution_spec_hash": arguments.execution_spec_hash,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )
    if arguments.print_env:
        _print_record(
            {
                "event": "probe_environment",
                "execution_identity": arguments.execution_identity,
                "execution_spec_hash": arguments.execution_spec_hash,
                "environment": {key: os.environ.get(key) for key in arguments.print_env},
            }
        )
    if arguments.child_sleep_seconds > 0:
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time; time.sleep(float(sys.argv[1]))",
                str(arguments.child_sleep_seconds),
            ],
            close_fds=True,
        )
    time.sleep(arguments.sleep_seconds)
    _print_record(
        {
            "event": "probe_terminal",
            "execution_identity": arguments.execution_identity,
            "execution_spec_hash": arguments.execution_spec_hash,
            "exit_code": arguments.exit_code,
            "timestamp": _timestamp(),
        }
    )
    return int(arguments.exit_code)


def _append_marker(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as marker:
        marker.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        marker.flush()
        os.fsync(marker.fileno())


def _print_record(record: dict[str, object]) -> None:
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
